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

ProgressCb = Callable[[str], None] | None


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
    RETURN m.qualified_name, m.path,
           f.qualified_name, f.name, f.signature, f.return_type,
           f.visibility, f.parameters, f.docstring,
           f.start_line, f.end_line, f.path
    ORDER BY m.qualified_name, f.start_line
"""

_TYPE_DOC_QUERY_CLASS = """
    MATCH (m:Module)-[:DEFINES]->(c:Class)
    RETURN m.qualified_name, c.name, c.kind, c.signature,
           c.parameters, c.start_line, c.end_line
    ORDER BY m.qualified_name, c.start_line
"""

_TYPE_DOC_QUERY_TYPE = """
    MATCH (m:Module)-[:DEFINES]->(t:Type)
    RETURN m.qualified_name, t.name, t.kind, t.signature,
           t.start_line, t.end_line
    ORDER BY m.qualified_name, t.start_line
"""

_CALLS_QUERY = """
    MATCH (caller:Function)-[:CALLS]->(callee:Function)
    RETURN caller.qualified_name, callee.qualified_name,
           callee.path, callee.start_line
"""


def _generate_api_docs(
    builder: Any,
    artifact_dir: Path,
    rebuild: bool,
    progress_cb: ProgressCb = None,
) -> None:
    """Sub-step of Step 1: generate hierarchical API docs from graph data."""
    from .api_doc_generator import generate_api_docs

    api_dir = artifact_dir / "api_docs"
    index_file = api_dir / "index.md"

    if not rebuild and index_file.exists():
        if progress_cb:
            progress_cb("[Step 1/3] Reusing cached API docs.")
        return

    try:
        func_rows = builder.query(_FUNC_DOC_QUERY)
        type_rows = builder.query(_TYPE_DOC_QUERY_CLASS) + builder.query(_TYPE_DOC_QUERY_TYPE)
        call_rows = builder.query(_CALLS_QUERY)
    except Exception as exc:
        logger.warning(f"API docs skipped — graph query failed: {exc}")
        return

    result = generate_api_docs(func_rows, type_rows, call_rows, artifact_dir)
    if progress_cb:
        progress_cb(
            f"[Step 1/3] API docs generated: "
            f"{result['module_count']} modules, "
            f"{result['func_count']} functions, "
            f"{result['type_count']} types."
        )


def build_graph(
    repo_path: Path,
    db_path: Path,
    artifact_dir: Path,
    rebuild: bool,
    progress_cb: ProgressCb = None,
    backend: str = "kuzu",
) -> Any:
    """Build or reuse a code knowledge graph, then generate API docs."""
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
                f"[Step 1/3] Graph built: "
                f"{result.nodes_created} nodes, "
                f"{result.relationships_created} relationships, "
                f"{result.files_processed} files processed."
            )
    else:
        stats = builder.get_statistics()
        if progress_cb:
            progress_cb(
                f"[Step 1/3] Reusing existing graph: "
                f"{stats.get('node_count', '?')} nodes, "
                f"{stats.get('relationship_count', '?')} relationships."
            )

    _generate_api_docs(builder, artifact_dir, rebuild, progress_cb)

    return builder


# ---------------------------------------------------------------------------
# Step 2: vector index with per-batch progress
# ---------------------------------------------------------------------------

_EMBED_BATCH_SIZE = 10


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
                f"[Step 2/3] Loaded {len(vector_store)} embeddings from cache: {vectors_path}"
            )
        return vector_store, embedder, func_map

    rows = builder.query(
        "MATCH (f:Function) RETURN f.name, f.qualified_name, f.start_line, f.end_line"
    )
    all_funcs: list[dict] = []
    for row in rows:
        r = row["result"]
        all_funcs.append({
            "name": r[0],
            "qualified_name": r[1],
            "start_line": r[2] or 0,
            "end_line": r[3] or 0,
        })

    embeddable: list[tuple[int, dict, str]] = []
    for i, func in enumerate(all_funcs):
        source = _read_function_source(func, repo_path)
        if source:
            embeddable.append((i, func, f"// {func['name']}\n{source}"))

    total = len(embeddable)
    if progress_cb:
        progress_cb(
            f"[Step 2/3] Embedding {total} functions "
            f"(batch size {_EMBED_BATCH_SIZE}, {(total + _EMBED_BATCH_SIZE - 1) // _EMBED_BATCH_SIZE} API calls)..."
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
        pct = done * 100 // total
        if progress_cb:
            progress_cb(f"[Step 2/3] Embedded {done}/{total} functions ({pct}%).")

    vector_store.store_embeddings_batch(records)

    with open(vectors_path, "wb") as fh:
        pickle.dump({"vector_store": vector_store, "func_map": func_map}, fh)

    if progress_cb:
        progress_cb(f"[Step 2/3] Done — {len(records)} embeddings saved.")

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
                "Set LLM_API_KEY, OPENAI_API_KEY, or MOONSHOT_API_KEY to enable wiki generation."
            )
        return output_dir / "index.md", 0
    agent = CamelAgent(
        role=f"{project_name} 技术文档专家",
        goal=f"结合真实源码，为 {project_name} 生成专业、准确、图文并茂的技术 Wiki",
        backstory=f"拥有丰富的技术写作和代码阅读经验，深入理解 {project_name} 源码架构",
        llm_backend=llm_backend,
    )

    # Phase 1: plan structure (or load cache)
    if not rebuild and structure_cache.exists():
        with open(structure_cache, "rb") as fh:
            planned_pages = pickle.load(fh)
        if progress_cb:
            progress_cb(
                f"[Step 3/3] Loaded wiki structure from cache: {len(planned_pages)} pages."
            )
    else:
        if progress_cb:
            progress_cb("[Step 3/3] Planning wiki structure (Phase 1)...")
        planned_pages = plan_wiki_structure(agent, repo_path, project_name, comprehensive)
        with open(structure_cache, "wb") as fh:
            pickle.dump(planned_pages, fh)
        if progress_cb:
            progress_cb(
                f"[Step 3/3] Wiki structure planned: {len(planned_pages)} pages."
            )

    high = [p for p in planned_pages if p["importance"] == "high"]
    others = [p for p in planned_pages if p["importance"] != "high"]
    pages_to_generate = (high + others)[:max_pages]
    total_pages = len(pages_to_generate)

    if progress_cb:
        progress_cb(
            f"[Step 3/3] Generating {total_pages} wiki pages "
            f"({'comprehensive' if comprehensive else 'concise'} mode)..."
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

            if progress_cb:
                progress_cb(
                    f"[Step 3/3] Page {i}/{total_pages} done: {page['id']} — {page['title']} "
                    f"({len(content)} chars)."
                )
        except Exception as exc:
            err_content = f"# {page['title']}\n\n*生成失败: {exc}*"
            (wiki_dir / f"{page['id']}.md").write_text(err_content, encoding="utf-8")
            generated.append({**page, "content": err_content})
            if progress_cb:
                progress_cb(
                    f"[Step 3/3] Page {i}/{total_pages} FAILED: {page['id']} — {exc}"
                )

    # Write index.md
    total_funcs_row = builder.query("MATCH (f:Function) RETURN count(f) AS cnt")
    total_funcs = total_funcs_row[0]["result"][0] if total_funcs_row else 0
    total_calls_row = builder.query("MATCH ()-[r:CALLS]->() RETURN count(r) AS cnt")
    total_calls = total_calls_row[0]["result"][0] if total_calls_row else 0

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
            f"[Step 3/3] Wiki complete: {len(generated)} pages at {output_dir}/"
        )

    return index_path, len(generated)


# ---------------------------------------------------------------------------
# Workspace helpers
# ---------------------------------------------------------------------------

def save_meta(artifact_dir: Path, repo_path: Path, wiki_page_count: int) -> None:
    meta = {
        "repo_path": str(repo_path),
        "indexed_at": datetime.now().isoformat(),
        "wiki_page_count": wiki_page_count,
    }
    (artifact_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))


def artifact_dir_for(workspace: Path, repo_path: Path) -> Path:
    import hashlib

    h = hashlib.md5(str(repo_path).encode()).hexdigest()[:8]
    return workspace / f"{repo_path.name}_{h}"
