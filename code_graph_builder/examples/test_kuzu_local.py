#!/usr/bin/env python3
"""Test code_graph_builder with Kùzu embedded database (no Docker)."""

from __future__ import annotations

import sys
import json
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from code_graph_builder import CodeGraphBuilder


def test_kuzu_backend():
    """Test Kùzu backend with tinycc repository."""
    repo_path = "/Users/jiaojeremy/CodeFile/tinycc"
    output_dir = PROJECT_ROOT / "tinycc_kuzu"
    output_dir.mkdir(exist_ok=True)

    print("=" * 80)
    print("Testing code_graph_builder with Kùzu (No Docker)")
    print("=" * 80)
    print(f"Repository path: {repo_path}")
    print(f"Backend: Kùzu embedded database")
    print()

    # Initialize builder with Kùzu backend
    print("Initializing CodeGraphBuilder with Kùzu backend...")
    builder = CodeGraphBuilder(
        repo_path=repo_path,
        backend="kuzu",  # No Docker required!
        db_config={
            "db_path": str(output_dir / "tinycc_graph.db"),
            "batch_size": 1000,
        },
        exclude_paths=frozenset({"tests", "win32", "examples"}),
    )

    # Build graph with timing
    print("Building code graph...")
    start_time = time.time()

    try:
        result = builder.build_graph(clean=True)
        duration = time.time() - start_time

        print()
        print("=" * 80)
        print("BUILD RESULTS")
        print("=" * 80)
        print(f"Duration: {duration:.2f} seconds")
        print(f"Files processed: {result.files_processed}")
        print(f"Nodes created: {result.nodes_created}")
        print(f"Relationships created: {result.relationships_created}")
        print(f"Functions found: {result.functions_found}")
        print(f"Classes found: {result.classes_found}")
        print()

        # Get statistics
        print("Getting statistics...")
        stats = builder.get_statistics()
        print(f"Total nodes: {stats.get('total_nodes', 0)}")
        print(f"Total relationships: {stats.get('total_relationships', 0)}")
        print()

        # Try a query
        print("Testing Cypher query...")
        results = builder.query("MATCH (f:Function) RETURN f.name LIMIT 5")
        print(f"Query returned {len(results)} results")
        print()

        # Export graph
        print("Exporting graph data...")
        graph_data = builder.export_graph()

        export_file = output_dir / "export.json"
        with open(export_file, "w") as f:
            json.dump(graph_data, f, indent=2, default=str)
        print(f"Exported to: {export_file}")
        print()

        print("=" * 80)
        print("KÙZU BACKEND TEST COMPLETED SUCCESSFULLY")
        print("=" * 80)
        print()
        print(f"Database location: {output_dir / 'tinycc_graph.db'}")
        print("You can query this database directly using Kùzu CLI or Python API")

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


def test_memory_backend():
    """Test memory backend (no persistence)."""
    repo_path = "/Users/jiaojeremy/CodeFile/tinycc"
    output_dir = PROJECT_ROOT / "tinycc_memory"
    output_dir.mkdir(exist_ok=True)

    print("\n" + "=" * 80)
    print("Testing code_graph_builder with Memory backend")
    print("=" * 80)

    # Initialize builder with memory backend
    print("Initializing CodeGraphBuilder with Memory backend...")
    builder = CodeGraphBuilder(
        repo_path=repo_path,
        backend="memory",  # No database at all!
        exclude_paths=frozenset({"tests", "win32", "examples"}),
    )

    # Build graph
    print("Building code graph...")
    start_time = time.time()

    try:
        result = builder.build_graph()
        duration = time.time() - start_time

        print()
        print("=" * 80)
        print("BUILD RESULTS")
        print("=" * 80)
        print(f"Duration: {duration:.2f} seconds")
        print(f"Nodes created: {result.nodes_created}")
        print(f"Relationships created: {result.relationships_created}")
        print()

        # Get statistics
        stats = builder.get_statistics()
        print(f"Total nodes: {stats.get('total_nodes', 0)}")
        print(f"Total relationships: {stats.get('total_relationships', 0)}")

        # Export to JSON
        graph_data = builder.export_graph()
        export_file = output_dir / "graph.json"
        with open(export_file, "w") as f:
            json.dump(graph_data, f, indent=2, default=str)
        print(f"Exported to: {export_file}")

        print()
        print("MEMORY BACKEND TEST COMPLETED SUCCESSFULLY")

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0


def main():
    """Run all tests."""
    print("Code Graph Builder - Local Deployment Test")
    print("No Docker required!")
    print()

    # Test Kùzu backend
    ret1 = test_kuzu_backend()

    # Test Memory backend
    ret2 = test_memory_backend()

    print("\n" + "=" * 80)
    print("ALL TESTS COMPLETED")
    print("=" * 80)
    print()
    print("Summary:")
    print(f"  Kùzu backend: {'✅ PASSED' if ret1 == 0 else '❌ FAILED'}")
    print(f"  Memory backend: {'✅ PASSED' if ret2 == 0 else '❌ FAILED'}")
    print()
    print("You can now use code_graph_builder without Docker!")
    print()
    print("Usage:")
    print('  builder = CodeGraphBuilder(repo_path, backend="kuzu")')
    print('  builder = CodeGraphBuilder(repo_path, backend="memory")')

    return max(ret1, ret2)


if __name__ == "__main__":
    sys.exit(main())
