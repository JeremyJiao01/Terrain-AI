#!/usr/bin/env python3
"""Test code_graph_builder with tinycc repository (memory mode, no database)."""

from __future__ import annotations

import sys
import json
import time
from pathlib import Path
from collections import defaultdict

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from loguru import logger
from code_graph_builder.parser_loader import load_parsers
from code_graph_builder.parsers.structure_processor import StructureProcessor
from code_graph_builder.parsers.definition_processor import DefinitionProcessor
from code_graph_builder.parsers.call_processor import CallProcessor
from code_graph_builder.parsers.import_processor import ImportProcessor
from code_graph_builder.parsers.call_resolver import CallResolver
from code_graph_builder import constants as cs
from code_graph_builder.types import NodeType, SimpleNameLookup


class MockIngestor:
    """Mock ingestor that stores data in memory without database."""

    def __init__(self):
        self.nodes = []
        self.relationships = []
        self._node_batch = []
        self._rel_batch = []
        self._batch_size = 1000

    def ensure_node_batch(self, label: str, properties: dict) -> None:
        """Store node in memory."""
        self._node_batch.append({"label": label, "properties": properties.copy()})
        if len(self._node_batch) >= self._batch_size:
            self.flush_nodes()

    def ensure_relationship_batch(
        self,
        source: tuple[str, str, str],
        rel_type: str,
        target: tuple[str, str, str],
    ) -> None:
        """Store relationship in memory."""
        self._rel_batch.append({
            "source": {"label": source[0], "key": source[1], "value": source[2]},
            "type": rel_type,
            "target": {"label": target[0], "key": target[1], "value": target[2]},
        })
        if len(self._rel_batch) >= self._batch_size:
            self.flush_relationships()

    def flush_nodes(self) -> None:
        """Flush node batch."""
        self.nodes.extend(self._node_batch)
        self._node_batch = []

    def flush_relationships(self) -> None:
        """Flush relationship batch."""
        self.relationships.extend(self._rel_batch)
        self._rel_batch = []

    def final_flush(self) -> None:
        """Final flush of all batches."""
        self.flush_nodes()
        self.flush_relationships()

    def get_stats(self) -> dict:
        """Get statistics."""
        return {
            "nodes": len(self.nodes),
            "relationships": len(self.relationships),
        }


