#!/usr/bin/env python3
"""Example: Using code_graph_builder with Kùzu backend (no Docker)."""

from __future__ import annotations

import json
from pathlib import Path

from code_graph_builder import CodeGraphBuilder


def main():
    """Demonstrate Kùzu backend usage."""
    # Example repository path (change to your repo)
    repo_path = "/Users/jiaojeremy/CodeFile/tinycc"

    print("Code Graph Builder - Kùzu Backend Example")
    print("=" * 60)
    print()

    # Step 1: Initialize builder with Kùzu backend
    print("1. Initializing CodeGraphBuilder with Kùzu backend...")
    builder = CodeGraphBuilder(
        repo_path=repo_path,
        backend="kuzu",
        db_config={
            "db_path": "./example_graph.db",
            "batch_size": 1000,
        },
        exclude_paths=frozenset({"tests", "win32", "examples", ".git"}),
    )
    print("   ✅ Builder initialized")
    print()

    # Step 2: Build the graph
    print("2. Building code graph...")
    result = builder.build_graph(clean=True)
    print(f"   ✅ Graph built successfully")
    print(f"   - Nodes: {result.nodes_created}")
    print(f"   - Relationships: {result.relationships_created}")
    print()

    # Step 3: Get statistics
    print("3. Getting statistics...")
    stats = builder.get_statistics()
    print(f"   📊 Total nodes: {stats.get('total_nodes', 0)}")
    print(f"   📊 Total relationships: {stats.get('total_relationships', 0)}")
    node_labels = stats.get("node_labels", {})
    if node_labels:
        print(f"   📊 Node labels:")
        for label, count in list(node_labels.items())[:5]:  # Show first 5
            print(f"      - {label}: {count}")
    rel_types = stats.get("relationship_types", {})
    if rel_types:
        print(f"   📊 Relationship types:")
        for rel_type, count in list(rel_types.items())[:5]:  # Show first 5
            print(f"      - {rel_type}: {count}")
    print()

    # Step 4: Query the graph
    print("4. Querying the graph...")
    print("   Query: MATCH (f:Function) RETURN f.name LIMIT 5")
    results = builder.query("MATCH (f:Function) RETURN f.name LIMIT 5")
    print(f"   Results ({len(results)} found):")
    for i, row in enumerate(results, 1):
        print(f"      {i}. {row}")
    print()

    # Step 5: Export graph data
    print("5. Exporting graph data...")
    graph_data = builder.export_graph()
    output_file = Path("example_export.json")
    with open(output_file, "w") as f:
        json.dump(graph_data, f, indent=2, default=str)
    print(f"   ✅ Exported to {output_file}")
    print(f"   - Total nodes exported: {len(graph_data.get('nodes', []))}")
    print(f"   - Total relationships exported: {len(graph_data.get('relationships', []))}")
    print()

    # Step 6: Find specific function
    print("6. Finding specific function...")
    func_results = builder.query(
        """
        MATCH (f:Function)
        WHERE f.name CONTAINS 'parse'
        RETURN f.name, f.qualified_name
        LIMIT 3
        """
    )
    print(f"   Found {len(func_results)} functions matching 'parse':")
    for row in func_results:
        print(f"      - {row}")
    print()

    print("=" * 60)
    print("✅ Example completed successfully!")
    print()
    print("Summary:")
    print(f"  - Database: {Path('./example_graph.db').absolute()}")
    print(f"  - Export: {output_file.absolute()}")
    print()
    print("You can:")
    print("  1. Query the database directly using Kùzu CLI")
    print("  2. Load the database in another Python script")
    print("  3. Import the JSON export into other tools")


if __name__ == "__main__":
    main()
