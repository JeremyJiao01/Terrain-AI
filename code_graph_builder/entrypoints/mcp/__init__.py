"""MCP server for Code Graph Builder.

Exposes graph query, semantic search, and code retrieval tools
via the Model Context Protocol (MCP) stdio transport.

Usage:
    CGB_WORKSPACE=~/.code-graph-builder python3 -m code_graph_builder.mcp.server
"""

from __future__ import annotations


def main() -> None:
    import asyncio

    from .server import main as _main

    asyncio.run(_main())


__all__ = ["main"]
