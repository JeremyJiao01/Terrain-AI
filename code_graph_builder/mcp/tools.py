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

        llm = create_llm_backend()
        cypher_gen: CypherGenerator | None = None
        if llm.available:
            cypher_gen = CypherGenerator(llm)
        else:
            logger.warning("LLM not configured — query_code_graph will be unavailable")

        semantic_service: SemanticSearchService | None = None
        if vectors_path.exists():
            try:
                vector_store = _load_vector_store(vectors_path)
                from ..embeddings.qwen3_embedder import create_embedder
                embedder = create_embedder(batch_size=10)
                semantic_service = SemanticSearchService(
                    embedder=embedder,
                    vector_store=vector_store,
                    graph_service=ingestor,
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

    def _require_active(self) -> None:
        """Raise :class:`ToolError` when no repository has been indexed."""
        if self._ingestor is None:
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
                        "backend": {
                            "type": "string",
                            "enum": ["kuzu", "memgraph", "memory"],
                            "description": (
                                "Graph database backend. Default: kuzu (embedded, no Docker)."
                            ),
                        },
                        "skip_wiki": {
                            "type": "boolean",
                            "description": (
                                "Skip wiki generation (graph + embeddings only). "
                                "Use generate_wiki later to create wiki separately. "
                                "Default: false."
                            ),
                        },
                        "skip_embed": {
                            "type": "boolean",
                            "description": (
                                "Skip embeddings and wiki (graph only, fastest). "
                                "Semantic search will be unavailable. Default: false."
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
                name="list_api_interfaces",
                description=(
                    "List public API interfaces for a module or the entire project. "
                    "Returns function signatures, struct/union/enum definitions with "
                    "members, typedef declarations, and macro definitions. Particularly "
                    "useful for C codebases to understand module boundaries."
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
                            "enum": ["public", "static", "extern", "all"],
                            "description": (
                                "Filter by visibility: 'public' (default) for functions "
                                "declared in headers, 'extern' for non-static functions "
                                "not in headers, 'static' for file-local functions, "
                                "'all' for everything."
                            ),
                        },
                        "include_types": {
                            "type": "boolean",
                            "description": (
                                "Include struct/union/enum definitions and typedefs. "
                                "Defaults to true."
                            ),
                        },
                    },
                    "required": [],
                },
            ),
            ToolDefinition(
                name="list_api_docs",
                description=(
                    "List available API documentation. Returns the L1 module index "
                    "or the L2 module detail page listing all interfaces in that module. "
                    "Use this for efficient hierarchical browsing: first list modules, "
                    "then drill into a specific module."
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
                    "Read the detailed API documentation for a specific function. "
                    "Includes signature, docstring, and full call graph "
                    "(who calls it and what it calls)."
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
            ToolDefinition(
                name="find_api",
                description=(
                    "Find relevant APIs by natural language description. "
                    "Combines semantic vector search with API documentation lookup "
                    "in a single call — returns matching functions along with their "
                    "signatures, docstrings, and call graphs. "
                    "Equivalent to running semantic_search + get_api_doc for each result."
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
                name="generate_wiki",
                description=(
                    "Regenerate the wiki using existing graph and embeddings. "
                    "Use this when wiki generation failed or you want to regenerate "
                    "with different settings, without rebuilding the graph or embeddings. "
                    "Requires initialize_repository to have been run at least once."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "wiki_mode": {
                            "type": "string",
                            "enum": ["comprehensive", "concise"],
                            "description": (
                                "comprehensive: 8-10 wiki pages (default). "
                                "concise: 4-5 wiki pages."
                            ),
                        },
                        "rebuild": {
                            "type": "boolean",
                            "description": (
                                "If true, force-regenerate wiki structure and all pages "
                                "even if cached. Default: false (regenerates pages only)."
                            ),
                        },
                    },
                    "required": [],
                },
            ),
            ToolDefinition(
                name="rebuild_embeddings",
                description=(
                    "Rebuild vector embeddings using the existing knowledge graph. "
                    "Use this when embeddings are missing, corrupted, or when you "
                    "want to re-embed after changing the embedding model/config. "
                    "Requires a graph to have been built first "
                    "(via initialize_repository or build_graph)."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "rebuild": {
                            "type": "boolean",
                            "description": (
                                "If true, force-rebuild embeddings even if cached. "
                                "Default: false (reuses cache if available)."
                            ),
                        },
                    },
                    "required": [],
                },
            ),
            ToolDefinition(
                name="build_graph",
                description=(
                    "Build the code knowledge graph from source code using "
                    "Tree-sitter AST parsing. This is step 1 of the pipeline. "
                    "After building, use generate_api_docs, rebuild_embeddings, "
                    "and generate_wiki as separate steps."
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
                                "If true, force-rebuild graph even if cached. "
                                "Default: false."
                            ),
                        },
                        "backend": {
                            "type": "string",
                            "enum": ["kuzu", "memgraph", "memory"],
                            "description": (
                                "Graph database backend. Default: kuzu (embedded)."
                            ),
                        },
                    },
                    "required": ["repo_path"],
                },
            ),
            ToolDefinition(
                name="generate_api_docs",
                description=(
                    "Generate hierarchical API documentation from the existing "
                    "knowledge graph. Produces L1 module index, L2 per-module pages, "
                    "and L3 per-function detail pages with call graphs. "
                    "Requires only a graph database — no embeddings or LLM needed. "
                    "This is step 2 of the pipeline."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "rebuild": {
                            "type": "boolean",
                            "description": (
                                "If true, force-regenerate API docs even if cached. "
                                "Default: false."
                            ),
                        },
                    },
                    "required": [],
                },
            ),
            ToolDefinition(
                name="prepare_guidance",
                description=(
                    "Analyze a design document and generate a code generation "
                    "guidance file. An internal LLM agent searches the codebase "
                    "for relevant APIs, similar implementations, and dependency "
                    "relationships, then synthesises a structured guidance "
                    "Markdown document for downstream code generation."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "design_doc": {
                            "type": "string",
                            "description": (
                                "The design document content (Markdown). "
                                "The agent reads this, researches the codebase, "
                                "and produces a guidance file."
                            ),
                        },
                    },
                    "required": ["design_doc"],
                },
            ),
        ]

        return defs

    def get_handler(self, name: str):
        handlers: dict[str, Any] = {
            "initialize_repository": self._handle_initialize_repository,
            "get_repository_info": self._handle_get_repository_info,
            "list_repositories": self._handle_list_repositories,
            "switch_repository": self._handle_switch_repository,
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
        backend: str = "kuzu",
        skip_wiki: bool = False,
        skip_embed: bool = False,
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
            lambda: self._run_pipeline(
                repo, rebuild, wiki_mode, sync_progress,
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
        """Synchronous pipeline orchestrator: graph → api_docs → embeddings → wiki.

        Each step calls the same standalone pipeline functions that the
        individual tool handlers use, so behaviour is identical whether
        invoked from ``initialize_repository`` or step-by-step.
        """
        from ..examples.generate_wiki import MAX_PAGES_COMPREHENSIVE, MAX_PAGES_CONCISE

        if skip_embed:
            skip_wiki = True  # wiki requires embeddings

        artifact_dir = artifact_dir_for(self._workspace, repo_path)
        artifact_dir.mkdir(parents=True, exist_ok=True)

        db_path = artifact_dir / "graph.db"
        vectors_path = artifact_dir / "vectors.pkl"
        wiki_dir = artifact_dir / "wiki"
        comprehensive = wiki_mode != "concise"
        max_pages = MAX_PAGES_COMPREHENSIVE if comprehensive else MAX_PAGES_CONCISE

        def _step_progress(step: int, total: int, msg: str, pct: float) -> None:
            if progress_cb:
                progress_cb(f"[Step {step}/{total}] {msg}", pct)

        total_steps = 4
        if skip_embed:
            total_steps = 2  # graph + api_docs only
        elif skip_wiki:
            total_steps = 3  # graph + api_docs + embeddings

        try:
            # Step 1: build graph
            builder = build_graph(
                repo_path, db_path, rebuild, progress_cb=lambda msg, pct: _step_progress(1, total_steps, msg, pct),
                backend=backend,
            )

            # Step 2: generate API docs
            generate_api_docs_step(
                builder, artifact_dir, rebuild,
                progress_cb=lambda msg, pct: _step_progress(2, total_steps, msg, pct),
            )

            # Step 2b: LLM description generation for undocumented functions
            generate_descriptions_step(
                artifact_dir=artifact_dir,
                repo_path=repo_path,
                progress_cb=lambda msg, pct: _step_progress(2, total_steps, msg, pct),
            )

            page_count = 0
            index_path = wiki_dir / "index.md"
            skipped = []

            if not skip_embed:
                # Step 3: build embeddings
                vector_store, embedder, func_map = build_vector_index(
                    builder, repo_path, vectors_path, rebuild,
                    progress_cb=lambda msg, pct: _step_progress(3, total_steps, msg, pct),
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
                        progress_cb=lambda msg, pct: _step_progress(4, total_steps, msg, pct),
                    )
                else:
                    skipped.append("wiki")
                    _step_progress(4, total_steps, "Wiki generation skipped.", 100.0)
            else:
                skipped.extend(["embed", "wiki"])
                _step_progress(3, total_steps, "Embedding skipped.", 40.0)
                _step_progress(4, total_steps, "Wiki skipped (requires embeddings).", 100.0)

            save_meta(artifact_dir, repo_path, page_count)
            self._set_active(artifact_dir)
            self._load_services(artifact_dir)

            return {
                "status": "success",
                "repo_path": str(repo_path),
                "artifact_dir": str(artifact_dir),
                "wiki_index": str(index_path),
                "wiki_pages": page_count,
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
        meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}

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

        # Merge graph statistics + language stats
        if self._ingestor is not None:
            try:
                result["graph_stats"] = self._ingestor.get_statistics()
            except Exception as exc:
                result["graph_stats"] = {"error": str(exc)}

            # Language extraction stats
            try:
                file_rows = self._ingestor.query(
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
            active_name = active_file.read_text(encoding="utf-8").strip()

        repos: list[dict[str, Any]] = []
        for child in sorted(self._workspace.iterdir()):
            if not child.is_dir():
                continue
            meta_file = child / "meta.json"
            if not meta_file.exists():
                continue
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
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
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
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

        meta = json.loads((target / "meta.json").read_text(encoding="utf-8"))
        return {
            "status": "success",
            "active_repo": meta.get("repo_name", target.name),
            "repo_path": meta.get("repo_path"),
            "artifact_dir": str(target),
            "steps": meta.get("steps", {}),
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
        assert self._ingestor is not None

        try:
            cypher = self._cypher_gen.generate(question)
        except Exception as exc:
            raise ToolError({"error": f"Cypher generation failed: {exc}", "question": question}) from exc

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

        assert self._ingestor is not None

        vis_filter = None if visibility == "all" else visibility

        try:
            rows = self._ingestor.fetch_module_apis(
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
            if include_types and hasattr(self._ingestor, "fetch_module_type_apis"):
                type_rows = self._ingestor.fetch_module_type_apis(module_qn=module)
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

            assert self._ingestor is not None

            index_path, page_count = run_wiki_generation(
                builder=self._ingestor,
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
            assert self._ingestor is not None

            vector_store, embedder, func_map = build_vector_index(
                self._ingestor, repo_path, vectors_path, rebuild, progress_cb
            )

            # Preserve existing wiki_page_count in meta
            meta_file = artifact_dir / "meta.json"
            page_count = 0
            if meta_file.exists():
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
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
            builder = build_graph(
                repo_path, db_path, rebuild, progress_cb, backend=backend,
            )

            stats = builder.get_statistics()
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
        rebuild: bool = False,
        _progress_cb: ProgressCb = None,
    ) -> dict[str, Any]:
        self._require_active()

        if self._active_artifact_dir is None or self._active_repo_path is None:
            raise ToolError("No active repository. Call build_graph or initialize_repository first.")

        artifact_dir = self._active_artifact_dir

        loop = asyncio.get_event_loop()

        def sync_progress(msg: str, pct: float = 0.0) -> None:
            if _progress_cb is not None:
                asyncio.run_coroutine_threadsafe(_progress_cb(msg, pct), loop)

        repo_path = self._active_repo_path
        result = await loop.run_in_executor(
            None,
            lambda: self._run_generate_api_docs(artifact_dir, repo_path, rebuild, sync_progress),
        )
        return result

    def _run_generate_api_docs(
        self,
        artifact_dir: Path,
        repo_path: Path | None,
        rebuild: bool,
        progress_cb: ProgressCb = None,
    ) -> dict[str, Any]:
        """Synchronous API docs generation from existing graph."""
        try:
            assert self._ingestor is not None

            result = generate_api_docs_step(
                self._ingestor, artifact_dir, rebuild, progress_cb,
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
            ingestor=self._ingestor,
            artifact_dir=self._active_artifact_dir,
        )
        agent = GuidanceAgent(toolset=tool_set, llm=llm)

        try:
            guidance = await agent.run(design_doc)
        except Exception as exc:
            logger.exception("Guidance generation failed")
            raise ToolError({"error": str(exc), "status": "error"}) from exc

        return {"guidance": guidance}
