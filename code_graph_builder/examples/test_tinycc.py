#!/usr/bin/env python3
"""Test code_graph_builder with tinycc repository."""

from __future__ import annotations

import sys
import json
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from code_graph_builder import CodeGraphBuilder

def main():
    repo_path = "/Users/jiaojeremy/CodeFile/tinycc"
    output_dir = PROJECT_ROOT / "tinycc_analysis"
    output_dir.mkdir(exist_ok=True)

    print("=" * 80)
    print("Testing code_graph_builder with tinycc repository")
    print("=" * 80)
    print(f"Repository path: {repo_path}")
    print()

    # Initialize builder
    print("Initializing CodeGraphBuilder...")
    builder = CodeGraphBuilder(
        repo_path=repo_path,
        db_config={"host": "localhost", "port": 7687, "batch_size": 1000},
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

        # Export graph
        print("Exporting graph data...")
        graph_data = builder.export_graph()

        output_file = output_dir / "tinycc_graph.json"
        with open(output_file, "w") as f:
            json.dump(graph_data, f, indent=2, default=str)
        print(f"Graph exported to: {output_file}")
        print()

        # Analyze node types
        print("=" * 80)
        print("NODE TYPE STATISTICS")
        print("=" * 80)
        node_counts = {}
        for node in graph_data.get("nodes", []):
            label = node.get("label", "UNKNOWN")
            node_counts[label] = node_counts.get(label, 0) + 1

        for label, count in sorted(node_counts.items(), key=lambda x: -x[1]):
            print(f"  {label}: {count}")
        print()

        # Analyze relationship types
        print("=" * 80)
        print("RELATIONSHIP TYPE STATISTICS")
        print("=" * 80)
        rel_counts = {}
        for rel in graph_data.get("relationships", []):
            rel_type = rel.get("type", "UNKNOWN")
            rel_counts[rel_type] = rel_counts.get(rel_type, 0) + 1

        for rel_type, count in sorted(rel_counts.items(), key=lambda x: -x[1]):
            print(f"  {rel_type}: {count}")
        print()

        # Find CALLS relationships
        print("=" * 80)
        print("SAMPLE CALL RELATIONSHIPS")
        print("=" * 80)
        calls = [r for r in graph_data.get("relationships", []) if r.get("type") == "CALLS"]
        print(f"Total CALLS relationships: {len(calls)}")
        print()

        # Show first 10 CALLS
        for i, call in enumerate(calls[:10]):
            source = call.get("source", {}).get("qualified_name", "N/A")
            target = call.get("target", {}).get("qualified_name", "N/A")
            print(f"  {i+1}. {source} -> {target}")
        print()

        # Export summary
        summary = {
            "repository": "tinycc",
            "duration_seconds": duration,
            "node_counts": node_counts,
            "relationship_counts": rel_counts,
            "total_calls": len(calls),
            "sample_calls": [
                {
                    "source": c.get("source", {}).get("qualified_name"),
                    "target": c.get("target", {}).get("qualified_name"),
                }
                for c in calls[:20]
            ],
        }

        summary_file = output_dir / "summary.json"
        with open(summary_file, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"Summary exported to: {summary_file}")

        # Save function list
        functions = [
            node for node in graph_data.get("nodes", [])
            if node.get("label") in ("Function", "Method")
        ]
        func_file = output_dir / "functions.txt"
        with open(func_file, "w") as f:
            for func in sorted(functions, key=lambda x: x.get("properties", {}).get("qualified_name", "")):
                qn = func.get("properties", {}).get("qualified_name", "N/A")
                f.write(f"{qn}\n")
        print(f"Function list exported to: {func_file} ({len(functions)} functions)")

        print()
        print("=" * 80)
        print("TEST COMPLETED SUCCESSFULLY")
        print("=" * 80)

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

    return 0

if __name__ == "__main__":
    sys.exit(main())
