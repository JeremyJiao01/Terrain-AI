"""Tests for Qwen3Embedder - API-based Qwen3 Embedding integration.

These tests verify the Qwen3Embedder class correctly:
1. Loads API configuration from environment variables
2. Makes HTTP requests to DashScope API
3. Handles API responses and errors
4. Implements batch processing with retry logic
5. Handles edge cases gracefully
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

if TYPE_CHECKING:
    from collections.abc import Sequence


class TestQwen3Embedder:
    """Test suite for Qwen3Embedder class (API mode)."""

    @pytest.fixture
    def mock_env(self, monkeypatch):
        """Set up mock environment variables."""
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test-key")
        monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://test.api.com")

    @pytest.fixture
    def sample_api_response(self):
        """Create a sample successful API response."""
        return {
            "output": {
                "embeddings": [
                    {"embedding": [0.1] * 1536, "text_index": 0}
                ]
            },
            "usage": {"total_tokens": 10}
        }

    @pytest.fixture
    def sample_batch_response(self):
        """Create a sample batch API response."""
        return {
            "output": {
                "embeddings": [
                    {"embedding": [0.1] * 1536, "text_index": 0},
                    {"embedding": [0.2] * 1536, "text_index": 1},
                    {"embedding": [0.3] * 1536, "text_index": 2},
                ]
            },
            "usage": {"total_tokens": 30}
        }

    def test_embedder_initialization_with_env_var(self, mock_env):
        """Test that Qwen3Embedder loads API key from environment."""
        from code_graph_builder.embeddings.qwen3_embedder import Qwen3Embedder

        embedder = Qwen3Embedder()

        assert embedder.api_key == "sk-test-key"
        assert embedder.base_url == "https://test.api.com"
        assert embedder.model == "text-embedding-v4"

    def test_embedder_initialization_with_api_key_param(self):
        """Test that Qwen3Embedder accepts API key as parameter."""
        from code_graph_builder.embeddings.qwen3_embedder import Qwen3Embedder

        embedder = Qwen3Embedder(api_key="sk-param-key")

        assert embedder.api_key == "sk-param-key"

    def test_embedder_initialization_missing_api_key(self):
        """Test that Qwen3Embedder raises error without API key."""
        from code_graph_builder.embeddings.qwen3_embedder import Qwen3Embedder

        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(ValueError, match="DashScope API key required"):
                Qwen3Embedder()

    def test_embed_code_makes_api_request(self, mock_env, sample_api_response):
        """Test embed_code makes HTTP POST request to API."""
        from code_graph_builder.embeddings.qwen3_embedder import Qwen3Embedder

        with patch("requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = sample_api_response
            mock_post.return_value = mock_response

            embedder = Qwen3Embedder()
            result = embedder.embed_code("def test(): pass")

            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert "embeddings/text-embedding/text-embedding" in call_args[0][0]

            assert isinstance(result, list)
            assert len(result) == 1536

    def test_embed_code_with_instruction(self, mock_env, sample_api_response):
        """Test embed_code adds instruction when requested."""
        from code_graph_builder.embeddings.qwen3_embedder import Qwen3Embedder

        with patch("requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = sample_api_response
            mock_post.return_value = mock_response

            embedder = Qwen3Embedder()
            embedder.embed_code("test query", use_instruction=True)

            call_kwargs = mock_post.call_args[1]
            payload = call_kwargs["json"]
            text = payload["input"]["texts"][0]
            assert "Instruct:" in text
            assert "Query:" in text

    def test_embed_code_api_error(self, mock_env):
        """Test embed_code handles API errors gracefully."""
        from code_graph_builder.embeddings.qwen3_embedder import Qwen3Embedder

        with patch("requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_response.text = "Unauthorized"
            mock_response.json.side_effect = Exception("No JSON")
            mock_post.return_value = mock_response

            embedder = Qwen3Embedder()

            with pytest.raises(RuntimeError, match="API request failed"):
                embedder.embed_code("test")

    def test_embed_batch_makes_single_request(self, mock_env, sample_batch_response):
        """Test embed_batch makes API request for multiple texts."""
        from code_graph_builder.embeddings.qwen3_embedder import Qwen3Embedder

        with patch("requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = sample_batch_response
            mock_post.return_value = mock_response

            embedder = Qwen3Embedder(batch_size=5)
            texts = ["code1", "code2", "code3"]
            results = embedder.embed_batch(texts)

            assert len(results) == 3
            assert all(len(r) == 1536 for r in results)
            mock_post.assert_called_once()

    def test_embed_batch_respects_batch_size(self, mock_env):
        """Test embed_batch splits large batches correctly."""
        from code_graph_builder.embeddings.qwen3_embedder import Qwen3Embedder

        call_count = 0

        def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_response = MagicMock()
            mock_response.status_code = 200
            # Return embeddings for each text in the batch
            texts = kwargs["json"]["input"]["texts"]
            mock_response.json.return_value = {
                "output": {
                    "embeddings": [
                        {"embedding": [0.1] * 1536, "text_index": i}
                        for i in range(len(texts))
                    ]
                }
            }
            return mock_response

        with patch("requests.post", side_effect=mock_post):
            embedder = Qwen3Embedder(batch_size=2)
            texts = ["code1", "code2", "code3", "code4", "code5"]
            results = embedder.embed_batch(texts)

            assert len(results) == 5
            # Should make 3 calls: 2+2+1
            assert call_count == 3

    def test_embed_batch_empty_list(self, mock_env):
        """Test embed_batch handles empty list."""
        from code_graph_builder.embeddings.qwen3_embedder import Qwen3Embedder

        embedder = Qwen3Embedder()
        results = embedder.embed_batch([])

        assert results == []

    def test_embed_batch_api_failure(self, mock_env):
        """Test embed_batch handles API failure."""
        from code_graph_builder.embeddings.qwen3_embedder import Qwen3Embedder

        with patch("requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 500
            mock_response.text = "Internal Server Error"
            mock_response.json.side_effect = Exception("No JSON")
            mock_post.return_value = mock_response

            embedder = Qwen3Embedder()
            texts = ["code1", "code2", "code3"]

            with pytest.raises(RuntimeError):
                embedder.embed_batch(texts)

    def test_rate_limit_retry(self, mock_env, sample_api_response):
        """Test embed_code retries on rate limit (429)."""
        from code_graph_builder.embeddings.qwen3_embedder import Qwen3Embedder

        call_count = 0

        def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_response = MagicMock()

            if call_count < 3:
                mock_response.status_code = 429
            else:
                mock_response.status_code = 200
                mock_response.json.return_value = sample_api_response

            return mock_response

        with patch("requests.post", side_effect=mock_post):
            with patch("time.sleep") as mock_sleep:  # Don't actually sleep
                embedder = Qwen3Embedder(max_retries=3)
                result = embedder.embed_code("test")

                assert len(result) == 1536
                assert call_count == 3
                mock_sleep.assert_called()  # Should have waited between retries

    def test_get_embedding_dimension(self, mock_env):
        """Test get_embedding_dimension returns correct value."""
        from code_graph_builder.embeddings.qwen3_embedder import Qwen3Embedder

        embedder = Qwen3Embedder()
        dimension = embedder.get_embedding_dimension()

        assert dimension == 1536  # text-embedding-v4 dimension

    def test_health_check_success(self, mock_env, sample_api_response):
        """Test health_check returns True when API is accessible."""
        from code_graph_builder.embeddings.qwen3_embedder import Qwen3Embedder

        with patch("requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = sample_api_response
            mock_post.return_value = mock_response

            embedder = Qwen3Embedder()
            result = embedder.health_check()

            assert result is True

    def test_health_check_failure(self, mock_env):
        """Test health_check returns False when API fails."""
        from code_graph_builder.embeddings.qwen3_embedder import Qwen3Embedder

        with patch("requests.post") as mock_post:
            mock_response = MagicMock()
            mock_response.status_code = 401
            mock_post.return_value = mock_response

            embedder = Qwen3Embedder()
            result = embedder.health_check()

            assert result is False

    def test_request_timeout_retry(self, mock_env, sample_api_response):
        """Test request timeout triggers retry."""
        from code_graph_builder.embeddings.qwen3_embedder import Qwen3Embedder
        from requests.exceptions import Timeout

        call_count = 0

        def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Timeout("Connection timeout")

            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = sample_api_response
            return mock_response

        with patch("requests.post", side_effect=mock_post):
            embedder = Qwen3Embedder(max_retries=3)
            result = embedder.embed_code("test")

            assert len(result) == 1536
            assert call_count == 2


class TestEmbedderConfiguration:
    """Test suite for Qwen3Embedder configuration."""

    def test_default_model(self, monkeypatch):
        """Test default model is text-embedding-v4."""
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")

        from code_graph_builder.embeddings.qwen3_embedder import Qwen3Embedder

        embedder = Qwen3Embedder()
        assert embedder.model == "text-embedding-v4"

    def test_custom_model(self, monkeypatch):
        """Test custom model can be specified."""
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")

        from code_graph_builder.embeddings.qwen3_embedder import Qwen3Embedder

        embedder = Qwen3Embedder(model="custom-model")
        assert embedder.model == "custom-model"

    def test_batch_size_limit(self, monkeypatch):
        """Test batch size is capped at MAX_BATCH_SIZE."""
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")

        from code_graph_builder.embeddings.qwen3_embedder import Qwen3Embedder

        embedder = Qwen3Embedder(batch_size=100)
        assert embedder.batch_size == 25  # Capped at MAX_BATCH_SIZE

    def test_api_key_format_warning(self, monkeypatch):
        """Test warning for invalid API key format."""
        monkeypatch.setenv("DASHSCOPE_API_KEY", "invalid-key")

        from code_graph_builder.embeddings.qwen3_embedder import Qwen3Embedder

        # Should not raise, but log warning
        embedder = Qwen3Embedder()
        assert embedder.api_key == "invalid-key"


class TestDummyEmbedder:
    """Test suite for DummyEmbedder."""

    def test_embed_code_returns_zero_vector(self):
        """Test DummyEmbedder returns zero vector."""
        from code_graph_builder.embeddings.qwen3_embedder import DummyEmbedder

        embedder = DummyEmbedder(dimension=1536)
        result = embedder.embed_code("test")

        assert len(result) == 1536
        assert all(x == 0.0 for x in result)

    def test_embed_batch_returns_zero_vectors(self):
        """Test DummyEmbedder returns zero vectors for batch."""
        from code_graph_builder.embeddings.qwen3_embedder import DummyEmbedder

        embedder = DummyEmbedder(dimension=768)
        results = embedder.embed_batch(["a", "b", "c"])

        assert len(results) == 3
        assert all(len(r) == 768 and all(x == 0.0 for x in r) for r in results)


class TestCreateEmbedder:
    """Test suite for create_embedder factory function."""

    def test_create_embedder_with_dummy(self):
        """Test factory creates DummyEmbedder when requested."""
        from code_graph_builder.embeddings.qwen3_embedder import (
            DummyEmbedder,
            create_embedder,
        )

        embedder = create_embedder(use_dummy=True)

        assert isinstance(embedder, DummyEmbedder)

    def test_create_embedder_with_api_key(self, monkeypatch):
        """Test factory creates Qwen3Embedder with API key."""
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-factory")

        from code_graph_builder.embeddings.qwen3_embedder import (
            Qwen3Embedder,
            create_embedder,
        )

        embedder = create_embedder()

        assert isinstance(embedder, Qwen3Embedder)

    def test_create_embedder_passes_kwargs(self, monkeypatch):
        """Test factory passes kwargs to embedder."""
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")

        from code_graph_builder.embeddings.qwen3_embedder import (
            Qwen3Embedder,
            create_embedder,
        )

        embedder = create_embedder(batch_size=10, max_retries=5)

        assert isinstance(embedder, Qwen3Embedder)
        assert embedder.batch_size == 10
        assert embedder.max_retries == 5
