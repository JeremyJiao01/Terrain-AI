"""Configuration for Code Graph Builder.

This module provides configuration classes for different backends and
scanning options.

Examples:
    >>> from code_graph_builder import CodeGraphBuilder
    >>> from code_graph_builder.config import KuzuConfig, ScanConfig
    >>>
    >>> # Method 1: Using config objects
    >>> backend_config = KuzuConfig(db_path="./my_graph.db", batch_size=1000)
    >>> scan_config = ScanConfig(exclude_patterns={"tests", "docs"})
    >>>
    >>> builder = CodeGraphBuilder(
    ...     repo_path="/path/to/repo",
    ...     backend="kuzu",
    ...     backend_config=backend_config,
    ...     scan_config=scan_config
    ... )
    >>>
    >>> # Method 2: Using dict (simpler)
    >>> builder = CodeGraphBuilder(
    ...     repo_path="/path/to/repo",
    ...     backend="kuzu",
    ...     backend_config={"db_path": "./graph.db"},
    ...     scan_config={"exclude_patterns": {"tests"}}
    ... )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class KuzuConfig:
    """Configuration for Kùzu embedded database backend.

    Args:
        db_path: Path to store the Kùzu database files
        batch_size: Number of nodes/relationships to batch before writing
        read_only: Open database in read-only mode

    Examples:
        >>> config = KuzuConfig(db_path="./graph.db")
        >>> config = KuzuConfig(db_path="/data/graphs/myproj.db", batch_size=5000)
    """
    db_path: str | Path = "./code_graph.db"
    batch_size: int = 1000
    read_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "db_path": str(self.db_path),
            "batch_size": self.batch_size,
            "read_only": self.read_only,
        }


@dataclass
class MemgraphConfig:
    """Configuration for Memgraph database backend.

    Args:
        host: Memgraph server host
        port: Memgraph server port
        username: Authentication username (optional)
        password: Authentication password (optional)
        batch_size: Number of nodes/relationships to batch before writing

    Examples:
        >>> config = MemgraphConfig(host="localhost", port=7687)
        >>> config = MemgraphConfig(
        ...     host="192.168.1.100",
        ...     port=7687,
        ...     username="user",
        ...     password="pass"
        ... )
    """
    host: str = "localhost"
    port: int = 7687
    username: str | None = None
    password: str | None = None
    batch_size: int = 1000

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password": self.password,
            "batch_size": self.batch_size,
        }


@dataclass
class MemoryConfig:
    """Configuration for in-memory backend.

    This backend has no persistence options.
    Useful for testing and one-off analysis.

    Args:
        auto_save: Whether to auto-save to JSON on exit
        save_path: Path to save JSON when auto_save is True

    Examples:
        >>> config = MemoryConfig()
        >>> config = MemoryConfig(auto_save=True, save_path="./output.json")
    """
    auto_save: bool = False
    save_path: str | Path | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "auto_save": self.auto_save,
            "save_path": str(self.save_path) if self.save_path else None,
        }


@dataclass
class ScanConfig:
    """Configuration for repository scanning.

    Controls what files are included/excluded from analysis.

    Args:
        exclude_patterns: Set of patterns to exclude (directories or file patterns)
        unignore_paths: Set of paths to unignore (override default ignores)
        include_languages: Set of languages to include (None = all supported)
        max_file_size: Maximum file size in bytes to process (None = no limit)
        follow_symlinks: Whether to follow symbolic links

    Examples:
        >>> # Exclude tests and documentation
        >>> config = ScanConfig(exclude_patterns={"tests", "docs", "*.md"})
        >>>
        >>> # Only scan Python files
        >>> config = ScanConfig(
        ...     exclude_patterns={"tests"},
        ...     include_languages={"python"}
        ... )
    """
    exclude_patterns: set[str] = field(default_factory=set)
    unignore_paths: set[str] = field(default_factory=set)
    include_languages: set[str] | None = None
    max_file_size: int | None = None  # bytes
    follow_symlinks: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "exclude_patterns": list(self.exclude_patterns),
            "unignore_paths": list(self.unignore_paths),
            "include_languages": list(self.include_languages) if self.include_languages else None,
            "max_file_size": self.max_file_size,
            "follow_symlinks": self.follow_symlinks,
        }


@dataclass
class OutputConfig:
    """Configuration for output options.

    Controls what outputs are generated and where they are saved.

    Args:
        output_dir: Directory to save output files
        export_json: Whether to export graph to JSON
        json_filename: Name of the JSON export file
        export_statistics: Whether to export statistics
        statistics_filename: Name of statistics file
        save_call_graph: Whether to save call relationships separately
        verbose: Enable verbose logging

    Examples:
        >>> config = OutputConfig(output_dir="./analysis_output")
        >>> config = OutputConfig(
        ...     output_dir="./output",
        ...     export_json=True,
        ...     json_filename="my_graph.json",
        ...     verbose=True
        ... )
    """
    output_dir: str | Path = "./code_graph_output"
    export_json: bool = True
    json_filename: str = "graph.json"
    export_statistics: bool = True
    statistics_filename: str = "statistics.json"
    save_call_graph: bool = True
    call_graph_filename: str = "call_graph.json"
    save_functions_list: bool = True
    functions_filename: str = "functions.txt"
    verbose: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "output_dir": str(self.output_dir),
            "export_json": self.export_json,
            "json_filename": self.json_filename,
            "export_statistics": self.export_statistics,
            "statistics_filename": self.statistics_filename,
            "save_call_graph": self.save_call_graph,
            "call_graph_filename": self.call_graph_filename,
            "save_functions_list": self.save_functions_list,
            "functions_filename": self.functions_filename,
            "verbose": self.verbose,
        }


@dataclass
class EmbeddingConfig:
    """Configuration for semantic embedding generation via API.

    Controls Qwen3 embedding model settings (via Alibaba Cloud Bailian API)
    and vector store backend.

    Args:
        enabled: Whether to enable embedding generation
        api_key: DashScope API key (or set DASHSCOPE_API_KEY env var)
        model: API model name (default: text-embedding-v4 for Qwen3)
        base_url: API base URL (default: https://dashscope.aliyuncs.com/api/v1)
        batch_size: Batch size for embedding generation (max 25 for API)
        max_retries: Maximum retries for failed API requests
        vector_store_backend: Vector store backend ("memory" or "qdrant")
        vector_store_path: Path for vector store (for qdrant local mode)
        vector_dimension: Embedding dimension (1536 for text-embedding-v4)

    Examples:
        >>> config = EmbeddingConfig(enabled=True)
        >>> config = EmbeddingConfig(
        ...     enabled=True,
        ...     api_key="sk-xxxxx",
        ...     batch_size=25
        ... )
    """
    enabled: bool = False
    api_key: str | None = None
    model: str = "text-embedding-v4"
    base_url: str | None = None
    batch_size: int = 25  # API limit
    max_retries: int = 3
    vector_store_backend: str = "memory"
    vector_store_path: str | Path | None = None
    vector_dimension: int = 1536  # text-embedding-v4 dimension

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "enabled": self.enabled,
            "api_key": self.api_key,
            "model": self.model,
            "base_url": self.base_url,
            "batch_size": self.batch_size,
            "max_retries": self.max_retries,
            "vector_store_backend": self.vector_store_backend,
            "vector_store_path": str(self.vector_store_path) if self.vector_store_path else None,
            "vector_dimension": self.vector_dimension,
        }


# Type alias for backend configs
BackendConfig = KuzuConfig | MemgraphConfig | MemoryConfig | dict[str, Any]


# Type alias for all config types
GraphBuilderConfig = KuzuConfig | MemgraphConfig | MemoryConfig | EmbeddingConfig | dict[str, Any]


class ConfigValidator:
    """Validator for configuration combinations."""

    @staticmethod
    def validate_backend_config(backend: str, config: BackendConfig | None) -> dict[str, Any]:
        """Validate and convert backend config to dict.

        Args:
            backend: Backend type ("kuzu", "memgraph", "memory")
            config: Configuration object or dict

        Returns:
            Validated configuration dictionary

        Raises:
            ValueError: If backend or config is invalid
        """
        # Convert dataclass to dict
        if hasattr(config, 'to_dict'):
            config = config.to_dict()
        elif config is None:
            config = {}
        elif not isinstance(config, dict):
            raise ValueError(f"Config must be a dict or dataclass, got {type(config)}")

        # Validate based on backend type
        if backend == "kuzu":
            return ConfigValidator._validate_kuzu_config(config)
        elif backend == "memgraph":
            return ConfigValidator._validate_memgraph_config(config)
        elif backend == "memory":
            return ConfigValidator._validate_memory_config(config)
        else:
            raise ValueError(f"Unknown backend: {backend}. Use 'kuzu', 'memgraph', or 'memory'")

    @staticmethod
    def _validate_kuzu_config(config: dict[str, Any]) -> dict[str, Any]:
        """Validate Kùzu configuration."""
        defaults = KuzuConfig()
        return {
            "db_path": config.get("db_path", defaults.db_path),
            "batch_size": config.get("batch_size", defaults.batch_size),
            "read_only": config.get("read_only", defaults.read_only),
        }

    @staticmethod
    def _validate_memgraph_config(config: dict[str, Any]) -> dict[str, Any]:
        """Validate Memgraph configuration."""
        defaults = MemgraphConfig()
        return {
            "host": config.get("host", defaults.host),
            "port": config.get("port", defaults.port),
            "username": config.get("username", defaults.username),
            "password": config.get("password", defaults.password),
            "batch_size": config.get("batch_size", defaults.batch_size),
        }

    @staticmethod
    def _validate_memory_config(config: dict[str, Any]) -> dict[str, Any]:
        """Validate Memory configuration."""
        defaults = MemoryConfig()
        return {
            "auto_save": config.get("auto_save", defaults.auto_save),
            "save_path": config.get("save_path", defaults.save_path),
        }
