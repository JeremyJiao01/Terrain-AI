"""Natural language to Cypher query translator.

Uses an LLM backend to convert user questions into Cypher queries
that can be executed against the code knowledge graph.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from .llm_backend import LLMBackend

# System prompt describing the graph schema for Cypher generation.
_SCHEMA_PROMPT = """\
You are a Cypher query generator for a code knowledge graph stored in Kùzu.

Node labels: Project, Package, Folder, File, Module, Class, Function, Method, \
Interface, Enum, Type, Union, ExternalPackage.

Common properties: qualified_name (PK), name, path, start_line, end_line, \
docstring, return_type, signature, visibility, parameters (STRING[]), kind.

Relationship types: CONTAINS_PACKAGE, CONTAINS_FOLDER, CONTAINS_FILE, \
CONTAINS_MODULE, DEFINES, DEFINES_METHOD, IMPORTS, EXPORTS, EXPORTS_MODULE, \
IMPLEMENTS_MODULE, INHERITS, IMPLEMENTS, OVERRIDES, CALLS, DEPENDS_ON_EXTERNAL.

Rules:
- Output ONLY a single Cypher query, nothing else.
- Do NOT use OPTIONAL MATCH.
- Always LIMIT results to at most 50 unless the user specifies otherwise.
"""


class CypherGenerator:
    """Translates natural-language questions to Cypher queries using an LLM."""

    def __init__(self, llm: LLMBackend) -> None:
        self._llm = llm

    def generate(self, question: str) -> str:
        """Return a Cypher query string for *question*."""
        if not self._llm.api_key:
            raise RuntimeError(
                "LLM backend has no API key configured. "
                "Set MOONSHOT_API_KEY to enable query_code_graph."
            )

        messages = [
            {"role": "system", "content": _SCHEMA_PROMPT},
            {"role": "user", "content": question},
        ]

        raw = self._llm.chat(messages, temperature=0.0)

        # Strip markdown code fences if present
        query = raw.strip()
        if query.startswith("```"):
            lines = query.splitlines()
            # Remove first and last fence lines
            lines = [l for l in lines if not l.strip().startswith("```")]
            query = "\n".join(lines).strip()

        logger.debug(f"Generated Cypher: {query}")
        return query
