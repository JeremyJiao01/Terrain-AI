"""MCP server entry-point for Code Graph Builder.

Reads workspace path from environment and starts an MCP stdio server
that exposes code graph pipeline and query tools.

Environment variables:
    CGB_WORKSPACE    Workspace directory (default: ~/.code-graph-builder/)
                     Stores all indexed repos, graphs, embeddings, and wikis.

Optional (for LLM-backed tools — first match wins):
    LLM_API_KEY        Generic LLM API key (highest priority)
    LLM_BASE_URL       LLM API base URL
    LLM_MODEL          LLM model name
    OPENAI_API_KEY     OpenAI (or compatible) API key
    OPENAI_BASE_URL    OpenAI-compatible base URL
    OPENAI_MODEL       OpenAI model name
    MOONSHOT_API_KEY   Moonshot / Kimi API key (legacy)
    MOONSHOT_MODEL     Moonshot model name (default: kimi-k2.5)
    DASHSCOPE_API_KEY  DashScope API key (required for semantic_search embeddings)

Usage:
    CGB_WORKSPACE=~/.code-graph-builder python3 -m code_graph_builder.mcp.server
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# Force unbuffered stdout/stderr on Windows to prevent MCP stdio deadlock.
# When Python's stdout is connected to a pipe (MCP JSON-RPC transport),
# it defaults to full buffering, which can hold back responses indefinitely.
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(write_through=True)
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(write_through=True)

from dotenv import load_dotenv

# Load .env from workspace first (written by setup wizard), then local .env
_ws = Path(os.environ.get("CGB_WORKSPACE", Path.home() / ".code-graph-builder"))
load_dotenv(_ws.expanduser() / ".env", override=False)
load_dotenv(override=False)

from code_graph_builder.foundation.utils.settings import load_settings  # noqa: E402

load_settings()

from loguru import logger
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# --- Disable loguru default stderr sink for MCP stdio mode ---
# MCP uses stdin/stdout for JSON-RPC.  loguru's default stderr sink can
# fill the OS pipe buffer on Windows (where the MCP client may not consume
# stderr), blocking the entire Python process and hanging the agent.
logger.remove()  # Remove default stderr sink

# --- CGB_DEBUG file logging ---
_debug_enabled = os.environ.get("CGB_DEBUG", "").strip().lower() in ("1", "true", "yes")
_debug_log = _ws.expanduser() / "debug.log"
_debug_log.parent.mkdir(parents=True, exist_ok=True)
_log_format = "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}"
logger.add(
    str(_debug_log),
    level="DEBUG" if _debug_enabled else "WARNING",
    rotation="10 MB",
    retention="3 days",
    format=_log_format,
)

if _debug_enabled:
    logger.debug("CGB_DEBUG enabled, logging to {}", _debug_log)

from code_graph_builder.entrypoints.mcp.tools import MCPToolsRegistry, ToolError

SERVER_NAME = "code-graph-builder"

# ---------------------------------------------------------------------------
# Incremental sync state
# ---------------------------------------------------------------------------
_cached_head: str | None = None
"""Process-level cache of the last-seen HEAD. Avoids subprocess on every call."""

INCREMENTAL_FILE_LIMIT: int = 50
"""Fall back to full rebuild if more than this many files changed."""


def _cascade_api_docs(repo_path: Path, db_path: Path, artifact_dir: Path) -> None:
    """Regenerate API docs using a temporary read-only Kuzu connection.

    Opens the connection, runs queries, and closes immediately — no lingering
    file locks that would block subsequent operations on Windows.
    """
    import gc
    from code_graph_builder.foundation.services.kuzu_service import KuzuIngestor
    from code_graph_builder.entrypoints.mcp.pipeline import (
        generate_api_docs_step,
        _FUNC_DOC_QUERY,
        _TYPE_DOC_QUERY_CLASS,
        _TYPE_DOC_QUERY_TYPE,
        _CALLS_QUERY,
    )
    from code_graph_builder.domains.upper.apidoc.api_doc_generator import generate_api_docs

    # Use a read-only connection, closed immediately after queries
    with KuzuIngestor(db_path, read_only=True) as ingestor:
        func_rows = ingestor.query(_FUNC_DOC_QUERY)
        type_rows = ingestor.query(_TYPE_DOC_QUERY_CLASS) + ingestor.query(_TYPE_DOC_QUERY_TYPE)
        call_rows = ingestor.query(_CALLS_QUERY)
    # Connection is closed here — file locks released
    gc.collect()

    generate_api_docs(func_rows, type_rows, call_rows, artifact_dir, repo_path=repo_path)


async def _maybe_incremental_sync(registry: "MCPToolsRegistry") -> None:
    """Check for committed code changes and run incremental graph update if needed.

    Called before every tool invocation. The fast path (HEAD unchanged) costs ~0ms
    since it only compares two strings in memory.
    """
    global _cached_head

    state = registry.active_state
    if state is None:
        logger.debug("  sync: no active repo, skipping")
        return  # No active repo yet

    repo_path, artifact_dir = state
    db_path = artifact_dir / "graph.db"
    vectors_path = artifact_dir / "vectors.pkl"

    if not db_path.exists():
        logger.debug("  sync: graph.db not found, skipping")
        return  # Graph not built yet

    from code_graph_builder.foundation.services.git_service import GitChangeDetector

    logger.debug("  sync: calling get_current_head...")
    detector = GitChangeDetector()
    current_head = detector.get_current_head(repo_path)
    logger.debug("  sync: get_current_head returned: {}", current_head[:8] if current_head else None)

    if current_head is None:
        return  # Not a git repo

    if current_head == _cached_head:
        return  # Fast path: HEAD hasn't changed since last check

    # HEAD changed — read last indexed commit from meta.json
    import json as _json
    last_commit: str | None = None
    meta_file = artifact_dir / "meta.json"
    if meta_file.exists():
        try:
            last_commit = _json.loads(
                meta_file.read_text(encoding="utf-8", errors="replace")
            ).get("last_indexed_commit")
        except Exception:
            pass

    changed_files, new_head = detector.get_changed_files(repo_path, last_commit)

    if new_head is not None:
        _cached_head = new_head  # Update cache regardless of outcome below

    if changed_files is None:
        logger.warning(
            "last_indexed_commit {} not in git history — incremental sync skipped",
            (last_commit or "")[:8],
        )
        return

    if not changed_files:
        return  # No changes

    if len(changed_files) > INCREMENTAL_FILE_LIMIT:
        logger.info(
            "Too many changed files ({} > {}), skipping incremental sync",
            len(changed_files), INCREMENTAL_FILE_LIMIT,
        )
        return

    # Run incremental update
    from code_graph_builder.domains.core.graph.incremental_updater import IncrementalUpdater

    try:
        result = await asyncio.to_thread(
            IncrementalUpdater().run,
            changed_files,
            repo_path,
            db_path,
        )
        logger.info(
            "Incremental sync: {} files, {} callers in {:.0f}ms",
            result.files_reindexed, result.callers_reindexed, result.duration_ms,
        )

        # Cascade: regenerate API docs and vector index.
        # IMPORTANT: Each step creates its own temporary Kuzu connection
        # and releases it before the next step starts.  On Windows, Kuzu
        # holds mandatory file locks — overlapping connections cause deadlock.
        from code_graph_builder.entrypoints.mcp.pipeline import (
            generate_api_docs_step,
            build_vector_index,
        )

        if (artifact_dir / "api_docs").exists():
            try:
                await asyncio.to_thread(
                    _cascade_api_docs, repo_path, db_path, artifact_dir,
                )
            except Exception as e:
                logger.warning("API docs update failed: {}", e)
        if vectors_path.exists():
            try:
                await asyncio.to_thread(
                    build_vector_index, None, repo_path, vectors_path,
                    rebuild=True
                )
            except Exception as e:
                logger.warning("Vector index rebuild failed: {}", e)

        # Persist new last_indexed_commit
        if new_head:
            try:
                import json as _json2
                existing_meta = {}
                if meta_file.exists():
                    existing_meta = _json2.loads(
                        meta_file.read_text(encoding="utf-8", errors="replace")
                    )
                existing_meta["last_indexed_commit"] = new_head
                meta_file.write_text(_json2.dumps(existing_meta, ensure_ascii=False, indent=2))
            except Exception as e:
                logger.debug("Failed to update last_indexed_commit in meta.json: {}", e)

    except Exception as e:
        logger.warning("Incremental sync failed (will retry next call): {}", e)


async def main() -> None:
    import time as _time
    _t_main = _time.monotonic()
    logger.debug("=== main() entered ===")

    workspace = Path(
        os.environ.get("CGB_WORKSPACE", Path.home() / ".code-graph-builder")
    ).expanduser().resolve()
    logger.debug("  workspace: {}", workspace)

    logger.debug("  creating MCPToolsRegistry (includes _try_auto_load)...")
    registry = MCPToolsRegistry(workspace=workspace)
    logger.debug("  MCPToolsRegistry created ({:.0f}ms)", (_time.monotonic() - _t_main) * 1000)

    server = Server(
        SERVER_NAME,
        instructions=(
            "You are powered by Code Graph Builder -- an MCP server that turns "
            "any codebase into a searchable knowledge graph with API documentation "
            "and semantic search.\n\n"
            "## What you can do for the user\n"
            "- **Instant code understanding**: index a repo once, then answer any "
            "question about its architecture, functions, and call chains.\n"
            "- **Semantic search**: find APIs by vague description, not exact names "
            '(e.g. "how does PWM duty cycle get updated?").\n'
            "- **Call graph analysis**: trace callers, callees, and full call chains "
            "to understand impact and data flow.\n"
            "- **Auto-generated API docs**: every function gets a rich doc page with "
            "signature, call tree, caller list, and source code.\n\n"
            "## Recommended workflow\n"
            "1. `initialize_repository` -- index the codebase (graph + docs + embeddings).\n"
            "2. `find_api` -- ALWAYS start here when the user asks about code. "
            "It combines semantic search with API docs.\n"
            "3. `get_api_doc` -- deep-dive into a specific function.\n"
            "4. `find_callers` / `trace_call_chain` -- understand who calls what.\n\n"
            "Proactively tell the user what you found and suggest next steps. "
            "Be an enthusiastic guide to their codebase."
        ),
    )

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name=t.name,
                description=t.description,
                inputSchema=t.input_schema,
            )
            for t in registry.tools()
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        import time as _time
        _t0 = _time.monotonic()
        logger.debug("┌── call_tool START: {} args={}", name, arguments)

        # Check for committed code changes and sync incrementally if needed.
        # Skip for initialize_repository (full rebuild) and other write-heavy
        # tools to avoid Kuzu lock contention on Windows.
        _SKIP_SYNC_TOOLS = {"initialize_repository", "build_graph", "rebuild_embeddings"}
        if name not in _SKIP_SYNC_TOOLS:
            logger.debug("│  incremental_sync BEGIN")
            _ts = _time.monotonic()
            try:
                await asyncio.wait_for(_maybe_incremental_sync(registry), timeout=30)
            except asyncio.TimeoutError:
                logger.warning("Incremental sync timed out (30s), skipping")
            except Exception as exc:
                logger.warning("Incremental sync failed: {}", exc)
            logger.debug("│  incremental_sync END ({:.0f}ms)", (_time.monotonic() - _ts) * 1000)
        else:
            logger.debug("│  incremental_sync SKIPPED (tool in skip list)")

        handler = registry.get_handler(name)
        if handler is None:
            raise ValueError(f"Unknown tool: {name}")

        kwargs = dict(arguments or {})

        # Tools that run long and support progress callbacks
        _PROGRESS_TOOLS = {
            "initialize_repository",
            "build_graph",
            "generate_api_docs",
            "rebuild_embeddings",
            "generate_wiki",
        }

        if name in _PROGRESS_TOOLS:
            session = server.request_context.session

            # Extract progress token from request metadata (if client supports it)
            progress_token = None
            meta = getattr(server.request_context, "meta", None)
            if meta is not None:
                progress_token = getattr(meta, "progressToken", None)

            async def _progress_cb(msg: str, pct: float = 0.0) -> None:
                try:
                    await session.send_log_message(
                        level="info",
                        data=f"[{pct:.0f}%] {msg}" if pct > 0 else msg,
                        logger="code-graph-builder",
                    )
                except Exception:
                    pass

                # Send MCP progress notification (rendered as progress bar
                # by clients that support it, e.g. Claude Code)
                if progress_token is not None:
                    try:
                        await session.send_progress_notification(
                            progress_token=progress_token,
                            progress=pct,
                            total=100.0,
                            message=msg,
                        )
                    except Exception:
                        pass

            kwargs["_progress_cb"] = _progress_cb

        logger.debug("│  handler BEGIN: {}", name)
        _th = _time.monotonic()
        try:
            result = await handler(**kwargs)
        except ToolError:
            logger.debug("│  handler RAISED ToolError ({:.0f}ms)", (_time.monotonic() - _th) * 1000)
            # ToolError already carries structured JSON in str(exc).
            # Re-raise so the MCP framework returns isError=True to the agent.
            raise
        except Exception as exc:
            logger.debug("│  handler RAISED Exception ({:.0f}ms)", (_time.monotonic() - _th) * 1000)
            # Unexpected exception — wrap into ToolError for consistent handling.
            logger.exception(f"Tool '{name}' raised an unhandled exception")
            raise ToolError({"error": str(exc), "tool": name}) from exc
        logger.debug("│  handler END ({:.0f}ms)", (_time.monotonic() - _th) * 1000)

        # Notify client that tool list may have changed after state-changing ops
        _STATE_CHANGING_TOOLS = {"initialize_repository", "build_graph", "switch_repository"}
        if name in _STATE_CHANGING_TOOLS and isinstance(result, dict) and result.get("status") == "success":
            try:
                await server.request_context.session.send_tools_list_changed()
            except Exception:
                pass

        if isinstance(result, (dict, list)):
            text = json.dumps(result, ensure_ascii=False, indent=2, default=str)
        else:
            text = str(result)
        logger.debug("└── call_tool DONE: {} total={:.0f}ms", name, (_time.monotonic() - _t0) * 1000)
        return [TextContent(type="text", text=text)]

    logger.debug("=== MCP server starting stdio transport ===")
    try:
        async with stdio_server() as (read_stream, write_stream):
            logger.debug("=== stdio_server ready, entering server.run() ===")
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        logger.debug("=== MCP server shutting down ===")
        registry.close()


if __name__ == "__main__":
    asyncio.run(main())
