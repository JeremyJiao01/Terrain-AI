"""Command-line interface for Code Graph Builder.

Examples:
    # Scan a repository with Kùzu backend
    $ code-graph-builder scan /path/to/repo --backend kuzu --db-path ./graph.db

    # Scan with specific exclusions
    $ code-graph-builder scan /path/to/repo --exclude tests,docs --exclude-pattern "*.md"

    # Query the graph
    $ code-graph-builder query "MATCH (f:Function) RETURN f.name LIMIT 5" --db-path ./graph.db

    # Export to JSON
    $ code-graph-builder export /path/to/repo --output ./output.json

    # Use configuration file
    $ code-graph-builder scan /path/to/repo --config ./config.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from loguru import logger

from . import __version__
from .builder import CodeGraphBuilder
from .config import (
    KuzuConfig,
    MemgraphConfig,
    MemoryConfig,
    OutputConfig,
    ScanConfig,
)


def setup_logging(verbose: bool = False) -> None:
    """Setup logging configuration."""
    level = "DEBUG" if verbose else "INFO"
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    )


def load_config_file(config_path: str | Path) -> dict[str, Any]:
    """Load configuration from YAML or JSON file."""
    config_path = Path(config_path)

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    if config_path.suffix in (".yaml", ".yml"):
        try:
            import yaml

            with open(config_path) as f:
                return yaml.safe_load(f)
        except ImportError:
            raise ImportError("PyYAML is required for YAML config files. Install with: pip install pyyaml")
    elif config_path.suffix == ".json":
        with open(config_path) as f:
            return json.load(f)
    else:
        raise ValueError(f"Unsupported config file format: {config_path.suffix}")


def create_builder_from_args(args: argparse.Namespace) -> CodeGraphBuilder:
    """Create CodeGraphBuilder from command-line arguments."""
    # Load config file if specified
    config = {}
    if hasattr(args, 'config') and args.config:
        config = load_config_file(args.config)

    # Determine backend
    backend = getattr(args, 'backend', None) or config.get("backend", "kuzu")

    # Build backend config
    backend_config = config.get("backend_config", {})
    if backend == "kuzu":
        if getattr(args, 'db_path', None):
            backend_config["db_path"] = args.db_path
        if getattr(args, 'batch_size', None):
            backend_config["batch_size"] = args.batch_size
    elif backend == "memgraph":
        if getattr(args, 'host', None):
            backend_config["host"] = args.host
        if getattr(args, 'port', None):
            backend_config["port"] = args.port
        if getattr(args, 'username', None):
            backend_config["username"] = args.username
        if getattr(args, 'password', None):
            backend_config["password"] = args.password
        if getattr(args, 'batch_size', None):
            backend_config["batch_size"] = args.batch_size

    # Build scan config
    scan_config = config.get("scan_config", {})
    if getattr(args, 'exclude', None):
        # Parse comma-separated exclusions
        exclude_set = set()
        for item in args.exclude:
            exclude_set.update(item.split(","))
        scan_config["exclude_patterns"] = exclude_set
    if getattr(args, 'exclude_pattern', None):
        scan_config.setdefault("exclude_patterns", set()).update(args.exclude_pattern)
    if getattr(args, 'language', None):
        scan_config["include_languages"] = set(args.language.split(","))
    if getattr(args, 'max_file_size', None):
        scan_config["max_file_size"] = args.max_file_size

    # Create builder
    return CodeGraphBuilder(
        repo_path=args.repo_path,
        backend=backend,
        backend_config=backend_config,
        scan_config=scan_config,
    )


def cmd_scan(args: argparse.Namespace) -> int:
    """Execute the scan command."""
    setup_logging(args.verbose)

    try:
        logger.info(f"Starting scan of: {args.repo_path}")
        logger.info(f"Backend: {args.backend or 'kuzu'}")

        builder = create_builder_from_args(args)

        # Build the graph
        result = builder.build_graph(clean=args.clean)

        print()
        print("=" * 60)
        print("SCAN COMPLETE")
        print("=" * 60)
        print(f"Repository: {result.project_name}")
        print(f"Nodes created: {result.nodes_created}")
        print(f"Relationships created: {result.relationships_created}")
        print(f"Functions found: {result.functions_found}")
        print(f"Classes found: {result.classes_found}")
        print(f"Files processed: {result.files_processed}")

        if args.backend == "kuzu" or (not args.backend and args.db_path):
            db_path = args.db_path or f"./{Path(args.repo_path).name}_graph.db"
            print(f"Database saved to: {db_path}")

        # Export to JSON if requested
        if args.output:
            logger.info(f"Exporting to: {args.output}")
            data = builder.export_graph()
            with open(args.output, "w") as f:
                json.dump(data, f, indent=2, default=str)
            print(f"Exported to: {args.output}")

        return 0

    except Exception as e:
        logger.error(f"Scan failed: {e}")
        if args.verbose:
            import traceback

            traceback.print_exc()
        return 1


def cmd_query(args: argparse.Namespace) -> int:
    """Execute the query command."""
    setup_logging(args.verbose)

    try:
        # Determine backend from args
        backend = args.backend or "kuzu"
        backend_config = {"db_path": args.db_path} if args.db_path else {}

        builder = CodeGraphBuilder(
            repo_path=args.repo_path or ".",
            backend=backend,
            backend_config=backend_config,
        )

        logger.info(f"Executing query: {args.cypher_query}")
        results = builder.query(args.cypher_query)

        print()
        print("=" * 60)
        print("QUERY RESULTS")
        print("=" * 60)
        print(f"Query: {args.cypher_query}")
        print(f"Results: {len(results)}")
        print()

        if results:
            # Print as table
            if args.format == "table":
                headers = list(results[0].keys())
                # Calculate column widths
                widths = {h: len(h) for h in headers}
                for row in results:
                    for h in headers:
                        widths[h] = max(widths[h], len(str(row.get(h, ""))))

                # Print header
                header_line = " | ".join(h.ljust(widths[h]) for h in headers)
                print(header_line)
                print("-" * len(header_line))

                # Print rows
                for row in results:
                    print(" | ".join(str(row.get(h, "")).ljust(widths[h]) for h in headers))
            else:
                # JSON format
                for i, row in enumerate(results, 1):
                    print(f"{i}. {json.dumps(row, default=str)}")
        else:
            print("No results found.")

        return 0

    except Exception as e:
        logger.error(f"Query failed: {e}")
        if args.verbose:
            import traceback

            traceback.print_exc()
        return 1


def cmd_export(args: argparse.Namespace) -> int:
    """Execute the export command."""
    setup_logging(args.verbose)

    try:
        builder = create_builder_from_args(args)

        logger.info(f"Exporting graph from: {args.repo_path}")

        # Build if not already built
        if args.build:
            logger.info("Building graph first...")
            builder.build_graph(clean=args.clean)

        data = builder.export_graph()

        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w") as f:
            json.dump(data, f, indent=2, default=str)

        print()
        print("=" * 60)
        print("EXPORT COMPLETE")
        print("=" * 60)
        print(f"Output: {output_path.absolute()}")
        print(f"Nodes: {len(data.get('nodes', []))}")
        print(f"Relationships: {len(data.get('relationships', []))}")

        return 0

    except Exception as e:
        logger.error(f"Export failed: {e}")
        if args.verbose:
            import traceback

            traceback.print_exc()
        return 1


def cmd_stats(args: argparse.Namespace) -> int:
    """Execute the stats command."""
    setup_logging(args.verbose)

    try:
        backend = args.backend or "kuzu"
        backend_config = {"db_path": args.db_path} if args.db_path else {}

        builder = CodeGraphBuilder(
            repo_path=args.repo_path or ".",
            backend=backend,
            backend_config=backend_config,
        )

        stats = builder.get_statistics()

        print()
        print("=" * 60)
        print("GRAPH STATISTICS")
        print("=" * 60)
        print(f"Total nodes: {stats.get('total_nodes', 0)}")
        print(f"Total relationships: {stats.get('total_relationships', 0)}")
        print()

        node_labels = stats.get("node_labels", {})
        if node_labels:
            print("Node types:")
            for label, count in sorted(node_labels.items(), key=lambda x: -x[1]):
                label_str = str(label)
                print(f"  {label_str:20s}: {count:5d}")
            print()

        rel_types = stats.get("relationship_types", {})
        if rel_types:
            print("Relationship types:")
            for rel_type, count in sorted(rel_types.items(), key=lambda x: -x[1]):
                rel_str = str(rel_type)
                print(f"  {rel_str:20s}: {count:5d}")

        return 0

    except Exception as e:
        logger.error(f"Stats failed: {e}")
        if args.verbose:
            import traceback

            traceback.print_exc()
        return 1


def main() -> int:
    """Main entry point for CLI."""
    parser = argparse.ArgumentParser(
        prog="code-graph-builder",
        description="Build and query code knowledge graphs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Scan repository with Kùzu backend
  code-graph-builder scan /path/to/repo --db-path ./graph.db

  # Scan with exclusions
  code-graph-builder scan /path/to/repo --exclude tests,docs --exclude-pattern "*.md"

  # Query the graph
  code-graph-builder query "MATCH (f:Function) RETURN f.name LIMIT 5" --db-path ./graph.db

  # Export to JSON
  code-graph-builder export /path/to/repo --output ./graph.json --build

  # Show statistics
  code-graph-builder stats --db-path ./graph.db

For more information, visit: https://github.com/your-repo/code-graph-builder
        """,
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose output",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Scan command
    scan_parser = subparsers.add_parser(
        "scan",
        help="Scan a repository and build the knowledge graph",
        description="Scan source code and build a knowledge graph.",
    )
    scan_parser.add_argument(
        "repo_path",
        type=str,
        help="Path to the repository to scan",
    )
    scan_parser.add_argument(
        "--backend",
        choices=["kuzu", "memgraph", "memory"],
        default="kuzu",
        help="Storage backend (default: kuzu)",
    )
    scan_parser.add_argument(
        "--db-path",
        type=str,
        help="Path to store the database (for Kùzu backend)",
    )
    scan_parser.add_argument(
        "--host",
        type=str,
        help="Memgraph host (for Memgraph backend)",
    )
    scan_parser.add_argument(
        "--port",
        type=int,
        help="Memgraph port (for Memgraph backend)",
    )
    scan_parser.add_argument(
        "--username",
        type=str,
        help="Memgraph username",
    )
    scan_parser.add_argument(
        "--password",
        type=str,
        help="Memgraph password",
    )
    scan_parser.add_argument(
        "--batch-size",
        type=int,
        help="Batch size for database writes",
    )
    scan_parser.add_argument(
        "--exclude",
        action="append",
        help="Comma-separated patterns to exclude (can be used multiple times)",
    )
    scan_parser.add_argument(
        "--exclude-pattern",
        action="append",
        help="Additional exclude pattern (can be used multiple times)",
    )
    scan_parser.add_argument(
        "--language",
        type=str,
        help="Comma-separated list of languages to include",
    )
    scan_parser.add_argument(
        "--max-file-size",
        type=int,
        help="Maximum file size in bytes",
    )
    scan_parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean existing database before scanning",
    )
    scan_parser.add_argument(
        "--output",
        "-o",
        type=str,
        help="Export graph to JSON file after scanning",
    )
    scan_parser.add_argument(
        "--config",
        "-c",
        type=str,
        help="Configuration file (YAML or JSON)",
    )
    scan_parser.set_defaults(func=cmd_scan)

    # Query command
    query_parser = subparsers.add_parser(
        "query",
        help="Execute a Cypher query against the graph",
        description="Query the knowledge graph using Cypher.",
    )
    query_parser.add_argument(
        "cypher_query",
        type=str,
        help="Cypher query to execute",
    )
    query_parser.add_argument(
        "--repo-path",
        type=str,
        help="Path to the repository (for reference)",
    )
    query_parser.add_argument(
        "--backend",
        choices=["kuzu", "memgraph", "memory"],
        default="kuzu",
        help="Storage backend (default: kuzu)",
    )
    query_parser.add_argument(
        "--db-path",
        type=str,
        help="Path to the database (for Kùzu backend)",
    )
    query_parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)",
    )
    query_parser.set_defaults(func=cmd_query)

    # Export command
    export_parser = subparsers.add_parser(
        "export",
        help="Export the graph to JSON",
        description="Export the knowledge graph to a JSON file.",
    )
    export_parser.add_argument(
        "repo_path",
        type=str,
        help="Path to the repository",
    )
    export_parser.add_argument(
        "--output",
        "-o",
        type=str,
        required=True,
        help="Output JSON file path",
    )
    export_parser.add_argument(
        "--backend",
        choices=["kuzu", "memgraph", "memory"],
        default="memory",
        help="Storage backend (default: memory)",
    )
    export_parser.add_argument(
        "--build",
        action="store_true",
        help="Build the graph before exporting",
    )
    export_parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean existing data before building",
    )
    export_parser.add_argument(
        "--exclude",
        action="append",
        help="Patterns to exclude",
    )
    export_parser.set_defaults(func=cmd_export)

    # Stats command
    stats_parser = subparsers.add_parser(
        "stats",
        help="Show graph statistics",
        description="Display statistics about the knowledge graph.",
    )
    stats_parser.add_argument(
        "--repo-path",
        type=str,
        help="Path to the repository",
    )
    stats_parser.add_argument(
        "--backend",
        choices=["kuzu", "memgraph", "memory"],
        default="kuzu",
        help="Storage backend (default: kuzu)",
    )
    stats_parser.add_argument(
        "--db-path",
        type=str,
        help="Path to the database",
    )
    stats_parser.set_defaults(func=cmd_stats)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
