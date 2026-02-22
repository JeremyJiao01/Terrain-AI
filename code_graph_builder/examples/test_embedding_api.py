#!/usr/bin/env python3
"""Example: Test Qwen3 Embedding via Alibaba Cloud Bailian API.

This script demonstrates how to use the API-based Qwen3 embedder.

Prerequisites:
    1. Set your API key:
       export DASHSCOPE_API_KEY="sk-xxxxx"

    2. Or create a .env file in the project root with:
       DASHSCOPE_API_KEY=sk-xxxxx

Usage:
    python examples/test_embedding_api.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from loguru import logger

# Load environment variables from .env file
load_dotenv(Path(__file__).parent.parent / ".env")


def test_embedder() -> None:
    """Test the Qwen3 embedder with API."""
    from code_graph_builder.embeddings.qwen3_embedder import Qwen3Embedder, create_embedder

    # Check API key
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        logger.error("DASHSCOPE_API_KEY not set!")
        logger.info("Please set your API key:")
        logger.info("  export DASHSCOPE_API_KEY='sk-xxxxx'")
        sys.exit(1)

    logger.info(f"API Key found: {api_key[:10]}...")

    # Create embedder
    logger.info("Creating Qwen3 embedder...")
    embedder = create_embedder()

    # Health check
    logger.info("Running health check...")
    if embedder.health_check():
        logger.success("✓ API is accessible")
    else:
        logger.error("✗ API health check failed")
        sys.exit(1)

    # Test single embedding
    logger.info("\nTesting single code embedding...")
    code = """
def fibonacci(n):
    if n <= 1:
        return n
    return fibonacci(n-1) + fibonacci(n-2)
"""

    try:
        embedding = embedder.embed_code(code)
        logger.success(f"✓ Generated embedding with {len(embedding)} dimensions")
        logger.info(f"  First 5 values: {embedding[:5]}")
    except Exception as e:
        logger.error(f"✗ Failed to generate embedding: {e}")
        sys.exit(1)

    # Test batch embedding
    logger.info("\nTesting batch embedding...")
    codes = [
        "def add(a, b): return a + b",
        "class Calculator:\n    def multiply(self, x, y):\n        return x * y",
        "import os\nprint(os.getcwd())",
    ]

    try:
        embeddings = embedder.embed_batch(codes, show_progress=True)
        logger.success(f"✓ Generated {len(embeddings)} embeddings")
        for i, emb in enumerate(embeddings):
            logger.info(f"  Code {i+1}: {len(emb)} dimensions")
    except Exception as e:
        logger.error(f"✗ Failed to generate batch embeddings: {e}")
        sys.exit(1)

    # Test query embedding (with instruction)
    logger.info("\nTesting query embedding (with instruction)...")
    query = "functions that calculate Fibonacci numbers"

    try:
        query_embedding = embedder.embed_query(query)
        logger.success(f"✓ Generated query embedding with {len(query_embedding)} dimensions")
    except Exception as e:
        logger.error(f"✗ Failed to generate query embedding: {e}")
        sys.exit(1)

    logger.info("\n" + "=" * 50)
    logger.success("All tests passed! ✓")
    logger.info("=" * 50)


def test_vector_store() -> None:
    """Test the vector store with embeddings."""
    from code_graph_builder.embeddings.qwen3_embedder import create_embedder
    from code_graph_builder.embeddings.vector_store import create_vector_store

    logger.info("\nTesting Vector Store...")

    # Create embedder and vector store
    embedder = create_embedder()
    vector_store = create_vector_store(backend="memory", dimension=1536)

    # Store some embeddings
    codes = [
        (1, "def add(a, b): return a + b"),
        (2, "def subtract(a, b): return a - b"),
        (3, "class Calculator:\n    def multiply(self, x, y): return x * y"),
    ]

    logger.info("Storing embeddings...")
    for node_id, code in codes:
        embedding = embedder.embed_code(code)
        vector_store.store_embedding(
            node_id=node_id,
            qualified_name=f"module.function_{node_id}",
            embedding=embedding,
        )

    stats = vector_store.get_stats()
    logger.success(f"✓ Stored {stats['count']} embeddings")

    # Search
    logger.info("\nSearching for similar code...")
    query = "addition function"
    query_embedding = embedder.embed_query(query)

    results = vector_store.search_similar(query_embedding, top_k=3)

    logger.success(f"✓ Found {len(results)} results:")
    for i, result in enumerate(results, 1):
        logger.info(f"  {i}. {result.qualified_name} (score: {result.score:.4f})")


if __name__ == "__main__":
    test_embedder()
    test_vector_store()
