#!/usr/bin/env python3
"""RAG全流程测试脚本 - 使用 Redis 代码仓数据，按模块生成多页 wiki。

源码上下文方案（参考 deepwiki）：
  - 通过 qualified_name 推导 .c 文件路径（redis.src.<module>.<func> -> src/<module>.c）
  - 用 start_line/end_line 精确读取函数体
  - 按 deepwiki 格式组装 <File Path> 上下文块传给 LLM

Usage:
    python test_rag_redis.py
    python test_rag_redis.py --max-pages 5
    python test_rag_redis.py --repo-path /path/to/redis --max-pages 20
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

MAX_SOURCE_CHARS_PER_FUNC = 2000
MAX_FUNCS_IN_CONTEXT = 6

PROJECT_ROOT = Path(__file__).parent.parent.parent


def setup_environment():
    sys.path.insert(0, str(PROJECT_ROOT))
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())
    if not os.getenv("MOONSHOT_API_KEY"):
        print("错误: MOONSHOT_API_KEY 未设置")
        sys.exit(1)


def load_redis_graph(db_path: Path, repo_path: Path):
    from code_graph_builder import CodeGraphBuilder

    builder = CodeGraphBuilder(
        repo_path=str(repo_path),
        backend="kuzu",
        backend_config={"db_path": str(db_path)},
        scan_config={"include_languages": {"c"}},
    )
    return builder


def qualified_name_to_file(qname: str, repo_path: Path) -> Path | None:
    """将 qualified_name 映射到源文件路径。

    规则：redis.src.<module>.<func> -> <repo>/src/<module>.c
    其余子目录格式（utils 等）暂不处理。
    """
    parts = qname.split(".")
    if len(parts) < 4:
        return None
    if parts[1] != "src":
        return None
    module = parts[2]
    file_path = repo_path / "src" / f"{module}.c"
    return file_path if file_path.exists() else None


def read_function_source(func: dict, repo_path: Path) -> str | None:
    """从磁盘读取函数的真实源码，跳过单行前向声明。"""
    qname = func.get("qualified_name", "")
    start_line = func.get("start_line", 0)
    end_line = func.get("end_line", 0)

    if start_line == 0 or start_line == end_line:
        return None

    file_path = qualified_name_to_file(qname, repo_path)
    if file_path is None:
        return None

    try:
        with open(file_path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        source = "".join(lines[start_line - 1 : end_line])
        if len(source) > MAX_SOURCE_CHARS_PER_FUNC:
            source = source[:MAX_SOURCE_CHARS_PER_FUNC] + "\n    /* ... truncated ... */"
        return source
    except OSError:
        return None


def build_source_context(functions: list[dict], repo_path: Path) -> str:
    """按 deepwiki 格式组装源码上下文块。"""
    file_chunks: dict[str, list[str]] = {}

    for func in functions:
        source = read_function_source(func, repo_path)
        if not source:
            continue
        qname = func.get("qualified_name", "")
        parts = qname.split(".")
        module = parts[2] if len(parts) >= 4 and parts[1] == "src" else "unknown"
        filename = f"src/{module}.c"
        entry = f"// {func['name']} (line {func['start_line']}-{func['end_line']})\n{source}"
        file_chunks.setdefault(filename, []).append(entry)

    if not file_chunks:
        return ""

    result_parts = []
    for filename, chunks in file_chunks.items():
        header = f"## File Path: {filename}"
        body = "\n\n".join(f"```c\n{chunk}\n```" for chunk in chunks)
        result_parts.append(f"{header}\n\n{body}")

    return "\n\n----------\n\n".join(result_parts)


def build_module_index(builder) -> dict[str, list[dict]]:
    """通过 Kùzu 查询按模块名分组函数节点（仅 src/ 下的 C 文件）。"""
    rows = builder.query(
        "MATCH (f:Function) RETURN f.name, f.qualified_name, f.start_line, f.end_line"
    )
    modules: dict[str, list[dict]] = {}
    for row in rows:
        name, qname, start_line, end_line = row["result"]
        parts = qname.split(".")
        if len(parts) < 4 or parts[1] != "src":
            continue
        module = parts[2]
        modules.setdefault(module, []).append({
            "name": name,
            "qualified_name": qname,
            "start_line": start_line or 0,
            "end_line": end_line or 0,
        })
    return modules


def select_representative_functions(functions: list[dict], n: int) -> list[dict]:
    """选取最具代表性的函数：优先选多行函数（真实实现），按行数降序。"""
    multi_line = [f for f in functions if f["end_line"] > f["start_line"] + 2]
    multi_line.sort(key=lambda f: f["end_line"] - f["start_line"], reverse=True)
    return multi_line[:n]


def get_module_call_stats(builder, module_name: str) -> dict:
    """获取模块的调用关系统计。"""
    rows = builder.query(f"""
        MATCH (caller:Function)-[:CALLS]->(callee:Function)
        WHERE caller.qualified_name CONTAINS '.src.{module_name}.'
        RETURN callee.name AS name, count(*) AS cnt
        ORDER BY cnt DESC LIMIT 5
    """)
    top_called = [(r["result"][0], r["result"][1]) for r in rows]

    rows2 = builder.query(f"""
        MATCH (caller:Function)-[:CALLS]->(callee:Function)
        WHERE callee.qualified_name CONTAINS '.src.{module_name}.'
        RETURN caller.name AS name, count(*) AS cnt
        ORDER BY cnt DESC LIMIT 5
    """)
    top_callers = [(r["result"][0], r["result"][1]) for r in rows2]

    return {"top_called": top_called, "top_callers": top_callers}


def analyze_module_page(
    module_name: str,
    functions: list[dict],
    agent,
    repo_path: Path,
    call_stats: dict,
) -> str:
    """用 CamelAgent 分析一个 Redis 模块，注入真实源码上下文。"""
    representative = select_representative_functions(functions, MAX_FUNCS_IN_CONTEXT)
    source_context = build_source_context(representative, repo_path)

    func_list = "\n".join(
        f"- `{f['name']}` (line {f['start_line']}-{f['end_line']})"
        for f in sorted(functions, key=lambda f: f["start_line"])[:15]
    )

    top_called_str = "\n".join(
        f"  - `{name}`: 被调用 {cnt} 次" for name, cnt in call_stats["top_called"]
    )
    top_callers_str = "\n".join(
        f"  - `{name}`: 调用 {cnt} 次" for name, cnt in call_stats["top_callers"]
    )

    if source_context:
        context_section = (
            f"以下是从源文件 `src/{module_name}.c` 中提取的代表性函数实现：\n\n"
            f"<START_OF_CONTEXT>\n{source_context}\n<END_OF_CONTEXT>"
        )
    else:
        context_section = "（源文件不可访问，仅凭函数名分析）"

    call_info = ""
    if top_called_str:
        call_info += f"\n该模块最常调用的外部函数：\n{top_called_str}\n"
    if top_callers_str:
        call_info += f"\n最常调用该模块函数的外部函数：\n{top_callers_str}\n"

    task = (
        f"请对 Redis 数据库的 `{module_name}` 模块（`src/{module_name}.c`）进行系统性分析。\n\n"
        f"该模块共 {len(functions)} 个函数，部分列表：\n{func_list}\n"
        f"{call_info}\n"
        f"{context_section}\n\n"
        "请按如下结构输出（Markdown 格式）：\n"
        "## 模块概述\n"
        "## 核心函数分析（结合上方真实代码）\n"
        "## 模块间依赖关系\n"
        "## 关键实现细节\n"
    )

    response = agent.analyze(task=task)
    return response.content


def generate_wiki(
    builder,
    output_dir: Path,
    max_pages: int,
    repo_path: Path,
) -> tuple[Path, int]:
    from code_graph_builder.rag.camel_agent import CamelAgent
    from code_graph_builder.rag.kimi_client import create_kimi_client

    kimi_client = create_kimi_client(
        api_key=os.getenv("MOONSHOT_API_KEY"),
        model=os.getenv("MOONSHOT_MODEL", "kimi-k2.5"),
        temperature=1.0,
    )
    agent = CamelAgent(
        role="Redis 数据库代码分析专家",
        goal="结合真实源码系统分析 Redis 各模块的功能、实现细节和架构关系",
        backstory="拥有20年 C 语言和数据库系统开发经验，深入理解 Redis 源码架构",
        kimi_client=kimi_client,
    )

    modules = build_module_index(builder)
    sorted_modules = sorted(modules.items(), key=lambda x: -len(x[1]))
    pages_to_generate = sorted_modules[:max_pages]

    print(f"\n共 {len(modules)} 个模块（src/），将生成 {len(pages_to_generate)} 个 wiki 页面")
    print(f"源码路径: {repo_path}")
    print("模块（按函数数量排序）:")
    for name, funcs in pages_to_generate:
        rep = select_representative_functions(funcs, MAX_FUNCS_IN_CONTEXT)
        src_count = sum(1 for f in rep if read_function_source(f, repo_path))
        print(f"  {name}: {len(funcs)} 函数，{src_count}/{len(rep)} 个有源码")

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = output_dir / f"redis_wiki_{timestamp}.md"

    pages: list[dict] = []

    for i, (module_name, functions) in enumerate(pages_to_generate, 1):
        rep = select_representative_functions(functions, MAX_FUNCS_IN_CONTEXT)
        src_count = sum(1 for f in rep if read_function_source(f, repo_path))
        print(f"\n[{i}/{len(pages_to_generate)}] {module_name} ({len(functions)} 函数, {src_count} 段真实源码)...")
        try:
            call_stats = get_module_call_stats(builder, module_name)
            content = analyze_module_page(module_name, functions, agent, repo_path, call_stats)
            pages.append({
                "id": module_name,
                "title": module_name,
                "function_count": len(functions),
                "source_snippets": src_count,
                "content": content,
            })
            print(f"  完成 ({len(content)} 字符)")
        except Exception as e:
            print(f"  失败: {e}")
            pages.append({
                "id": module_name,
                "title": module_name,
                "function_count": len(functions),
                "source_snippets": 0,
                "content": f"分析失败: {e}",
            })

    # 全局统计
    total_funcs_row = builder.query("MATCH (f:Function) RETURN count(f) AS cnt")
    total_funcs = total_funcs_row[0]["result"][0] if total_funcs_row else 0
    total_calls_row = builder.query("MATCH ()-[r:CALLS]->() RETURN count(r) AS cnt")
    total_calls = total_calls_row[0]["result"][0] if total_calls_row else 0

    lines = [
        "# Redis 源码 Wiki",
        "",
        f"*生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
        f"*模型: {os.getenv('MOONSHOT_MODEL', 'kimi-k2.5')}*",
        f"*页面数: {len(pages)} | 源码上下文: 已启用*",
        "",
        "## 图数据概览",
        "",
        f"| 指标 | 数值 |",
        f"|------|------|",
        f"| 总函数数 | {total_funcs:,} |",
        f"| 总调用关系 | {total_calls:,} |",
        f"| src/ 模块数 | {len(modules)} |",
        f"| 本次生成页面 | {len(pages)} |",
        "",
        "---",
        "",
        "## 目录",
        "",
    ]
    for p in pages:
        lines.append(
            f"- [{p['title']}](#{p['id']}) "
            f"({p['function_count']} 函数, {p['source_snippets']} 段源码)"
        )
    lines += ["", "---", ""]

    for p in pages:
        lines += [
            f"<a id='{p['id']}'></a>",
            "",
            f"## {p['title']}",
            "",
            f"*函数数量: {p['function_count']} | 源码片段: {p['source_snippets']}*",
            "",
            p["content"],
            "",
            "---",
            "",
        ]

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nWiki 已保存: {report_path}")
    print(f"文件大小: {report_path.stat().st_size:,} 字节")
    print(f"总页面数: {len(pages)}")
    return report_path, len(pages)


def main():
    parser = argparse.ArgumentParser(description="Redis Wiki 生成器（含真实源码上下文）")
    parser.add_argument("--max-pages", type=int, default=10, help="最多生成几个 wiki 页面 (默认: 10)")
    parser.add_argument("--output-dir", type=Path, default=Path("./rag_output"), help="输出目录")
    parser.add_argument(
        "--repo-path",
        type=Path,
        default=Path("/Users/jiaojeremy/CodeFile/redis"),
        help="Redis 源码仓路径",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=Path("./redis_graph.db"),
        help="Kùzu 数据库路径（需已构建）",
    )
    args = parser.parse_args()

    setup_environment()

    print("=" * 60)
    print("Redis Wiki 生成器（含真实源码上下文）")
    print("=" * 60)
    print(f"数据库: {args.db_path}")
    print(f"源码路径: {args.repo_path}")

    builder = load_redis_graph(args.db_path, args.repo_path)

    try:
        report_path, page_count = generate_wiki(
            builder=builder,
            output_dir=args.output_dir,
            max_pages=args.max_pages,
            repo_path=args.repo_path,
        )
        print(f"\n完成! 生成了 {page_count} 个页面")
        print(f"报告: {report_path}")
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
