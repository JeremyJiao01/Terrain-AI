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
from contextlib import contextmanager
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
    _collect_todo_funcs,
    artifact_dir_for,
    build_graph,
    build_vector_index,
    enhance_api_docs_step,
    generate_api_docs_step,
    generate_descriptions_step,
    run_wiki_generation,
    save_meta,
)


@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]


class ToolError(Exception):
    """Error raised by tool handlers.

    The MCP framework catches exceptions and returns ``CallToolResult`` with
    ``isError=True``, so the agent can detect errors via the protocol-level
    flag instead of having to parse JSON response bodies.
    """

    def __init__(self, error_data: dict[str, Any] | str) -> None:
        if isinstance(error_data, str):
            error_data = {"error": error_data}
        self.error_data = error_data
        super().__init__(json.dumps(error_data, ensure_ascii=False, default=str))


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
        artifact_dir_name = active_file.read_text(encoding="utf-8", errors="replace").strip()
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

        meta = json.loads(meta_file.read_text(encoding="utf-8", errors="replace"))
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

        # Note: We don't keep a persistent ingestor connection open anymore
        # to avoid file locks. Each tool that needs Kuzu will create a temporary
        # connection and close it after use.
        self._db_path = db_path

        llm = create_llm_backend()
        cypher_gen: CypherGenerator | None = None
        if llm.available:
            cypher_gen = CypherGenerator(llm)
        else:
            logger.warning("LLM not configured — query_code_graph will be unavailable")

        # Load semantic search service without graph_service (Kuzu) dependency
        # find_api will work with vector search only, avoiding Kuzu file locks
        semantic_service: SemanticSearchService | None = None
        if vectors_path.exists():
            try:
                vector_store = _load_vector_store(vectors_path)
                from ..embeddings.qwen3_embedder import create_embedder
                embedder = create_embedder(batch_size=10)
                semantic_service = SemanticSearchService(
                    embedder=embedder,
                    vector_store=vector_store,
                    graph_service=None,  # No Kuzu dependency for find_api
                )
                logger.info(f"Loaded vector store: {vector_store.get_stats()}")
            except Exception as exc:
                logger.warning(
                    f"Semantic search unavailable: {exc}. "
                    "Check DASHSCOPE_API_KEY or EMBEDDING_API_KEY / OPENAI_API_KEY."
                )

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

    @contextmanager
    def _temporary_ingestor(self):
        """Context manager for temporary Kuzu ingestor connection.

        Usage:
            with self._temporary_ingestor() as ingestor:
                # Use ingestor for queries
                rows = ingestor.query(...)
            # Connection automatically closed here
        """
        if self._active_artifact_dir is None:
            raise ToolError("No active repository. Call initialize_repository first.")

        db_path = self._active_artifact_dir / "graph.db"
        if not db_path.exists():
            raise ToolError("Graph database not found. Run initialize_repository first.")

        ingestor = KuzuIngestor(db_path, read_only=True)
        try:
            ingestor.__enter__()
            yield ingestor
        finally:
            ingestor.__exit__(None, None, None)

    def _require_active(self) -> None:
        """Raise :class:`ToolError` when no repository has been indexed."""
        if self._active_artifact_dir is None:
            raise ToolError("No repository indexed yet. Call initialize_repository first.")

    def _require_repo_path(self) -> None:
        """Raise :class:`ToolError` when no repository path is set."""
        if self._active_repo_path is None:
            raise ToolError("No repository path set. Call initialize_repository first.")

    def tools(self) -> list[ToolDefinition]:
        defs: list[ToolDefinition] = [
            ToolDefinition(
                name="initialize_repository",
                description=(
                    "Index a code repository: builds the knowledge graph, generates "
                    "API documentation, and creates vector embeddings for semantic search. "
                    "Must be called before using any query tools. "
                    "Always performs a fresh build (cached data is ignored). "
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
                                "Always forces a fresh rebuild. This parameter is kept for "
                                "compatibility but rebuild is always enabled. Default: true."
                            ),
                        },
                        "skip_embed": {
                            "type": "boolean",
                            "description": (
                                "Skip embedding generation (graph + API docs only, fastest). "
                                "Semantic search (find_api) will be unavailable. Default: false."
                            ),
                        },
                    },
                    "required": ["repo_path"],
                },
            ),
            ToolDefinition(
                name="get_repository_info",
                description=(
                    "Return information about the currently active (indexed) repository, "
                    "including graph statistics (node/relationship counts), wiki pages, "
                    "and service availability."
                ),
                input_schema={"type": "object", "properties": {}, "required": []},
            ),
            ToolDefinition(
                name="list_repositories",
                description=(
                    "List all previously indexed repositories in the workspace. "
                    "Shows repo name, path, last indexed time, which pipeline steps "
                    "have been completed (graph, api_docs, embeddings, wiki), and "
                    "which one is currently active. Use this to discover available "
                    "repos and switch between them with switch_repository."
                ),
                input_schema={"type": "object", "properties": {}, "required": []},
            ),
            ToolDefinition(
                name="switch_repository",
                description=(
                    "Switch the active repository to a previously indexed one. "
                    "After switching, all query tools (query_code_graph, semantic_search, "
                    "list_wiki_pages, etc.) will operate on the selected repo. "
                    "Use list_repositories first to see available repos."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "repo_name": {
                            "type": "string",
                            "description": (
                                "Repository name or artifact directory name "
                                "(e.g. 'my-project' or 'my-project_a1b2c3d4'). "
                                "Use list_repositories to see available names."
                            ),
                        },
                    },
                    "required": ["repo_name"],
                },
            ),
            ToolDefinition(
                name="link_repository",
                description=(
                    "Link a new repository path to an existing index. "
                    "Reuses the graph database, API docs, and embeddings from a "
                    "previously indexed repository without re-generating anything. "
                    "Useful when multiple working copies share the same codebase."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "repo_path": {
                            "type": "string",
                            "description": "Absolute path to the new repository to link.",
                        },
                        "source_repo": {
                            "type": "string",
                            "description": (
                                "Name of the already-indexed repository to link to "
                                "(e.g. 'tinycc' or 'tinycc_4a16f1cf'). "
                                "Use list_repositories to see available names."
                            ),
                        },
                    },
                    "required": ["repo_path", "source_repo"],
                },
            ),
            # -----------------------------------------------------------------
            # Core query tools: fuzzy locate → browse → deep dive
            # -----------------------------------------------------------------
            ToolDefinition(
                name="find_api",
                description=(
                    "Find relevant APIs by natural language description. "
                    "Combines semantic vector search with API documentation lookup "
                    "in a single call — returns matching functions along with their "
                    "signatures, docstrings, and call graphs. "
                    "This is the primary tool for locating code from vague requirements."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural language description of the API to find.",
                        },
                        "top_k": {
                            "type": "integer",
                            "description": "Number of results. Default: 5.",
                        },
                    },
                    "required": ["query"],
                },
            ),
            ToolDefinition(
                name="list_api_docs",
                description=(
                    "Browse API documentation hierarchically. Without arguments, "
                    "returns the L1 module index. With a module name, returns the "
                    "L2 module page listing all functions, types, and macros. "
                    "Use when you need to explore a module's structure."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "module": {
                            "type": "string",
                            "description": (
                                "Module qualified name (e.g. 'project.api'). "
                                "If omitted, returns the L1 index listing all modules."
                            ),
                        },
                    },
                    "required": [],
                },
            ),
            ToolDefinition(
                name="get_api_doc",
                description=(
                    "Read the detailed L3 API documentation for a specific function. "
                    "Includes signature, description, full call tree (callees with depth), "
                    "caller list with locations, real usage examples extracted from "
                    "the codebase, parameter ownership, and source code implementation. "
                    "Use this to understand how to call a function and how to combine "
                    "it with other APIs."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "qualified_name": {
                            "type": "string",
                            "description": (
                                "Fully qualified function name "
                                "(e.g. 'project.api.api_init')."
                            ),
                        },
                    },
                    "required": ["qualified_name"],
                },
            ),
            # -----------------------------------------------------------------
            # Doc generation
            # -----------------------------------------------------------------
            ToolDefinition(
                name="generate_api_docs",
                description=(
                    "Generate and enhance API documentation. Supports three modes:\n"
                    "  • 'full' — rebuild all docs from graph + LLM function descriptions\n"
                    "  • 'resume' — only generate LLM descriptions for remaining TODOs\n"
                    "  • 'enhance' — generate module summaries and API usage workflows via LLM\n"
                    "All modes are resumable with circuit breaker protection."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "mode": {
                            "type": "string",
                            "enum": ["full", "resume", "enhance"],
                            "description": (
                                "'full': regenerate all API docs from the graph database, "
                                "then run LLM description generation. "
                                "'resume': only run LLM descriptions for remaining TODOs. "
                                "'enhance': generate module-level summaries (what each module does) "
                                "and API usage workflows (how to combine functions). "
                                "Default: 'full'."
                            ),
                        },
                    },
                    "required": [],
                },
            ),
            # -----------------------------------------------------------------
            # Configuration / diagnostics
            # -----------------------------------------------------------------
            ToolDefinition(
                name="get_config",
                description=(
                    "Show current MCP server configuration: LLM provider, model, "
                    "embedding provider, workspace path, and service availability. "
                    "Useful for debugging connection issues or verifying setup."
                ),
                input_schema={"type": "object", "properties": {}, "required": []},
            ),
            # -----------------------------------------------------------------
            # Embedding
            # -----------------------------------------------------------------
            ToolDefinition(
                name="rebuild_embeddings",
                description=(
                    "Build or rebuild vector embeddings for the active repository. "
                    "Requires a previously initialized repository with API docs. "
                    "After completion, semantic_search and find_api will use the "
                    "new embeddings. Set rebuild=true to force regeneration even "
                    "if cached embeddings exist."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "rebuild": {
                            "type": "boolean",
                            "description": (
                                "Force rebuild embeddings even if cached. "
                                "Default: false (reuse cache if available)."
                            ),
                        },
                    },
                    "required": [],
                },
            ),
            # -----------------------------------------------------------------
            # Hidden tools — handlers preserved, not exposed to MCP clients.
            # Superseded by API-doc-based workflows above.
            #   query_code_graph, get_code_snippet, semantic_search,
            #   locate_function, list_api_interfaces,
            #   list_wiki_pages, get_wiki_page, generate_wiki,
            #   build_graph, prepare_guidance
            # -----------------------------------------------------------------
        ]

        return defs

    def get_handler(self, name: str):
        handlers: dict[str, Any] = {
            "initialize_repository": self._handle_initialize_repository,
            "get_repository_info": self._handle_get_repository_info,
            "list_repositories": self._handle_list_repositories,
            "switch_repository": self._handle_switch_repository,
            "link_repository": self._handle_link_repository,
            "query_code_graph": self._handle_query_code_graph,
            "get_code_snippet": self._handle_get_code_snippet,
            "semantic_search": self._handle_semantic_search,
            "list_wiki_pages": self._handle_list_wiki_pages,
            "get_wiki_page": self._handle_get_wiki_page,
            "locate_function": self._handle_locate_function,
            "list_api_interfaces": self._handle_list_api_interfaces,
            "list_api_docs": self._handle_list_api_docs,
            "get_api_doc": self._handle_get_api_doc,
            "find_api": self._handle_find_api,
            "generate_wiki": self._handle_generate_wiki,
            "rebuild_embeddings": self._handle_rebuild_embeddings,
            "build_graph": self._handle_build_graph,
            "generate_api_docs": self._handle_generate_api_docs,
            "prepare_guidance": self._handle_prepare_guidance,
            "get_config": self._handle_get_config,
        }
        return handlers.get(name)

    # -------------------------------------------------------------------------
    # initialize_repository — runs the full pipeline in a thread pool
    # -------------------------------------------------------------------------

    async def _handle_initialize_repository(
        self,
        repo_path: str,
        rebuild: bool = True,
        wiki_mode: str = "comprehensive",
        backend: str = "kuzu",
        skip_wiki: bool = False,
        skip_embed: bool = False,
        _progress_cb: ProgressCb = None,
    ) -> dict[str, Any]:
        # Hot-reload config from .env / settings.json before running the
        # pipeline, so any changes made via --setup or manual edits take
        # effect without restarting the MCP server.
        from ..settings import reload_env
        changes = reload_env(workspace=self._workspace)
        # if changes.get("updated") or changes.get("removed"):
        #     logger.info(f"Config hot-reloaded before initialize: {changes}")

        repo = Path(repo_path).resolve()
        if not repo.exists():
            raise ToolError(f"Repository path does not exist: {repo}")

        loop = asyncio.get_event_loop()

        def sync_progress(msg: str, pct: float = 0.0) -> None:
            if _progress_cb is not None:
                asyncio.run_coroutine_threadsafe(_progress_cb(msg, pct), loop)

        # Force rebuild to ensure fresh graph data
        effective_rebuild = True

        result = await loop.run_in_executor(
            None,
            lambda: self._run_pipeline(
                repo, effective_rebuild, wiki_mode, sync_progress,
                backend=backend, skip_wiki=skip_wiki, skip_embed=skip_embed,
            ),
        )
        return result

    def _run_pipeline(
        self,
        repo_path: Path,
        rebuild: bool,
        wiki_mode: str,
        progress_cb: ProgressCb = None,
        backend: str = "kuzu",
        skip_wiki: bool = False,
        skip_embed: bool = False,
    ) -> dict[str, Any]:
        """Synchronous pipeline orchestrator: graph → api_docs → embeddings.

        Wiki generation is not part of the main pipeline — use the
        ``generate_wiki`` tool separately if needed.
        """
        artifact_dir = artifact_dir_for(self._workspace, repo_path)
        artifact_dir.mkdir(parents=True, exist_ok=True)

        db_path = artifact_dir / "graph.db"
        vectors_path = artifact_dir / "vectors.pkl"

        total_steps = 2 if skip_embed else 3

        def _step_progress(step: int, total: int, msg: str, pct: float) -> None:
            if progress_cb:
                progress_cb(f"[Step {step}/{total}] {msg}", pct)

        try:
            # Close existing MCP connection first so the builder can
            # open the database without lock contention.
            self.close()

            # Step 1: build graph (read-write)
            builder = build_graph(
                repo_path, db_path, rebuild, progress_cb=lambda msg, pct: _step_progress(1, total_steps, msg, pct),
                backend=backend,
            )
            # IMPORTANT: On Windows, Kuzu holds mandatory file locks via
            # the C++ Database object.  We must delete all references and
            # force GC before opening a new connection.
            import gc
            if hasattr(builder, '_ingestor'):
                builder._ingestor = None
            del builder
            gc.collect()

            # Step 2: generate API docs (needs read-only Kuzu access)
            ro_ingestor = KuzuIngestor(db_path, read_only=True)
            with ro_ingestor:
                generate_api_docs_step(
                    ro_ingestor, artifact_dir, rebuild,
                    progress_cb=lambda msg, pct: _step_progress(2, total_steps, msg, pct),
                    repo_path=repo_path,
                )
            # Kuzu no longer needed after this point

            # Step 2b: LLM description generation for undocumented functions
            generate_descriptions_step(
                artifact_dir=artifact_dir,
                repo_path=repo_path,
                progress_cb=lambda msg, pct: _step_progress(2, total_steps, msg, pct),
            )

            skipped = []

            if not skip_embed:
                # Step 3: build embeddings from API doc Markdown files (no Kuzu)
                build_vector_index(
                    None, repo_path, vectors_path, rebuild,
                    progress_cb=lambda msg, pct: _step_progress(3, total_steps, msg, pct),
                )
            else:
                skipped.append("embed")
                _step_progress(total_steps, total_steps, "Embedding skipped.", 100.0)

            #
            save_meta(artifact_dir, repo_path, 0)
            self._set_active(artifact_dir)
            self._load_services(artifact_dir)

            return {
                "status": "success",
                "repo_path": str(repo_path),
                "artifact_dir": str(artifact_dir),
                "skipped": skipped,
            }

        except Exception as exc:
            logger.exception("Pipeline failed")
            raise ToolError({"error": str(exc), "status": "error"}) from exc

    # -------------------------------------------------------------------------
    # get_repository_info (merged: active repo metadata + graph statistics)
    # -------------------------------------------------------------------------

    async def _handle_get_repository_info(self) -> dict[str, Any]:
        if self._active_artifact_dir is None:
            raise ToolError("No active repository. Call initialize_repository first.")

        meta_file = self._active_artifact_dir / "meta.json"
        meta = json.loads(meta_file.read_text(encoding="utf-8", errors="replace")) if meta_file.exists() else {}

        wiki_pages = []
        wiki_subdir = self._active_artifact_dir / "wiki" / "wiki"
        if wiki_subdir.exists():
            wiki_pages = [p.stem for p in sorted(wiki_subdir.glob("*.md"))]

        warnings: list[str] = []
        if self._semantic_service is None:
            warnings.append(
                "Semantic search unavailable — check embedding API keys "
                "(DASHSCOPE_API_KEY or EMBEDDING_API_KEY/OPENAI_API_KEY)."
            )
        if self._cypher_gen is None:
            warnings.append(
                "Cypher query unavailable — set LLM_API_KEY, OPENAI_API_KEY, "
                "or MOONSHOT_API_KEY to enable natural language queries."
            )

        result: dict[str, Any] = {
            "repo_path": str(self._active_repo_path),
            "artifact_dir": str(self._active_artifact_dir),
            "indexed_at": meta.get("indexed_at"),
            "semantic_search_available": self._semantic_service is not None,
            "cypher_query_available": self._cypher_gen is not None,
            "wiki_pages": wiki_pages,
        }
        if warnings:
            result["warnings"] = warnings

        # Merge graph statistics + language stats using temporary connection
        try:
            with self._temporary_ingestor() as ingestor:
                result["graph_stats"] = ingestor.get_statistics()

                # Language extraction stats
                try:
                    file_rows = ingestor.query(
                        "MATCH (f:File) RETURN f.path AS path"
                    )
                    from ..language_spec import get_language_for_extension
                    lang_counts: dict[str, int] = {}
                    total_files = 0
                    for row in file_rows:
                        raw = row.get("result", row)
                        fpath = raw[0] if isinstance(raw, (list, tuple)) else raw
                        if isinstance(fpath, str):
                            ext = Path(fpath).suffix.lower()
                            lang = get_language_for_extension(ext)
                            if lang:
                                lang_counts[lang.value] = lang_counts.get(lang.value, 0) + 1
                                total_files += 1
                    result["language_stats"] = {
                        "total_code_files": total_files,
                        "by_language": dict(sorted(lang_counts.items(), key=lambda x: -x[1])),
                    }
                except Exception:
                    pass  # language stats are optional
        except Exception as exc:
            result["graph_stats"] = {"error": str(exc)}

        # Supported languages
        from ..constants import LANGUAGE_METADATA, LanguageStatus
        result["supported_languages"] = {
            "full": [m.display_name for _, m in LANGUAGE_METADATA.items() if m.status == LanguageStatus.FULL],
            "in_development": [m.display_name for _, m in LANGUAGE_METADATA.items() if m.status == LanguageStatus.DEV],
        }

        return result

    # -------------------------------------------------------------------------
    # list_repositories / switch_repository
    # -------------------------------------------------------------------------

    async def _handle_list_repositories(self) -> dict[str, Any]:
        active_name = None
        active_file = self._workspace / "active.txt"
        if active_file.exists():
            active_name = active_file.read_text(encoding="utf-8", errors="replace").strip()

        repos: list[dict[str, Any]] = []
        for child in sorted(self._workspace.iterdir()):
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

        return {
            "workspace": str(self._workspace),
            "repository_count": len(repos),
            "repositories": repos,
            "hint": (
                "Use switch_repository with repo_name to change the active repo. "
                "Use initialize_repository or build_graph to index a new repo."
            ),
        }

    async def _handle_switch_repository(self, repo_name: str) -> dict[str, Any]:
        # Try exact match on artifact_dir name first
        target: Path | None = None
        for child in self._workspace.iterdir():
            if not child.is_dir():
                continue
            if child.name == repo_name:
                target = child
                break

        # Fallback: match by repo_name in meta.json
        if target is None:
            for child in sorted(self._workspace.iterdir()):
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
            raise ToolError({
                "error": f"Repository not found: {repo_name}",
                "hint": "Use list_repositories to see available repos.",
            })

        try:
            self._set_active(target)
            self._load_services(target)
        except Exception as exc:
            raise ToolError({
                "error": f"Failed to switch: {exc}",
                "repo_name": repo_name,
            }) from exc

        meta = json.loads((target / "meta.json").read_text(encoding="utf-8", errors="replace"))
        return {
            "status": "success",
            "active_repo": meta.get("repo_name", target.name),
            "repo_path": meta.get("repo_path"),
            "artifact_dir": str(target),
            "steps": meta.get("steps", {}),
        }

    # -------------------------------------------------------------------------
    # link_repository — symlink a new repo path to an existing index
    # -------------------------------------------------------------------------

    async def _handle_link_repository(
        self,
        repo_path: str,
        source_repo: str,
    ) -> dict[str, Any]:
        repo = Path(repo_path).resolve()
        if not repo.is_dir():
            raise ToolError(f"repo_path does not exist: {repo_path}")

        # Find source artifact dir (same logic as switch_repository)
        source_dir: Path | None = None
        for child in self._workspace.iterdir():
            if not child.is_dir():
                continue
            if child.name == source_repo:
                source_dir = child
                break
        if source_dir is None:
            for child in sorted(self._workspace.iterdir()):
                if not child.is_dir():
                    continue
                meta_file = child / "meta.json"
                if not meta_file.exists():
                    continue
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8", errors="replace"))
                except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                    continue
                if meta.get("repo_name") == source_repo:
                    source_dir = child
                    break

        if source_dir is None or not (source_dir / "meta.json").exists():
            raise ToolError({
                "error": f"Source repository not found: {source_repo}",
                "hint": "Use list_repositories to see available repos.",
            })

        # Create new artifact dir for this repo_path
        new_dir = artifact_dir_for(self._workspace, repo)
        if new_dir == source_dir:
            raise ToolError("repo_path resolves to the same artifact as source_repo.")

        if new_dir.exists():
            raise ToolError({
                "error": f"Artifact directory already exists: {new_dir.name}",
                "hint": "Use switch_repository to activate it, or delete it first.",
            })

        new_dir.mkdir(parents=True)

        # Symlink all data artifacts from source
        artifacts = ["graph.db", "api_docs", "vectors.pkl", "wiki"]
        linked = []
        for name in artifacts:
            src = source_dir / name
            if src.exists():
                dst = new_dir / name
                dst.symlink_to(src)
                linked.append(name)

        # Write meta.json (not symlinked — stores this repo's own path)
        source_meta = json.loads(
            (source_dir / "meta.json").read_text(encoding="utf-8", errors="replace")
        )
        new_meta = {
            **source_meta,
            "repo_path": str(repo),
            "repo_name": repo.name,
            "linked_to": str(source_dir),
            "linked_source_repo": source_meta.get("repo_name", source_dir.name),
        }
        (new_dir / "meta.json").write_text(
            json.dumps(new_meta, ensure_ascii=False, indent=2)
        )

        # Activate the new linked repo
        self._set_active(new_dir)
        self._load_services(new_dir)

        return {
            "status": "success",
            "repo_path": str(repo),
            "artifact_dir": str(new_dir),
            "linked_to": source_dir.name,
            "linked_artifacts": linked,
            "message": (
                f"Linked {repo.name} → {source_dir.name}. "
                f"Shared artifacts: {', '.join(linked)}. "
                f"Now active."
            ),
        }

    # -------------------------------------------------------------------------
    # query_code_graph
    # -------------------------------------------------------------------------

    async def _handle_query_code_graph(self, question: str) -> dict[str, Any]:
        self._require_active()

        if self._cypher_gen is None:
            raise ToolError(
                "LLM not configured. Set one of: LLM_API_KEY, OPENAI_API_KEY, "
                "or MOONSHOT_API_KEY in the MCP server environment."
            )

        try:
            cypher = self._cypher_gen.generate(question)
        except Exception as exc:
            raise ToolError({"error": f"Cypher generation failed: {exc}", "question": question}) from exc

        try:
            with self._temporary_ingestor() as ingestor:
                rows = ingestor.query(cypher)
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
            raise ToolError({
                "error": f"Query execution failed: {exc}",
                "question": question,
                "cypher": cypher,
            }) from exc

    # -------------------------------------------------------------------------
    # get_code_snippet
    # -------------------------------------------------------------------------

    async def _handle_get_code_snippet(self, qualified_name: str) -> dict[str, Any]:
        self._require_active()

        safe_qn = qualified_name.replace("'", "\\'")
        cypher = (
            f"MATCH (n) WHERE n.qualified_name = '{safe_qn}' "
            "RETURN n.qualified_name, n.name, n.source_code, n.path, n.start_line, n.end_line "
            "LIMIT 1"
        )

        try:
            with self._temporary_ingestor() as ingestor:
                rows = ingestor.query(cypher)
        except Exception as exc:
            raise ToolError({"error": f"Graph query failed: {exc}", "qualified_name": qualified_name}) from exc

        if not rows:
            raise ToolError({"error": "Not found", "qualified_name": qualified_name})

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
                from ..utils.encoding import read_source_file
                lines = read_source_file(fp).splitlines(keepends=True)
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
        self._require_active()

        if self._semantic_service is None:
            raise ToolError("Semantic search not available. Re-run initialize_repository to build embeddings.")

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
            raise ToolError({"error": f"Semantic search failed: {exc}", "query": query}) from exc

    # -------------------------------------------------------------------------
    # path safety helper (used by locate_function)
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

    # -------------------------------------------------------------------------
    # wiki tools
    # -------------------------------------------------------------------------

    def _wiki_dir(self) -> Path | None:
        if self._active_artifact_dir is None:
            return None
        return self._active_artifact_dir / "wiki"

    async def _handle_list_wiki_pages(self) -> dict[str, Any]:
        self._require_active()

        wiki_dir = self._wiki_dir()
        if wiki_dir is None or not wiki_dir.exists():
            raise ToolError("Wiki not generated yet. Run initialize_repository first.")

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
        self._require_active()

        wiki_dir = self._wiki_dir()
        if wiki_dir is None or not wiki_dir.exists():
            raise ToolError("Wiki not generated yet. Run initialize_repository first.")

        if page_id == "index":
            target = wiki_dir / "index.md"
        else:
            target = wiki_dir / "wiki" / f"{page_id}.md"

        if not target.exists():
            raise ToolError({"error": f"Wiki page not found: {page_id}", "page_id": page_id})

        content = target.read_text(encoding="utf-8", errors="ignore")
        return {
            "page_id": page_id,
            "file_path": str(target),
            "content": content,
        }

    # -------------------------------------------------------------------------
    # locate_function
    # -------------------------------------------------------------------------

    async def _handle_locate_function(
        self,
        file_path: str,
        function_name: str,
        line_number: int | None = None,
    ) -> dict[str, Any]:
        self._require_repo_path()
        if self._file_editor is None:
            raise ToolError("File editor not initialized.")

        target = self._safe_path(file_path)
        if target is None:
            raise ToolError({"error": "Path outside repository root.", "file_path": file_path})
        if not target.exists():
            raise ToolError({"error": "File not found.", "file_path": file_path})

        result = self._file_editor.locate_function(target, function_name, line_number)
        if result is None:
            raise ToolError({
                "error": f"Function '{function_name}' not found in {file_path}.",
                "file_path": file_path,
                "function_name": function_name,
            })
        return result

    # -------------------------------------------------------------------------
    # list_api_interfaces
    # -------------------------------------------------------------------------

    async def _handle_list_api_interfaces(
        self,
        module: str | None = None,
        visibility: str = "public",
        include_types: bool = True,
    ) -> dict[str, Any]:
        self._require_active()

        vis_filter = None if visibility == "all" else visibility

        try:
            with self._temporary_ingestor() as ingestor:
                rows = ingestor.fetch_module_apis(
                    module_qn=module,
                    visibility=vis_filter,
                )

            # Group function results by module
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
                        "entity_type": "function",
                    }
                else:
                    mod_name = raw.get("module", "unknown") if isinstance(raw, dict) else "unknown"
                    entry = raw if isinstance(raw, dict) else {"raw": raw}
                    if isinstance(entry, dict):
                        entry["entity_type"] = "function"

                if mod_name not in by_module:
                    by_module[mod_name] = []
                by_module[mod_name].append(entry)

            # Fetch type APIs (structs, unions, enums, typedefs) if requested
            type_count = 0
            if include_types and hasattr(ingestor, "fetch_module_type_apis"):
                type_rows = ingestor.fetch_module_type_apis(module_qn=module)
                for row in type_rows:
                    raw = row.get("result", row)
                    if isinstance(raw, (list, tuple)) and len(raw) >= 6:
                        mod_name = raw[0] or "unknown"
                        entry = {
                            "name": raw[1],
                            "kind": raw[2],
                            "signature": raw[3],
                            "members": raw[4] if len(raw) > 4 else None,
                            "start_line": raw[4 if len(raw) <= 5 else 5],
                            "end_line": raw[5 if len(raw) <= 6 else 6],
                            "entity_type": raw[2] or "type",
                        }
                    else:
                        mod_name = raw.get("module", "unknown") if isinstance(raw, dict) else "unknown"
                        entry = raw if isinstance(raw, dict) else {"raw": raw}

                    if mod_name not in by_module:
                        by_module[mod_name] = []
                    by_module[mod_name].append(entry)
                    type_count += 1

            total = sum(len(v) for v in by_module.values())
            return {
                "total_apis": total,
                "function_count": total - type_count,
                "type_count": type_count,
                "module_count": len(by_module),
                "visibility_filter": visibility,
                "modules": by_module,
            }

        except Exception as exc:
            raise ToolError(f"Failed to list API interfaces: {exc}") from exc

    # -------------------------------------------------------------------------
    # list_api_docs / get_api_doc  (hierarchical API documentation)
    # -------------------------------------------------------------------------

    def _api_docs_dir(self) -> Path | None:
        if self._active_artifact_dir is None:
            return None
        return self._active_artifact_dir / "api_docs"

    async def _handle_list_api_docs(
        self,
        module: str | None = None,
    ) -> dict[str, Any]:
        self._require_active()

        api_dir = self._api_docs_dir()
        if api_dir is None or not (api_dir / "index.md").exists():
            raise ToolError(
                "API docs not generated yet. "
                "Re-run initialize_repository to generate them."
            )

        if module:
            # L2: module detail page
            safe = module.replace("/", "_").replace("\\", "_")
            target = api_dir / "modules" / f"{safe}.md"
            if not target.exists():
                raise ToolError({
                    "error": f"Module doc not found: {module}",
                    "module": module,
                    "hint": "Use list_api_docs (no args) to see available modules.",
                })
            return {
                "level": "module",
                "module": module,
                "content": target.read_text(encoding="utf-8", errors="ignore"),
            }

        # L1: global index
        index_path = api_dir / "index.md"
        return {
            "level": "index",
            "content": index_path.read_text(encoding="utf-8", errors="ignore"),
        }

    async def _handle_get_api_doc(
        self,
        qualified_name: str,
    ) -> dict[str, Any]:
        self._require_active()

        api_dir = self._api_docs_dir()
        if api_dir is None or not (api_dir / "index.md").exists():
            raise ToolError(
                "API docs not generated yet. "
                "Re-run initialize_repository to generate them."
            )

        safe = qualified_name.replace("/", "_").replace("\\", "_")
        target = api_dir / "funcs" / f"{safe}.md"
        if not target.exists():
            raise ToolError({
                "error": f"API doc not found: {qualified_name}",
                "qualified_name": qualified_name,
                "hint": "Use list_api_docs to browse modules first.",
            })

        return {
            "qualified_name": qualified_name,
            "content": target.read_text(encoding="utf-8", errors="ignore"),
        }

    # -------------------------------------------------------------------------
    # find_api  (aggregated: semantic search + API doc lookup)
    # -------------------------------------------------------------------------

    async def _handle_find_api(
        self,
        query: str,
        top_k: int = 5,
    ) -> dict[str, Any]:
        self._require_active()

        if self._semantic_service is None:
            raise ToolError(
                "Semantic search not available. "
                "Re-run initialize_repository to build embeddings."
            )

        try:
            results = self._semantic_service.search(query, top_k=top_k)
        except Exception as exc:
            raise ToolError(
                {"error": f"Semantic search failed: {exc}", "query": query}
            ) from exc

        api_dir = self._api_docs_dir()
        funcs_dir = api_dir / "funcs" if api_dir else None
        has_api_docs = funcs_dir is not None and funcs_dir.exists()

        combined = []
        for r in results:
            entry: dict[str, Any] = {
                "qualified_name": r.qualified_name,
                "name": r.name,
                "type": r.type,
                "score": r.score,
                "file_path": r.file_path,
                "start_line": r.start_line,
                "end_line": r.end_line,
                "source_code": r.source_code,
                "api_doc": None,
            }

            if has_api_docs and r.qualified_name:
                safe_qn = r.qualified_name.replace("/", "_").replace("\\", "_")
                doc_file = funcs_dir / f"{safe_qn}.md"
                if doc_file.exists():
                    entry["api_doc"] = doc_file.read_text(
                        encoding="utf-8", errors="ignore"
                    )

            combined.append(entry)

        return {
            "query": query,
            "result_count": len(combined),
            "api_docs_available": has_api_docs,
            "results": combined,
        }

    # -------------------------------------------------------------------------
    # generate_wiki  (standalone wiki regeneration)
    # -------------------------------------------------------------------------

    async def _handle_generate_wiki(
        self,
        wiki_mode: str = "comprehensive",
        rebuild: bool = False,
        _progress_cb: ProgressCb = None,
    ) -> dict[str, Any]:
        self._require_active()

        if self._active_artifact_dir is None or self._active_repo_path is None:
            raise ToolError("No active repository. Call initialize_repository first.")

        artifact_dir = self._active_artifact_dir
        repo_path = self._active_repo_path
        vectors_path = artifact_dir / "vectors.pkl"

        if not vectors_path.exists():
            raise ToolError(
                "Embeddings not found. Run initialize_repository first "
                "to build the graph and embeddings."
            )

        loop = asyncio.get_event_loop()

        def sync_progress(msg: str, pct: float = 0.0) -> None:
            if _progress_cb is not None:
                asyncio.run_coroutine_threadsafe(_progress_cb(msg, pct), loop)

        result = await loop.run_in_executor(
            None,
            lambda: self._run_wiki_generation(
                repo_path, artifact_dir, vectors_path,
                wiki_mode, rebuild, sync_progress,
            ),
        )
        return result

    def _run_wiki_generation(
        self,
        repo_path: Path,
        artifact_dir: Path,
        vectors_path: Path,
        wiki_mode: str,
        rebuild: bool,
        progress_cb: ProgressCb = None,
    ) -> dict[str, Any]:
        """Synchronous wiki generation using existing graph + embeddings."""
        from ..examples.generate_wiki import MAX_PAGES_COMPREHENSIVE, MAX_PAGES_CONCISE

        comprehensive = wiki_mode != "concise"
        max_pages = MAX_PAGES_COMPREHENSIVE if comprehensive else MAX_PAGES_CONCISE
        wiki_dir = artifact_dir / "wiki"

        try:
            # Load existing embeddings
            with open(vectors_path, "rb") as fh:
                cache = pickle.load(fh)
            vector_store = cache["vector_store"]
            func_map: dict[int, dict] = cache["func_map"]
            from ..embeddings.qwen3_embedder import create_embedder
            embedder = create_embedder()

            # Delete structure cache if rebuild
            structure_cache = wiki_dir / f"{repo_path.name}_structure.pkl"
            if rebuild and structure_cache.exists():
                structure_cache.unlink()

            with self._temporary_ingestor() as ingestor:
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

            save_meta(artifact_dir, repo_path, page_count)

            return {
                "status": "success",
                "repo_path": str(repo_path),
                "wiki_index": str(index_path),
                "wiki_pages": page_count,
            }

        except Exception as exc:
            logger.exception("Wiki generation failed")
            raise ToolError({"error": str(exc), "status": "error"}) from exc

    # -------------------------------------------------------------------------
    # rebuild_embeddings  (standalone embedding rebuild)
    # -------------------------------------------------------------------------

    async def _handle_rebuild_embeddings(
        self,
        rebuild: bool = False,
        _progress_cb: ProgressCb = None,
    ) -> dict[str, Any]:
        self._require_active()

        if self._active_artifact_dir is None or self._active_repo_path is None:
            raise ToolError("No active repository. Call initialize_repository first.")

        artifact_dir = self._active_artifact_dir
        repo_path = self._active_repo_path
        db_path = artifact_dir / "graph.db"

        if not db_path.exists():
            raise ToolError(
                "Graph database not found. Run initialize_repository first "
                "to build the knowledge graph."
            )

        loop = asyncio.get_event_loop()

        def sync_progress(msg: str, pct: float = 0.0) -> None:
            if _progress_cb is not None:
                asyncio.run_coroutine_threadsafe(_progress_cb(msg, pct), loop)

        result = await loop.run_in_executor(
            None,
            lambda: self._run_rebuild_embeddings(
                repo_path, artifact_dir, rebuild, sync_progress,
            ),
        )

        # Reload services so semantic search picks up new embeddings
        self._load_services(artifact_dir)

        return result

    def _run_rebuild_embeddings(
        self,
        repo_path: Path,
        artifact_dir: Path,
        rebuild: bool,
        progress_cb: ProgressCb = None,
    ) -> dict[str, Any]:
        """Synchronous embedding rebuild using existing graph."""
        vectors_path = artifact_dir / "vectors.pkl"

        try:
            with self._temporary_ingestor() as ingestor:
                vector_store, embedder, func_map = build_vector_index(
                    ingestor, repo_path, vectors_path, rebuild, progress_cb
                )

            # Preserve existing wiki_page_count in meta
            meta_file = artifact_dir / "meta.json"
            page_count = 0
            if meta_file.exists():
                meta = json.loads(meta_file.read_text(encoding="utf-8", errors="replace"))
                page_count = meta.get("wiki_page_count", 0)

            save_meta(artifact_dir, repo_path, page_count)

            return {
                "status": "success",
                "repo_path": str(repo_path),
                "vectors_path": str(vectors_path),
                "embedding_count": len(vector_store),
            }

        except Exception as exc:
            logger.exception("Embedding rebuild failed")
            raise ToolError({"error": str(exc), "status": "error"}) from exc

    # -------------------------------------------------------------------------
    # build_graph  (standalone graph build)
    # -------------------------------------------------------------------------

    async def _handle_build_graph(
        self,
        repo_path: str,
        rebuild: bool = False,
        backend: str = "kuzu",
        _progress_cb: ProgressCb = None,
    ) -> dict[str, Any]:
        repo = Path(repo_path).resolve()
        if not repo.exists():
            raise ToolError(f"Repository path does not exist: {repo}")

        loop = asyncio.get_event_loop()

        def sync_progress(msg: str, pct: float = 0.0) -> None:
            if _progress_cb is not None:
                asyncio.run_coroutine_threadsafe(_progress_cb(msg, pct), loop)

        result = await loop.run_in_executor(
            None,
            lambda: self._run_build_graph(repo, rebuild, backend, sync_progress),
        )
        return result

    def _run_build_graph(
        self,
        repo_path: Path,
        rebuild: bool,
        backend: str,
        progress_cb: ProgressCb = None,
    ) -> dict[str, Any]:
        """Synchronous graph build. Runs in thread pool."""
        artifact_dir = artifact_dir_for(self._workspace, repo_path)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        db_path = artifact_dir / "graph.db"

        try:
            # Close existing MCP connection first so the builder can
            # open the database without lock contention.
            self.close()

            builder = build_graph(
                repo_path, db_path, rebuild, progress_cb, backend=backend,
            )

            # Release builder references so Windows file locks are freed
            import gc
            if hasattr(builder, '_ingestor'):
                builder._ingestor = None
            del builder
            gc.collect()

            # Build is done (write connection closed). Open read-only
            # to get statistics, then release for _load_services.
            ro_ingestor = KuzuIngestor(db_path, read_only=True)
            with ro_ingestor:
                stats = ro_ingestor.get_statistics()

            save_meta(artifact_dir, repo_path, 0)
            self._set_active(artifact_dir)
            self._load_services(artifact_dir)

            return {
                "status": "success",
                "repo_path": str(repo_path),
                "artifact_dir": str(artifact_dir),
                "node_count": stats.get("node_count", 0),
                "relationship_count": stats.get("relationship_count", 0),
            }

        except Exception as exc:
            logger.exception("Graph build failed")
            raise ToolError({"error": str(exc), "status": "error"}) from exc

    # -------------------------------------------------------------------------
    # generate_api_docs  (standalone API doc generation)
    # -------------------------------------------------------------------------

    async def _handle_generate_api_docs(
        self,
        mode: str = "full",
        _progress_cb: ProgressCb = None,
        # Legacy param kept for backward compatibility
        rebuild: bool = False,
    ) -> dict[str, Any]:
        self._require_active()

        if self._active_artifact_dir is None or self._active_repo_path is None:
            raise ToolError("No active repository. Call build_graph or initialize_repository first.")

        if mode not in ("full", "resume", "enhance"):
            raise ToolError(f"Invalid mode '{mode}'. Must be 'full', 'resume', or 'enhance'.")

        artifact_dir = self._active_artifact_dir
        repo_path = self._active_repo_path

        loop = asyncio.get_event_loop()

        def sync_progress(msg: str, pct: float = 0.0) -> None:
            if _progress_cb is not None:
                asyncio.run_coroutine_threadsafe(_progress_cb(msg, pct), loop)

        result = await loop.run_in_executor(
            None,
            lambda: self._run_generate_api_docs(artifact_dir, repo_path, mode, sync_progress),
        )
        return result

    def _run_generate_api_docs(
        self,
        artifact_dir: Path,
        repo_path: Path | None,
        mode: str = "full",
        progress_cb: ProgressCb = None,
    ) -> dict[str, Any]:
        """Synchronous API docs generation from existing graph.

        Args:
            mode: 'full' = regenerate docs from graph + LLM descriptions.
                  'resume' = only run LLM descriptions for remaining TODOs.
        """
        try:
            if mode == "full":
                with self._temporary_ingestor() as ingestor:
                    result = generate_api_docs_step(
                        ingestor, artifact_dir, True, progress_cb,
                        repo_path=repo_path,
                    )

                # LLM description generation for undocumented functions
                if repo_path is not None:
                    desc_stats = generate_descriptions_step(
                        artifact_dir=artifact_dir,
                        repo_path=repo_path,
                        progress_cb=progress_cb,
                    )
                    result["desc_stats"] = desc_stats

                return {
                    "status": result.get("status", "success"),
                    "artifact_dir": str(artifact_dir),
                    **{k: v for k, v in result.items() if k != "status"},
                }

            elif mode == "resume":
                funcs_dir = artifact_dir / "api_docs" / "funcs"
                if not funcs_dir.exists():
                    raise ToolError(
                        "No API docs found. Run with mode='full' first."
                    )

                todo_funcs = _collect_todo_funcs(funcs_dir)
                total_todo = len(todo_funcs)

                if total_todo == 0:
                    return {
                        "status": "success",
                        "message": "All functions already have LLM descriptions.",
                        "remaining_todo": 0,
                    }

                if progress_cb:
                    progress_cb(
                        f"Resuming LLM description generation: {total_todo} functions remaining",
                        0.0,
                    )

                desc_stats = generate_descriptions_step(
                    artifact_dir=artifact_dir,
                    repo_path=repo_path,
                    progress_cb=progress_cb,
                )

                remaining = len(_collect_todo_funcs(funcs_dir))

                return {
                    "status": "success" if not desc_stats.get("interrupted") else "interrupted",
                    "artifact_dir": str(artifact_dir),
                    "generated_count": desc_stats["generated_count"],
                    "error_count": desc_stats["error_count"],
                    "remaining_todo": remaining,
                    "message": (
                        f"Generated {desc_stats['generated_count']} descriptions. "
                        f"{remaining} functions still need descriptions."
                        + (" Run again with mode='resume' to continue."
                           if remaining > 0 else "")
                    ),
                }

            else:  # mode == "enhance"
                if not (artifact_dir / "api_docs" / "modules").exists():
                    raise ToolError(
                        "No API docs found. Run with mode='full' first."
                    )

                if progress_cb:
                    progress_cb("Generating module summaries and usage workflows...", 0.0)

                result = enhance_api_docs_step(
                    artifact_dir=artifact_dir,
                    progress_cb=progress_cb,
                )

                return {
                    "status": "success" if not result.get("interrupted") else "interrupted",
                    "artifact_dir": str(artifact_dir),
                    **result,
                    "message": (
                        f"Enhanced {result['generated_count']} modules with summaries and workflows."
                    ),
                }

        except ToolError:
            raise
        except Exception as exc:
            logger.exception("API docs generation failed")
            raise ToolError({"error": str(exc), "status": "error"}) from exc

    # -------------------------------------------------------------------------
    # prepare_guidance
    # -------------------------------------------------------------------------

    async def _handle_prepare_guidance(
        self,
        design_doc: str,
    ) -> dict[str, Any]:
        """Run the internal GuidanceAgent to produce a code generation guidance file."""
        self._require_active()

        llm = create_llm_backend()
        if not llm.available:
            raise ToolError(
                "LLM not configured. Set one of: LLM_API_KEY, OPENAI_API_KEY, "
                "or MOONSHOT_API_KEY to use prepare_guidance."
            )

        from ..guidance.agent import GuidanceAgent
        from ..guidance.toolset import MCPToolSet

        tool_set = MCPToolSet(
            semantic_service=self._semantic_service,
            cypher_gen=self._cypher_gen,
            ingestor_factory=self._temporary_ingestor,
            artifact_dir=self._active_artifact_dir,
        )
        agent = GuidanceAgent(toolset=tool_set, llm=llm)

        try:
            guidance = await agent.run(design_doc)
        except Exception as exc:
            logger.exception("Guidance generation failed")
            raise ToolError({"error": str(exc), "status": "error"}) from exc

        return {"guidance": guidance}

    # -------------------------------------------------------------------------
    # get_config — show current server configuration
    # -------------------------------------------------------------------------

    async def _handle_get_config(self) -> dict[str, Any]:
        """Return current MCP server configuration for debugging and verification."""
        import os as _os

        def _mask(val: str | None) -> str:
            """Mask API key for security: show first 4 and last 4 chars."""
            if not val:
                return "(not set)"
            if len(val) < 10:
                return "****"
            return val[:4] + "****" + val[-4:]

        # --- LLM configuration ---
        llm = create_llm_backend()
        llm_config: dict[str, Any] = {
            "available": llm.available,
            "model": llm.model,
            "base_url": llm.base_url,
            "api_key": _mask(llm.api_key),
        }

        # Detect which provider env var was used
        from ..rag.llm_backend import _PROVIDER_ENVS
        detected_provider = None
        for key_env, *_ in _PROVIDER_ENVS:
            if _os.environ.get(key_env):
                detected_provider = key_env
                break
        llm_config["detected_via"] = detected_provider or "(none)"

        # --- Embedding configuration ---
        embedding_config: dict[str, Any] = {}
        try:
            from ..embeddings.qwen3_embedder import create_embedder
            embedder = create_embedder()
            embedder_type = type(embedder).__name__

            embedding_config["provider"] = embedder_type
            if hasattr(embedder, "model"):
                embedding_config["model"] = embedder.model
            if hasattr(embedder, "base_url"):
                embedding_config["base_url"] = embedder.base_url
            if hasattr(embedder, "api_key"):
                embedding_config["api_key"] = _mask(embedder.api_key)
            embedding_config["dimension"] = embedder.get_embedding_dimension()
            embedding_config["available"] = True
        except Exception as exc:
            embedding_config["available"] = False
            embedding_config["error"] = str(exc)

        # Detect embedding provider source
        embed_provider = _os.environ.get("EMBEDDING_PROVIDER", "")
        if not embed_provider:
            if _os.environ.get("DASHSCOPE_API_KEY"):
                embed_provider = "qwen3 (auto-detected via DASHSCOPE_API_KEY)"
            elif _os.environ.get("EMBEDDING_API_KEY") or _os.environ.get("OPENAI_API_KEY"):
                embed_provider = "openai (auto-detected)"
            else:
                embed_provider = "(none)"
        embedding_config["detected_via"] = embed_provider

        # --- Workspace ---
        workspace_config: dict[str, Any] = {
            "path": str(self._workspace),
            "active_repo": str(self._active_repo_path) if self._active_repo_path else None,
            "active_artifact_dir": str(self._active_artifact_dir) if self._active_artifact_dir else None,
        }

        # --- Service status ---
        # Check if graph database exists (without opening persistent connection)
        has_graph = (
            self._active_artifact_dir is not None
            and (self._active_artifact_dir / "graph.db").exists()
        )
        services: dict[str, bool] = {
            "graph_database": has_graph,
            "cypher_query": self._cypher_gen is not None and has_graph,
            "semantic_search": self._semantic_service is not None,
            "file_editor": self._file_editor is not None,
        }

        # --- Environment variable overview ---
        env_keys = [
            "CGB_WORKSPACE",
            "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL",
            "LITELLM_API_KEY", "LITELLM_BASE_URL", "LITELLM_MODEL",
            "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
            "MOONSHOT_API_KEY", "MOONSHOT_MODEL",
            "DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL",
            "EMBEDDING_API_KEY", "EMBEDDING_BASE_URL", "EMBEDDING_MODEL",
            "EMBEDDING_PROVIDER",
        ]
        env_status: dict[str, str] = {}
        for key in env_keys:
            val = _os.environ.get(key)
            if val is None:
                env_status[key] = "(not set)"
            elif "KEY" in key:
                env_status[key] = _mask(val)
            else:
                env_status[key] = val

        return {
            "llm": llm_config,
            "embedding": embedding_config,
            "workspace": workspace_config,
            "services": services,
            "environment_variables": env_status,
        }

