"""ToolSet abstraction and MCPToolSet adapter for GuidanceAgent.

The ``ToolSet`` protocol defines the contract between the agent and its tools.
``MCPToolSet`` implements this contract by wrapping the existing MCP services
(semantic search, Cypher generation, API doc lookup) without going through
the MCP protocol layer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from loguru import logger


class ToolSet(Protocol):
    """Abstract tool interface that GuidanceAgent depends on."""

    def tool_specs(self) -> list[dict[str, Any]]:
        """Return OpenAI function-calling format tool definitions."""
        ...

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        """Execute a tool by name with the given arguments.

        Returns a JSON-encoded string suitable for inclusion in the
        LLM conversation as a tool result message.
        """
        ...


# ---------------------------------------------------------------------------
# Tool schema definitions (OpenAI function-calling format)
# ---------------------------------------------------------------------------

_SEMANTIC_SEARCH_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "semantic_search",
        "description": (
            "Search the codebase for functions, classes, or methods that are "
            "semantically similar to the query. Returns source code snippets "
            "with similarity scores."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language description of what to search for",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (default: 5)",
                },
            },
            "required": ["query"],
        },
    },
}

_FIND_API_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "find_api",
        "description": (
            "Find existing API interfaces by semantic search and return their "
            "detailed documentation including function signatures, parameters, "
            "call trees, and source code."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language description of the API to find",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (default: 5)",
                },
            },
            "required": ["query"],
        },
    },
}

_QUERY_CODE_GRAPH_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "query_code_graph",
        "description": (
            "Query the code knowledge graph using natural language. "
            "Useful for finding call relationships, module dependencies, "
            "class hierarchies, and structural patterns in the codebase."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Natural language question about code structure",
                },
            },
            "required": ["question"],
        },
    },
}

_ALL_SPECS = [_SEMANTIC_SEARCH_SPEC, _FIND_API_SPEC, _QUERY_CODE_GRAPH_SPEC]

# Maximum characters per tool result to avoid blowing up the context window.
_DEFAULT_MAX_RESULT_CHARS = 4000


# ---------------------------------------------------------------------------
# MCPToolSet — adapter that wraps existing Python services
# ---------------------------------------------------------------------------


class MCPToolSet:
    """Adapter that exposes existing MCP services as a :class:`ToolSet`.

    This calls the underlying Python service objects directly — it does NOT
    go through the MCP protocol.
    """

    def __init__(
        self,
        semantic_service: Any | None,
        cypher_gen: Any | None,
        ingestor_factory: Any | None,
        artifact_dir: Path | None,
        max_result_chars: int = _DEFAULT_MAX_RESULT_CHARS,
    ) -> None:
        self._semantic_service = semantic_service
        self._cypher_gen = cypher_gen
        self._ingestor_factory = ingestor_factory
        self._artifact_dir = artifact_dir
        self._max_chars = max_result_chars

        self._dispatch = {
            "semantic_search": self._call_semantic_search,
            "find_api": self._call_find_api,
            "query_code_graph": self._call_query_code_graph,
        }

    def tool_specs(self) -> list[dict[str, Any]]:
        """Return tool definitions, excluding tools whose services are unavailable."""
        specs: list[dict[str, Any]] = []
        if self._semantic_service is not None:
            specs.append(_SEMANTIC_SEARCH_SPEC)
            specs.append(_FIND_API_SPEC)
        if self._cypher_gen is not None and self._ingestor_factory is not None:
            specs.append(_QUERY_CODE_GRAPH_SPEC)
        return specs

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        handler = self._dispatch.get(name)
        if handler is None:
            return json.dumps({"error": f"Unknown tool: {name}"}, ensure_ascii=False)

        try:
            result = await handler(**arguments)
        except Exception as exc:
            logger.warning(f"Tool '{name}' failed: {exc}")
            return json.dumps(
                {"error": f"Tool execution failed: {exc}"},
                ensure_ascii=False,
                default=str,
            )

        text = json.dumps(result, ensure_ascii=False, default=str)
        if len(text) > self._max_chars:
            text = text[: self._max_chars] + "\n... (truncated)"
        return text

    # -- Tool implementations ------------------------------------------------

    async def _call_semantic_search(
        self, query: str, top_k: int = 5, **_: Any
    ) -> dict[str, Any]:
        assert self._semantic_service is not None
        results = self._semantic_service.search(query, top_k=top_k)
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

    async def _call_find_api(
        self, query: str, top_k: int = 5, **_: Any
    ) -> dict[str, Any]:
        assert self._semantic_service is not None
        results = self._semantic_service.search(query, top_k=top_k)

        api_dir = self._artifact_dir / "api_docs" if self._artifact_dir else None
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
                "source_code": r.source_code,
                "api_doc": None,
            }
            if has_api_docs and r.qualified_name:
                safe_qn = r.qualified_name.replace("/", "_").replace("\\", "_")
                doc_file = funcs_dir / f"{safe_qn}.md"  # type: ignore[union-attr]
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

    async def _call_query_code_graph(
        self, question: str, **_: Any
    ) -> dict[str, Any]:
        assert self._cypher_gen is not None
        assert self._ingestor_factory is not None

        cypher = self._cypher_gen.generate(question)
        with self._ingestor_factory() as ingestor:
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
