#!/usr/bin/env python3
"""通用代码 Wiki 生成器 - 对齐 deepwiki 两阶段流程。

流程（对齐 deepwiki）：
  阶段一（determineWikiStructure）：
    读取文件树 + README → LLM 规划 XML 格式 Wiki 目录结构
    → 解析出若干页面（每页含标题、描述、相关源文件）

  阶段二（generatePageContent）：
    对每个规划页面，用页面标题作 query → 向量检索相关源码片段
    → 使用 deepwiki page content prompt 生成含 Mermaid 图/表格/行号引用的 Markdown

输出结构（对齐 deepwiki）：
  output_dir/
  ├── index.md              # summary hub：项目概览 + 页面索引表
  └── wiki/
      ├── <page-id>.md      # 每个规划页面独立一个文件
      └── ...

Wiki 模式（对齐 deepwiki）：
  --comprehensive  生成 8-12 页详细 wiki（默认）
  --concise        生成 4-6 页简洁 wiki

Usage:
    python generate_wiki.py --repo-path /path/to/repo
    python generate_wiki.py --repo-path /path/to/repo --concise
    python generate_wiki.py --repo-path /path/to/repo --max-pages 12
    python generate_wiki.py --repo-path /path/to/repo --rebuild
    python generate_wiki.py --repo-path /path/to/repo --output-dir ./my_wiki
    python generate_wiki.py --repo-path /path/to/repo --pages page-1 page-3  # 重跑指定页面
"""

from __future__ import annotations

import argparse
import os
import pickle
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

MAX_SOURCE_CHARS_PER_FUNC = 2000
MAX_FUNCS_IN_CONTEXT = 8
EMBED_BATCH_SIZE = 10

MAX_PAGES_COMPREHENSIVE = 10
MAX_PAGES_CONCISE = 5

# 文件树最大行数，避免 prompt 过长
MAX_FILETREE_LINES = 300
# README 最大字符数
MAX_README_CHARS = 3000

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
        print("错误: MOONSHOT_API_KEY 未设置，请在 .env 文件或环境变量中配置")
        sys.exit(1)
    if not os.getenv("DASHSCOPE_API_KEY"):
        print("错误: DASHSCOPE_API_KEY 未设置，向量检索需要 DashScope API Key")
        sys.exit(1)


# ---------------------------------------------------------------------------
# 图构建
# ---------------------------------------------------------------------------

def build_or_load_graph(repo_path: Path, db_path: Path, rebuild: bool):
    """构建或加载代码图，返回 CodeGraphBuilder 实例。"""
    from code_graph_builder import CodeGraphBuilder

    builder = CodeGraphBuilder(
        repo_path=str(repo_path),
        backend="kuzu",
        backend_config={"db_path": str(db_path), "batch_size": 1000},
    )

    if rebuild or not db_path.exists():
        print(f"构建代码图: {repo_path} -> {db_path}")
        result = builder.build_graph(clean=rebuild)
        print(f"  节点: {result.nodes_created:,}  关系: {result.relationships_created:,}")
    else:
        print(f"复用已有图数据库: {db_path}")

    return builder


# ---------------------------------------------------------------------------
# 源码读取（通用路径推导）
# ---------------------------------------------------------------------------

def resolve_source_file(qname: str, repo_path: Path) -> Path | None:
    parts = qname.split(".")
    if len(parts) < 3:
        return None
    dir_parts = parts[1:-1]
    for depth in range(len(dir_parts), 0, -1):
        for suffix in (".c", ".py", ".h", ".cpp", ".go", ".rs", ".js", ".ts"):
            candidate = repo_path.joinpath(*dir_parts[:depth]).with_suffix(suffix)
            if candidate.exists():
                return candidate
    return None


def read_function_source(func: dict, repo_path: Path) -> str | None:
    qname = func.get("qualified_name", "")
    start_line = func.get("start_line", 0)
    end_line = func.get("end_line", 0)
    if start_line == 0 or start_line == end_line:
        return None
    file_path = resolve_source_file(qname, repo_path)
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


