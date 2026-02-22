#!/usr/bin/env python3
"""RAG全流程测试脚本 - 使用 tinycc 代码仓数据，按模块生成多页 wiki。

源码上下文方案（参考 deepwiki）：
  - 通过 qualified_name 推导 .c 文件路径
  - 用 start_line/end_line 精确读取函数体（天然 chunk）
  - 按 deepwiki 格式组装 <File Path> 上下文块传给 LLM

Usage:
    python test_rag_tinycc.py
    python test_rag_tinycc.py --max-pages 5
    python test_rag_tinycc.py --repo-path /path/to/tinycc --max-pages 10
"""

from __future__ import annotations

import argparse
import json
import os
import sys
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


def load_tinycc_graph():
    export_path = PROJECT_ROOT / "tinycc_kuzu" / "export.json"
    if not export_path.exists():
        print(f"错误: 找不到 {export_path}，请先运行 tinycc 代码分析")
        sys.exit(1)
    with open(export_path) as f:
        data = json.load(f)
    print(f"已加载图数据: {len(data.get('nodes', []))} 节点, {len(data.get('relationships', []))} 关系")
    return data


def module_to_filename(module_name: str) -> str:
    """将模块名映射为源文件名，例如 tccgen -> tccgen.c。"""
    return f"{module_name}.c"


def read_function_source(func: dict, repo_path: Path) -> str | None:
    """从磁盘读取函数的真实源码。

    通过 qualified_name 推导文件路径，用 start_line/end_line 截取函数体。
    跳过单行前向声明（start_line == end_line）。
    """
    qname = func.get("qualified_name", "")
    parts = qname.split(".")
    if len(parts) < 3:
        return None

    module_name = parts[1]
    start_line = func.get("start_line", 0)
    end_line = func.get("end_line", 0)

    if start_line == 0 or start_line == end_line:
        return None

    file_path = repo_path / module_to_filename(module_name)
    if not file_path.exists():
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
    """按 deepwiki 格式组装源码上下文块。

    格式：
        ## File Path: tccgen.c

        ```c
        <函数源码>
        ```

        ----------
    """
    file_chunks: dict[str, list[str]] = {}

    for func in functions:
        source = read_function_source(func, repo_path)
        if not source:
            continue
        qname = func.get("qualified_name", "")
        module_name = qname.split(".")[1] if len(qname.split(".")) >= 3 else "unknown"
        filename = module_to_filename(module_name)
        entry = f"// {func['name']} (line {func['start_line']}-{func['end_line']})\n{source}"
        file_chunks.setdefault(filename, []).append(entry)

    if not file_chunks:
        return ""

    parts = []
    for filename, chunks in file_chunks.items():
        header = f"## File Path: {filename}"
        body = "\n\n".join(f"```c\n{chunk}\n```" for chunk in chunks)
        parts.append(f"{header}\n\n{body}")

    return "\n\n----------\n\n".join(parts)


def build_module_index(graph_data: dict) -> dict[str, list[dict]]:
    """按模块名分组函数节点。"""
    modules: dict[str, list[dict]] = {}
    for node in graph_data.get("nodes", []):
        if node.get("label") != "Function":
            continue
        props = node.get("properties", {})
        qname = props.get("qualified_name", "")
        parts = qname.split(".")
        module = parts[1] if len(parts) >= 3 else "unknown"
        modules.setdefault(module, []).append({
            "name": props.get("name", ""),
            "qualified_name": qname,
            "start_line": props.get("start_line", 0),
            "end_line": props.get("end_line", 0),
        })
    return modules


def select_representative_functions(functions: list[dict], n: int) -> list[dict]:
    """选取最具代表性的函数：优先选多行函数（真实实现），按行数降序。"""
    multi_line = [
        f for f in functions
        if f["end_line"] > f["start_line"] + 2
    ]
    multi_line.sort(key=lambda f: f["end_line"] - f["start_line"], reverse=True)
    return multi_line[:n]