def analyze_tinycc():
    """Analyze tinycc repository."""
    repo_path = Path("/Users/jiaojeremy/CodeFile/tinycc")
    output_dir = PROJECT_ROOT / "tinycc_analysis"
    output_dir.mkdir(exist_ok=True)

    print("=" * 80)
    print("Testing code_graph_builder with tinycc repository (Memory Mode)")
    print("=" * 80)
    print(f"Repository path: {repo_path}")
    print()

    # Load parsers
    print("Loading Tree-sitter parsers...")
    parsers, queries = load_parsers()
    print(f"Loaded parsers for: {', '.join(parsers.keys())}")
    print()

    # Create mock ingestor
    ingestor = MockIngestor()

    # Initialize processors
    project_name = "tinycc"
    function_registry = {}
    simple_name_lookup: SimpleNameLookup = defaultdict(set)
    module_qn_to_file_path: dict[str, Path] = {}

    import_processor = ImportProcessor(project_name, module_qn_to_file_path)
    call_resolver = CallResolver(
        function_registry=function_registry,
        import_processor=import_processor,
    )

    # Create processors
    structure_processor = StructureProcessor(
        ingestor=ingestor,
        repo_path=repo_path,
        project_name=project_name,
        queries=queries,
        exclude_paths=frozenset({"tests", "win32", "examples"}),
    )

    definition_processor = DefinitionProcessor(
        ingestor=ingestor,
        repo_path=repo_path,
        project_name=project_name,
        function_registry=function_registry,
        simple_name_lookup=simple_name_lookup,
        import_processor=import_processor,
        module_qn_to_file_path=module_qn_to_file_path,
    )

    # Initialize type_inference as None for C code
    type_inference = None
    class_inheritance: dict[str, list[str]] = {}

    call_processor = CallProcessor(
        ingestor=ingestor,
        repo_path=repo_path,
        project_name=project_name,
        function_registry=function_registry,
        import_processor=import_processor,
        type_inference=type_inference,
        class_inheritance=class_inheritance,
    )

    # Run processing
    print("Running 3-pass analysis...")
    start_time = time.time()

    # Pass 1: Structure
    print("  Pass 1: Identifying structure...")
    structure_processor.identify_structure()
    structural_elements = structure_processor.structural_elements
    print(f"    Found {len(structural_elements)} structural elements")

    # Get C files
    c_files = [
        f for f in repo_path.rglob("*.c")
        if not any(x in str(f) for x in ["tests", "win32", "examples"])
    ]
    print(f"    Found {len(c_files)} C files to process")

    # Pass 2: Definitions
    print("  Pass 2: Processing definitions...")
    processed_files = []
    for file_path in c_files:
        result = definition_processor.process_file(
            file_path=file_path,
            language=cs.SupportedLanguage.C,
            queries=queries,
            structural_elements=structural_elements,
        )
        if result:
            processed_files.append((file_path, result))

    print(f"    Processed {len(processed_files)} files")

    # Pass 3: Call relationships
    print("  Pass 3: Processing call relationships...")
    for file_path, (root_node, language) in processed_files:
        call_processor.process_calls_in_file(
            file_path=file_path,
            root_node=root_node,
            language=language,
            queries=queries,
        )

    # Final flush
    ingestor.final_flush()

    duration = time.time() - start_time
    print()
    print("=" * 80)
    print("BUILD RESULTS")
    print("=" * 80)
    print(f"Duration: {duration:.2f} seconds")
    print(f"Files processed: {len(processed_files)}")
    print(f"Nodes created: {len(ingestor.nodes)}")
    print(f"Relationships created: {len(ingestor.relationships)}")
    print()

    # Analyze node types
    print("=" * 80)
    print("NODE TYPE STATISTICS")
    print("=" * 80)
    node_counts = defaultdict(int)
    for node in ingestor.nodes:
        label = node.get("label", "UNKNOWN")
        node_counts[label] += 1

    for label, count in sorted(node_counts.items(), key=lambda x: -x[1]):
        print(f"  {label}: {count}")
    print()

    # Analyze relationship types
    print("=" * 80)
    print("RELATIONSHIP TYPE STATISTICS")
    print("=" * 80)
    rel_counts = defaultdict(int)
    for rel in ingestor.relationships:
        rel_type = rel.get("type", "UNKNOWN")
        rel_counts[rel_type] += 1

    for rel_type, count in sorted(rel_counts.items(), key=lambda x: -x[1]):
        print(f"  {rel_type}: {count}")
    print()

    # Sample CALLS relationships
    print("=" * 80)
    print("SAMPLE CALL RELATIONSHIPS (first 20)")
    print("=" * 80)
    calls = [r for r in ingestor.relationships if r.get("type") == "CALLS"]
    print(f"Total CALLS relationships: {len(calls)}")
    print()

    for i, call in enumerate(calls[:20]):
        source = call.get("source", {}).get("value", "N/A")
        target = call.get("target", {}).get("value", "N/A")
        # Shorten for display
        source_short = source.split(".")[-1] if "." in source else source
        target_short = target.split(".")[-1] if "." in target else target
        print(f"  {i+1:2}. {source_short} -> {target_short}")
    print()

    # Top called functions
    print("=" * 80)
    print("TOP CALLED FUNCTIONS")
    print("=" * 80)
    called_counts = defaultdict(int)
    for call in calls:
        target = call.get("target", {}).get("value", "")
        if target:
            called_counts[target] += 1

    for func, count in sorted(called_counts.items(), key=lambda x: -x[1])[:15]:
        func_short = func.split(".")[-1] if "." in func else func
        print(f"  {func_short}: {count} calls")
    print()

    # Export data
    print("=" * 80)
    print("EXPORTING DATA")
    print("=" * 80)

    # Full graph
    graph_data = {
        "nodes": ingestor.nodes,
        "relationships": ingestor.relationships,
    }
    graph_file = output_dir / "tinycc_graph.json"
    with open(graph_file, "w") as f:
        json.dump(graph_data, f, indent=2, default=str)
    print(f"Full graph exported to: {graph_file}")

    # Summary
    summary = {
        "repository": "tinycc",
        "duration_seconds": duration,
        "files_processed": len(processed_files),
        "node_counts": dict(node_counts),
        "relationship_counts": dict(rel_counts),
        "total_calls": len(calls),
    }
    summary_file = output_dir / "summary.json"
    with open(summary_file, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"Summary exported to: {summary_file}")

    # Function list
    functions = [n for n in ingestor.nodes if n.get("label") in ("Function", "Method")]
    func_file = output_dir / "functions.txt"
    with open(func_file, "w") as f:
        for func in sorted(functions, key=lambda x: x.get("properties", {}).get("qualified_name", "")):
            qn = func.get("properties", {}).get("qualified_name", "N/A")
            f.write(f"{qn}\n")
    print(f"Function list exported to: {func_file} ({len(functions)} functions)")

    # Call graph
    call_graph = {
        "calls": [
            {
                "source": c.get("source", {}).get("value"),
                "target": c.get("target", {}).get("value"),
            }
            for c in calls
        ]
    }
    call_file = output_dir / "call_graph.json"
    with open(call_file, "w") as f:
        json.dump(call_graph, f, indent=2)
    print(f"Call graph exported to: {call_file}")
    print()

    print("=" * 80)
    print("TEST COMPLETED SUCCESSFULLY")
    print("=" * 80)

    return 0


if __name__ == "__main__":
    sys.exit(analyze_tinycc())
