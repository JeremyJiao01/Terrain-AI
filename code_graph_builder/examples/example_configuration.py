#!/usr/bin/env python3
"""Examples showing all configuration options for Code Graph Builder.

This script demonstrates various ways to configure the builder:
1. Simple dict-based configuration (quick start)
2. Type-safe dataclass configuration (recommended)
3. Different backends (Kùzu, Memory, Memgraph)
4. Scan configuration options
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from code_graph_builder import CodeGraphBuilder
from code_graph_builder.config import (
    KuzuConfig,
    MemgraphConfig,
    MemoryConfig,
    OutputConfig,
    ScanConfig,
)


def example_1_simple_dict_config():
    """Example 1: Simple dict-based configuration (quickest way)."""
    print("=" * 80)
    print("Example 1: Simple Dict Configuration")
    print("=" * 80)
    print()

    builder = CodeGraphBuilder(
        # Required: Path to code repository
        repo_path="/Users/jiaojeremy/CodeFile/tinycc",
        # Required: Backend type ("kuzu", "memgraph", or "memory")
        backend="kuzu",
        # Optional: Backend-specific configuration as dict
        backend_config={
            "db_path": "./example1_graph.db",  # Where to store the database
            "batch_size": 1000,  # Batch size for writes
        },
        # Optional: Scan configuration as dict
        scan_config={
            "exclude_patterns": {"tests", "win32", "examples"},  # Skip these
            "max_file_size": 10 * 1024 * 1024,  # Skip files > 10MB
        },
    )

    print(f"Builder initialized:")
    print(f"  Repository: {builder.repo_path}")
    print(f"  Backend: {builder.backend}")
    print(f"  Backend config: {builder.backend_config}")
    print(f"  Scan config: {builder.scan_config}")
    print()
    return builder


def example_2_type_safe_config():
    """Example 2: Type-safe dataclass configuration (recommended)."""
    print("=" * 80)
    print("Example 2: Type-Safe Dataclass Configuration")
    print("=" * 80)
    print()

    # Create type-safe configurations
    kuzu_config = KuzuConfig(
        db_path="./example2_graph.db",
        batch_size=5000,  # Larger batch for better performance
        read_only=False,
    )

    scan_config = ScanConfig(
        exclude_patterns={"tests", "docs", "*.md", ".git"},
        include_languages={"c", "python"},  # Only scan these languages
        max_file_size=5 * 1024 * 1024,  # 5MB limit
        follow_symlinks=False,
    )

    builder = CodeGraphBuilder(
        repo_path="/Users/jiaojeremy/CodeFile/tinycc",
        backend="kuzu",
        backend_config=kuzu_config,  # Pass dataclass instead of dict
        scan_config=scan_config,  # Pass dataclass instead of dict
    )

    print(f"Builder initialized with dataclasses:")
    print(f"  Kùzu DB path: {kuzu_config.db_path}")
    print(f"  Batch size: {kuzu_config.batch_size}")
    print(f"  Excluded: {scan_config.exclude_patterns}")
    print(f"  Languages: {scan_config.include_languages}")
    print()
    return builder


def example_3_memory_backend():
    """Example 3: Memory backend (no persistence, for testing)."""
    print("=" * 80)
    print("Example 3: Memory Backend (No Persistence)")
    print("=" * 80)
    print()

    mem_config = MemoryConfig(
        auto_save=True,  # Auto-save to JSON on exit
        save_path="./memory_export.json",
    )

    builder = CodeGraphBuilder(
        repo_path="/Users/jiaojeremy/CodeFile/tinycc",
        backend="memory",
        backend_config=mem_config,
        scan_config=ScanConfig(
            exclude_patterns={"tests", "win32"},
        ),
    )

    print(f"Memory builder initialized:")
    print(f"  Auto-save: {mem_config.auto_save}")
    print(f"  Save path: {mem_config.save_path}")
    print()
    return builder


def example_4_memgraph_backend():
    """Example 4: Memgraph backend (requires Docker)."""
    print("=" * 80)
    print("Example 4: Memgraph Backend (Docker Required)")
    print("=" * 80)
    print()

    memgraph_config = MemgraphConfig(
        host="localhost",
        port=7687,
        username=None,  # Set if authentication enabled
        password=None,
        batch_size=1000,
    )

    builder = CodeGraphBuilder(
        repo_path="/Users/jiaojeremy/CodeFile/tinycc",
        backend="memgraph",
        backend_config=memgraph_config,
    )

    print(f"Memgraph builder initialized:")
    print(f"  Host: {memgraph_config.host}:{memgraph_config.port}")
    print(f"  Auth: {'Yes' if memgraph_config.username else 'No'}")
    print()
    return builder


def example_5_full_configuration():
    """Example 5: Full configuration with all options."""
    print("=" * 80)
    print("Example 5: Full Configuration")
    print("=" * 80)
    print()

    # Complete backend configuration
    backend_config = KuzuConfig(
        db_path="/tmp/full_example_graph.db",
        batch_size=2000,
    )

    # Complete scan configuration
    scan_config = ScanConfig(
        exclude_patterns={
            "tests",           # Exclude test directories
            "test_",           # Exclude files starting with test_
            "_test.py",        # Exclude Python test files
            "node_modules",    # Exclude JS dependencies
            ".git",            # Exclude git directory
            "*.min.js",        # Exclude minified JS
            "vendor",          # Exclude vendored code
        },
        unignore_paths={"tests/conftest.py"},  # But keep this file
        include_languages=None,  # Include all supported languages
        max_file_size=50 * 1024 * 1024,  # 50MB max file size
        follow_symlinks=False,
    )

    builder = CodeGraphBuilder(
        repo_path="/Users/jiaojeremy/CodeFile/tinycc",
        backend="kuzu",
        backend_config=backend_config,
        scan_config=scan_config,
    )

    print("Full configuration:")
    print(f"  Backend: Kùzu at {backend_config.db_path}")
    print(f"  Batch size: {backend_config.batch_size}")
    print(f"  Exclusions: {len(scan_config.exclude_patterns)} patterns")
    print(f"  Max file size: {scan_config.max_file_size / 1024 / 1024:.1f}MB")
    print()

    # Show how to access config values
    print("Configuration summary:")
    print(f"  Repository path: {builder.repo_path}")
    print(f"  Backend type: {builder.backend}")
    print(f"  Database path: {builder.backend_config.get('db_path')}")
    print(f"  Excluded patterns: {builder.scan_config.exclude_patterns}")
    print()
    return builder


def example_6_backward_compatibility():
    """Example 6: Backward compatible (old API still works)."""
    print("=" * 80)
    print("Example 6: Backward Compatible API")
    print("=" * 80)
    print()

    # Old API (still works but deprecated)
    builder = CodeGraphBuilder(
        repo_path="/Users/jiaojeremy/CodeFile/tinycc",
        backend="kuzu",
        db_config={"db_path": "./old_api_graph.db"},  # Deprecated, use backend_config
        exclude_paths=frozenset({"tests"}),  # Deprecated, use scan_config
    )

    print("Builder created with old API (deprecated but working):")
    print(f"  db_config -> backend_config: {builder.backend_config}")
    print(f"  exclude_paths -> scan_config.exclude_patterns: {builder.scan_config.exclude_patterns}")
    print()
    return builder


def main():
    """Run all examples."""
    print("Code Graph Builder - Configuration Examples")
    print("=" * 80)
    print()

    examples = [
        ("Simple Dict Config", example_1_simple_dict_config),
        ("Type-Safe Config", example_2_type_safe_config),
        ("Memory Backend", example_3_memory_backend),
        ("Memgraph Backend", example_4_memgraph_backend),
        ("Full Configuration", example_5_full_configuration),
        ("Backward Compatible", example_6_backward_compatibility),
    ]

    builders = []
    for name, example_func in examples:
        try:
            builder = example_func()
            builders.append((name, builder))
        except Exception as e:
            print(f"Error in {name}: {e}")
            print()

    # Summary
    print("=" * 80)
    print("Summary")
    print("=" * 80)
    print()
    print("Created builders:")
    for name, builder in builders:
        print(f"  ✓ {name}: {builder.backend} backend")
    print()
    print("All examples completed!")
    print()
    print("Quick reference:")
    print("  backend='kuzu'     -> Embedded database, no Docker")
    print("  backend='memory'   -> In-memory, no persistence")
    print("  backend='memgraph' -> Full database, requires Docker")
    print()
    print("For more details, see:")
    print("  - LOCAL_DEPLOYMENT.md")
    print("  - code_graph_builder/config.py")


if __name__ == "__main__":
    main()
