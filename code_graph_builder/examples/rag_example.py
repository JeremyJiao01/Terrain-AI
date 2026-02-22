"""Example usage of the RAG module for code analysis.

This example demonstrates how to use the RAG module to:
1. Query code using natural language
2. Explain specific code entities
3. Analyze module architecture
4. Use CAMEL agents for specialized analysis

Prerequisites:
    - Set MOONSHOT_API_KEY environment variable
    - Have a code graph built with code_graph_builder
    - Have embeddings generated in vector store

Example:
    export MOONSHOT_API_KEY="sk-xxxxx"
    uv run examples/rag_example.py
"""

from __future__ import annotations

import os
from pathlib import Path

from code_graph_builder.embeddings import create_embedder, create_vector_store
from code_graph_builder.rag import (
    RAGConfig,
    RAGEngine,
    create_rag_engine,
)
from code_graph_builder.rag.camel_agent import CamelAgent, MultiAgentRAG
from code_graph_builder.services import MemgraphIngestor


def setup_rag_engine() -> RAGEngine:
    """Set up the RAG engine with configuration."""
    # Load configuration from environment
    config = RAGConfig.from_env()
    config.validate()

    print(f"Using model: {config.moonshot.model}")
    print(f"Semantic top-k: {config.retrieval.semantic_top_k}")

    # Create embedder and vector store
    embedder = create_embedder()
    vector_store = create_vector_store(
        backend="memory",
        dimension=embedder.get_embedding_dimension(),
    )

    # Connect to graph database
    graph_service = MemgraphIngestor(
        host=os.getenv("MEMGRAPH_HOST", "localhost"),
        port=int(os.getenv("MEMGRAPH_PORT", "7687")),
    )

    # Create RAG engine
    engine = create_rag_engine(
        config=config,
        embedder=embedder,
        vector_store=vector_store,
        graph_service=graph_service,
    )

    return engine


def example_natural_language_query(engine: RAGEngine) -> None:
    """Example: Query code using natural language."""
    print("\n" + "=" * 60)
    print("Example 1: Natural Language Query")
    print("=" * 60)

    query = "How does the authentication system work?"
    print(f"\nQuery: {query}")

    result = engine.query(query, top_k=5)

    print(f"\nResponse:\n{result.response}")
    print(f"\nSources used:")
    for source in result.sources:
        print(f"  - {source.qualified_name} ({source.file_path})")

    # Save result to file
    output_path = engine.save_result(result)
    print(f"\nSaved to: {output_path}")


def example_explain_code(engine: RAGEngine) -> None:
    """Example: Explain a specific code entity."""
    print("\n" + "=" * 60)
    print("Example 2: Explain Code Entity")
    print("=" * 60)

    # Example qualified name - adjust to your codebase
    qualified_name = "code_graph_builder.rag.rag_engine.RAGEngine.query"
    print(f"\nExplaining: {qualified_name}")

    result = engine.explain_code(qualified_name, include_related=True)

    print(f"\nExplanation:\n{result.response}")


def example_architecture_analysis(engine: RAGEngine) -> None:
    """Example: Analyze module architecture."""
    print("\n" + "=" * 60)
    print("Example 3: Architecture Analysis")
    print("=" * 60)

    module_name = "code_graph_builder.rag"
    print(f"\nAnalyzing module: {module_name}")

    result = engine.analyze_architecture(module_name)

    print(f"\nArchitecture Analysis:\n{result.response}")


def example_camel_agent() -> None:
    """Example: Use CAMEL agent for code review."""
    print("\n" + "=" * 60)
    print("Example 4: CAMEL Agent Code Review")
    print("=" * 60)

    # Create a specialized agent
    agent = CamelAgent(
        role="Senior Python Developer",
        goal="Review code for best practices and potential issues",
        backstory="10+ years of Python development experience, expert in clean code",
    )

    # Code to review
    code = """
def process_data(data):
    result = []
    for i in range(len(data)):
        if data[i] > 0:
            result.append(data[i] * 2)
    return result
"""

    print("\nCode to review:")
    print(code)

    # Run review
    response = agent.review_code(code, review_type="general")
    print(f"\nReview:\n{response.content}")

    # Get improvement suggestions
    suggestions = agent.suggest_improvements(
        code,
        focus_areas=["readability", "performance"],
    )
    print(f"\nSuggestions:\n{suggestions.content}")


def example_multi_agent_analysis(engine: RAGEngine) -> None:
    """Example: Multi-agent comprehensive analysis."""
    print("\n" + "=" * 60)
    print("Example 5: Multi-Agent Analysis")
    print("=" * 60)

    # Create multi-agent system
    multi_agent = MultiAgentRAG(engine)

    query = "Explain the RAG engine implementation"
    print(f"\nQuery: {query}")

    # Run multi-agent analysis
    results = multi_agent.analyze(
        query=query,
        analysis_types=["architecture", "docs"],
    )

    for agent_type, response in results.items():
        print(f"\n--- {agent_type.upper()} ANALYSIS ---")
        print(response.content[:500] + "..." if len(response.content) > 500 else response.content)


def main() -> None:
    """Run all examples."""
    print("RAG Module Examples")
    print("===================")

    # Check API key
    if not os.getenv("MOONSHOT_API_KEY"):
        print("\nError: MOONSHOT_API_KEY environment variable not set")
        print("Please set it before running: export MOONSHOT_API_KEY='your-key'")
        return

    try:
        # Set up RAG engine
        engine = setup_rag_engine()

        # Run examples
        example_natural_language_query(engine)
        example_explain_code(engine)
        example_architecture_analysis(engine)
        example_camel_agent()
        example_multi_agent_analysis(engine)

    except Exception as e:
        print(f"\nError: {e}")
        raise


if __name__ == "__main__":
    main()
