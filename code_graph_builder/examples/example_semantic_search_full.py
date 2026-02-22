#!/usr/bin/env python3
"""Complete example of semantic search with embedding integration.

This example demonstrates:
1. P0: GraphUpdater with embedding generation
2. P1: Semantic search tools
3. P2: Graph query layer with Kuzu/Memgraph compatibility

Usage:
    # With Kuzu (no Docker required)
    python example_semantic_search_full.py --backend kuzu --repo ./my_repo

    # With Memgraph (requires Docker)
    python example_semantic_search_full.py --backend memgraph --repo ./my_repo

    # Search only (skip building)
    python example_semantic_search_full.py --backend kuzu --search "recursive function"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def setup_environment():
    """Add parent directory to path for imports."""
    sys.path.insert(0, str(Path(__file__).parent.parent))


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Semantic search example")
    parser.add_argument(
        "--backend",
        choices=["kuzu", "memgraph"],
        default="kuzu",
        help="Graph database backend",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path("./test_repo"),
        help="Path to code repository",
    )
    parser.add_argument(
        "--search",
        type=str,
        help="Search query (skip building if provided)",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean database before building",
    )
    return parser.parse_args()


def create_sample_repo(repo_path: Path) -> Path:
    """Create a sample repository for testing."""
    if repo_path.exists():
        return repo_path

    print(f"Creating sample repository at {repo_path}")
    repo_path.mkdir(parents=True, exist_ok=True)

    # Create sample Python files
    (repo_path / "math_utils.py").write_text('''
def factorial(n):
    """Calculate factorial recursively."""
    if n <= 1:
        return 1
    return n * factorial(n - 1)

def fibonacci(n):
    """Calculate Fibonacci number recursively."""
    if n <= 1:
        return n
    return fibonacci(n - 1) + fibonacci(n - 2)

def add(a, b):
    """Add two numbers."""
    return a + b
''')

    (repo_path / "string_utils.py").write_text('''
def reverse_string(s):
    """Reverse a string."""
    return s[::-1]

def is_palindrome(s):
    """Check if string is palindrome."""
    cleaned = s.lower().replace(" ", "")
    return cleaned == cleaned[::-1]
''')

    (repo_path / "data_structures.py").write_text('''
class Stack:
    """A simple stack implementation."""

    def __init__(self):
        self.items = []

    def push(self, item):
        """Push item onto stack."""
        self.items.append(item)

    def pop(self):
        """Pop item from stack."""
        if not self.items:
            return None
        return self.items.pop()

    def peek(self):
        """View top item without removing."""
        if not self.items:
            return None
        return self.items[-1]


class Queue:
    """A simple queue implementation."""

    def __init__(self):
        self.items = []

    def enqueue(self, item):
        """Add item to queue."""
        self.items.append(item)

    def dequeue(self):
        """Remove and return first item."""
        if not self.items:
            return None
        return self.items.pop(0)
''')

    return repo_path


def build_graph_with_embeddings(
    repo_path: Path,
    backend: str,
    clean: bool = False,
) -> tuple:
    """Build code graph with embedding generation.

    Args:
        repo_path: Path to repository
        backend: "kuzu" or "memgraph"
        clean: Whether to clean database first

    Returns:
        Tuple of (graph_service, vector_store, embedder)
    """
    from code_graph_builder.embeddings import create_embedder, create_vector_store
    from code_graph_builder.embeddings.qwen3_embedder import DummyEmbedder

    print(f"\n{'='*60}")
    print(f"Building code graph with {backend} backend")
    print(f"Repository: {repo_path}")
    print(f"{'='*60}\n")

    # Initialize embedding components
    print("Initializing embedding components...")

    # Use DummyEmbedder for testing (no API key needed)
    # In production, use Qwen3Embedder with your API key
    try:
        embedder = create_embedder(backend="qwen3")
        print(f"  Using Qwen3 embedder (dimension: {embedder.get_embedding_dimension()})")
    except Exception as e:
        print(f"  Failed to create Qwen3 embedder: {e}")
        print("  Falling back to DummyEmbedder")
        embedder = DummyEmbedder(dimension=1536)

    vector_store = create_vector_store(backend="memory", dimension=1536)
    print(f"  Using MemoryVectorStore (dimension: 1536)")

    # Initialize graph service based on backend
    if backend == "kuzu":
        from code_graph_builder.services.kuzu_service import KuzuIngestor

        db_path = Path("./example_graph.db")
        if clean and db_path.exists():
            import shutil
            shutil.rmtree(db_path)

        graph_service = KuzuIngestor(db_path)
        print(f"  Using Kuzu database at {db_path}")
    else:
        from code_graph_builder.services.graph_service import MemgraphIngestor

        graph_service = MemgraphIngestor("localhost", 7687)
        print("  Using Memgraph at localhost:7687")

    # Note: Full graph building would require parser setup
    # This is simplified for the example
    print("\nNote: Full graph building requires parser setup")
    print("See code_graph_builder/graph_updater.py for complete implementation")

    return graph_service, vector_store, embedder


def perform_semantic_search(
    query: str,
    graph_service,
    vector_store,
    embedder,
) -> list:
    """Perform semantic code search.

    Args:
        query: Natural language query
        graph_service: Graph database service
        vector_store: Vector store instance
        embedder: Embedder instance

    Returns:
        List of search results
    """
    from code_graph_builder.tools.semantic_search import SemanticSearchService

    print(f"\n{'='*60}")
    print(f"Semantic Search: '{query}'")
    print(f"{'='*60}\n")

    # Create semantic search service
    search_service = SemanticSearchService(
        embedder=embedder,
        vector_store=vector_store,
        graph_service=graph_service if hasattr(graph_service, 'fetch_all') else None,
    )

    # Add some sample data if vector store is empty
    if len(vector_store) == 0:
        print("Adding sample embeddings to vector store...")
        # In real usage, these would be generated from actual code
        sample_data = [
            (1, "math_utils.factorial", "Calculate factorial recursively"),
            (2, "math_utils.fibonacci", "Calculate Fibonacci recursively"),
            (3, "math_utils.add", "Add two numbers"),
            (4, "string_utils.reverse_string", "Reverse a string"),
            (5, "string_utils.is_palindrome", "Check if palindrome"),
            (6, "data_structures.Stack", "Stack implementation"),
            (7, "data_structures.Queue", "Queue implementation"),
        ]

        for node_id, qn, description in sample_data:
            # Generate embedding for description
            embedding = embedder.embed_code(description)
            vector_store.store_embedding(
                node_id=node_id,
                qualified_name=qn,
                embedding=embedding,
                metadata={"type": "Function" if "." in qn else "Class"},
            )
        print(f"  Added {len(sample_data)} sample embeddings")

    # Perform search
    print(f"\nSearching for: '{query}'")
    results = search_service.search(query, top_k=5)

    print(f"\nFound {len(results)} results:\n")
    for i, result in enumerate(results, 1):
        print(f"{i}. {result.qualified_name}")
        print(f"   Type: {result.type}")
        print(f"   Score: {result.score:.3f}")
        if result.docstring:
            print(f"   Doc: {result.docstring}")
        print()

    return results


def demonstrate_graph_query(graph_service, backend: str) -> None:
    """Demonstrate graph query capabilities.

    Args:
        graph_service: Graph database service
        backend: Backend type
    """
    from code_graph_builder.tools.graph_query import GraphQueryService

    print(f"\n{'='*60}")
    print(f"Graph Query Layer ({backend})")
    print(f"{'='*60}\n")

    query_service = GraphQueryService(graph_service, backend=backend)

    # Example: Fetch nodes by IDs
    print("Example: Fetch nodes by IDs")
    print("  Query: nodes [1, 2, 3]")

    # This would work with real data
    print("  (Requires populated database)")

    # Example: Query by qualified name
    print("\nExample: Query by qualified name")
    print("  Query: math_utils.factorial")
    print("  (Requires populated database)")


def main():
    """Main entry point."""
    setup_environment()
    args = parse_args()

    print("\n" + "=" * 60)
    print("Semantic Search with Embedding Integration Example")
    print("=" * 60)

    # Create sample repo if needed
    repo_path = create_sample_repo(args.repo)

    # Build or load graph
    graph_service, vector_store, embedder = build_graph_with_embeddings(
        repo_path=repo_path,
        backend=args.backend,
        clean=args.clean,
    )

    # Perform search if query provided
    if args.search:
        with graph_service:
            perform_semantic_search(
                query=args.search,
                graph_service=graph_service,
                vector_store=vector_store,
                embedder=embedder,
            )
    else:
        print("\nTip: Use --search '<query>' to perform semantic search")
        print("Example: python example_semantic_search_full.py --search 'recursive function'")

    # Demonstrate graph query layer
    with graph_service:
        demonstrate_graph_query(graph_service, args.backend)

    print("\n" + "=" * 60)
    print("Example complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()
