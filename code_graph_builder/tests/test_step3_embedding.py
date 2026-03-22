"""Step 3 integration test: vector embedding generation from tinycc graph.

Builds graph, generates API docs, then creates vector embeddings.
Validates embedding quality, vector store integrity, and semantic search.
"""

from __future__ import annotations

import os
import pickle
from pathlib import Path

import pytest

TINYCC_PATH = Path(__file__).resolve().parents[3] / "tinycc"

pytestmark = [
    pytest.mark.skipif(
        not TINYCC_PATH.exists(),
        reason=f"tinycc source not found at {TINYCC_PATH}",
    ),
    pytest.mark.skipif(
        not os.environ.get("DASHSCOPE_API_KEY"),
        reason="DASHSCOPE_API_KEY not set (required for embedding API)",
    ),
]


@pytest.fixture(scope="module")
def pipeline_artifacts(tmp_path_factory):
    """Run Step 1 (graph) + Step 2 (api-docs) + Step 3 (embedding) once."""
    from code_graph_builder.mcp.pipeline import (
        build_graph,
        build_vector_index,
        generate_api_docs_step,
    )

    artifact_dir = tmp_path_factory.mktemp("artifacts")
    db_path = artifact_dir / "graph.db"
    vectors_path = artifact_dir / "vectors.pkl"

    # Step 1: build graph
    builder = build_graph(
        repo_path=TINYCC_PATH,
        db_path=db_path,
        rebuild=True,
        backend="kuzu",
    )

    # Step 2: generate API docs
    generate_api_docs_step(builder=builder, artifact_dir=artifact_dir, rebuild=True)

    # Step 3: build embeddings
    vector_store, embedder, func_map = build_vector_index(
        builder=builder,
        repo_path=TINYCC_PATH,
        vectors_path=vectors_path,
        rebuild=True,
    )

    yield {
        "builder": builder,
        "vector_store": vector_store,
        "embedder": embedder,
        "func_map": func_map,
        "vectors_path": vectors_path,
        "artifact_dir": artifact_dir,
    }

    if hasattr(builder, "close"):
        builder.close()


# ---------------------------------------------------------------------------
# Vector store basics
# ---------------------------------------------------------------------------


class TestVectorStoreStructure:
    """Verify the vector store is populated correctly."""

    def test_store_not_empty(self, pipeline_artifacts):
        vs = pipeline_artifacts["vector_store"]
        assert len(vs) > 50, f"Expected many embeddings, got {len(vs)}"

    def test_func_map_matches_store(self, pipeline_artifacts):
        vs = pipeline_artifacts["vector_store"]
        fm = pipeline_artifacts["func_map"]
        assert len(fm) == len(vs), (
            f"func_map ({len(fm)}) should match vector_store ({len(vs)})"
        )

    def test_embedding_dimension(self, pipeline_artifacts):
        """Embeddings should have a reasonable dimension (1024 or 1536)."""
        vs = pipeline_artifacts["vector_store"]
        for record in vs._records.values():
            dim = len(record.embedding)
            assert dim in (1024, 1536), (
                f"Unexpected embedding dimension: {dim}"
            )
            break

    def test_vectors_file_persisted(self, pipeline_artifacts):
        vp = pipeline_artifacts["vectors_path"]
        assert vp.exists()
        assert vp.stat().st_size > 1000, "vectors.pkl should be substantial"

    def test_vectors_file_loadable(self, pipeline_artifacts):
        vp = pipeline_artifacts["vectors_path"]
        with open(vp, "rb") as f:
            cache = pickle.load(f)
        assert "vector_store" in cache
        assert "func_map" in cache
        assert len(cache["func_map"]) > 0


# ---------------------------------------------------------------------------
# Embedding content quality
# ---------------------------------------------------------------------------


class TestEmbeddingContent:
    """Verify embedding text is rich and includes expected context."""

    def test_func_map_has_names(self, pipeline_artifacts):
        fm = pipeline_artifacts["func_map"]
        named = sum(1 for f in fm.values() if f.get("name"))
        assert named == len(fm), "All funcs in func_map should have names"

    def test_func_map_has_qualified_names(self, pipeline_artifacts):
        fm = pipeline_artifacts["func_map"]
        qn_count = sum(1 for f in fm.values() if f.get("qualified_name"))
        assert qn_count == len(fm), "All funcs should have qualified_name"

    def test_func_map_has_paths(self, pipeline_artifacts):
        """Most functions should have file paths."""
        fm = pipeline_artifacts["func_map"]
        with_path = sum(1 for f in fm.values() if f.get("path"))
        ratio = with_path / len(fm) if fm else 0
        assert ratio > 0.5, f"Only {ratio:.0%} functions have paths"

    def test_func_map_has_line_numbers(self, pipeline_artifacts):
        fm = pipeline_artifacts["func_map"]
        with_lines = sum(
            1 for f in fm.values()
            if f.get("start_line") and f.get("end_line")
        )
        ratio = with_lines / len(fm) if fm else 0
        assert ratio > 0.8, f"Only {ratio:.0%} functions have line numbers"


