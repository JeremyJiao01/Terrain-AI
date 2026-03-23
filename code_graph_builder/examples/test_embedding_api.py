#!/usr/bin/env python3
"""Test Embedding API via OpenAI-compatible endpoint.

Reads ``~/.code-graph-builder/.env`` (written by ``npx code-graph-builder --setup``)
and verifies the embedding endpoint using the same request style as production:
  - requests + verify=False
  - OpenAI-compatible /embeddings endpoint
  - Response format: {"data": [{"embedding": [...], "index": 0}, ...]}

Usage:
    python -m code_graph_builder.examples.test_embedding_api
"""

from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

# Suppress SSL warnings (consistent with qwen3_embedder.py)
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

# Load .env — prefer npx setup wizard location, fallback to project root
_NPX_ENV = Path.home() / ".code-graph-builder" / ".env"
_LOCAL_ENV = Path(__file__).resolve().parent.parent.parent / ".env"

for env_path in (_NPX_ENV, _LOCAL_ENV):
    if env_path.exists():
        load_dotenv(env_path)
        logger.info(f"Loaded env from: {env_path}")
        break
else:
    logger.warning(
        "No .env file found. Falling back to shell environment.\n"
        f"  Expected: {_NPX_ENV}\n"
        "  Run `npx code-graph-builder --setup` to create one."
    )


def test_embedder() -> None:
    """Test the embedder via create_embedder() factory."""
    from code_graph_builder.embeddings.qwen3_embedder import create_embedder

    # Check API key (any supported key)
    api_key = (
        os.getenv("DASHSCOPE_API_KEY")
        or os.getenv("EMBEDDING_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("LLM_API_KEY")
    )
    if not api_key:
        logger.error(
            "No embedding API key found!\n"
            "Please set one of: DASHSCOPE_API_KEY, EMBEDDING_API_KEY, OPENAI_API_KEY, LLM_API_KEY\n"
            "Run `npx code-graph-builder --setup` to configure."
        )
        sys.exit(1)

    logger.info(f"API Key found: {api_key[:6]}****{api_key[-4:]}")

    # Create embedder
    logger.info("Creating embedder via create_embedder()...")
    embedder = create_embedder()

    # Health check
    logger.info("Running health check...")
    if embedder.health_check():
        logger.success("API is accessible")
    else:
        logger.error("API health check failed")
        sys.exit(1)

    # Test single embedding
    logger.info("\nTesting single code embedding...")
    code = "def fibonacci(n):\n    if n <= 1:\n        return n\n    return fibonacci(n-1) + fibonacci(n-2)"

    try:
        embedding = embedder.embed_code(code)
        logger.success(f"Generated embedding with {len(embedding)} dimensions")
        logger.info(f"  First 5 values: {embedding[:5]}")
    except Exception as e:
        logger.error(f"Failed to generate embedding: {e}")
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
        logger.success(f"Generated {len(embeddings)} embeddings")
        for i, emb in enumerate(embeddings):
            logger.info(f"  Code {i+1}: {len(emb)} dimensions")
    except Exception as e:
        logger.error(f"Failed to generate batch embeddings: {e}")
        sys.exit(1)

    # Test query embedding (with instruction)
    logger.info("\nTesting query embedding (with instruction)...")
    query = "functions that calculate Fibonacci numbers"

    try:
        query_embedding = embedder.embed_query(query)
        logger.success(f"Generated query embedding with {len(query_embedding)} dimensions")
    except Exception as e:
        logger.error(f"Failed to generate query embedding: {e}")
        sys.exit(1)

    logger.info("\n" + "=" * 50)
    logger.success("All embedding tests passed!")
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
    logger.success(f"Stored {stats['count']} embeddings")

    # Search
    logger.info("\nSearching for similar code...")
    query = "addition function"
    query_embedding = embedder.embed_query(query)

    results = vector_store.search_similar(query_embedding, top_k=3)

    logger.success(f"Found {len(results)} results:")
    for i, result in enumerate(results, 1):
        logger.info(f"  {i}. {result.qualified_name} (score: {result.score:.4f})")


if __name__ == "__main__":
    test_embedder()
    test_vector_store()
