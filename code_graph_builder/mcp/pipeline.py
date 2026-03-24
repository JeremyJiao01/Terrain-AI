"""Pipeline with progress callbacks for MCP: graph (+ api_docs) → embedding → wiki.

Each stage calls `progress_cb(message)` after every meaningful unit of work
so the MCP server can relay real-time updates to the client.
"""

from __future__ import annotations

import json
import os
import pickle
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

try:
    from ..rag.client import create_llm_client, LLMClient
except ImportError:
    create_llm_client = None  # type: ignore[assignment,misc]
    LLMClient = None  # type: ignore[assignment,misc]

ProgressCb = Callable[[str, float], None] | None
"""Progress callback: (message, percentage_0_to_100) -> None.

Pipeline weight allocation:
    Step 1 (graph + API docs):  0 – 15 %
    Step 2 (embeddings):       15 – 40 %
    Step 3 (wiki generation):  40 – 100 %
"""


# ---------------------------------------------------------------------------
# Helpers shared with generate_wiki.py
# ---------------------------------------------------------------------------

def _resolve_source_file(qname: str, repo_path: Path) -> Path | None:
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


_MAX_SOURCE_CHARS = 2000


def _read_function_source(func: dict, repo_path: Path) -> str | None:
    qname = func.get("qualified_name", "")
    start_line = func.get("start_line", 0)
    end_line = func.get("end_line", 0)
    if start_line == 0 or start_line == end_line:
        return None
    file_path = _resolve_source_file(qname, repo_path)
    if file_path is None:
        return None
    try:
        with open(file_path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        source = "".join(lines[start_line - 1: end_line])
        if len(source) > _MAX_SOURCE_CHARS:
            source = source[:_MAX_SOURCE_CHARS] + "\n    /* ... truncated ... */"
        return source
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Step 1: graph build + API docs generation
# ---------------------------------------------------------------------------

_FUNC_DOC_QUERY = """
    MATCH (m:Module)-[:DEFINES]->(f:Function)
    RETURN DISTINCT m.qualified_name, m.path,
           f.qualified_name, f.name, f.signature, f.return_type,
           f.visibility, f.parameters, f.docstring,
           f.start_line, f.end_line, f.path, f.kind
    ORDER BY m.qualified_name, f.start_line
"""

_TYPE_DOC_QUERY_CLASS = """
    MATCH (c:Class)
    RETURN DISTINCT c.qualified_name, c.name, c.kind, c.signature,
           c.parameters, c.start_line, c.end_line
    ORDER BY c.qualified_name, c.start_line
"""

_TYPE_DOC_QUERY_TYPE = """
    MATCH (t:Type)
    RETURN DISTINCT t.qualified_name, t.name, t.kind, t.signature,
           t.start_line, t.end_line
    ORDER BY t.qualified_name, t.start_line
"""

_CALLS_QUERY = """
    MATCH (caller:Function)-[:CALLS]->(callee:Function)
    RETURN DISTINCT caller.qualified_name, callee.qualified_name,
           callee.path, callee.start_line
"""


def build_graph(
    repo_path: Path,
    db_path: Path,
    rebuild: bool,
    progress_cb: ProgressCb = None,
    backend: str = "kuzu",
) -> Any:
    """Build or reuse a code knowledge graph.

    This step only creates the graph database.  API docs, embeddings, and
    wiki generation are separate steps.
    """
    from ..builder import CodeGraphBuilder

    builder = CodeGraphBuilder(
        repo_path=str(repo_path),
        backend=backend,
        backend_config={"db_path": str(db_path), "batch_size": 1000},
    )

    if rebuild or not db_path.exists():
        result = builder.build_graph(clean=rebuild)
        if progress_cb:
            progress_cb(
                f"Graph built: "
                f"{result.nodes_created} nodes, "
                f"{result.relationships_created} relationships, "
                f"{result.files_processed} files processed.",
                10.0,
            )
    else:
        stats = builder.get_statistics()
        if progress_cb:
            progress_cb(
                f"Reusing existing graph: "
                f"{stats.get('node_count', '?')} nodes, "
                f"{stats.get('relationship_count', '?')} relationships.",
                10.0,
            )

    return builder


# ---------------------------------------------------------------------------
# Step 2: API docs generation (graph-only, no embeddings needed)
# ---------------------------------------------------------------------------

def generate_api_docs_step(
    builder: Any,
    artifact_dir: Path,
    rebuild: bool,
    progress_cb: ProgressCb = None,
) -> dict[str, Any]:
    """Generate hierarchical API docs from the knowledge graph.

    Requires only a populated graph database — no embeddings or LLM needed.
    """
    from .api_doc_generator import generate_api_docs

    api_dir = artifact_dir / "api_docs"
    index_file = api_dir / "index.md"

    if not rebuild and index_file.exists():
        if progress_cb:
            progress_cb("Reusing cached API docs.", 15.0)
        return {"status": "cached"}

    try:
        func_rows = builder.query(_FUNC_DOC_QUERY)
        type_rows = builder.query(_TYPE_DOC_QUERY_CLASS) + builder.query(_TYPE_DOC_QUERY_TYPE)
        call_rows = builder.query(_CALLS_QUERY)
    except Exception as exc:
        msg = f"API docs skipped — graph query failed: {exc}"
        logger.warning(msg)
        if progress_cb:
            progress_cb(msg, 15.0)
        return {"status": "skipped", "error": str(exc)}

    result = generate_api_docs(func_rows, type_rows, call_rows, artifact_dir)
    if progress_cb:
        progress_cb(
            f"API docs generated: "
            f"{result['module_count']} modules, "
            f"{result['func_count']} functions, "
            f"{result['type_count']} types.",
            15.0,
        )
    return {"status": "success", **result}


# ---------------------------------------------------------------------------
# Step 1b: LLM-powered description generation for undocumented functions
# ---------------------------------------------------------------------------

_DESC_SYSTEM_PROMPT = """\
You are a code documentation assistant. Given a C/C++ function's signature, \
source code, and module context, generate a single concise sentence (in the \
same language as any existing comments in the code, defaulting to English) \
describing what the function does. Focus on the function's PURPOSE, not its \
implementation details. Do NOT include the function name in the description. \
Reply with ONLY the description sentence, nothing else."""

_DESC_BATCH_SIZE = 10


def _build_desc_prompt(funcs: list[dict]) -> str:
    """Build a batched prompt for multiple functions."""
    parts: list[str] = []
    for i, f in enumerate(funcs):
        sig = f.get("signature") or f.get("name", "unknown")
        source = f.get("source", "")
        module = f.get("module_qn", "")
        parts.append(
            f"[{i+1}] Module: {module}\n"
            f"    Signature: {sig}\n"
            f"    Source:\n{source}\n"
        )
    parts.append(
        f"\nGenerate exactly {len(funcs)} descriptions, one per line, "
        f"numbered [1] to [{len(funcs)}]. Each description should be a "
        f"single concise sentence."
    )
    return "\n".join(parts)


def _parse_desc_response(response: str, count: int) -> list[str]:
    """Parse numbered descriptions from LLM response."""
    import re

    descriptions: list[str] = [""] * count
    for line in response.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        # Match "[N] desc", "N. desc", or "N) desc" with regex
        m = re.match(r"^\[?(\d+)[.\)\]]\s*(.*)", line)
        if m:
            idx = int(m.group(1)) - 1  # 1-based to 0-based
            desc = m.group(2).strip()
            if 0 <= idx < count and desc:
                descriptions[idx] = desc
    return descriptions


def generate_descriptions_step(
    artifact_dir: Path,
    repo_path: Path,
    progress_cb: ProgressCb = None,
) -> dict[str, Any]:
    """Generate LLM descriptions for functions missing docstrings.

    Reads L3 API doc files, finds those with TODO placeholders,
    generates descriptions via LLM, and writes them back.

    This step is optional -- skipped silently if no LLM API key is configured.

    Returns:
        Summary dict with generated_count, skipped_count, error_count.
    """
    if create_llm_client is None:
        logger.info("LLM client not available, skipping description generation")
        return {"generated_count": 0, "skipped_count": 0, "error_count": 0}

    try:
        client = create_llm_client()
    except (ValueError, RuntimeError) as e:
        logger.info(f"No LLM API key configured, skipping description generation: {e}")
        return {"generated_count": 0, "skipped_count": 0, "error_count": 0}

    funcs_dir = artifact_dir / "api_docs" / "funcs"
    if not funcs_dir.exists():
        logger.warning("No API docs found, skipping description generation")
        return {"generated_count": 0, "skipped_count": 0, "error_count": 0}

    # Collect functions needing descriptions
    todo_funcs: list[dict] = []  # {path, name, signature, source, module_qn, content}

    for md_file in sorted(funcs_dir.glob("*.md")):
        content = md_file.read_text(encoding="utf-8")
        if "<!-- TODO:" not in content:
            continue

        # Parse minimal info from the markdown
        func_info: dict = {"path": md_file, "content": content}
        for line in content.splitlines():
            if line.startswith("# "):
                func_info["name"] = line[2:].strip()
            elif line.startswith("- 签名:") or line.startswith("- 定义:"):
                # Extract signature from backticks
                start = line.find("`")
                end = line.rfind("`")
                if start != -1 and end > start:
                    func_info["signature"] = line[start + 1 : end]
            elif line.startswith("- 模块:"):
                func_info["module_qn"] = (
                    line[len("- 模块:") :].strip().split(" —")[0].strip()
                )

        # Read source from the implementation section
        if "## 实现" in content:
            source_start = content.index("## 实现")
            # Extract code between ``` markers
            code_start = content.find("```", source_start)
            code_end = (
                content.find("```", code_start + 3) if code_start != -1 else -1
            )
            if code_start != -1 and code_end != -1:
                # Skip the ```c or ```cpp line
                first_newline = content.index("\n", code_start)
                func_info["source"] = content[first_newline + 1 : code_end].strip()

        if "name" in func_info:
            todo_funcs.append(func_info)

    if not todo_funcs:
        logger.info("All functions already have descriptions")
        return {"generated_count": 0, "skipped_count": 0, "error_count": 0}

    logger.info(f"Generating descriptions for {len(todo_funcs)} functions")

    generated = 0
    errors = 0
    total_batches = (len(todo_funcs) + _DESC_BATCH_SIZE - 1) // _DESC_BATCH_SIZE

    for batch_idx in range(0, len(todo_funcs), _DESC_BATCH_SIZE):
        batch = todo_funcs[batch_idx : batch_idx + _DESC_BATCH_SIZE]
        current_batch = batch_idx // _DESC_BATCH_SIZE + 1

        if progress_cb:
            pct = int(current_batch / total_batches * 100)
            progress_cb(
                f"Generating descriptions: batch {current_batch}/{total_batches}",
                float(pct),
            )

        prompt = _build_desc_prompt(batch)

        try:
            response = client.chat(
                query=prompt,
                system_prompt=_DESC_SYSTEM_PROMPT,
                max_tokens=4096,
                temperature=1.0,
            )

            descriptions = _parse_desc_response(response.content, len(batch))

            for func_info, desc in zip(batch, descriptions):
                if not desc:
                    errors += 1
                    continue

                # Replace TODO placeholder with generated description
                old_content = func_info["content"]
                new_content = ""
                for line in old_content.splitlines(keepends=True):
                    if "<!-- TODO:" in line and "-->" in line:
                        new_content += f"> {desc}\n"
                    else:
                        new_content += line

                func_info["path"].write_text(new_content, encoding="utf-8")
                generated += 1

        except Exception as e:
            logger.warning(f"LLM description generation failed for batch: {e}")
            errors += len(batch)

    logger.info(f"Generated {generated} descriptions, {errors} errors")
    return {
        "generated_count": generated,
        "skipped_count": len(todo_funcs) - generated - errors,
        "error_count": errors,
    }


# ---------------------------------------------------------------------------
# Step 2: vector index with per-batch progress
# ---------------------------------------------------------------------------

_EMBED_BATCH_SIZE = 10


def _build_embedding_text(
    func: dict,
    callers: list[str],
    callees: list[str],
    source: str,
) -> str:
    """Compose rich embedding text for a function.

    Combines name, file location, docstring, call relationships, and source
    code so that semantic search can match abstract descriptions even when
    functions lack formal documentation.
    """
    parts: list[str] = [f"Function: {func['name']}"]
    if func.get("path"):
        parts.append(f"File: {func['path']}")
    if func.get("docstring"):
        parts.append(f"Description: {func['docstring']}")
    if callers:
        parts.append(f"Called by: {', '.join(callers[:10])}")
    if callees:
        parts.append(f"Calls: {', '.join(callees[:10])}")
    parts.append("---")
    parts.append(source)
    return "\n".join(parts)


def build_vector_index(
    builder: Any,
    repo_path: Path,
    vectors_path: Path,
    rebuild: bool,
    progress_cb: ProgressCb = None,
) -> tuple[Any, Any, dict[int, dict]]:
    """Build or load vector embeddings, reporting after every API batch call."""
    from ..embeddings.qwen3_embedder import create_embedder
    from ..embeddings.vector_store import MemoryVectorStore, VectorRecord

    embedder = create_embedder(batch_size=_EMBED_BATCH_SIZE)

    if not rebuild and vectors_path.exists():
        with open(vectors_path, "rb") as fh:
            cache = pickle.load(fh)
        vector_store: MemoryVectorStore = cache["vector_store"]
        func_map: dict[int, dict] = cache["func_map"]
        if progress_cb:
            progress_cb(
                f"[Step 2/3] Loaded {len(vector_store)} embeddings from cache: {vectors_path}",
                40.0,
            )
        return vector_store, embedder, func_map

    # ---- Query functions with docstring and module path ----
    rows = builder.query("""
        MATCH (m:Module)-[:DEFINES]->(f:Function)
        RETURN DISTINCT f.name AS name,
               f.qualified_name AS qualified_name,
               f.start_line AS start_line,
               f.end_line AS end_line,
               f.docstring AS docstring,
               m.path AS path
    """)
    all_funcs: list[dict] = []
    seen_qn: set[str] = set()
    for row in rows:
        qn = row.get("qualified_name") or ""
        if not qn or qn in seen_qn:
            continue
        seen_qn.add(qn)
        all_funcs.append({
            "name": row.get("name") or "",
            "qualified_name": qn,
            "start_line": row.get("start_line") or 0,
            "end_line": row.get("end_line") or 0,
            "docstring": row.get("docstring") or "",
            "path": row.get("path") or "",
        })

    # ---- Build caller/callee maps for richer embedding context ----
    from collections import defaultdict
    call_rows = builder.query("""
        MATCH (caller:Function)-[:CALLS]->(callee:Function)
        RETURN DISTINCT caller.qualified_name AS caller_qn,
               callee.qualified_name AS callee_qn
    """)
    callees_of: dict[str, list[str]] = defaultdict(list)
    callers_of: dict[str, list[str]] = defaultdict(list)
    seen_edges: set[tuple[str, str]] = set()
    for row in call_rows:
        caller_qn = row.get("caller_qn") or ""
        callee_qn = row.get("callee_qn") or ""
        if not caller_qn or not callee_qn:
            continue
        edge = (caller_qn, callee_qn)
        if edge in seen_edges:
            continue
        seen_edges.add(edge)
        callees_of[caller_qn].append(callee_qn.split(".")[-1])
        callers_of[callee_qn].append(caller_qn.split(".")[-1])

    embeddable: list[tuple[int, dict, str]] = []
    for i, func in enumerate(all_funcs):
        source = _read_function_source(func, repo_path)
        if source:
            text = _build_embedding_text(
                func,
                callers=callers_of.get(func["qualified_name"], []),
                callees=callees_of.get(func["qualified_name"], []),
                source=source,
            )
            embeddable.append((i, func, text))

    total = len(embeddable)
    if progress_cb:
        progress_cb(
            f"[Step 2/3] Embedding {total} functions "
            f"(batch size {_EMBED_BATCH_SIZE}, {(total + _EMBED_BATCH_SIZE - 1) // _EMBED_BATCH_SIZE} API calls)...",
            16.0,
        )

    vector_store = MemoryVectorStore(dimension=embedder.get_embedding_dimension())
    func_map = {}
    records: list[VectorRecord] = []

    for batch_start in range(0, total, _EMBED_BATCH_SIZE):
        batch = embeddable[batch_start: batch_start + _EMBED_BATCH_SIZE]
        batch_texts = [t for _, _, t in batch]

        batch_embeddings = embedder.embed_batch(batch_texts)

        for (node_id, func, _), embedding in zip(batch, batch_embeddings):
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

        done = min(batch_start + _EMBED_BATCH_SIZE, total)
        local_pct = done * 100 // total
        # Map local 0-100% to overall 16-40%
        overall_pct = 16.0 + (done / total) * 24.0
        if progress_cb:
            progress_cb(
                f"[Step 2/3] Embedded {done}/{total} functions ({local_pct}%).",
                overall_pct,
            )

    vector_store.store_embeddings_batch(records)

    with open(vectors_path, "wb") as fh:
        pickle.dump({"vector_store": vector_store, "func_map": func_map}, fh)

    if progress_cb:
        progress_cb(f"[Step 2/3] Done — {len(records)} embeddings saved.", 40.0)

    return vector_store, embedder, func_map


# ---------------------------------------------------------------------------
# Step 3: wiki generation with per-page progress
# ---------------------------------------------------------------------------

def run_wiki_generation(
    builder: Any,
    repo_path: Path,
    output_dir: Path,
    max_pages: int,
    rebuild: bool,
    comprehensive: bool,
    vector_store: Any,
    embedder: Any,
    func_map: dict[int, dict],
    progress_cb: ProgressCb = None,
) -> tuple[Path, int]:
    """Two-phase wiki generation with per-page progress callbacks."""
    import re
    from datetime import datetime

    from ..examples.generate_wiki import (
        MAX_MERMAID_FIX_ATTEMPTS,
        build_source_context,
        fix_mermaid_errors,
        plan_wiki_structure,
        generate_page_content,
        semantic_search_funcs,
        validate_mermaid_blocks,
    )
    from ..rag.camel_agent import CamelAgent
    from ..rag.llm_backend import create_llm_backend

    project_name = repo_path.name
    output_dir.mkdir(parents=True, exist_ok=True)

    structure_cache = output_dir / f"{project_name}_structure.pkl"

    llm_backend = create_llm_backend(temperature=1.0)

    if not llm_backend.available:
        if progress_cb:
            progress_cb(
                "[Step 3/3] Skipped — no LLM API key configured. "
                "Set LLM_API_KEY, OPENAI_API_KEY, or MOONSHOT_API_KEY to enable wiki generation.",
                100.0,
            )
        return output_dir / "index.md", 0
    agent = CamelAgent(
        role=f"{project_name} 技术文档专家",
        goal=f"结合真实源码，为 {project_name} 生成专业、准确、图文并茂的技术 Wiki",
        backstory=f"拥有丰富的技术写作和代码阅读经验，深入理解 {project_name} 源码架构",
    )

    # Phase 1: plan structure (or load cache)
    if not rebuild and structure_cache.exists():
        with open(structure_cache, "rb") as fh:
            planned_pages = pickle.load(fh)
        if progress_cb:
            progress_cb(
                f"[Step 3/3] Loaded wiki structure from cache: {len(planned_pages)} pages.",
                45.0,
            )
    else:
        if progress_cb:
            progress_cb("[Step 3/3] Planning wiki structure (Phase 1)...", 41.0)
        planned_pages = plan_wiki_structure(agent, repo_path, project_name, comprehensive)
        with open(structure_cache, "wb") as fh:
            pickle.dump(planned_pages, fh)
        if progress_cb:
            progress_cb(
                f"[Step 3/3] Wiki structure planned: {len(planned_pages)} pages.",
                45.0,
            )

    high = [p for p in planned_pages if p["importance"] == "high"]
    others = [p for p in planned_pages if p["importance"] != "high"]
    pages_to_generate = (high + others)[:max_pages]
    total_pages = len(pages_to_generate)

    if progress_cb:
        progress_cb(
            f"[Step 3/3] Generating {total_pages} wiki pages "
            f"({'comprehensive' if comprehensive else 'concise'} mode)...",
            46.0,
        )

    wiki_dir = output_dir / "wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    gen_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    model_name = os.getenv("MOONSHOT_MODEL", "kimi-k2.5")

    generated: list[dict] = []

    for i, page in enumerate(pages_to_generate, 1):
        try:
            content = generate_page_content(
                page, agent, repo_path, vector_store, embedder, func_map
            )
            mermaid_errors = validate_mermaid_blocks(content)
            if mermaid_errors:
                content, _ = fix_mermaid_errors(content, mermaid_errors, agent)

            page_file = wiki_dir / f"{page['id']}.md"
            page_file.write_text(content, encoding="utf-8")
            generated.append({**page, "content": content})

            # Map page progress to overall 46-98%
            page_pct = 46.0 + (i / total_pages) * 52.0
            if progress_cb:
                progress_cb(
                    f"[Step 3/3] Page {i}/{total_pages} done: {page['id']} — {page['title']} "
                    f"({len(content)} chars).",
                    page_pct,
                )
        except Exception as exc:
            err_content = f"# {page['title']}\n\n*生成失败: {exc}*"
            (wiki_dir / f"{page['id']}.md").write_text(err_content, encoding="utf-8")
            generated.append({**page, "content": err_content})
            page_pct = 46.0 + (i / total_pages) * 52.0
            if progress_cb:
                progress_cb(
                    f"[Step 3/3] Page {i}/{total_pages} FAILED: {page['id']} — {exc}",
                    page_pct,
                )

    # Write index.md
    total_funcs_row = builder.query("MATCH (f:Function) RETURN count(f) AS cnt")
    total_funcs = list(total_funcs_row[0].values())[0] if total_funcs_row else 0
    total_calls_row = builder.query("MATCH ()-[r:CALLS]->() RETURN count(r) AS cnt")
    total_calls = list(total_calls_row[0].values())[0] if total_calls_row else 0

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
        desc = p["description"]
        short_desc = desc[:60] + "..." if len(desc) > 60 else desc
        index_lines.append(
            f"| {importance_icon} {p['importance']} | [{p['title']}](./wiki/{p['id']}.md) | {short_desc} |"
        )
    index_lines += ["", "---", "", "## 详细文档", ""]
    for p in generated:
        index_lines.append(f"- [{p['title']}](./wiki/{p['id']}.md) — {p['description']}")

    index_path.write_text("\n".join(index_lines), encoding="utf-8")

    if progress_cb:
        progress_cb(
            f"[Step 3/3] Wiki complete: {len(generated)} pages at {output_dir}/",
            100.0,
        )

    return index_path, len(generated)


# ---------------------------------------------------------------------------
# Workspace helpers
# ---------------------------------------------------------------------------

def save_meta(artifact_dir: Path, repo_path: Path, wiki_page_count: int) -> None:
    """Save or update artifact metadata.

    Preserves existing fields (like step-completion flags) and updates
    the timestamp and wiki page count.
    """
    meta_file = artifact_dir / "meta.json"
    existing: dict = {}
    if meta_file.exists():
        try:
            existing = json.loads(meta_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    # Auto-detect which artifacts exist
    has_graph = (artifact_dir / "graph.db").exists()
    has_api_docs = (artifact_dir / "api_docs" / "index.md").exists()
    has_embeddings = (artifact_dir / "vectors.pkl").exists()
    has_wiki = wiki_page_count > 0 or (artifact_dir / "wiki" / "index.md").exists()

    meta = {
        **existing,
        "repo_path": str(repo_path),
        "repo_name": repo_path.name,
        "indexed_at": datetime.now().isoformat(),
        "wiki_page_count": wiki_page_count,
        "steps": {
            "graph": has_graph,
            "api_docs": has_api_docs,
            "embeddings": has_embeddings,
            "wiki": has_wiki,
        },
    }
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))


def artifact_dir_for(workspace: Path, repo_path: Path) -> Path:
    import hashlib

    h = hashlib.md5(str(repo_path).encode()).hexdigest()[:8]
    return workspace / f"{repo_path.name}_{h}"