# ---------------------------------------------------------------------------
# Semantic search
# ---------------------------------------------------------------------------


class TestSemanticSearch:
    """Verify semantic search returns meaningful results."""

    def test_search_by_function_name(self, pipeline_artifacts):
        """Searching for a known function name should return it."""
        vs = pipeline_artifacts["vector_store"]
        embedder = pipeline_artifacts["embedder"]

        query_emb = embedder.embed_query("tcc_compile")
        results = vs.search_similar(query_emb, top_k=5)

        assert len(results) > 0, "Search should return results"
        # Check that at least one result is related to compilation
        qns = [r.qualified_name for r in results]
        found = any("compile" in qn.lower() or "tcc" in qn.lower() for qn in qns)
        assert found, f"Expected compilation-related results, got: {qns}"

    def test_search_by_concept(self, pipeline_artifacts):
        """Searching by abstract concept should return relevant functions."""
        vs = pipeline_artifacts["vector_store"]
        embedder = pipeline_artifacts["embedder"]

        query_emb = embedder.embed_query("parse expression")
        results = vs.search_similar(query_emb, top_k=10)

        assert len(results) > 0
        qns = [r.qualified_name for r in results]
        # Should find parsing-related functions
        found = any(
            "parse" in qn.lower() or "expr" in qn.lower()
            for qn in qns
        )
        assert found, f"Expected parse/expr results, got: {qns}"

    def test_search_returns_scores(self, pipeline_artifacts):
        """Search results should have similarity scores between 0 and 1."""
        vs = pipeline_artifacts["vector_store"]
        embedder = pipeline_artifacts["embedder"]

        query_emb = embedder.embed_query("memory allocation")
        results = vs.search_similar(query_emb, top_k=5)

        assert len(results) > 0
        for r in results:
            assert 0.0 <= r.score <= 1.0, f"Score {r.score} out of range"

    def test_search_scores_descending(self, pipeline_artifacts):
        """Results should be sorted by score descending."""
        vs = pipeline_artifacts["vector_store"]
        embedder = pipeline_artifacts["embedder"]

        query_emb = embedder.embed_query("generate code")
        results = vs.search_similar(query_emb, top_k=10)

        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True), "Scores should be descending"

    def test_search_top_k_limit(self, pipeline_artifacts):
        """Should return at most top_k results."""
        vs = pipeline_artifacts["vector_store"]
        embedder = pipeline_artifacts["embedder"]

        query_emb = embedder.embed_query("function")
        results = vs.search_similar(query_emb, top_k=3)
        assert len(results) <= 3

    def test_different_queries_different_results(self, pipeline_artifacts):
        """Different queries should return different top results."""
        vs = pipeline_artifacts["vector_store"]
        embedder = pipeline_artifacts["embedder"]

        emb1 = embedder.embed_query("parse tokens lexer")
        emb2 = embedder.embed_query("generate machine code output")

        r1 = vs.search_similar(emb1, top_k=3)
        r2 = vs.search_similar(emb2, top_k=3)

        qns1 = {r.qualified_name for r in r1}
        qns2 = {r.qualified_name for r in r2}
        # At least some results should differ
        assert qns1 != qns2, "Different queries should return different results"


# ---------------------------------------------------------------------------
# Cache reload
# ---------------------------------------------------------------------------


class TestCacheReload:
    """Verify embeddings can be loaded from cache."""

    def test_reload_matches_original(self, pipeline_artifacts):
        """Reloaded vector store should have same size as original."""
        from code_graph_builder.mcp.pipeline import build_vector_index

        vs_original = pipeline_artifacts["vector_store"]
        builder = pipeline_artifacts["builder"]
        vp = pipeline_artifacts["vectors_path"]

        # Load from cache (rebuild=False)
        vs_cached, _, fm_cached = build_vector_index(
            builder=builder,
            repo_path=TINYCC_PATH,
            vectors_path=vp,
            rebuild=False,
        )

        assert len(vs_cached) == len(vs_original), "Cached store size should match"
        assert len(fm_cached) == len(pipeline_artifacts["func_map"])

    def test_cached_search_works(self, pipeline_artifacts):
        """Semantic search on cached store should work."""
        vp = pipeline_artifacts["vectors_path"]
        embedder = pipeline_artifacts["embedder"]

        with open(vp, "rb") as f:
            cache = pickle.load(f)
        vs = cache["vector_store"]

        query_emb = embedder.embed_query("compile source file")
        results = vs.search_similar(query_emb, top_k=5)
        assert len(results) > 0, "Cached store search should return results"
