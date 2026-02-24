"""MCP tool registry and handler implementations for Code Graph Builder.

Architecture: workspace-based, dynamic service loading.

Workspace layout:
    {CGB_WORKSPACE}/               default: ~/.code-graph-builder/
        active.txt                 name of the currently active artifact dir
        {repo_name}_{hash8}/
            meta.json              {repo_path, indexed_at, wiki_page_count}
            graph.db               KùzuDB database
            vectors.pkl            embedding cache
            {repo_name}_structure.pkl  wiki structure cache
            wiki/
                index.md
                wiki/
                    page-1.md
                    ...
"""

from __future__ import annotations

import asyncio
import json
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

from ..embeddings.qwen3_embedder import Qwen3Embedder
from ..embeddings.vector_store import MemoryVectorStore, VectorRecord
from ..rag.cypher_generator import CypherGenerator
from ..rag.llm_backend import create_llm_backend
from ..services.kuzu_service import KuzuIngestor
from ..tools.semantic_search import SemanticSearchService
from .file_editor import FileEditor
from .pipeline import (
    ProgressCb,
    artifact_dir_for,
    build_graph,
    build_vector_index,
    run_wiki_generation,
    save_meta,
)


@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]


def _load_vector_store(vectors_path: Path) -> MemoryVectorStore:
    """Load MemoryVectorStore from a pickle cache file."""
    if not vectors_path.exists():
        raise FileNotFoundError(f"Vectors file not found: {vectors_path}")

    with open(vectors_path, "rb") as fh:
        data = pickle.load(fh)

    if isinstance(data, dict) and "vector_store" in data:
        store = data["vector_store"]
        if isinstance(store, MemoryVectorStore):
            return store
        raise RuntimeError(
            f"'vector_store' key found but value is not MemoryVectorStore: {type(store)}"
        )

    if not isinstance(data, list) or len(data) == 0:
        raise RuntimeError(
            f"Unexpected vectors file content: expected non-empty list, got {type(data)}"
        )

    first = data[0]
    if isinstance(first, VectorRecord):
        dimension = len(first.embedding)
        store = MemoryVectorStore(dimension=dimension)
        store.store_embeddings_batch(data)
        return store

    if isinstance(first, dict) and "embedding" in first:
        dimension = len(first["embedding"])
        store = MemoryVectorStore(dimension=dimension)
        for idx, item in enumerate(data):
            store.store_embedding(
                node_id=item.get("node_id", idx),
                qualified_name=item.get("qualified_name", str(idx)),
                embedding=item["embedding"],
                metadata={
                    k: v
                    for k, v in item.items()
                    if k not in ("node_id", "qualified_name", "embedding")
                    and isinstance(v, (str, int, float, type(None)))
                },
            )
        return store

    raise RuntimeError(
        f"Unrecognised vectors file format. First element type: {type(first)}"
    )


