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
from pathlib import Path

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

# --- CGB_DEBUG file logging ---
if os.environ.get("CGB_DEBUG", "").strip().lower() in ("1", "true", "yes"):
    _debug_log = _ws.expanduser() / "debug.log"
    _debug_log.parent.mkdir(parents=True, exist_ok=True)
    logger.add(
        str(_debug_log),
        level="DEBUG",
        rotation="10 MB",
        retention="3 days",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
    )
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


async def _maybe_incremental_sync(registry: "MCPToolsRegistry") -> None:
    """Check for committed code changes and run incremental graph update if needed.

    Called before every tool invocation. The fast path (HEAD unchanged) costs ~0ms
    since it only compares two strings in memory.
    """
    global _cached_head

    state = registry.active_state
    if state is None:
        return  # No active repo yet

    repo_path, artifact_dir = state
    db_path = artifact_dir / "graph.db"
    vectors_path = artifact_dir / "vectors.pkl"

    if not db_path.exists():
        return  # Graph not built yet

    from code_graph_builder.foundation.services.git_service import GitChangeDetector

    detector = GitChangeDetector()
    current_head = detector.get_current_head(repo_path)

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
        result = IncrementalUpdater().run(
            changed_files=changed_files,
            repo_path=repo_path,
            db_path=db_path,
            artifact_dir=artifact_dir,
            vectors_path=vectors_path,
        )
        logger.info(
            "Incremental sync: {} files, {} callers in {:.0f}ms",
            result.files_reindexed, result.callers_reindexed, result.duration_ms,
        )

        # Persist new last_indexed_commit
        if new_head and meta_file.exists():
            try:
                existing = _json.loads(meta_file.read_text(encoding="utf-8", errors="replace"))
                existing["last_indexed_commit"] = new_head
                meta_file.write_text(_json.dumps(existing, ensure_ascii=False, indent=2))
            except Exception as e:
                logger.debug("Failed to update last_indexed_commit in meta.json: {}", e)

    except Exception as e:
        logger.warning("Incremental sync failed (will retry next call): {}", e)


async def main() -> None:
    workspace = Path(
        os.environ.get("CGB_WORKSPACE", Path.home() / ".code-graph-builder")
    ).expanduser().resolve()

    registry = MCPToolsRegistry(workspace=workspace)

    server = Server(SERVER_NAME)

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
        # Check for committed code changes and sync incrementally if needed
        await _maybe_incremental_sync(registry)
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

        try:
            result = await handler(**kwargs)
        except ToolError:
            # ToolError already carries structured JSON in str(exc).
            # Re-raise so the MCP framework returns isError=True to the agent.
            raise
        except Exception as exc:
            # Unexpected exception — wrap into ToolError for consistent handling.
            logger.exception(f"Tool '{name}' raised an unhandled exception")
            raise ToolError({"error": str(exc), "tool": name}) from exc

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
        return [TextContent(type="text", text=text)]

    try:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )
    finally:
        registry.close()


if __name__ == "__main__":
    asyncio.run(main())
