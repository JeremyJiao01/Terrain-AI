"""MCP server entry-point for Code Graph Builder.

Reads workspace path from environment and starts an MCP stdio server
that exposes code graph pipeline and query tools.

Environment variables:
    CGB_WORKSPACE    Workspace directory (default: ~/.code-graph-builder/)
                     Stores all indexed repos, graphs, embeddings, and wikis.

Optional (for LLM-backed tools):
    MOONSHOT_API_KEY   Moonshot / Kimi API key (required for query_code_graph)
    MOONSHOT_MODEL     Model name (default: kimi-k2.5)
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

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .tools import MCPToolsRegistry

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

        if name == "initialize_repository":
            session = server.request_context.session

            async def _progress_cb(msg: str) -> None:
                try:
                    await session.send_log_message(
                        level="info", data=msg, logger="code-graph-builder"
                    )
                except Exception:
                    pass

            kwargs["_progress_cb"] = _progress_cb

        result = await handler(**kwargs)

        if name == "initialize_repository" and isinstance(result, dict) and result.get("status") == "success":
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
