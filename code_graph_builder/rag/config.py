"""Configuration for RAG module.

This module provides configuration classes for RAG components including
Moonshot API settings, retrieval parameters, and output options.

Examples:
    >>> from code_graph_builder.rag.config import RAGConfig
    >>> config = RAGConfig.from_env()
    >>> print(config.moonshot.model)
    kimi-k2.5
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MoonshotConfig:
    """Configuration for Moonshot AI API (Kimi k2.5).

    Args:
        api_key: Moonshot API key (or from MOONSHOT_API_KEY env var)
        model: Model name (default: kimi-k2.5)
        base_url: API base URL
        max_tokens: Maximum tokens for generation
        temperature: Sampling temperature (0-2)
        timeout: Request timeout in seconds

    Examples:
        >>> config = MoonshotConfig(api_key="sk-xxxxx")
        >>> config = MoonshotConfig(
        ...     api_key="sk-xxxxx",
        ...     model="kimi-k2.5",
        ...     temperature=0.7
        ... )
    """

    api_key: str | None = None
    model: str = "kimi-k2.5"
    base_url: str = "https://api.moonshot.cn/v1"
    max_tokens: int = 4096
    temperature: float = 0.7
    timeout: int = 120

    def __post_init__(self):
        """Load API key from environment if not provided."""
        if self.api_key is None:
            self.api_key = os.getenv("MOONSHOT_API_KEY")

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "api_key": self.api_key,
            "model": self.model,
            "base_url": self.base_url,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "timeout": self.timeout,
        }

    def validate(self) -> None:
        """Validate configuration.

        Raises:
            ValueError: If configuration is invalid
        """
        if not self.api_key:
            raise ValueError(
                "Moonshot API key is required. "
                "Set MOONSHOT_API_KEY environment variable or pass api_key."
            )
        if not self.api_key.startswith("sk-"):
            raise ValueError(
                "Moonshot API key format is invalid. Expected to start with 'sk-'."
            )
        if self.temperature < 0 or self.temperature > 2:
            raise ValueError("Temperature must be between 0 and 2.")


@dataclass
class RetrievalConfig:
    """Configuration for code retrieval.

    Args:
        semantic_top_k: Number of semantic search results
        graph_max_depth: Maximum depth for graph traversal
        include_callers: Whether to include calling functions
        include_callees: Whether to include called functions
        include_related: Whether to include related nodes
        max_context_tokens: Maximum tokens for context
        code_chunk_size: Maximum size of code chunks

    Examples:
        >>> config = RetrievalConfig(semantic_top_k=10, include_callers=True)
    """

    semantic_top_k: int = 10
    graph_max_depth: int = 2
    include_callers: bool = True
    include_callees: bool = True
    include_related: bool = True
    max_context_tokens: int = 8000
    code_chunk_size: int = 2000

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "semantic_top_k": self.semantic_top_k,
            "graph_max_depth": self.graph_max_depth,
            "include_callers": self.include_callers,
            "include_callees": self.include_callees,
            "include_related": self.include_related,
            "max_context_tokens": self.max_context_tokens,
            "code_chunk_size": self.code_chunk_size,
        }


@dataclass
class OutputConfig:
    """Configuration for RAG output.

    Args:
        format: Output format (markdown, json)
        include_source_links: Whether to include source code links
        include_code_snippets: Whether to include code snippets
        output_dir: Directory for output files

    Examples:
        >>> config = OutputConfig(format="markdown", include_source_links=True)
    """

    format: str = "markdown"
    include_source_links: bool = True
    include_code_snippets: bool = True
    output_dir: str | Path = "./rag_output"

    def __post_init__(self):
        """Normalize output directory path."""
        if isinstance(self.output_dir, str):
            self.output_dir = Path(self.output_dir)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "format": self.format,
            "include_source_links": self.include_source_links,
            "include_code_snippets": self.include_code_snippets,
            "output_dir": str(self.output_dir),
        }


@dataclass
class RAGConfig:
    """Main configuration for RAG module.

    Combines all sub-configurations for Moonshot API, retrieval,
    and output settings.

    Args:
        moonshot: Moonshot API configuration
        retrieval: Retrieval configuration
        output: Output configuration
        verbose: Enable verbose logging

    Examples:
        >>> # From environment variables
        >>> config = RAGConfig.from_env()
        >>>
        >>> # With explicit settings
        >>> config = RAGConfig(
        ...     moonshot=MoonshotConfig(api_key="sk-xxxxx"),
        ...     retrieval=RetrievalConfig(semantic_top_k=15)
        ... )
    """

    moonshot: MoonshotConfig = field(default_factory=MoonshotConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)
    output: OutputConfig = field(default_factory=OutputConfig)
    verbose: bool = False

    @classmethod
    def from_env(cls) -> RAGConfig:
        """Create configuration from environment variables.

        Environment variables:
            MOONSHOT_API_KEY: Moonshot API key
            MOONSHOT_MODEL: Model name (default: kimi-k2.5)
            MOONSHOT_BASE_URL: API base URL
            RAG_SEMANTIC_TOP_K: Number of semantic search results
            RAG_OUTPUT_FORMAT: Output format
            RAG_VERBOSE: Enable verbose logging

        Returns:
            RAGConfig instance
        """
        moonshot_config = MoonshotConfig(
            api_key=os.getenv("MOONSHOT_API_KEY"),
            model=os.getenv("MOONSHOT_MODEL", "kimi-k2.5"),
            base_url=os.getenv("MOONSHOT_BASE_URL", "https://api.moonshot.cn/v1"),
        )

        retrieval_config = RetrievalConfig(
            semantic_top_k=int(os.getenv("RAG_SEMANTIC_TOP_K", "10")),
        )

        output_config = OutputConfig(
            format=os.getenv("RAG_OUTPUT_FORMAT", "markdown"),
            output_dir=os.getenv("RAG_OUTPUT_DIR", "./rag_output"),
        )

        verbose = os.getenv("RAG_VERBOSE", "false").lower() == "true"

        return cls(
            moonshot=moonshot_config,
            retrieval=retrieval_config,
            output=output_config,
            verbose=verbose,
        )

    def validate(self) -> None:
        """Validate all configurations.

        Raises:
            ValueError: If any configuration is invalid
        """
        self.moonshot.validate()

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "moonshot": self.moonshot.to_dict(),
            "retrieval": self.retrieval.to_dict(),
            "output": self.output.to_dict(),
            "verbose": self.verbose,
        }