def build_source_context(results: list[dict], repo_path: Path) -> str:
    """组装向量检索结果的源码上下文，按文件分组，标注行号。"""
    file_chunks: dict[tuple[str, str], list[str]] = {}
    for func in results:
        source = read_function_source(func, repo_path)
        if not source:
            continue
        file_path = resolve_source_file(func.get("qualified_name", ""), repo_path)
        filename = str(file_path.relative_to(repo_path)) if file_path else "unknown"
        suffix = file_path.suffix.lstrip(".") if file_path else "c"
        entry = f"// {func['name']} (line {func['start_line']}-{func['end_line']})\n{source}"
        file_chunks.setdefault((filename, suffix), []).append(entry)

    if not file_chunks:
        return ""

    result_parts = []
    for (filename, suffix), chunks in file_chunks.items():
        header = f"## File Path: {filename}"
        body = "\n\n".join(f"```{suffix}\n{chunk}\n```" for chunk in chunks)
        result_parts.append(f"{header}\n\n{body}")

    return "\n\n----------\n\n".join(result_parts)


# ---------------------------------------------------------------------------
# Mermaid 语法验证（mmdc）
# ---------------------------------------------------------------------------

MMDC_PATH = shutil.which("mmdc") or "/usr/local/bin/mmdc"


def validate_mermaid_blocks(content: str) -> list[dict]:
    """用 mmdc 逐一验证 Markdown 内容中的所有 Mermaid 块。

    返回错误列表，每项: {index, code, error}
    index 从 1 开始，代表第几个 Mermaid 块。
    """
    blocks = re.findall(r"```mermaid\n(.*?)```", content, re.DOTALL)
    if not blocks:
        return []

    errors = []
    for idx, code in enumerate(blocks, 1):
        with tempfile.NamedTemporaryFile(suffix=".mmd", mode="w", delete=False) as f:
            f.write(code)
            tmp_in = f.name
        tmp_out = tmp_in.replace(".mmd", ".svg")
        try:
            result = subprocess.run(
                [MMDC_PATH, "-i", tmp_in, "-o", tmp_out],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                error_text = (result.stderr or result.stdout).strip()
                # 只保留 Error: 那一行，去掉 stack trace
                error_lines = [l for l in error_text.splitlines() if l.startswith("Error:")]
                short_error = error_lines[0] if error_lines else error_text.split("\n")[0]
                errors.append({"index": idx, "code": code.strip(), "error": short_error})
        except subprocess.TimeoutExpired:
            errors.append({"index": idx, "code": code.strip(), "error": "mmdc timeout"})
        finally:
            Path(tmp_in).unlink(missing_ok=True)
            Path(tmp_out).unlink(missing_ok=True)

    return errors


MAX_MERMAID_FIX_ATTEMPTS = 3


def _try_fix_once(code: str, error: str, attempt: int, agent) -> str | None:
    """向 LLM 发起一次 Mermaid 修复请求，验证通过后返回代码，否则返回 None。"""
    attempt_note = f"（第 {attempt} 次尝试）" if attempt > 1 else ""
    prompt = f"""以下 Mermaid 图表代码存在语法错误，请修复它。{attempt_note}

错误信息：{error}

原始代码：
```mermaid
{code}
```

要求：
- 只输出修复后的 Mermaid 代码，不要任何解释或 markdown 围栏
- 保持图表的原始意图和内容不变
- 使用合法的 Mermaid 语法（graph TD、sequenceDiagram、classDiagram 等）
- 节点 ID 只使用字母、数字和下划线，不使用特殊字符
- 节点标签中的特殊字符用双引号包裹

直接输出修复后的代码："""

    try:
        response = agent.analyze(task=prompt)
        fixed = response.content.strip()
        fixed = re.sub(r"^```mermaid\s*\n?", "", fixed)
        fixed = re.sub(r"\n?```\s*$", "", fixed)
        fixed = fixed.strip()
        if not fixed:
            return None
        test_errors = validate_mermaid_blocks(f"```mermaid\n{fixed}\n```")
        if test_errors:
            return None
        return fixed
    except Exception:
        return None


def fix_mermaid_errors(content: str, errors: list[dict], agent) -> tuple[str, list[dict]]:
    """尝试用 LLM 修复所有有语法错误的 Mermaid 块。

    每个块最多尝试 MAX_MERMAID_FIX_ATTEMPTS 次，超过后直接删除该块。
    返回 (修复后的 content, 删除的 errors 列表)。
    """
    if not errors:
        return content, []

    error_map = {e["index"]: e for e in errors}
    deleted_errors = []
    idx = 0
    result_parts = []
    pos = 0

    for m in re.finditer(r"```mermaid\n(.*?)```", content, re.DOTALL):
        idx += 1
        result_parts.append(content[pos:m.start()])

        if idx in error_map:
            err = error_map[idx]
            fixed_code = None
            for attempt in range(1, MAX_MERMAID_FIX_ATTEMPTS + 1):
                print(f"    修复 Mermaid 块 #{idx}（第 {attempt}/{MAX_MERMAID_FIX_ATTEMPTS} 次）...")
                fixed_code = _try_fix_once(err["code"], err["error"], attempt, agent)
                if fixed_code is not None:
                    print(f"    块 #{idx} 第 {attempt} 次修复成功")
                    break
                print(f"    块 #{idx} 第 {attempt} 次修复失败")

            if fixed_code is not None:
                result_parts.append(f"```mermaid\n{fixed_code}\n```")
            else:
                # 超过最大次数，删除该块
                deleted_errors.append(err)
                print(f"    块 #{idx} 超过 {MAX_MERMAID_FIX_ATTEMPTS} 次仍失败，已删除")
        else:
            result_parts.append(m.group(0))

        pos = m.end()

    result_parts.append(content[pos:])
    return "".join(result_parts), deleted_errors


# ---------------------------------------------------------------------------
# Embedding 索引构建（deepwiki 风格）
# ---------------------------------------------------------------------------

def build_vector_index(builder, repo_path: Path, vectors_path: Path, rebuild: bool):
    """对所有函数源码做 embedding，写入内存向量存储。"""
    from code_graph_builder.domains.core.embedding.qwen3_embedder import create_embedder
    from code_graph_builder.domains.core.embedding.vector_store import MemoryVectorStore, VectorRecord

    embedder = create_embedder(batch_size=EMBED_BATCH_SIZE)

    if not rebuild and vectors_path.exists():
        print(f"从缓存加载向量索引: {vectors_path}")
        with open(vectors_path, "rb") as fh:
            cache = pickle.load(fh)
        vector_store: MemoryVectorStore = cache["vector_store"]
        func_map: dict[int, dict] = cache["func_map"]
        print(f"  已加载 {len(vector_store)} 条 embedding")
        return vector_store, embedder, func_map

    print("构建向量索引（Embedding 所有函数源码）...")
    rows = builder.query(
        "MATCH (f:Function) RETURN f.name, f.qualified_name, f.start_line, f.end_line"
    )
    all_funcs: list[dict] = []
    for row in rows:
        vals = row.get("result") or list(row.values())
        name, qname, start_line, end_line = vals
        all_funcs.append({
            "name": name,
            "qualified_name": qname,
            "start_line": start_line or 0,
            "end_line": end_line or 0,
        })

    embeddable: list[tuple[int, dict, str]] = []
    for i, func in enumerate(all_funcs):
        source = read_function_source(func, repo_path)
        if source:
            text = f"// {func['name']}\n{source}"
            embeddable.append((i, func, text))

    print(f"  共 {len(all_funcs)} 个函数，{len(embeddable)} 个有源码，开始 embedding...")
    texts = [t for _, _, t in embeddable]
    embeddings = embedder.embed_documents(texts, show_progress=True)

    vector_store = MemoryVectorStore(dimension=embedder.get_embedding_dimension())
    func_map = {}
    records = []
    for idx, ((node_id, func, _), embedding) in enumerate(zip(embeddable, embeddings)):
        records.append(VectorRecord(
            node_id=node_id,
            qualified_name=func["qualified_name"],
            embedding=embedding,
            metadata={
                "name": func["name"],
                "start_line": func["start_line"],
                "end_line": func["end_line"],
            },
        ))
        func_map[node_id] = func

    vector_store.store_embeddings_batch(records)
    print(f"  写入 {len(records)} 条 embedding，保存缓存到 {vectors_path}")
    with open(vectors_path, "wb") as fh:
        pickle.dump({"vector_store": vector_store, "func_map": func_map}, fh)

    return vector_store, embedder, func_map


def semantic_search_funcs(
    query: str,
    vector_store,
    embedder,
    func_map: dict[int, dict],
    top_k: int,
) -> list[dict]:
    """向量检索相关函数，失败时返回空列表。"""
    try:
        query_embedding = embedder.embed_query(query)
    except Exception:
        return []
    results = vector_store.search_similar(query_embedding, top_k=top_k)
    found = []
    for r in results:
        func = func_map.get(r.node_id)
        if func:
            found.append(func)
    return found


# ---------------------------------------------------------------------------
# 阶段一：deepwiki determineWikiStructure — 规划 Wiki 目录
# ---------------------------------------------------------------------------

def build_file_tree(repo_path: Path) -> str:
    """生成仓库文件树（忽略隐藏目录和常见无关目录）。"""
    ignore_dirs = {
        ".git", ".github", "__pycache__", "node_modules", ".venv", "venv",
        "dist", "build", ".idea", ".vscode", "*.egg-info",
    }
    lines = []
    for p in sorted(repo_path.rglob("*")):
        # 跳过隐藏目录和忽略目录
        parts = p.relative_to(repo_path).parts
        if any(part.startswith(".") or part in ignore_dirs for part in parts):
            continue
        if p.is_file():
            rel = str(p.relative_to(repo_path))
            lines.append(rel)
        if len(lines) >= MAX_FILETREE_LINES:
            lines.append("... (truncated)")
            break
    return "\n".join(lines)


def read_readme(repo_path: Path) -> str:
    """读取仓库 README 文件。"""
    for name in ("README.md", "README.rst", "README.txt", "README"):
        readme = repo_path / name
        if readme.exists():
            from ..utils.encoding import read_source_file
            text = read_source_file(readme)
            if len(text) > MAX_README_CHARS:
                text = text[:MAX_README_CHARS] + "\n... (truncated)"
            return text
    return "(no README found)"


def plan_wiki_structure(agent, repo_path: Path, project_name: str, comprehensive: bool) -> list[dict]:
    """阶段一：让 LLM 规划 Wiki 结构，返回页面列表。

    对齐 deepwiki determineWikiStructure prompt。
    每个页面: {id, title, description, importance, relevant_files, related_pages}
    """
    file_tree = build_file_tree(repo_path)
    readme = read_readme(repo_path)
    page_count = "8-12" if comprehensive else "4-6"

    prompt = f"""Analyze this repository "{project_name}" and create a wiki structure for it.

1. The complete file tree of the project:
<file_tree>
{file_tree}
</file_tree>

2. The README file of the project:
<readme>
{readme}
</readme>

I want to create a wiki for this repository. Determine the most logical structure for a wiki based on the repository's content.

The wiki content will be generated in Mandarin Chinese (中文).

When designing the wiki structure, include pages that would benefit from visual diagrams, such as:
- Architecture overviews
- Data flow descriptions
- Component relationships
- Process workflows
- State machines
- Class hierarchies

{"Create a structured wiki with sections covering: Overview, System Architecture, Core Features, Data Management/Flow, Key Modules/Components, APIs/Interfaces, and Deployment/Configuration." if comprehensive else "Create a concise wiki focusing on the most important aspects."}

Return your analysis in the following XML format:

<wiki_structure>
  <title>[Overall title for the wiki]</title>
  <description>[Brief description of the repository]</description>
  <pages>
    <page id="page-1">
      <title>[Page title]</title>
      <description>[Brief description of what this page will cover]</description>
      <importance>high|medium|low</importance>
      <relevant_files>
        <file_path>[Path to a relevant file]</file_path>
      </relevant_files>
      <related_pages>
        <related>page-2</related>
      </related_pages>
    </page>
  </pages>
</wiki_structure>

IMPORTANT FORMATTING INSTRUCTIONS:
- Return ONLY the valid XML structure specified above
- DO NOT wrap the XML in markdown code blocks
- DO NOT include any explanation text before or after the XML
- Start directly with <wiki_structure> and end with </wiki_structure>

IMPORTANT:
1. Create {page_count} pages that would make a {"comprehensive" if comprehensive else "concise"} wiki for this repository
2. Each page should focus on a specific aspect of the codebase
3. The relevant_files should be actual files from the repository
4. Return ONLY valid XML"""

    print("阶段一：规划 Wiki 目录结构...")
    response = agent.analyze(task=prompt)
    xml_text = response.content.strip()

    # 提取 XML（防止 LLM 包了 markdown 代码块）
    xml_match = re.search(r"<wiki_structure>.*?</wiki_structure>", xml_text, re.DOTALL)
    if not xml_match:
        print("  警告：未能解析 XML，使用空结构")
        return []

    xml_text = xml_match.group(0)

    # 解析页面列表
    pages = []
    for page_match in re.finditer(r"<page\s+id=[\"']([^\"']+)[\"']>(.*?)</page>", xml_text, re.DOTALL):
        page_id = page_match.group(1)
        page_xml = page_match.group(2)

        title_m = re.search(r"<title>(.*?)</title>", page_xml, re.DOTALL)
        desc_m = re.search(r"<description>(.*?)</description>", page_xml, re.DOTALL)
        importance_m = re.search(r"<importance>(.*?)</importance>", page_xml)
        files = re.findall(r"<file_path>(.*?)</file_path>", page_xml)
        related = re.findall(r"<related>(.*?)</related>", page_xml)

        pages.append({
            "id": page_id,
            "title": title_m.group(1).strip() if title_m else page_id,
            "description": desc_m.group(1).strip() if desc_m else "",
            "importance": importance_m.group(1).strip() if importance_m else "medium",
            "relevant_files": [f.strip() for f in files],
            "related_pages": related,
        })

    print(f"  规划了 {len(pages)} 个页面:")
    for p in pages:
        print(f"    [{p['importance']}] {p['id']}: {p['title']}")

    return pages


# ---------------------------------------------------------------------------
# 阶段二：deepwiki generatePageContent — 生成页面内容
# ---------------------------------------------------------------------------

def generate_page_content(
    page: dict,
    agent,
    repo_path: Path,
    vector_store,
    embedder,
    func_map: dict[int, dict],
) -> str:
    """阶段二：对齐 deepwiki generatePageContent prompt 生成页面 Markdown。

    - 用页面标题做向量检索，获取相关源码作为 context
    - 要求输出: <details> 源文件块、Mermaid 图、表格、行号引用
    """
    # 向量检索相关源码
    query = f"{page['title']} {page['description']}"
    funcs = semantic_search_funcs(query, vector_store, embedder, func_map, MAX_FUNCS_IN_CONTEXT)

    # 若有 relevant_files 指定，也尝试直接读取
    extra_context_parts = []
    for rel_file in page.get("relevant_files", [])[:5]:
        fpath = repo_path / rel_file
        if fpath.exists() and fpath.is_file():
            try:
                from ..utils.encoding import read_source_file
                text = read_source_file(fpath)
                if len(text) > 4000:
                    text = text[:4000] + "\n... (truncated)"
                suffix = fpath.suffix.lstrip(".") or "txt"
                extra_context_parts.append(
                    f"## File Path: {rel_file}\n\n```{suffix}\n{text}\n```"
                )
            except OSError:
                pass

    source_context = build_source_context(funcs, repo_path)

    # 组合 context
    all_context_parts = []
    if extra_context_parts:
        all_context_parts.extend(extra_context_parts)
    if source_context:
        all_context_parts.append(source_context)

    full_context = "\n\n----------\n\n".join(all_context_parts) if all_context_parts else "(源码暂不可访问)"

    # 所有引用文件列表（用于 <details> 块）
    file_refs = list(page.get("relevant_files", []))
    for func in funcs:
        fp = resolve_source_file(func.get("qualified_name", ""), repo_path)
        if fp:
            rel = str(fp.relative_to(repo_path))
            if rel not in file_refs:
                file_refs.append(rel)

    details_files = "\n".join(f"- {f}" for f in file_refs) if file_refs else "- (自动检索)"

    prompt = f"""你是一名专家级技术作家和软件架构师。
你的任务是为软件项目生成一篇关于特定功能、系统或模块的全面、准确的技术 Wiki 页面（Markdown 格式）。

Wiki 页面主题：**{page['title']}**
页面描述：{page['description']}

以下是从项目中检索到的相关源文件内容，你必须以此作为内容的唯一依据：

<START_OF_CONTEXT>
{full_context}
<END_OF_CONTEXT>

请严格按照以下要求生成内容：

**开头必须是 `<details>` 块**，列出所有参考源文件，格式如下（不得在此之前输出任何内容）：

<details>
<summary>Relevant source files</summary>

以下文件被用于生成本 Wiki 页面：

{details_files}
</details>

紧接 `<details>` 块之后，使用 H1 标题：`# {page['title']}`

然后按以下要求生成正文内容：

1. **引言**：1-2 段，说明本页面主题的目的、范围和高层概述。

2. **详细章节**：使用 H2/H3 标题分节，说明架构、组件、数据流或核心逻辑。
   识别关键函数、类、数据结构、API 端点或配置项。

3. **Mermaid 图表**（必须大量使用）：
   - 使用 `graph TD`（从上到下，禁止 `graph LR`）、`sequenceDiagram`、`classDiagram`、`erDiagram` 等
   - 图表必须准确反映源文件中的实际结构和流程
   - 每个图表前后都要有简短说明
   - 序列图箭头规范：`->>` 请求、`-->>` 响应、`->x` 失败

4. **表格**（必须使用）：用 Markdown 表格汇总关键信息，如：
   - 关键函数/组件及其描述
   - API 参数、类型、说明
   - 配置项及默认值
   - 数据模型字段

5. **代码片段**（可选）：直接引用源文件中的关键实现片段，标注语言。

6. **源码引用**（极其重要）：
   - 每个重要信息点、图表、表格后必须标注来源
   - 格式：`Sources: [filename.ext:start_line-end_line]()`
   - 整篇文档必须引用至少 5 个不同源文件

7. **技术准确性**：所有信息必须且只能来自上方提供的源文件。

8. **结语**：用简短段落总结本页面的关键内容及其在项目中的意义。

请用**中文**生成内容。记住：
- 每个论断都必须来自源文件
- 优先保证准确性和对代码实际功能的直接描述
- 文档结构要便于其他开发者理解"""

    response = agent.analyze(task=prompt)
    return response.content


# ---------------------------------------------------------------------------
# Wiki 生成主流程
# ---------------------------------------------------------------------------

def generate_wiki(
    builder,
    repo_path: Path,
    output_dir: Path,
    max_pages: int,
    rebuild: bool,
    comprehensive: bool = True,
    only_pages: list[str] | None = None,
) -> tuple[Path, int]:
    from code_graph_builder.domains.upper.rag.camel_agent import CamelAgent
    from code_graph_builder.domains.upper.rag.client import create_llm_client

    project_name = repo_path.name

    llm_client = create_llm_client(
        api_key=os.getenv("MOONSHOT_API_KEY"),
        model=os.getenv("MOONSHOT_MODEL", "kimi-k2.5"),
        temperature=1.0,
    )
    agent = CamelAgent(
        role=f"{project_name} 技术文档专家",
        goal=f"结合真实源码，为 {project_name} 生成专业、准确、图文并茂的技术 Wiki",
        backstory=f"拥有丰富的技术写作和代码阅读经验，深入理解 {project_name} 源码架构",
        llm_client=llm_client,
    )

    vectors_path = output_dir / f"{project_name}_vectors.pkl"
    output_dir.mkdir(parents=True, exist_ok=True)

    # 构建向量索引
    vector_store, embedder, func_map = build_vector_index(
        builder, repo_path, vectors_path, rebuild
    )

    # 阶段一：规划 Wiki 目录（或加载已有规划）
    structure_cache = output_dir / f"{project_name}_structure.pkl"
    if not rebuild and structure_cache.exists():
        print(f"从缓存加载 Wiki 结构: {structure_cache}")
        with open(structure_cache, "rb") as fh:
            planned_pages = pickle.load(fh)
        print(f"  已加载 {len(planned_pages)} 个页面规划")
    else:
        planned_pages = plan_wiki_structure(agent, repo_path, project_name, comprehensive)
        with open(structure_cache, "wb") as fh:
            pickle.dump(planned_pages, fh)

    # 截取页面数量
    if only_pages:
        pages_to_generate = [p for p in planned_pages if p["id"] in only_pages]
    else:
        # 高重要性优先，再按规划顺序，取 max_pages 个
        high = [p for p in planned_pages if p["importance"] == "high"]
        others = [p for p in planned_pages if p["importance"] != "high"]
        ordered = high + others
        pages_to_generate = ordered[:max_pages]

    wiki_mode = "详细（Comprehensive，8-12页）" if comprehensive else "简洁（Concise，4-6页）"
    print(f"\n将生成 {len(pages_to_generate)} 个 Wiki 页面  [模式: {wiki_mode}]")

    # 阶段二：逐页生成内容
    wiki_dir = output_dir / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)

    gen_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    model_name = os.getenv("MOONSHOT_MODEL", "kimi-k2.5")

    generated: list[dict] = []
    all_mermaid_errors: dict[str, list[dict]] = {}

    for i, page in enumerate(pages_to_generate, 1):
        print(f"\n[{i}/{len(pages_to_generate)}] {page['id']}: {page['title']}...")
        try:
            content = generate_page_content(
                page, agent, repo_path, vector_store, embedder, func_map
            )
            mermaid_errors = validate_mermaid_blocks(content)
            if mermaid_errors:
                print(f"  ⚠️ Mermaid 语法错误: {len(mermaid_errors)} 个块，尝试修复...")
                content, deleted = fix_mermaid_errors(content, mermaid_errors, agent)
                if deleted:
                    all_mermaid_errors[page["id"]] = deleted
                    print(f"  ⚠️ {len(deleted)} 个块超过 {MAX_MERMAID_FIX_ATTEMPTS} 次修复失败，已删除")
                else:
                    print(f"  ✓ 全部 {len(mermaid_errors)} 个 Mermaid 块修复成功")
            page_file = wiki_dir / f"{page['id']}.md"
            page_file.write_text(content, encoding="utf-8")
            generated.append({**page, "content": content})
            print(f"  完成 ({len(content)} 字符, {page_file.stat().st_size:,} 字节)")
        except Exception as e:
            print(f"  失败: {e}")
            err_content = f"# {page['title']}\n\n*生成失败: {e}*"
            page_file = wiki_dir / f"{page['id']}.md"
            page_file.write_text(err_content, encoding="utf-8")
            generated.append({**page, "content": err_content})

    # only_pages 模式只更新指定页面，跳过 index.md
    if only_pages:
        print(f"\n页面文件已更新:")
        for p in generated:
            pf = wiki_dir / f"{p['id']}.md"
            print(f"  wiki/{p['id']}.md ({pf.stat().st_size:,} 字节)")
        if all_mermaid_errors:
            total_deleted = sum(len(v) for v in all_mermaid_errors.values())
            print(f"\nMermaid 删除报告 ({total_deleted} 个块已删除):")
            for pid, errs in all_mermaid_errors.items():
                for e in errs:
                    print(f"  [{pid}] 块#{e['index']}: {e['error']}")
        return output_dir / "index.md", len(generated)

    # 统计数据
    total_funcs_row = builder.query("MATCH (f:Function) RETURN count(f) AS cnt")
    total_funcs = list(total_funcs_row[0].values())[0] if total_funcs_row else 0
    total_calls_row = builder.query("MATCH ()-[r:CALLS]->() RETURN count(r) AS cnt")
    total_calls = list(total_calls_row[0].values())[0] if total_calls_row else 0

    # 写 index.md
    mode_label = "详细 Comprehensive" if comprehensive else "简洁 Concise"
    index_path = output_dir / "index.md"
    index_lines = [
        f"# {project_name} 源码 Wiki",
        "",
        f"*生成时间: {gen_time}*",
        f"*模型: {model_name} | 模式: {mode_label} | 上下文检索: 向量语义检索（Qwen3 Embedding）*",
        "",
        "---",
        "",
        "## 项目概览",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 总函数数 | {total_funcs:,} |",
        f"| 总调用关系 | {total_calls:,} |",
        f"| 本次生成页面 | {len(generated)} |",
        "",
        "---",
        "",
        "## Wiki 页面索引",
        "",
        "| 重要性 | 页面 | 描述 |",
        "|--------|------|------|",
    ]
    for p in generated:
        importance_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(p["importance"], "⚪")
        index_lines.append(
            f"| {importance_icon} {p['importance']} | [{p['title']}](./wiki/{p['id']}.md) | {p['description'][:60]}... |"
            if len(p["description"]) > 60
            else f"| {importance_icon} {p['importance']} | [{p['title']}](./wiki/{p['id']}.md) | {p['description']} |"
        )
    index_lines += ["", "---", "", "## 详细文档", ""]
    for p in generated:
        index_lines.append(f"- [{p['title']}](./wiki/{p['id']}.md) — {p['description']}")

    index_path.write_text("\n".join(index_lines), encoding="utf-8")

    total_size = sum((wiki_dir / f"{p['id']}.md").stat().st_size for p in generated) + index_path.stat().st_size
    print(f"\nWiki 已保存到: {output_dir}/")
    print(f"  index.md ({index_path.stat().st_size:,} 字节)")
    for p in generated:
        pf = wiki_dir / f"{p['id']}.md"
        print(f"  wiki/{p['id']}.md ({pf.stat().st_size:,} 字节)")
    print(f"总大小: {total_size:,} 字节 | 总页面数: {len(generated)}")
    if all_mermaid_errors:
        total_deleted = sum(len(v) for v in all_mermaid_errors.values())
        print(f"\nMermaid 删除报告 ({total_deleted} 个块已删除，共 {len(all_mermaid_errors)} 个页面):")
        for pid, errs in all_mermaid_errors.items():
            for e in errs:
                print(f"  [{pid}] 块#{e['index']}: {e['error']}")
    else:
        print("Mermaid 验证: 全部通过 ✓")
    return index_path, len(generated)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="通用代码 Wiki 生成器（对齐 deepwiki 两阶段流程）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python generate_wiki.py --repo-path /path/to/redis              # 默认详细模式
  python generate_wiki.py --repo-path /path/to/redis --concise    # 简洁模式
  python generate_wiki.py --repo-path /path/to/redis --max-pages 12
  python generate_wiki.py --repo-path /path/to/redis --rebuild    # 重新规划 + 重新 embedding
  python generate_wiki.py --repo-path /path/to/redis --pages page-1 page-3  # 重跑指定页面
        """,
    )
    parser.add_argument("--repo-path", type=Path, required=True, help="目标代码仓库路径")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--comprehensive", action="store_true", default=False,
                            help="生成详细 wiki（8-12 页，默认）")
    mode_group.add_argument("--concise", action="store_true", default=False,
                            help="生成简洁 wiki（4-6 页）")
    parser.add_argument("--max-pages", type=int, default=None,
                        help="最多生成几个页面（默认: comprehensive=10，concise=5）")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="wiki 输出目录（默认: ./<repo_name>_wiki/）")
    parser.add_argument("--db-path", type=Path, default=None,
                        help="Kùzu 数据库路径（默认: ./<repo_name>_graph.db）")
    parser.add_argument("--rebuild", action="store_true",
                        help="强制重新构建图、向量索引和 Wiki 结构规划")
    parser.add_argument("--pages", nargs="+", default=None, metavar="PAGE_ID",
                        help="只重新生成指定 page-id 的页面（空格分隔），需先有结构缓存")
    args = parser.parse_args()

    setup_environment()

    repo_path = args.repo_path.resolve()
    if not repo_path.exists():
        print(f"错误: 仓库路径不存在: {repo_path}")
        sys.exit(1)

    comprehensive = not args.concise
    max_pages = args.max_pages if args.max_pages is not None else (
        MAX_PAGES_COMPREHENSIVE if comprehensive else MAX_PAGES_CONCISE
    )

    project_name = repo_path.name
    db_path = args.db_path or Path(f"./{project_name}_graph.db")
    output_dir = args.output_dir or Path(f"./{project_name}_wiki")

    print("=" * 60)
    print("通用代码 Wiki 生成器（deepwiki 两阶段流程）")
    print("=" * 60)
    print(f"仓库:     {repo_path}")
    print(f"项目名:   {project_name}")
    print(f"数据库:   {db_path}")
    print(f"输出目录: {output_dir}")
    print(f"模式:     {'详细 Comprehensive' if comprehensive else '简洁 Concise'}")
    print(f"最大页面: {max_pages}")

    builder = build_or_load_graph(repo_path, db_path, args.rebuild)

    try:
        index_path, page_count = generate_wiki(
            builder=builder,
            repo_path=repo_path,
            output_dir=output_dir,
            max_pages=max_pages,
            rebuild=args.rebuild,
            comprehensive=comprehensive,
            only_pages=args.pages,
        )
        print(f"\n完成! 生成了 {page_count} 个页面")
        print(f"目录: {index_path.parent}/")
        print(f"入口: {index_path}")
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
