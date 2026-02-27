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

load_dotenv()

from ..settings import load_settings  # noqa: E402

load_settings()

from loguru import logger
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .tools import MCPToolsRegistry, ToolError

SERVER_NAME = "code-graph-builder"


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
        _STATE_CHANGING_TOOLS = {"initialize_repository", "build_graph"}
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
