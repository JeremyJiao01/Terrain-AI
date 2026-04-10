"""Tests for OpenAIEmbedder - OpenAI-compatible embedding integration.

Matches current .env configuration:
    EMBED_API_KEY=...
    EMBED_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
    EMBED_MODEL=text-embedding-v4

create_embedder() detects EMBED_API_KEY and routes to OpenAIEmbedder.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

if TYPE_CHECKING:
    from collections.abc import Sequence

ENV_KEY = "EMBED_API_KEY"
ENV_BASE_URL = "EMBED_BASE_URL"
ENV_MODEL = "EMBED_MODEL"
CONFIGURED_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
CONFIGURED_MODEL = "text-embedding-v4"
EMBEDDING_DIM = 1536


def _make_openai_response(n: int = 1, dim: int = EMBEDDING_DIM) -> dict:
    """Build a minimal OpenAI-compatible embeddings response."""
    return {
        "data": [
            {"embedding": [0.1 * (i + 1)] * dim, "index": i}
            for i in range(n)
        ],
        "usage": {"prompt_tokens": n * 5, "total_tokens": n * 5},
    }


class TestOpenAIEmbedder:
    """Test suite for OpenAIEmbedder (the class activated by .env EMBED_API_KEY)."""

    @pytest.fixture
    def mock_env(self, monkeypatch):
        """Set up env vars matching .env configuration."""
        monkeypatch.setenv(ENV_KEY, "sk-test-embed-key")
        monkeypatch.setenv(ENV_BASE_URL, CONFIGURED_BASE_URL)
        monkeypatch.setenv(ENV_MODEL, CONFIGURED_MODEL)

    def test_embedder_initialization_with_env_var(self, mock_env):
        """OpenAIEmbedder reads EMBED_API_KEY / EMBED_BASE_URL / EMBED_MODEL."""
        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        embedder = OpenAIEmbedder()

        assert embedder.api_key == "sk-test-embed-key"
        assert embedder.base_url == CONFIGURED_BASE_URL
        assert embedder.model == CONFIGURED_MODEL

    def test_embedder_initialization_with_api_key_param(self):
        """OpenAIEmbedder accepts api_key as constructor parameter."""
        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        embedder = OpenAIEmbedder(api_key="sk-param-key")

        assert embedder.api_key == "sk-param-key"

    def test_embedder_initialization_missing_api_key(self, monkeypatch):
        """OpenAIEmbedder raises ValueError when no API key is available."""
        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        for key in ("EMBED_API_KEY", "EMBEDDING_API_KEY", "OPENAI_API_KEY", "LLM_API_KEY"):
            monkeypatch.delenv(key, raising=False)

        with pytest.raises(ValueError, match="API key required"):
            OpenAIEmbedder()

    def test_embed_code_makes_api_request(self, mock_env):
        """embed_code POSTs to {base_url}/embeddings."""
        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: _make_openai_response(1),
            )

            embedder = OpenAIEmbedder()
            result = embedder.embed_code("def test(): pass")

            mock_post.assert_called_once()
            url = mock_post.call_args[0][0]
            assert url.endswith("/embeddings")
            assert CONFIGURED_BASE_URL in url

            assert isinstance(result, list)
            assert len(result) == EMBEDDING_DIM

    def test_embed_code_payload_format(self, mock_env):
        """embed_code sends OpenAI-compatible payload: {model, input: [...]}."""
        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: _make_openai_response(1),
            )

            embedder = OpenAIEmbedder()
            embedder.embed_code("hello world")

            payload = mock_post.call_args[1]["json"]
            assert payload["model"] == CONFIGURED_MODEL
            assert isinstance(payload["input"], list)
            assert "hello world" in payload["input"]

    def test_embed_code_api_error(self, mock_env):
        """embed_code raises RuntimeError on non-200 status."""
        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=401,
                text="Unauthorized",
                json=MagicMock(side_effect=Exception("No JSON")),
            )

            embedder = OpenAIEmbedder()

            with pytest.raises(RuntimeError):
                embedder.embed_code("test")

    def test_embed_batch_makes_single_request(self, mock_env):
        """embed_batch batches texts into one API call when under batch_size."""
        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: _make_openai_response(3),
            )

            embedder = OpenAIEmbedder(batch_size=5)
            results = embedder.embed_batch(["code1", "code2", "code3"])

            assert len(results) == 3
            assert all(len(r) == EMBEDDING_DIM for r in results)
            mock_post.assert_called_once()

    def test_embed_batch_respects_batch_size(self, mock_env):
        """embed_batch splits large inputs into multiple API calls."""
        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        call_count = 0

        def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            texts = kwargs["json"]["input"]
            return MagicMock(
                status_code=200,
                json=lambda: {
                    "data": [
                        {"embedding": [0.1] * EMBEDDING_DIM, "index": i}
                        for i in range(len(texts))
                    ]
                },
            )

        with patch("requests.post", side_effect=mock_post):
            embedder = OpenAIEmbedder(batch_size=2)
            results = embedder.embed_batch(["c1", "c2", "c3", "c4", "c5"])

            assert len(results) == 5
            assert call_count == 3  # 2 + 2 + 1

    def test_embed_batch_empty_list(self, mock_env):
        """embed_batch returns [] for empty input without hitting the API."""
        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        embedder = OpenAIEmbedder()
        assert embedder.embed_batch([]) == []

    def test_embed_batch_api_failure(self, mock_env):
        """embed_batch propagates API errors."""
        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=500,
                text="Internal Server Error",
                json=MagicMock(side_effect=Exception("No JSON")),
            )

            embedder = OpenAIEmbedder()

            with pytest.raises(RuntimeError):
                embedder.embed_batch(["code1", "code2", "code3"])

    def test_rate_limit_retry(self, mock_env):
        """embed_code retries on HTTP 429 and succeeds on third attempt."""
        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        call_count = 0

        def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return MagicMock(status_code=429)
            return MagicMock(status_code=200, json=lambda: _make_openai_response(1))

        with patch("requests.post", side_effect=mock_post):
            with patch("time.sleep"):
                embedder = OpenAIEmbedder(max_retries=3)
                result = embedder.embed_code("test")

                assert len(result) == EMBEDDING_DIM
                assert call_count == 3

    def test_get_embedding_dimension(self, mock_env):
        """get_embedding_dimension returns 1536 for text-embedding-v4."""
        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        embedder = OpenAIEmbedder()
        assert embedder.get_embedding_dimension() == EMBEDDING_DIM

    def test_request_timeout_retry(self, mock_env):
        """embed_code retries on Timeout and succeeds on second attempt."""
        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder
        from requests.exceptions import Timeout

        call_count = 0

        def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Timeout("Connection timeout")
            return MagicMock(status_code=200, json=lambda: _make_openai_response(1))

        with patch("requests.post", side_effect=mock_post):
            embedder = OpenAIEmbedder(max_retries=3)
            result = embedder.embed_code("test")

            assert len(result) == EMBEDDING_DIM
            assert call_count == 2


class TestEmbedderConfiguration:
    """Test OpenAIEmbedder reads EMBED_MODEL and EMBED_BASE_URL from .env."""

    def test_default_model_from_env(self, monkeypatch):
        """EMBED_MODEL env var sets the model."""
        monkeypatch.setenv(ENV_KEY, "sk-test")
        monkeypatch.setenv(ENV_MODEL, CONFIGURED_MODEL)

        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        embedder = OpenAIEmbedder()
        assert embedder.model == CONFIGURED_MODEL

    def test_custom_model_param(self, monkeypatch):
        """Constructor model param overrides env var."""
        monkeypatch.setenv(ENV_KEY, "sk-test")

        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        embedder = OpenAIEmbedder(model="custom-model")
        assert embedder.model == "custom-model"

    def test_base_url_from_env(self, monkeypatch):
        """EMBED_BASE_URL env var sets the base URL."""
        monkeypatch.setenv(ENV_KEY, "sk-test")
        monkeypatch.setenv(ENV_BASE_URL, CONFIGURED_BASE_URL)

        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        embedder = OpenAIEmbedder()
        assert embedder.base_url == CONFIGURED_BASE_URL

    def test_known_model_dimension(self, monkeypatch):
        """text-embedding-v4 falls back to default 1536 dimension."""
        monkeypatch.setenv(ENV_KEY, "sk-test")
        monkeypatch.setenv(ENV_MODEL, CONFIGURED_MODEL)

        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        embedder = OpenAIEmbedder()
        assert embedder.get_embedding_dimension() == EMBEDDING_DIM


class TestDummyEmbedder:
    """Test suite for DummyEmbedder."""

    def test_embed_code_returns_zero_vector(self):
        """DummyEmbedder returns zero vector."""
        from terrain.domains.core.embedding.qwen3_embedder import DummyEmbedder

        embedder = DummyEmbedder(dimension=1536)
        result = embedder.embed_code("test")

        assert len(result) == 1536
        assert all(x == 0.0 for x in result)

    def test_embed_batch_returns_zero_vectors(self):
        """DummyEmbedder returns zero vectors for batch."""
        from terrain.domains.core.embedding.qwen3_embedder import DummyEmbedder

        embedder = DummyEmbedder(dimension=768)
        results = embedder.embed_batch(["a", "b", "c"])

        assert len(results) == 3
        assert all(len(r) == 768 and all(x == 0.0 for x in r) for r in results)


class TestCreateEmbedder:
    """Test create_embedder factory — mirrors .env auto-detection logic."""

    def test_create_embedder_with_dummy(self):
        """use_dummy=True always returns DummyEmbedder."""
        from terrain.domains.core.embedding.qwen3_embedder import (
            DummyEmbedder,
            create_embedder,
        )

        embedder = create_embedder(use_dummy=True)
        assert isinstance(embedder, DummyEmbedder)

    def test_create_embedder_with_embed_api_key(self, monkeypatch):
        """EMBED_API_KEY triggers OpenAIEmbedder (the .env default path)."""
        monkeypatch.setenv(ENV_KEY, "sk-embed-key")
        monkeypatch.setenv(ENV_BASE_URL, CONFIGURED_BASE_URL)
        monkeypatch.setenv(ENV_MODEL, CONFIGURED_MODEL)
        monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

        from terrain.domains.core.embedding.qwen3_embedder import (
            OpenAIEmbedder,
            create_embedder,
        )

        embedder = create_embedder()
        assert isinstance(embedder, OpenAIEmbedder)

    def test_create_embedder_passes_kwargs(self, monkeypatch):
        """Extra kwargs are forwarded to OpenAIEmbedder."""
        monkeypatch.setenv(ENV_KEY, "sk-test")
        monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

        from terrain.domains.core.embedding.qwen3_embedder import (
            OpenAIEmbedder,
            create_embedder,
        )

        embedder = create_embedder(batch_size=10, max_retries=5)
        assert isinstance(embedder, OpenAIEmbedder)
        assert embedder.batch_size == 10
        assert embedder.max_retries == 5

    def test_create_embedder_embed_api_key_takes_priority_over_dashscope(
        self, monkeypatch
    ):
        """EMBED_API_KEY takes priority over DASHSCOPE_API_KEY."""
        monkeypatch.setenv(ENV_KEY, "sk-embed")
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-dashscope")
        monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)

        from terrain.domains.core.embedding.qwen3_embedder import (
            OpenAIEmbedder,
            create_embedder,
        )

        embedder = create_embedder()
        assert isinstance(embedder, OpenAIEmbedder)