def analyze_module_page(
    module_name: str,
    functions: list[dict],
    agent,
    repo_path: Path,
) -> str:
    """用 CamelAgent 分析一个模块，将真实源码注入上下文。"""
    representative = select_representative_functions(functions, MAX_FUNCS_IN_CONTEXT)
    source_context = build_source_context(representative, repo_path)

    func_list = "\n".join(
        f"- `{f['name']}` (line {f['start_line']}-{f['end_line']})"
        for f in sorted(functions, key=lambda f: f["start_line"])[:12]
    )

    if source_context:
        context_section = (
            f"以下是从源文件中提取的代表性函数实现：\n\n"
            f"<START_OF_CONTEXT>\n{source_context}\n<END_OF_CONTEXT>"
        )
    else:
        context_section = "（源文件不可访问，仅凭函数名分析）"

    task = (
        f"请对 TinyCC 编译器的 `{module_name}` 模块进行系统性分析。\n\n"
        f"该模块共 {len(functions)} 个函数，部分列表：\n{func_list}\n\n"
        f"{context_section}\n\n"
        "请按如下结构输出（Markdown 格式）：\n"
        "## 模块概述\n"
        "## 核心函数分析（结合上方真实代码）\n"
        "## 模块间依赖关系\n"
        "## 关键实现细节\n"
    )

    response = agent.analyze(task=task)
    return response.content


def generate_wiki(graph_data: dict, output_dir: Path, max_pages: int, repo_path: Path):
    from code_graph_builder.rag.camel_agent import CamelAgent
    from code_graph_builder.rag.kimi_client import create_kimi_client

    kimi_client = create_kimi_client(
        api_key=os.getenv("MOONSHOT_API_KEY"),
        model=os.getenv("MOONSHOT_MODEL", "kimi-k2.5"),
        temperature=1.0,
    )
    agent = CamelAgent(
        role="TinyCC 编译器代码分析专家",
        goal="结合真实源码系统分析 TinyCC 各模块的功能、实现细节和架构关系",
        backstory="拥有20年 C 语言和编译器开发经验，深入理解 TinyCC 源码",
        kimi_client=kimi_client,
    )

    modules = build_module_index(graph_data)
    sorted_modules = sorted(modules.items(), key=lambda x: -len(x[1]))
    pages_to_generate = sorted_modules[:max_pages]

    print(f"\n共 {len(modules)} 个模块，将生成 {len(pages_to_generate)} 个 wiki 页面")
    print(f"源码路径: {repo_path}")
    print("模块（按函数数量排序）:")
    for name, funcs in pages_to_generate:
        rep = select_representative_functions(funcs, MAX_FUNCS_IN_CONTEXT)
        src_count = sum(1 for f in rep if read_function_source(f, repo_path))
        print(f"  {name}: {len(funcs)} 函数，{src_count}/{len(rep)} 个有源码")

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = output_dir / f"tinycc_wiki_{timestamp}.md"

    pages: list[dict] = []

    for i, (module_name, functions) in enumerate(pages_to_generate, 1):
        rep = select_representative_functions(functions, MAX_FUNCS_IN_CONTEXT)
        src_count = sum(1 for f in rep if read_function_source(f, repo_path))
        print(f"\n[{i}/{len(pages_to_generate)}] {module_name} ({len(functions)} 函数, {src_count} 段真实源码)...")
        try:
            content = analyze_module_page(module_name, functions, agent, repo_path)
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

    lines = [
        "# TinyCC Wiki",
        "",
        f"*生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*",
        f"*模型: {os.getenv('MOONSHOT_MODEL', 'kimi-k2.5')}*",
        f"*页面数: {len(pages)}，源码上下文: 已启用*",
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
    parser = argparse.ArgumentParser(description="TinyCC Wiki 生成器（含真实源码上下文）")
    parser.add_argument("--max-pages", type=int, default=10, help="最多生成几个 wiki 页面 (默认: 10)")
    parser.add_argument("--output-dir", type=Path, default=Path("./rag_output"), help="输出目录")
    parser.add_argument(
        "--repo-path",
        type=Path,
        default=Path("/Users/jiaojeremy/CodeFile/tinycc"),
        help="tinycc 源码仓路径",
    )
    args = parser.parse_args()

    setup_environment()

    print("=" * 60)
    print("TinyCC Wiki 生成器（含真实源码上下文）")
    print("=" * 60)

    graph_data = load_tinycc_graph()

    try:
        report_path, page_count = generate_wiki(
            graph_data=graph_data,
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
