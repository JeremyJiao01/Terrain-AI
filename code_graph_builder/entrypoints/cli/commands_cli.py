"""CodeGraphWiki CLI — direct command interface for Claude Code custom commands.

Provides the same operations as the MCP server, but as a synchronous CLI
with stdout output.  Progress messages are printed inline so they appear
directly in the Claude Code conversation.

Usage:
    python3 -m code_graph_builder.commands_cli <command> [args...]

Commands:
    init         Initialize repository (graph → api-docs → embeddings → wiki)
    graph-build  Build knowledge graph only (step 1)
    api-doc-gen  Generate API docs from existing graph (step 2)
    embed-gen    Rebuild embeddings only (step 3, reuses graph)
    wiki-gen     Regenerate wiki only (step 4, reuses graph + embeddings)
    list-repos   List all indexed repositories in the workspace
    switch-repo  Switch active repository to a previously indexed one
    info         Show active repository info and graph statistics
    reload       Hot-reload .env configuration without restarting
    query        Translate natural-language question to Cypher and execute
    snippet      Retrieve source code by qualified name
    search       Semantic vector search
    list-wiki    List generated wiki pages
    get-wiki     Read a wiki page
    locate       Locate function via Tree-sitter AST
    list-api     List public API interfaces from graph
    api-docs     Browse hierarchical API documentation (L1/L2)
    api-doc      Read detailed API doc for a function (L3)
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load workspace .env first, then local .env (same priority as MCP server)
_ws = Path(os.environ.get("CGB_WORKSPACE", Path.home() / ".code-graph-builder"))
load_dotenv(_ws.expanduser() / ".env", override=False)
load_dotenv(override=False)

from code_graph_builder.foundation.services.git_service import GitChangeDetector as _GCD  # noqa: E402
from code_graph_builder.foundation.utils.settings import load_settings  # noqa: E402

load_settings()


# ---------------------------------------------------------------------------
# Workspace helper
# ---------------------------------------------------------------------------

class Workspace:
    """Manages the CodeGraphWiki workspace directory."""

    def __init__(self) -> None:
        self.root = Path(
            os.environ.get("CGB_WORKSPACE", Path.home() / ".code-graph-builder")
        ).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def active_artifact_dir(self) -> Path | None:
        active_file = self.root / "active.txt"
        if not active_file.exists():
            return None
        name = active_file.read_text(encoding="utf-8", errors="replace").strip()
        d = self.root / name
        return d if d.exists() else None

    def load_meta(self) -> dict | None:
        d = self.active_artifact_dir()
        if d is None:
            return None
        meta_file = d / "meta.json"
        if not meta_file.exists():
            return None
        return json.loads(meta_file.read_text(encoding="utf-8", errors="replace"))

    def set_active(self, artifact_dir: Path) -> None:
        (self.root / "active.txt").write_text(artifact_dir.name, encoding="utf-8")

    def require_active(self) -> Path:
        d = self.active_artifact_dir()
        if d is None:
            _die("No repository indexed yet. Run: /init-repo <path>")
        return d  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _progress(msg: str) -> None:
    """Print a progress line that will show in the conversation."""
    print(msg, flush=True)


def _result_json(data: dict | list) -> None:
    """Print JSON result."""
    print(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Service loaders (lazy, per-invocation)
# ---------------------------------------------------------------------------

def _open_ingestor(artifact_dir: Path):
    from code_graph_builder.foundation.services.kuzu_service import KuzuIngestor

    db_path = artifact_dir / "graph.db"
    if not db_path.exists():
        _die(f"Graph database not found: {db_path}")
    ingestor = KuzuIngestor(db_path)
    ingestor.__enter__()
    return ingestor


def _load_vector_store(vectors_path: Path):
    from code_graph_builder.domains.core.embedding.vector_store import MemoryVectorStore, VectorRecord

    if not vectors_path.exists():
        return None

    with open(vectors_path, "rb") as fh:
        data = pickle.load(fh)

    if isinstance(data, dict) and "vector_store" in data:
        store = data["vector_store"]
        if isinstance(store, MemoryVectorStore):
            return store

    if isinstance(data, list) and len(data) > 0:
        first = data[0]
        if isinstance(first, VectorRecord):
            dim = len(first.embedding)
            store = MemoryVectorStore(dimension=dim)
            store.store_embeddings_batch(data)
            return store

    return None


# ---------------------------------------------------------------------------
# Subcommand: init
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace, ws: Workspace) -> None:
    """Orchestrate: graph-build → api-doc-gen → embed-gen → wiki-gen."""
    from code_graph_builder.examples.generate_wiki import MAX_PAGES_COMPREHENSIVE, MAX_PAGES_CONCISE
    from code_graph_builder.entrypoints.mcp.pipeline import (
        artifact_dir_for,
        build_graph,
        build_vector_index,
        generate_api_docs_step,
        run_wiki_generation,
        save_meta,
    )

    repo_path = Path(args.repo_path).resolve()
    if not repo_path.exists():
        _die(f"Repository path does not exist: {repo_path}")

    rebuild = args.rebuild
    wiki_mode = args.mode
    backend = args.backend
    skip_embed = args.no_embed
    skip_wiki = args.no_wiki or skip_embed  # wiki requires embeddings
    comprehensive = wiki_mode != "concise"
    max_pages = MAX_PAGES_COMPREHENSIVE if comprehensive else MAX_PAGES_CONCISE

    artifact_dir = artifact_dir_for(ws.root, repo_path)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    db_path = artifact_dir / "graph.db"
    vectors_path = artifact_dir / "vectors.pkl"
    wiki_dir = artifact_dir / "wiki"

    total_steps = 4
    if skip_embed:
        total_steps = 2  # graph + api_docs only
    elif skip_wiki:
        total_steps = 3  # graph + api_docs + embeddings

    def step_progress(step: int, msg: str, pct: float = 0.0) -> None:
        prefix = f"[{pct:.0f}%] " if pct > 0 else ""
        _progress(f"{prefix}[Step {step}/{total_steps}] {msg}")

    step_names = "graph → api-docs"
    if not skip_embed:
        step_names += " → embeddings"
    if not skip_wiki:
        step_names += " → wiki"

    _progress(f"=== Initializing: {repo_path.name} ({step_names}) ===")
    _progress(f"    Workspace: {artifact_dir}")
    _progress(f"    Mode: {wiki_mode} | Backend: {backend} | Rebuild: {rebuild}")
    _progress("")

    try:
        # Step 1: build graph
        builder = build_graph(
            repo_path, db_path, rebuild,
            progress_cb=lambda msg, pct: step_progress(1, msg, pct),
            backend=backend,
        )

        # Step 2: generate API docs
        generate_api_docs_step(
            builder, artifact_dir, rebuild,
            progress_cb=lambda msg, pct: step_progress(2, msg, pct),
        )

        page_count = 0
        index_path = wiki_dir / "index.md"
        skipped = []

        if not skip_embed:
            # Step 3: build embeddings
            vector_store, embedder, func_map = build_vector_index(
                builder, repo_path, vectors_path, rebuild,
                progress_cb=lambda msg, pct: step_progress(3, msg, pct),
            )

            if not skip_wiki:
                # Step 4: generate wiki
                index_path, page_count = run_wiki_generation(
                    builder=builder,
                    repo_path=repo_path,
                    output_dir=wiki_dir,
                    max_pages=max_pages,
                    rebuild=rebuild,
                    comprehensive=comprehensive,
                    vector_store=vector_store,
                    embedder=embedder,
                    func_map=func_map,
                    progress_cb=lambda msg, pct: step_progress(4, msg, pct),
                )
            else:
                skipped.append("wiki")
                step_progress(4, "Wiki generation skipped (--no-wiki).")
        else:
            skipped.extend(["embed", "wiki"])
            step_progress(3, "Embedding generation skipped (--no-embed).")
            step_progress(4, "Wiki generation skipped (requires embeddings).")

        _head = _GCD().get_current_head(repo_path)
        save_meta(artifact_dir, repo_path, page_count, last_indexed_commit=_head)
        ws.set_active(artifact_dir)

        _progress("")
        _progress("=== Done ===")
        _result_json({
            "status": "success",
            "repo_path": str(repo_path),
            "artifact_dir": str(artifact_dir),
            "wiki_index": str(index_path),
            "wiki_pages": page_count,
            "skipped": skipped,
        })

    except Exception as exc:
        _progress(f"\nERROR: Pipeline failed: {exc}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Subcommand: graph-build
# ---------------------------------------------------------------------------

def cmd_graph_build(args: argparse.Namespace, ws: Workspace) -> None:
    """Build the code knowledge graph only (step 1)."""
    from code_graph_builder.entrypoints.mcp.pipeline import artifact_dir_for, build_graph, save_meta

    repo_path = Path(args.repo_path).resolve()
    if not repo_path.exists():
        _die(f"Repository path does not exist: {repo_path}")

    rebuild = args.rebuild
    backend = args.backend

    artifact_dir = artifact_dir_for(ws.root, repo_path)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    db_path = artifact_dir / "graph.db"

    def progress_cb(msg: str, pct: float = 0.0) -> None:
        prefix = f"[{pct:.0f}%] " if pct > 0 else ""
        _progress(f"{prefix}{msg}")

    _progress(f"=== Graph Build: {repo_path.name} ===")
    _progress(f"    Workspace: {artifact_dir}")
    _progress(f"    Backend: {backend} | Rebuild: {rebuild}")
    _progress("")

    try:
        builder = build_graph(repo_path, db_path, rebuild, progress_cb, backend=backend)

        stats = builder.get_statistics()
        _head = _GCD().get_current_head(repo_path)
        save_meta(artifact_dir, repo_path, 0, last_indexed_commit=_head)
        ws.set_active(artifact_dir)

        _progress("")
        _progress("=== Done ===")
        _result_json({
            "status": "success",
            "repo_path": str(repo_path),
            "artifact_dir": str(artifact_dir),
            "node_count": stats.get("node_count", 0),
            "relationship_count": stats.get("relationship_count", 0),
        })

    except Exception as exc:
        _progress(f"\nERROR: Graph build failed: {exc}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Subcommand: api-doc-gen
# ---------------------------------------------------------------------------

def cmd_api_doc_gen(args: argparse.Namespace, ws: Workspace) -> None:
    """Generate API docs from existing knowledge graph (step 2)."""
    from code_graph_builder.entrypoints.mcp.pipeline import generate_api_docs_step, save_meta

    artifact_dir = ws.require_active()
    meta = ws.load_meta()
    if meta is None:
        _die("No metadata found. Run /graph-build or /repo-init first.")

    repo_path = Path(meta["repo_path"]).resolve()
    db_path = artifact_dir / "graph.db"
    if not db_path.exists():
        _die("Graph database not found. Run /graph-build or /repo-init first.")

    rebuild = args.rebuild

    def progress_cb(msg: str, pct: float = 0.0) -> None:
        prefix = f"[{pct:.0f}%] " if pct > 0 else ""
        _progress(f"{prefix}{msg}")

    _progress(f"=== API Doc Generation: {repo_path.name} ===")
    _progress(f"    Rebuild: {rebuild}")
    _progress("")

    try:
        ingestor = _open_ingestor(artifact_dir)

        result = generate_api_docs_step(ingestor, artifact_dir, rebuild, progress_cb)

        ingestor.__exit__(None, None, None)

        _progress("")
        _progress("=== Done ===")
        _result_json({
            "status": result.get("status", "success"),
            "repo_path": str(repo_path),
            "artifact_dir": str(artifact_dir),
            **{k: v for k, v in result.items() if k != "status"},
        })

    except Exception as exc:
        _progress(f"\nERROR: API doc generation failed: {exc}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Subcommand: list-repos
# ---------------------------------------------------------------------------

def cmd_list_repos(_args: argparse.Namespace, ws: Workspace) -> None:
    """List all indexed repositories in the workspace."""
    active_file = ws.root / "active.txt"
    active_name = ""
    if active_file.exists():
        active_name = active_file.read_text(encoding="utf-8", errors="replace").strip()

    repos = []
    for child in sorted(ws.root.iterdir()):
        if not child.is_dir():
            continue
        meta_file = child / "meta.json"
        if not meta_file.exists():
            continue
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue

        repos.append({
            "artifact_dir": child.name,
            "repo_name": meta.get("repo_name", child.name),
            "repo_path": meta.get("repo_path", "unknown"),
            "indexed_at": meta.get("indexed_at"),
            "wiki_page_count": meta.get("wiki_page_count", 0),
            "steps": meta.get("steps", {}),
            "active": child.name == active_name,
        })

    _result_json({
        "workspace": str(ws.root),
        "repository_count": len(repos),
        "repositories": repos,
    })


# ---------------------------------------------------------------------------
# Subcommand: switch-repo
# ---------------------------------------------------------------------------

def cmd_switch_repo(args: argparse.Namespace, ws: Workspace) -> None:
    """Switch active repository to a previously indexed one."""
    repo_name = args.repo_name

    # Try exact match on artifact dir name
    target = None
    for child in ws.root.iterdir():
        if not child.is_dir():
            continue
        if child.name == repo_name:
            target = child
            break

    # Fallback: match by repo_name in meta.json
    if target is None:
        for child in sorted(ws.root.iterdir()):
            if not child.is_dir():
                continue
            meta_file = child / "meta.json"
            if not meta_file.exists():
                continue
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8", errors="replace"))
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                continue
            if meta.get("repo_name") == repo_name:
                target = child
                break

    if target is None or not (target / "meta.json").exists():
        _die(f"Repository not found: {repo_name}. Run /list-repos to see available repos.")

    ws.set_active(target)
    meta = json.loads((target / "meta.json").read_text(encoding="utf-8", errors="replace"))

    _progress(f"Switched to: {meta.get('repo_name', target.name)}")
    _result_json({
        "status": "success",
        "active_repo": meta.get("repo_name", target.name),
        "repo_path": meta.get("repo_path"),
        "artifact_dir": str(target),
        "steps": meta.get("steps", {}),
    })


# ---------------------------------------------------------------------------
# Subcommand: info
# ---------------------------------------------------------------------------

def cmd_info(_args: argparse.Namespace, ws: Workspace) -> None:
    artifact_dir = ws.require_active()
    meta = ws.load_meta() or {}

    wiki_pages = []
    wiki_subdir = artifact_dir / "wiki" / "wiki"
    if wiki_subdir.exists():
        wiki_pages = [p.stem for p in sorted(wiki_subdir.glob("*.md"))]

    result: dict = {
        "repo_path": meta.get("repo_path", "unknown"),
        "artifact_dir": str(artifact_dir),
        "indexed_at": meta.get("indexed_at"),
        "wiki_pages": wiki_pages,
    }

    # Graph statistics + language extraction stats
    db_path = artifact_dir / "graph.db"
    if db_path.exists():
        try:
            ingestor = _open_ingestor(artifact_dir)
            result["graph_stats"] = ingestor.get_statistics()

            # Language extraction stats: count files by extension
            try:
                file_rows = ingestor.query(
                    "MATCH (f:File) RETURN f.path AS path"
                )
                from code_graph_builder.foundation.parsers.language_spec import get_language_for_extension
                lang_counts: dict[str, int] = {}
                total_files = 0
                for row in file_rows:
                    raw = row.get("result", row)
                    fpath = raw[0] if isinstance(raw, (list, tuple)) else raw
                    if isinstance(fpath, str):
                        ext = Path(fpath).suffix.lower()
                        lang = get_language_for_extension(ext)
                        if lang:
                            lang_name = lang.value
                            lang_counts[lang_name] = lang_counts.get(lang_name, 0) + 1
                            total_files += 1
                result["language_stats"] = {
                    "total_code_files": total_files,
                    "by_language": dict(sorted(lang_counts.items(), key=lambda x: -x[1])),
                }
            except Exception:
                pass  # language stats are optional

            ingestor.__exit__(None, None, None)
        except Exception as exc:
            result["graph_stats"] = {"error": str(exc)}

    # Language support info
    from code_graph_builder.foundation.types.constants import LANGUAGE_METADATA, LanguageStatus
    result["supported_languages"] = {
        "full": [m.display_name for lang, m in LANGUAGE_METADATA.items() if m.status == LanguageStatus.FULL],
        "in_development": [m.display_name for lang, m in LANGUAGE_METADATA.items() if m.status == LanguageStatus.DEV],
    }

    # Service availability
    from code_graph_builder.domains.upper.rag.llm_backend import create_llm_backend

    llm = create_llm_backend()
    result["cypher_query_available"] = llm.available
    result["semantic_search_available"] = (artifact_dir / "vectors.pkl").exists()
    result["api_docs_available"] = (artifact_dir / "api_docs" / "index.md").exists()

    # Warnings for missing services
    warnings = []
    if not llm.available:
        warnings.append("LLM not configured — set LLM_API_KEY, OPENAI_API_KEY, or MOONSHOT_API_KEY.")
    if not (artifact_dir / "vectors.pkl").exists():
        warnings.append("Embeddings not built — semantic search unavailable.")
    if warnings:
        result["warnings"] = warnings

    _result_json(result)


# ---------------------------------------------------------------------------
# reload — hot-reload .env configuration
# ---------------------------------------------------------------------------

def cmd_reload(_args: argparse.Namespace, ws: Workspace) -> None:
    from code_graph_builder.foundation.utils.settings import reload_env

    changes = reload_env(workspace=ws.root)
    updated = changes.get("updated", [])
    removed = changes.get("removed", [])

    result: dict = {
        "status": "ok",
        "env_changes": {
            "updated": updated,
            "removed": removed,
        },
    }

    # Re-check service availability with the new config
    from code_graph_builder.domains.upper.rag.llm_backend import create_llm_backend

    llm = create_llm_backend()
    result["services"] = {
        "llm": llm.available,
    }

    # Check embedding service
    try:
        from code_graph_builder.domains.core.embedding.qwen3_embedder import create_embedder
        embedder = create_embedder()
        result["services"]["embedding"] = True
    except Exception:
        result["services"]["embedding"] = False

    # Check active repo
    active_dir = ws.active_artifact_dir()
    if active_dir:
        result["active_repo"] = str(active_dir)
        vectors_path = active_dir / "vectors.pkl"
        result["services"]["semantic_search"] = vectors_path.exists() and result["services"]["embedding"]

    if updated:
        result["hint"] = f"{len(updated)} key(s) updated: {', '.join(updated)}"
    elif removed:
        result["hint"] = f"{len(removed)} key(s) removed: {', '.join(removed)}"
    else:
        result["hint"] = "No changes detected — .env matches current environment."

    _result_json(result)


# ---------------------------------------------------------------------------
# Subcommand: query
# ---------------------------------------------------------------------------

def cmd_query(args: argparse.Namespace, ws: Workspace) -> None:
    artifact_dir = ws.require_active()

    from code_graph_builder.domains.upper.rag.cypher_generator import CypherGenerator
    from code_graph_builder.domains.upper.rag.llm_backend import create_llm_backend

    llm = create_llm_backend()
    if not llm.available:
        _die(
            "LLM not configured. Set one of: LLM_API_KEY, OPENAI_API_KEY, "
            "or MOONSHOT_API_KEY."
        )

    ingestor = _open_ingestor(artifact_dir)
    cypher_gen = CypherGenerator(llm)

    question = args.question
    _progress(f"Question: {question}")

    try:
        cypher = cypher_gen.generate(question)
        _progress(f"Cypher:   {cypher}")
    except Exception as exc:
        ingestor.__exit__(None, None, None)
        _die(f"Cypher generation failed: {exc}")

    try:
        rows = ingestor.query(cypher)
        serialisable = []
        for row in rows:
            raw = row.get("result", row)
            if isinstance(raw, (list, tuple)):
                serialisable.append(list(raw))
            else:
                serialisable.append(raw)
        _result_json({
            "question": question,
            "cypher": cypher,
            "row_count": len(serialisable),
            "rows": serialisable,
        })
    except Exception as exc:
        _die(f"Query execution failed: {exc}\nCypher: {cypher}")
    finally:
        ingestor.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Subcommand: snippet
# ---------------------------------------------------------------------------

def cmd_snippet(args: argparse.Namespace, ws: Workspace) -> None:
    artifact_dir = ws.require_active()
    meta = ws.load_meta() or {}
    repo_path = Path(meta.get("repo_path", "."))

    ingestor = _open_ingestor(artifact_dir)
    qn = args.qualified_name

    safe_qn = qn.replace("'", "\\'")
    cypher = (
        f"MATCH (n) WHERE n.qualified_name = '{safe_qn}' "
        "RETURN n.qualified_name, n.name, n.source_code, n.path, n.start_line, n.end_line "
        "LIMIT 1"
    )

    try:
        rows = ingestor.query(cypher)
    except Exception as exc:
        ingestor.__exit__(None, None, None)
        _die(f"Graph query failed: {exc}")

    if not rows:
        ingestor.__exit__(None, None, None)
        _die(f"Not found: {qn}")

    result = rows[0].get("result", [])
    qname = result[0] if len(result) > 0 else qn
    name = result[1] if len(result) > 1 else None
    source_code = result[2] if len(result) > 2 else None
    file_path = result[3] if len(result) > 3 else None
    start_line = result[4] if len(result) > 4 else None
    end_line = result[5] if len(result) > 5 else None

    if not source_code and file_path and start_line and end_line:
        fp = Path(str(file_path))
        if not fp.is_absolute():
            fp = repo_path / fp
        try:
            from code_graph_builder.foundation.utils.encoding import read_source_file
            lines = read_source_file(fp).splitlines(keepends=True)
            s = max(0, int(start_line) - 1)
            e = min(len(lines), int(end_line))
            source_code = "".join(lines[s:e])
        except Exception:
            pass

    ingestor.__exit__(None, None, None)
    _result_json({
        "qualified_name": qname,
        "name": name,
        "file_path": file_path,
        "start_line": start_line,
        "end_line": end_line,
        "source_code": source_code,
    })


# ---------------------------------------------------------------------------
# Subcommand: search
# ---------------------------------------------------------------------------

def cmd_search(args: argparse.Namespace, ws: Workspace) -> None:
    artifact_dir = ws.require_active()

    vectors_path = artifact_dir / "vectors.pkl"
    if not vectors_path.exists():
        _die("Embeddings not found. Run /init-repo first to build vector index.")

    from code_graph_builder.domains.core.embedding.qwen3_embedder import create_embedder
    from code_graph_builder.domains.core.search.semantic_search import SemanticSearchService

    vector_store = _load_vector_store(vectors_path)
    if vector_store is None:
        _die("Failed to load vector store.")

    ingestor = _open_ingestor(artifact_dir)
    try:
        embedder = create_embedder()
    except ValueError as exc:
        _die(f"Embedding API not configured: {exc}")
    service = SemanticSearchService(
        embedder=embedder, vector_store=vector_store, graph_service=ingestor,
    )

    query = args.query
    top_k = args.top_k
    _progress(f"Searching: \"{query}\" (top {top_k})")

    try:
        results = service.search(query, top_k=top_k)
        _result_json({
            "query": query,
            "result_count": len(results),
            "results": [
                {
                    "qualified_name": r.qualified_name,
                    "name": r.name,
                    "type": r.type,
                    "score": r.score,
                    "file_path": r.file_path,
                    "start_line": r.start_line,
                    "end_line": r.end_line,
                    "source_code": r.source_code,
                }
                for r in results
            ],
        })
    except Exception as exc:
        _die(f"Semantic search failed: {exc}")
    finally:
        ingestor.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Subcommand: list-wiki
# ---------------------------------------------------------------------------

def cmd_list_wiki(_args: argparse.Namespace, ws: Workspace) -> None:
    artifact_dir = ws.require_active()

    wiki_dir = artifact_dir / "wiki"
    if not wiki_dir.exists():
        _die("Wiki not generated yet. Run /init-repo first.")

    pages = []
    wiki_subdir = wiki_dir / "wiki"
    if wiki_subdir.exists():
        for p in sorted(wiki_subdir.glob("*.md")):
            pages.append({"page_id": p.stem, "file": f"wiki/{p.name}"})

    index_path = wiki_dir / "index.md"
    _result_json({
        "index_available": index_path.exists(),
        "page_count": len(pages),
        "pages": pages,
        "hint": "Use /get-wiki index or /get-wiki page-1 to read a page.",
    })


# ---------------------------------------------------------------------------
# Subcommand: get-wiki
# ---------------------------------------------------------------------------

def cmd_get_wiki(args: argparse.Namespace, ws: Workspace) -> None:
    artifact_dir = ws.require_active()

    wiki_dir = artifact_dir / "wiki"
    if not wiki_dir.exists():
        _die("Wiki not generated yet. Run /init-repo first.")

    page_id = args.page_id
    if page_id == "index":
        target = wiki_dir / "index.md"
    else:
        target = wiki_dir / "wiki" / f"{page_id}.md"

    if not target.exists():
        _die(f"Wiki page not found: {page_id}")

    content = target.read_text(encoding="utf-8", errors="ignore")
    _result_json({
        "page_id": page_id,
        "file_path": str(target),
        "content": content,
    })


# ---------------------------------------------------------------------------
# Subcommand: locate
# ---------------------------------------------------------------------------

def cmd_locate(args: argparse.Namespace, ws: Workspace) -> None:
    artifact_dir = ws.require_active()
    meta = ws.load_meta() or {}
    repo_path = Path(meta.get("repo_path", ".")).resolve()

    from code_graph_builder.entrypoints.mcp.file_editor import FileEditor

    try:
        editor = FileEditor(repo_path)
    except Exception as exc:
        _die(f"Failed to initialize FileEditor: {exc}")

    file_path = args.file_path
    target = (repo_path / file_path).resolve()

    try:
        target.relative_to(repo_path)
    except ValueError:
        _die(f"Path outside repository root: {file_path}")

    if not target.exists():
        _die(f"File not found: {file_path}")

    line_number = args.line if hasattr(args, "line") else None
    result = editor.locate_function(target, args.function_name, line_number)
    if result is None:
        _die(f"Function '{args.function_name}' not found in {file_path}")

    _result_json(result)


# ---------------------------------------------------------------------------
# Subcommand: list-api
# ---------------------------------------------------------------------------

def cmd_list_api(args: argparse.Namespace, ws: Workspace) -> None:
    artifact_dir = ws.require_active()
    ingestor = _open_ingestor(artifact_dir)

    module = args.module
    visibility = args.visibility
    vis_filter = None if visibility == "all" else visibility

    try:
        rows = ingestor.fetch_module_apis(module_qn=module, visibility=vis_filter)

        by_module: dict[str, list] = {}
        for row in rows:
            raw = row.get("result", row)
            if isinstance(raw, (list, tuple)) and len(raw) >= 8:
                mod_name = raw[0] or "unknown"
                entry = {
                    "name": raw[1],
                    "signature": raw[2],
                    "return_type": raw[3],
                    "visibility": raw[4],
                    "parameters": raw[5],
                    "start_line": raw[6],
                    "end_line": raw[7],
                    "entity_type": "function",
                }
            else:
                mod_name = "unknown"
                entry = {"raw": raw}

            if mod_name not in by_module:
                by_module[mod_name] = []
            by_module[mod_name].append(entry)

        # Types
        type_count = 0
        if args.include_types and hasattr(ingestor, "fetch_module_type_apis"):
            type_rows = ingestor.fetch_module_type_apis(module_qn=module)
            for row in type_rows:
                raw = row.get("result", row)
                if isinstance(raw, (list, tuple)) and len(raw) >= 6:
                    mod_name = raw[0] or "unknown"
                    entry = {
                        "name": raw[1],
                        "kind": raw[2],
                        "signature": raw[3],
                        "start_line": raw[4 if len(raw) <= 5 else 5],
                        "end_line": raw[5 if len(raw) <= 6 else 6],
                        "entity_type": raw[2] or "type",
                    }
                else:
                    mod_name = "unknown"
                    entry = {"raw": raw}
                if mod_name not in by_module:
                    by_module[mod_name] = []
                by_module[mod_name].append(entry)
                type_count += 1

        total = sum(len(v) for v in by_module.values())
        _result_json({
            "total_apis": total,
            "function_count": total - type_count,
            "type_count": type_count,
            "module_count": len(by_module),
            "visibility_filter": visibility,
            "modules": by_module,
        })

    except Exception as exc:
        _die(f"Failed to list API interfaces: {exc}")
    finally:
        ingestor.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Subcommand: api-docs
# ---------------------------------------------------------------------------

def cmd_api_docs(args: argparse.Namespace, ws: Workspace) -> None:
    artifact_dir = ws.require_active()

    api_dir = artifact_dir / "api_docs"
    if not (api_dir / "index.md").exists():
        _die("API docs not generated yet. Run /init-repo first.")

    module = args.module
    if module:
        safe = module.replace("/", "_").replace("\\", "_")
        target = api_dir / "modules" / f"{safe}.md"
        if not target.exists():
            _die(f"Module doc not found: {module}. Use /api-docs (no args) to see all modules.")
        _result_json({
            "level": "module",
            "module": module,
            "content": target.read_text(encoding="utf-8", errors="ignore"),
        })
    else:
        index_path = api_dir / "index.md"
        _result_json({
            "level": "index",
            "content": index_path.read_text(encoding="utf-8", errors="ignore"),
        })


# ---------------------------------------------------------------------------
# Subcommand: api-doc
# ---------------------------------------------------------------------------

def cmd_api_doc(args: argparse.Namespace, ws: Workspace) -> None:
    artifact_dir = ws.require_active()

    api_dir = artifact_dir / "api_docs"
    if not (api_dir / "index.md").exists():
        _die("API docs not generated yet. Run /init-repo first.")

    qn = args.qualified_name
    safe = qn.replace("/", "_").replace("\\", "_")
    target = api_dir / "funcs" / f"{safe}.md"
    if not target.exists():
        _die(f"API doc not found: {qn}. Use /api-docs to browse modules first.")

    _result_json({
        "qualified_name": qn,
        "content": target.read_text(encoding="utf-8", errors="ignore"),
    })


# ---------------------------------------------------------------------------
# Subcommand: api-find (aggregated: semantic search + API doc lookup)
# ---------------------------------------------------------------------------

def cmd_api_find(args: argparse.Namespace, ws: Workspace) -> None:
    artifact_dir = ws.require_active()

    vectors_path = artifact_dir / "vectors.pkl"
    if not vectors_path.exists():
        _die("Embeddings not found. Run /repo-init first to build vector index.")

    from code_graph_builder.domains.core.embedding.qwen3_embedder import create_embedder
    from code_graph_builder.domains.core.search.semantic_search import SemanticSearchService

    vector_store = _load_vector_store(vectors_path)
    if vector_store is None:
        _die("Failed to load vector store.")

    ingestor = _open_ingestor(artifact_dir)
    try:
        embedder = create_embedder()
    except ValueError as exc:
        ingestor.__exit__(None, None, None)
        _die(f"Embedding API not configured: {exc}")
    service = SemanticSearchService(
        embedder=embedder, vector_store=vector_store, graph_service=ingestor,
    )

    query = args.query
    top_k = args.top_k
    _progress(f"Searching APIs: \"{query}\" (top {top_k})")

    try:
        results = service.search(query, top_k=top_k)
    except Exception as exc:
        ingestor.__exit__(None, None, None)
        _die(f"Semantic search failed: {exc}")

    api_dir = artifact_dir / "api_docs"
    funcs_dir = api_dir / "funcs"
    has_api_docs = funcs_dir.exists()

    combined = []
    for r in results:
        entry: dict = {
            "qualified_name": r.qualified_name,
            "name": r.name,
            "type": r.type,
            "score": r.score,
            "file_path": r.file_path,
            "start_line": r.start_line,
            "end_line": r.end_line,
            "source_code": r.source_code,
        }

        # Try to attach API doc content
        if has_api_docs and r.qualified_name:
            safe_qn = r.qualified_name.replace("/", "_").replace("\\", "_")
            doc_file = funcs_dir / f"{safe_qn}.md"
            if doc_file.exists():
                entry["api_doc"] = doc_file.read_text(encoding="utf-8", errors="ignore")
            else:
                entry["api_doc"] = None
        else:
            entry["api_doc"] = None

        combined.append(entry)

    ingestor.__exit__(None, None, None)

    _result_json({
        "query": query,
        "result_count": len(combined),
        "api_docs_available": has_api_docs,
        "results": combined,
    })


# ---------------------------------------------------------------------------
# Subcommand: wiki-gen (standalone wiki regeneration)
# ---------------------------------------------------------------------------

def cmd_wiki_gen(args: argparse.Namespace, ws: Workspace) -> None:
    from code_graph_builder.examples.generate_wiki import MAX_PAGES_COMPREHENSIVE, MAX_PAGES_CONCISE
    from code_graph_builder.entrypoints.mcp.pipeline import build_vector_index, run_wiki_generation, save_meta

    artifact_dir = ws.require_active()
    meta = ws.load_meta()
    if meta is None:
        _die("No metadata found. Run /repo-init first.")

    repo_path = Path(meta["repo_path"]).resolve()
    if not repo_path.exists():
        _die(f"Repository path no longer exists: {repo_path}")

    db_path = artifact_dir / "graph.db"
    if not db_path.exists():
        _die("Graph database not found. Run /repo-init first to build the graph.")

    vectors_path = artifact_dir / "vectors.pkl"
    if not vectors_path.exists():
        _die("Embeddings not found. Run /repo-init first to build embeddings.")

    wiki_mode = args.mode
    comprehensive = wiki_mode != "concise"
    max_pages = MAX_PAGES_COMPREHENSIVE if comprehensive else MAX_PAGES_CONCISE
    wiki_dir = artifact_dir / "wiki"
    rebuild = args.rebuild

    def progress_cb(msg: str, pct: float = 0.0) -> None:
        prefix = f"[{pct:.0f}%] " if pct > 0 else ""
        _progress(f"{prefix}{msg}")

    _progress(f"=== Wiki Generation: {repo_path.name} ===")
    _progress(f"    Mode: {wiki_mode} | Rebuild: {rebuild}")
    _progress("")

    try:
        # Open existing graph (read-only)
        ingestor = _open_ingestor(artifact_dir)

        # Load existing embeddings (no re-computation)
        with open(vectors_path, "rb") as fh:
            cache = pickle.load(fh)
        vector_store = cache["vector_store"]
        func_map = cache["func_map"]

        from code_graph_builder.domains.core.embedding.qwen3_embedder import create_embedder
        embedder = create_embedder()

        _progress("Loaded existing graph and embeddings. Starting wiki generation...")
        _progress("")

        # Delete structure cache if rebuild requested
        structure_cache = wiki_dir / f"{repo_path.name}_structure.pkl"
        if rebuild and structure_cache.exists():
            structure_cache.unlink()

        index_path, page_count = run_wiki_generation(
            builder=ingestor,
            repo_path=repo_path,
            output_dir=wiki_dir,
            max_pages=max_pages,
            rebuild=rebuild,
            comprehensive=comprehensive,
            vector_store=vector_store,
            embedder=embedder,
            func_map=func_map,
            progress_cb=progress_cb,
        )

        _head = _GCD().get_current_head(repo_path)
        save_meta(artifact_dir, repo_path, page_count, last_indexed_commit=_head)
        ingestor.__exit__(None, None, None)

        _progress("")
        _progress("=== Done ===")
        _result_json({
            "status": "success",
            "repo_path": str(repo_path),
            "wiki_index": str(index_path),
            "wiki_pages": page_count,
        })

    except Exception as exc:
        _progress(f"\nERROR: Wiki generation failed: {exc}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Subcommand: embed-gen (standalone embedding rebuild)
# ---------------------------------------------------------------------------

def cmd_embed_gen(args: argparse.Namespace, ws: Workspace) -> None:
    from code_graph_builder.entrypoints.mcp.pipeline import build_vector_index, save_meta

    artifact_dir = ws.require_active()
    meta = ws.load_meta()
    if meta is None:
        _die("No metadata found. Run /repo-init first.")

    repo_path = Path(meta["repo_path"]).resolve()
    if not repo_path.exists():
        _die(f"Repository path no longer exists: {repo_path}")

    db_path = artifact_dir / "graph.db"
    if not db_path.exists():
        _die("Graph database not found. Run /repo-init first to build the graph.")

    vectors_path = artifact_dir / "vectors.pkl"
    rebuild = args.rebuild

    def progress_cb(msg: str, pct: float = 0.0) -> None:
        prefix = f"[{pct:.0f}%] " if pct > 0 else ""
        _progress(f"{prefix}{msg}")

    _progress(f"=== Embedding Generation: {repo_path.name} ===")
    _progress(f"    Rebuild: {rebuild}")
    _progress("")

    try:
        ingestor = _open_ingestor(artifact_dir)

        _progress("Loaded existing graph. Starting embedding generation...")
        _progress("")

        vector_store, embedder, func_map = build_vector_index(
            ingestor, repo_path, vectors_path, rebuild, progress_cb
        )

        _head = _GCD().get_current_head(repo_path)
        save_meta(artifact_dir, repo_path, meta.get("wiki_page_count", 0), last_indexed_commit=_head)
        ingestor.__exit__(None, None, None)

        _progress("")
        _progress("=== Done ===")
        _result_json({
            "status": "success",
            "repo_path": str(repo_path),
            "vectors_path": str(vectors_path),
            "embedding_count": len(vector_store),
        })

    except Exception as exc:
        _progress(f"\nERROR: Embedding generation failed: {exc}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Main — argparse
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="cgb",
        description="CodeGraphWiki CLI — local command interface",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init (orchestrator: graph-build → api-doc-gen → embed-gen → wiki-gen)
    p = subparsers.add_parser("init", help="Initialize repository (graph → api-docs → embeddings → wiki)")
    p.add_argument("repo_path", help="Absolute path to the repository")
    p.add_argument("--rebuild", action="store_true", help="Force rebuild everything")
    p.add_argument("--mode", choices=["comprehensive", "concise"], default="comprehensive",
                   help="Wiki mode: comprehensive (8-10 pages) or concise (4-5 pages)")
    p.add_argument("--backend", choices=["kuzu", "memgraph", "memory"], default="kuzu",
                   help="Graph database backend")
    p.add_argument("--no-wiki", action="store_true",
                   help="Skip wiki generation (graph + api-docs + embeddings only)")
    p.add_argument("--no-embed", action="store_true",
                   help="Skip embeddings and wiki (graph + api-docs only, fastest)")

    # graph-build (step 1: standalone)
    p = subparsers.add_parser("graph-build", help="Build knowledge graph only (step 1)")
    p.add_argument("repo_path", help="Absolute path to the repository")
    p.add_argument("--rebuild", action="store_true", help="Force rebuild graph")
    p.add_argument("--backend", choices=["kuzu", "memgraph", "memory"], default="kuzu",
                   help="Graph database backend")

    # api-doc-gen (step 2: standalone)
    p = subparsers.add_parser("api-doc-gen", help="Generate API docs from existing graph (step 2)")
    p.add_argument("--rebuild", action="store_true", help="Force regenerate API docs")

    # list-repos
    subparsers.add_parser("list-repos", help="List all indexed repositories in the workspace")

    # switch-repo
    p = subparsers.add_parser("switch-repo", help="Switch active repository")
    p.add_argument("repo_name", help="Repository name or artifact dir name (see /list-repos)")

    # info
    subparsers.add_parser("info", help="Show active repository info and graph statistics")

    # query
    p = subparsers.add_parser("query", help="Natural-language query → Cypher → execute")
    p.add_argument("question", help="Natural language question about the codebase")

    # snippet
    p = subparsers.add_parser("snippet", help="Get source code by qualified name")
    p.add_argument("qualified_name", help="e.g. 'mymodule.MyClass.my_method'")

    # search
    p = subparsers.add_parser("search", help="Semantic vector search")
    p.add_argument("query", help="Natural language description of what to find")
    p.add_argument("--top-k", type=int, default=5, help="Number of results (default: 5)")

    # list-wiki
    subparsers.add_parser("list-wiki", help="List generated wiki pages")

    # get-wiki
    p = subparsers.add_parser("get-wiki", help="Read a wiki page")
    p.add_argument("page_id", help="Page ID: 'index' or 'page-1', 'page-2', etc.")

    # locate
    p = subparsers.add_parser("locate", help="Locate function via Tree-sitter AST")
    p.add_argument("file_path", help="Relative path from repo root")
    p.add_argument("function_name", help="Function/method name (use 'Class.method' for methods)")
    p.add_argument("--line", type=int, default=None, help="Line number to disambiguate overloads")

    # list-api
    p = subparsers.add_parser("list-api", help="List public API interfaces from graph")
    p.add_argument("--module", default=None, help="Filter by module qualified name")
    p.add_argument("--visibility", choices=["public", "static", "extern", "all"],
                   default="public", help="Visibility filter (default: public)")
    p.add_argument("--include-types", action="store_true", default=True,
                   help="Include struct/enum/typedef definitions")

    # api-docs
    p = subparsers.add_parser("api-docs", help="Browse hierarchical API docs (L1 index or L2 module)")
    p.add_argument("--module", default=None, help="Module name for L2 detail (omit for L1 index)")

    # api-doc
    p = subparsers.add_parser("api-doc", help="Read detailed API doc for a function (L3)")
    p.add_argument("qualified_name", help="Fully qualified function name")

    # api-find
    p = subparsers.add_parser("api-find", help="Find APIs by natural language (search + doc lookup)")
    p.add_argument("query", help="Natural language description of what API to find")
    p.add_argument("--top-k", type=int, default=5, help="Number of results (default: 5)")

    # wiki-gen
    p = subparsers.add_parser("wiki-gen", help="Regenerate wiki only (reuses existing graph + embeddings)")
    p.add_argument("--rebuild", action="store_true", help="Force regenerate wiki structure and pages")
    p.add_argument("--mode", choices=["comprehensive", "concise"], default="comprehensive",
                   help="Wiki mode: comprehensive (8-10 pages) or concise (4-5 pages)")

    # embed-gen
    p = subparsers.add_parser("embed-gen", help="Rebuild embeddings only (reuses existing graph)")
    p.add_argument("--rebuild", action="store_true", help="Force rebuild embeddings even if cached")

    # reload
    subparsers.add_parser("reload", help="Hot-reload .env configuration without restarting")

    args = parser.parse_args()

    ws = Workspace()

    dispatch = {
        "init": cmd_init,
        "graph-build": cmd_graph_build,
        "api-doc-gen": cmd_api_doc_gen,
        "list-repos": cmd_list_repos,
        "switch-repo": cmd_switch_repo,
        "info": cmd_info,
        "query": cmd_query,
        "snippet": cmd_snippet,
        "search": cmd_search,
        "list-wiki": cmd_list_wiki,
        "get-wiki": cmd_get_wiki,
        "locate": cmd_locate,
        "list-api": cmd_list_api,
        "api-docs": cmd_api_docs,
        "api-doc": cmd_api_doc,
        "api-find": cmd_api_find,
        "wiki-gen": cmd_wiki_gen,
        "embed-gen": cmd_embed_gen,
        "reload": cmd_reload,
    }

    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    handler(args, ws)


if __name__ == "__main__":
    main()