class MCPToolsRegistry:
    """Registry that manages workspace-based repo services and tool handlers."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self._workspace.mkdir(parents=True, exist_ok=True)

        self._ingestor: KuzuIngestor | None = None
        self._cypher_gen: CypherGenerator | None = None
        self._semantic_service: SemanticSearchService | None = None
        self._file_editor: FileEditor | None = None
        self._active_repo_path: Path | None = None
        self._active_artifact_dir: Path | None = None

        self._try_auto_load()

    def _try_auto_load(self) -> None:
        """Try to load the last active repo from workspace."""
        active_file = self._workspace / "active.txt"
        if not active_file.exists():
            return
        artifact_dir_name = active_file.read_text(encoding="utf-8").strip()
        artifact_dir = self._workspace / artifact_dir_name
        if artifact_dir.exists():
            try:
                self._load_services(artifact_dir)
                logger.info(f"Auto-loaded repo from: {artifact_dir}")
            except Exception as exc:
                logger.warning(f"Graph/LLM services unavailable: {exc}")

    def _load_services(self, artifact_dir: Path) -> None:
        """Load KuzuIngestor + CypherGenerator + SemanticSearchService from artifact dir."""
        meta_file = artifact_dir / "meta.json"
        if not meta_file.exists():
            raise FileNotFoundError(f"meta.json not found in {artifact_dir}")

        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        repo_path = Path(meta["repo_path"])
        db_path = artifact_dir / "graph.db"
        vectors_path = artifact_dir / "vectors.pkl"

        self.close()
        self._active_repo_path = repo_path
        self._active_artifact_dir = artifact_dir
        try:
            self._file_editor = FileEditor(repo_path)
        except Exception as exc:
            logger.warning(f"File editor unavailable: {exc}")

        ingestor = KuzuIngestor(db_path)
        ingestor.__enter__()
        self._ingestor = ingestor

        cypher_gen = CypherGenerator(create_llm_backend())

        semantic_service: SemanticSearchService | None = None
        if vectors_path.exists():
            try:
                vector_store = _load_vector_store(vectors_path)
                embedder = Qwen3Embedder()
                semantic_service = SemanticSearchService(
                    embedder=embedder,
                    vector_store=vector_store,
                    graph_service=ingestor,
                )
                logger.info(f"Loaded vector store: {vector_store.get_stats()}")
            except Exception as exc:
                logger.warning(f"Semantic search unavailable: {exc}")

        self._cypher_gen = cypher_gen
        self._semantic_service = semantic_service

    def _set_active(self, artifact_dir: Path) -> None:
        """Mark artifact_dir as active in workspace."""
        (self._workspace / "active.txt").write_text(
            artifact_dir.name, encoding="utf-8"
        )

    def close(self) -> None:
        if self._ingestor is not None:
            try:
                self._ingestor.__exit__(None, None, None)
            except Exception:
                pass
            self._ingestor = None
        self._file_editor = None

    def _require_active(self) -> str:
        if self._ingestor is None:
            return "No repository indexed yet. Call initialize_repository first."
        return ""

    def _require_repo_path(self) -> str:
        if self._active_repo_path is None:
            return "No repository path set. Call initialize_repository first."
        return ""

    def tools(self) -> list[ToolDefinition]:
        defs: list[ToolDefinition] = [
            ToolDefinition(
                name="initialize_repository",
                description=(
                    "Index a code repository: builds the knowledge graph, generates vector "
                    "embeddings, and produces a multi-page wiki. "
                    "Must be called before using any query tools. "
                    "Takes 2-10 minutes depending on repo size."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "repo_path": {
                            "type": "string",
                            "description": "Absolute path to the repository to index.",
                        },
                        "rebuild": {
                            "type": "boolean",
                            "description": (
                                "If true, force-rebuild graph, embeddings, and wiki "
                                "even if cached data exists. Default: false."
                            ),
                        },
                        "wiki_mode": {
                            "type": "string",
                            "enum": ["comprehensive", "concise"],
                            "description": (
                                "comprehensive: 8-10 wiki pages (default). "
                                "concise: 4-5 wiki pages."
                            ),
                        },
                    },
                    "required": ["repo_path"],
                },
            ),
            ToolDefinition(
                name="get_active_repository",
                description="Return information about the currently active (indexed) repository.",
                input_schema={"type": "object", "properties": {}, "required": []},
            ),
            ToolDefinition(
                name="query_code_graph",
                description=(
                    "Translate a natural-language question into Cypher and execute it "
                    "against the code knowledge graph. Returns raw graph rows."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "Natural language question about the codebase.",
                        }
                    },
                    "required": ["question"],
                },
            ),
            ToolDefinition(
                name="get_code_snippet",
                description=(
                    "Retrieve source code of a function, method, or class by fully qualified name."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "qualified_name": {
                            "type": "string",
                            "description": "Fully qualified name, e.g. 'mymodule.MyClass.my_method'.",
                        }
                    },
                    "required": ["qualified_name"],
                },
            ),
            ToolDefinition(
                name="get_graph_stats",
                description="Return statistics about the code knowledge graph (node/relationship counts).",
                input_schema={"type": "object", "properties": {}, "required": []},
            ),
            ToolDefinition(
                name="read_file",
                description="Read the contents of a file within the indexed repository (paginated).",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative path from the repository root.",
                        },
                        "start_line": {
                            "type": "integer",
                            "description": "First line to return (1-indexed). Default: 1.",
                        },
                        "end_line": {
                            "type": "integer",
                            "description": "Last line to return (inclusive). Default: all lines.",
                        },
                    },
                    "required": ["path"],
                },
            ),
            ToolDefinition(
                name="list_directory",
                description="List files and subdirectories within the indexed repository.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Relative path from repository root. Default: '.'.",
                        }
                    },
                    "required": [],
                },
            ),
            ToolDefinition(
                name="semantic_search",
                description=(
                    "Search the codebase semantically using vector embeddings. "
                    "Returns the most relevant functions/classes for the query. "
                    "Available after initialize_repository completes."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural language description of what to find.",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Number of results. Default: 5.",
                        },
                        "entity_types": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Filter by type: 'Function', 'Class', 'Method', etc.",
                        },
                    },
                    "required": ["query"],
                },
            ),
            ToolDefinition(
                name="list_wiki_pages",
                description="List all generated wiki pages for the active repository.",
                input_schema={"type": "object", "properties": {}, "required": []},
            ),
            ToolDefinition(
                name="get_wiki_page",
                description="Read the content of a generated wiki page.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "page_id": {
                            "type": "string",
                            "description": (
                                "Page ID (e.g. 'page-1') or 'index' for the summary page. "
                                "Use list_wiki_pages to see available pages."
                            ),
                        }
                    },
                    "required": ["page_id"],
                },
            ),
            ToolDefinition(
                name="locate_function",
                description=(
                    "Locate a function or method in the repository using Tree-sitter AST. "
                    "Returns the source code, start/end line numbers, and qualified name."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Relative path from repo root.",
                        },
                        "function_name": {
                            "type": "string",
                            "description": (
                                "Function or method name. "
                                "Use 'ClassName.method' to disambiguate overloads."
                            ),
                        },
                        "line_number": {
                            "type": "integer",
                            "description": "Optional: line number to disambiguate overloads.",
                        },
                    },
                    "required": ["file_path", "function_name"],
                },
            ),
            ToolDefinition(
                name="get_function_diff",
                description=(
                    "Locate a function by AST and generate a unified diff between the "
                    "original source and the provided new code."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Relative path from repo root.",
                        },
                        "function_name": {
                            "type": "string",
                            "description": "Function or method name.",
                        },
                        "new_code": {
                            "type": "string",
                            "description": "Proposed replacement source code.",
                        },
                        "line_number": {
                            "type": "integer",
                            "description": "Optional: line number to disambiguate overloads.",
                        },
                    },
                    "required": ["file_path", "function_name", "new_code"],
                },
            ),
            ToolDefinition(
                name="surgical_replace_code",
                description=(
                    "Replace an exact code block in a file using diff-match-patch for "
                    "validation. The target_code must be an exact substring of the file."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "file_path": {
                            "type": "string",
                            "description": "Relative path from repo root.",
                        },
                        "target_code": {
                            "type": "string",
                            "description": "Exact code block to replace (must match file content).",
                        },
                        "replacement_code": {
                            "type": "string",
                            "description": "New code to substitute in place of target_code.",
                        },
                    },
                    "required": ["file_path", "target_code", "replacement_code"],
                },
            ),
            ToolDefinition(
                name="list_api_interfaces",
                description=(
                    "List public API interfaces (function signatures) for a module or "
                    "the entire project. Returns function name, full signature, return "
                    "type, parameters, and visibility. Particularly useful for C "
                    "codebases to understand module boundaries."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "module": {
                            "type": "string",
                            "description": (
                                "Module qualified name to query. "
                                "If omitted, returns APIs across all modules."
                            ),
                        },
                        "visibility": {
                            "type": "string",
                            "enum": ["public", "static", "all"],
                            "description": (
                                "Filter by visibility: 'public' (default) for externally "
                                "visible functions, 'static' for file-local functions, "
                                "'all' for both."
                            ),
                        },
                    },
                    "required": [],
                },
            ),
        ]

        return defs

    def get_handler(self, name: str):
        handlers: dict[str, Any] = {
            "initialize_repository": self._handle_initialize_repository,
            "get_active_repository": self._handle_get_active_repository,
            "query_code_graph": self._handle_query_code_graph,
            "get_code_snippet": self._handle_get_code_snippet,
            "semantic_search": self._handle_semantic_search,
            "get_graph_stats": self._handle_get_graph_stats,
            "read_file": self._handle_read_file,
            "list_directory": self._handle_list_directory,
            "list_wiki_pages": self._handle_list_wiki_pages,
            "get_wiki_page": self._handle_get_wiki_page,
            "locate_function": self._handle_locate_function,
            "get_function_diff": self._handle_get_function_diff,
            "surgical_replace_code": self._handle_surgical_replace_code,
            "list_api_interfaces": self._handle_list_api_interfaces,
        }
        return handlers.get(name)

    # -------------------------------------------------------------------------
    # initialize_repository — runs the full pipeline in a thread pool
    # -------------------------------------------------------------------------

    async def _handle_initialize_repository(
        self,
        repo_path: str,
        rebuild: bool = False,
        wiki_mode: str = "comprehensive",
        _progress_cb: ProgressCb = None,
    ) -> dict[str, Any]:
        repo = Path(repo_path).resolve()
        if not repo.exists():
            return {"error": f"Repository path does not exist: {repo}"}

        loop = asyncio.get_event_loop()

        def sync_progress(msg: str) -> None:
            if _progress_cb is not None:
                asyncio.run_coroutine_threadsafe(_progress_cb(msg), loop)

        result = await loop.run_in_executor(
            None,
            lambda: self._run_pipeline(repo, rebuild, wiki_mode, sync_progress),
        )
        return result

    def _run_pipeline(
        self,
        repo_path: Path,
        rebuild: bool,
        wiki_mode: str,
        progress_cb: ProgressCb = None,
    ) -> dict[str, Any]:
        """Synchronous pipeline: graph → embeddings → wiki. Runs in thread pool."""
        from ..examples.generate_wiki import MAX_PAGES_COMPREHENSIVE, MAX_PAGES_CONCISE

        artifact_dir = artifact_dir_for(self._workspace, repo_path)
        artifact_dir.mkdir(parents=True, exist_ok=True)

        db_path = artifact_dir / "graph.db"
        vectors_path = artifact_dir / "vectors.pkl"
        wiki_dir = artifact_dir / "wiki"
        comprehensive = wiki_mode != "concise"
        max_pages = MAX_PAGES_COMPREHENSIVE if comprehensive else MAX_PAGES_CONCISE

        try:
            builder = build_graph(repo_path, db_path, rebuild, progress_cb)

            vector_store, embedder, func_map = build_vector_index(
                builder, repo_path, vectors_path, rebuild, progress_cb
            )

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
                progress_cb=progress_cb,
            )

            save_meta(artifact_dir, repo_path, page_count)
            self._set_active(artifact_dir)
            self._load_services(artifact_dir)

            return {
                "status": "success",
                "repo_path": str(repo_path),
                "artifact_dir": str(artifact_dir),
                "wiki_index": str(index_path),
                "wiki_pages": page_count,
            }

        except Exception as exc:
            logger.exception("Pipeline failed")
            return {
                "status": "error",
                "error": str(exc),
            }

    # -------------------------------------------------------------------------
    # get_active_repository
    # -------------------------------------------------------------------------

    async def _handle_get_active_repository(self) -> dict[str, Any]:
        if self._active_artifact_dir is None:
            return {"status": "no_active_repo", "message": "Call initialize_repository first."}

        meta_file = self._active_artifact_dir / "meta.json"
        meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}

        wiki_pages = []
        wiki_subdir = self._active_artifact_dir / "wiki" / "wiki"
        if wiki_subdir.exists():
            wiki_pages = [p.stem for p in sorted(wiki_subdir.glob("*.md"))]

        return {
            "repo_path": str(self._active_repo_path),
            "artifact_dir": str(self._active_artifact_dir),
            "indexed_at": meta.get("indexed_at"),
            "semantic_search_available": self._semantic_service is not None,
            "wiki_pages": wiki_pages,
        }

    # -------------------------------------------------------------------------
    # query_code_graph
    # -------------------------------------------------------------------------

    async def _handle_query_code_graph(self, question: str) -> dict[str, Any]:
        err = self._require_active()
        if err:
            return {"error": err}

        assert self._cypher_gen is not None
        assert self._ingestor is not None

        try:
            cypher = self._cypher_gen.generate(question)
        except Exception as exc:
            return {"error": f"Cypher generation failed: {exc}", "question": question}

        try:
            rows = self._ingestor.query(cypher)
            serialisable = []
            for row in rows:
                raw = row.get("result", row)
                if isinstance(raw, (list, tuple)):
                    serialisable.append(list(raw))
                else:
                    serialisable.append(raw)
            return {
                "question": question,
                "cypher": cypher,
                "row_count": len(serialisable),
                "rows": serialisable,
            }
        except Exception as exc:
            return {
                "error": f"Query execution failed: {exc}",
                "question": question,
                "cypher": cypher,
            }

    # -------------------------------------------------------------------------
    # get_code_snippet
    # -------------------------------------------------------------------------

    async def _handle_get_code_snippet(self, qualified_name: str) -> dict[str, Any]:
        err = self._require_active()
        if err:
            return {"error": err}

        assert self._ingestor is not None

        safe_qn = qualified_name.replace("'", "\\'")
        cypher = (
            f"MATCH (n) WHERE n.qualified_name = '{safe_qn}' "
            "RETURN n.qualified_name, n.name, n.source_code, n.path, n.start_line, n.end_line "
            "LIMIT 1"
        )

        try:
            rows = self._ingestor.query(cypher)
        except Exception as exc:
            return {"error": f"Graph query failed: {exc}", "qualified_name": qualified_name}

        if not rows:
            return {"error": "Not found", "qualified_name": qualified_name}

        result = rows[0].get("result", [])
        qname = result[0] if len(result) > 0 else qualified_name
        name = result[1] if len(result) > 1 else None
        source_code = result[2] if len(result) > 2 else None
        file_path = result[3] if len(result) > 3 else None
        start_line = result[4] if len(result) > 4 else None
        end_line = result[5] if len(result) > 5 else None

        if not source_code and file_path and start_line and end_line:
            fp = Path(str(file_path))
            if not fp.is_absolute() and self._active_repo_path:
                fp = self._active_repo_path / fp
            try:
                lines = fp.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
                s = max(0, int(start_line) - 1)
                e = min(len(lines), int(end_line))
                source_code = "".join(lines[s:e])
            except Exception:
                pass

        return {
            "qualified_name": qname,
            "name": name,
            "file_path": file_path,
            "start_line": start_line,
            "end_line": end_line,
            "source_code": source_code,
        }

    # -------------------------------------------------------------------------
    # semantic_search
    # -------------------------------------------------------------------------

    async def _handle_semantic_search(
        self,
        query: str,
        top_k: int = 5,
        entity_types: list[str] | None = None,
    ) -> dict[str, Any]:
        err = self._require_active()
        if err:
            return {"error": err}

        if self._semantic_service is None:
            return {"error": "Semantic search not available. Re-run initialize_repository to build embeddings."}

        try:
            results = self._semantic_service.search(query, top_k=top_k, entity_types=entity_types)
            return {
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
            }
        except Exception as exc:
            return {"error": f"Semantic search failed: {exc}", "query": query}

    # -------------------------------------------------------------------------
    # get_graph_stats
    # -------------------------------------------------------------------------

    async def _handle_get_graph_stats(self) -> dict[str, Any]:
        err = self._require_active()
        if err:
            return {"error": err}

        assert self._ingestor is not None

        try:
            return self._ingestor.get_statistics()
        except Exception as exc:
            return {"error": f"Failed to get statistics: {exc}"}

    # -------------------------------------------------------------------------
    # read_file / list_directory  (path safety: must stay within repo root)
    # -------------------------------------------------------------------------

    def _safe_path(self, rel_path: str) -> Path | None:
        if self._active_repo_path is None:
            return None
        target = (self._active_repo_path / rel_path).resolve()
        try:
            target.relative_to(self._active_repo_path.resolve())
        except ValueError:
            return None
        return target

    async def _handle_read_file(
        self,
        path: str,
        start_line: int = 1,
        end_line: int | None = None,
    ) -> dict[str, Any]:
        err = self._require_active()
        if err:
            return {"error": err}

        target = self._safe_path(path)
        if target is None:
            return {"error": "Path is outside the repository root.", "path": path}
        if not target.exists():
            return {"error": "File not found.", "path": path}
        if not target.is_file():
            return {"error": "Path is not a file.", "path": path}

        try:
            lines = target.read_text(encoding="utf-8", errors="ignore").splitlines(keepends=True)
            total = len(lines)
            start_idx = max(0, start_line - 1)
            end_idx = min(total, end_line) if end_line is not None else total
            content = "".join(lines[start_idx:end_idx])
            return {
                "path": path,
                "start_line": start_line,
                "end_line": end_idx,
                "total_lines": total,
                "content": content,
            }
        except Exception as exc:
            return {"error": f"Failed to read file: {exc}", "path": path}

    async def _handle_list_directory(self, path: str = ".") -> dict[str, Any]:
        err = self._require_active()
        if err:
            return {"error": err}

        target = self._safe_path(path)
        if target is None:
            return {"error": "Path is outside the repository root.", "path": path}
        if not target.exists():
            return {"error": "Directory not found.", "path": path}
        if not target.is_dir():
            return {"error": "Path is not a directory.", "path": path}

        try:
            entries = sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name))
            return {
                "path": path,
                "entries": [
                    {
                        "name": e.name,
                        "type": "file" if e.is_file() else "directory",
                        "size": e.stat().st_size if e.is_file() else None,
                    }
                    for e in entries
                ],
            }
        except Exception as exc:
            return {"error": f"Failed to list directory: {exc}", "path": path}

    # -------------------------------------------------------------------------
    # wiki tools
    # -------------------------------------------------------------------------

    def _wiki_dir(self) -> Path | None:
        if self._active_artifact_dir is None:
            return None
        return self._active_artifact_dir / "wiki"

    async def _handle_list_wiki_pages(self) -> dict[str, Any]:
        err = self._require_active()
        if err:
            return {"error": err}

        wiki_dir = self._wiki_dir()
        if wiki_dir is None or not wiki_dir.exists():
            return {"error": "Wiki not generated yet. Run initialize_repository first."}

        pages = []
        wiki_subdir = wiki_dir / "wiki"
        if wiki_subdir.exists():
            for p in sorted(wiki_subdir.glob("*.md")):
                pages.append({"page_id": p.stem, "file": f"wiki/{p.name}"})

        index_path = wiki_dir / "index.md"
        return {
            "index_available": index_path.exists(),
            "page_count": len(pages),
            "pages": pages,
            "hint": "Use get_wiki_page with page_id='index' for the summary, or a specific page-N id.",
        }

    async def _handle_get_wiki_page(self, page_id: str) -> dict[str, Any]:
        err = self._require_active()
        if err:
            return {"error": err}

        wiki_dir = self._wiki_dir()
        if wiki_dir is None or not wiki_dir.exists():
            return {"error": "Wiki not generated yet. Run initialize_repository first."}

        if page_id == "index":
            target = wiki_dir / "index.md"
        else:
            target = wiki_dir / "wiki" / f"{page_id}.md"

        if not target.exists():
            return {"error": f"Wiki page not found: {page_id}", "page_id": page_id}

        content = target.read_text(encoding="utf-8", errors="ignore")
        return {
            "page_id": page_id,
            "file_path": str(target),
            "content": content,
        }

    # -------------------------------------------------------------------------
    # locate_function / get_function_diff / surgical_replace_code
    # -------------------------------------------------------------------------

    async def _handle_locate_function(
        self,
        file_path: str,
        function_name: str,
        line_number: int | None = None,
    ) -> dict[str, Any]:
        err = self._require_repo_path()
        if err:
            return {"error": err}
        if self._file_editor is None:
            return {"error": "File editor not initialized."}

        target = self._safe_path(file_path)
        if target is None:
            return {"error": "Path outside repository root.", "file_path": file_path}
        if not target.exists():
            return {"error": "File not found.", "file_path": file_path}

        result = self._file_editor.locate_function(target, function_name, line_number)
        if result is None:
            return {
                "error": f"Function '{function_name}' not found in {file_path}.",
                "file_path": file_path,
                "function_name": function_name,
            }
        return result

    async def _handle_get_function_diff(
        self,
        file_path: str,
        function_name: str,
        new_code: str,
        line_number: int | None = None,
    ) -> dict[str, Any]:
        err = self._require_repo_path()
        if err:
            return {"error": err}
        if self._file_editor is None:
            return {"error": "File editor not initialized."}

        target = self._safe_path(file_path)
        if target is None:
            return {"error": "Path outside repository root.", "file_path": file_path}
        if not target.exists():
            return {"error": "File not found.", "file_path": file_path}

        located = self._file_editor.locate_function(target, function_name, line_number)
        if located is None:
            return {
                "error": f"Function '{function_name}' not found in {file_path}.",
                "file_path": file_path,
                "function_name": function_name,
            }

        diff = self._file_editor.get_diff(
            located["source_code"], new_code, label=function_name
        )
        return {
            "file_path": file_path,
            "function_name": function_name,
            "qualified_name": located["qualified_name"],
            "start_line": located["start_line"],
            "end_line": located["end_line"],
            "diff": diff,
        }

    async def _handle_surgical_replace_code(
        self,
        file_path: str,
        target_code: str,
        replacement_code: str,
    ) -> dict[str, Any]:
        err = self._require_repo_path()
        if err:
            return {"error": err}
        if self._file_editor is None:
            return {"error": "File editor not initialized."}

        target = self._safe_path(file_path)
        if target is None:
            return {"error": "Path outside repository root.", "file_path": file_path}

        result = self._file_editor.replace_code_block(target, target_code, replacement_code)
        result["file_path"] = file_path
        return result

    # -------------------------------------------------------------------------
    # list_api_interfaces
    # -------------------------------------------------------------------------

    async def _handle_list_api_interfaces(
        self,
        module: str | None = None,
        visibility: str = "public",
    ) -> dict[str, Any]:
        err = self._require_active()
        if err:
            return {"error": err}

        assert self._ingestor is not None

        vis_filter = None if visibility == "all" else visibility

        try:
            rows = self._ingestor.fetch_module_apis(
                module_qn=module,
                visibility=vis_filter,
            )

            # Group results by module for readability
            by_module: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                raw = row.get("result", row)
                if isinstance(raw, (list, tuple)) and len(raw) >= 8:
                    mod_name = raw[0] or "unknown"
                    entry: dict[str, Any] = {
                        "name": raw[1],
                        "signature": raw[2],
                        "return_type": raw[3],
                        "visibility": raw[4],
                        "parameters": raw[5],
                        "start_line": raw[6],
                        "end_line": raw[7],
                    }
                else:
                    # Fallback for dict-based results
                    mod_name = raw.get("module", "unknown") if isinstance(raw, dict) else "unknown"
                    entry = raw if isinstance(raw, dict) else {"raw": raw}

                if mod_name not in by_module:
                    by_module[mod_name] = []
                by_module[mod_name].append(entry)

            total = sum(len(v) for v in by_module.values())
            return {
                "total_apis": total,
                "module_count": len(by_module),
                "visibility_filter": visibility,
                "modules": by_module,
            }

        except Exception as exc:
            return {"error": f"Failed to list API interfaces: {exc}"}
