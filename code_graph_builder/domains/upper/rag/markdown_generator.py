"""Markdown output generator for RAG responses.

This module provides utilities for generating well-formatted markdown
documentation from RAG analysis results.

Examples:
    >>> from code_graph_builder.rag.markdown_generator import MarkdownGenerator
    >>> generator = MarkdownGenerator()
    >>> markdown = generator.generate_analysis_doc(
    ...     title="Authentication System",
    ...     query="Explain authentication",
    ...     response="The auth system...",
    ...     sources=[{"name": "auth.py", "path": "src/auth.py"}]
    ... )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from .prompt_templates import CodeContext


@dataclass
class SourceReference:
    """Reference to a source code entity.

    Attributes:
        name: Entity name
        qualified_name: Fully qualified name
        file_path: Source file path
        line_start: Start line number
        line_end: End line number
        entity_type: Type of entity (Function, Class, etc.)
    """

    name: str
    qualified_name: str
    file_path: str
    line_start: int | None = None
    line_end: int | None = None
    entity_type: str | None = None

    def format_link(self) -> str:
        """Format as markdown link."""
        location = self.file_path
        if self.line_start:
            location += f":{self.line_start}"
            if self.line_end and self.line_end != self.line_start:
                location += f"-{self.line_end}"
        return f"[{self.qualified_name}]({location})"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "qualified_name": self.qualified_name,
            "file_path": self.file_path,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "entity_type": self.entity_type,
        }


@dataclass
class AnalysisResult:
    """Result of a RAG analysis.

    Attributes:
        query: Original user query
        response: Generated response
        sources: List of source references
        metadata: Additional metadata
        timestamp: Analysis timestamp
    """

    query: str
    response: str
    sources: list[SourceReference] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "query": self.query,
            "response": self.response,
            "sources": [s.to_dict() for s in self.sources],
            "metadata": self.metadata,
            "timestamp": self.timestamp.isoformat(),
        }


class MarkdownGenerator:
    """Generator for markdown documentation.

    Creates well-formatted markdown documents from RAG analysis results.

    Args:
        include_toc: Whether to include table of contents
        include_timestamp: Whether to include generation timestamp
        include_metadata: Whether to include metadata section

    Examples:
        >>> generator = MarkdownGenerator(include_toc=True)
        >>> doc = generator.generate_analysis_doc(
        ...     title="Code Analysis",
        ...     result=analysis_result
        ... )
    """

    def __init__(
        self,
        include_toc: bool = True,
        include_timestamp: bool = True,
        include_metadata: bool = True,
    ):
        self.include_toc = include_toc
        self.include_timestamp = include_timestamp
        self.include_metadata = include_metadata

    def generate_analysis_doc(
        self,
        title: str,
        result: AnalysisResult,
    ) -> str:
        """Generate markdown document from analysis result.

        Args:
            title: Document title
            result: Analysis result

        Returns:
            Markdown document as string
        """
        lines = []

        # Title
        lines.append(f"# {title}")
        lines.append("")

        # Timestamp
        if self.include_timestamp:
            lines.append(f"*Generated: {result.timestamp.strftime('%Y-%m-%d %H:%M:%S UTC')}*")
            lines.append("")

        # Table of Contents
        if self.include_toc:
            lines.append("## Table of Contents")
            lines.append("")
            lines.append("- [Query](#query)")
            lines.append("- [Analysis](#analysis)")
            if result.sources:
                lines.append("- [Sources](#sources)")
            if self.include_metadata and result.metadata:
                lines.append("- [Metadata](#metadata)")
            lines.append("")

        # Query section
        lines.append("## Query")
        lines.append("")
        lines.append(f"> {result.query}")
        lines.append("")

        # Analysis section
        lines.append("## Analysis")
        lines.append("")
        lines.append(result.response)
        lines.append("")

        # Sources section
        if result.sources:
            lines.append("## Sources")
            lines.append("")
            for i, source in enumerate(result.sources, 1):
                lines.append(f"{i}. {source.format_link()}")
                if source.entity_type:
                    lines.append(f"   - Type: {source.entity_type}")
            lines.append("")

        # Metadata section
        if self.include_metadata and result.metadata:
            lines.append("## Metadata")
            lines.append("")
            for key, value in result.metadata.items():
                lines.append(f"- **{key}**: {value}")
            lines.append("")

        return "\n".join(lines)

    def generate_code_documentation(
        self,
        context: CodeContext,
        analysis: str,
    ) -> str:
        """Generate documentation for a code entity.

        Args:
            context: Code context
            analysis: Analysis text

        Returns:
            Markdown documentation
        """
        lines = []

        # Title
        title = context.qualified_name or context.entity_type or "Code Documentation"
        lines.append(f"# {title}")
        lines.append("")

        # Entity info
        if context.entity_type:
            lines.append(f"**Type:** {context.entity_type}")
        if context.file_path:
            lines.append(f"**File:** `{context.file_path}`")
        lines.append("")

        # Documentation
        lines.append(analysis)
        lines.append("")

        # Source code
        lines.append("## Source Code")
        lines.append("")
        lines.append("```python")
        lines.append(context.source_code)
        lines.append("```")
        lines.append("")

        # Relationships
        if context.callers:
            lines.append("## Called By")
            lines.append("")
            for caller in context.callers:
                lines.append(f"- `{caller}`")
            lines.append("")

        if context.callees:
            lines.append("## Calls")
            lines.append("")
            for callee in context.callees:
                lines.append(f"- `{callee}`")
            lines.append("")

        return "\n".join(lines)

    def generate_comparison_doc(
        self,
        title: str,
        query: str,
        contexts: list[CodeContext],
        analysis: str,
    ) -> str:
        """Generate comparison document for multiple code entities.

        Args:
            title: Document title
            query: Original query
            contexts: List of code contexts
            analysis: Comparative analysis

        Returns:
            Markdown document
        """
        lines = []

        lines.append(f"# {title}")
        lines.append("")
        lines.append(f"**Query:** {query}")
        lines.append("")

        lines.append("## Comparison Analysis")
        lines.append("")
        lines.append(analysis)
        lines.append("")

        lines.append("## Compared Entities")
        lines.append("")
        for i, ctx in enumerate(contexts, 1):
            name = ctx.qualified_name or f"Entity {i}"
            lines.append(f"### {i}. {name}")
            if ctx.file_path:
                lines.append(f"**File:** `{ctx.file_path}`")
            lines.append("")
            lines.append("```python")
            lines.append(ctx.source_code[:500] + "..." if len(ctx.source_code) > 500 else ctx.source_code)
            lines.append("```")
            lines.append("")

        return "\n".join(lines)

    def save_document(
        self,
        content: str,
        output_path: str | Path,
    ) -> Path:
        """Save markdown document to file.

        Args:
            content: Markdown content
            output_path: Output file path

        Returns:
            Path to saved file
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)

        logger.info(f"Saved markdown document to {output_path}")
        return output_path


def create_source_reference_from_context(
    context: CodeContext,
) -> SourceReference:
    """Create a SourceReference from CodeContext.

    Args:
        context: Code context

    Returns:
        SourceReference instance
    """
    return SourceReference(
        name=context.qualified_name.split(".")[-1] if context.qualified_name else "unknown",
        qualified_name=context.qualified_name or "unknown",
        file_path=context.file_path or "",
        entity_type=context.entity_type,
    )


def format_code_block(code: str, language: str = "python") -> str:
    """Format code as markdown code block.

    Args:
        code: Source code
        language: Language identifier

    Returns:
        Formatted code block
    """
    return f"```{language}\n{code}\n```"
