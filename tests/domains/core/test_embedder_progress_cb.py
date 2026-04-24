"""Tests for embedder retry/backoff progress_cb callback (JER-116).

Verify that Qwen3Embedder / OpenAIEmbedder surface 429 / timeout / 5xx retry
state via an optional ``progress_cb`` callback so the CLI progress bar can
reflect "rate limited, retry in Ns (i/N)" instead of looking stuck.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from requests.exceptions import ConnectionError as ReqConnectionError
from requests.exceptions import Timeout


def _ok_response(n: int = 1, dim: int = 1536) -> MagicMock:
    return MagicMock(
        status_code=200,
        json=lambda: {
            "data": [{"embedding": [0.1] * dim, "index": i} for i in range(n)]
        },
    )


class TestOpenAIEmbedderProgressCb:
    """progress_cb should be called on 429 / timeout / 5xx before each retry."""

    @pytest.fixture
    def env(self, monkeypatch):
        monkeypatch.setenv("EMBED_API_KEY", "sk-test")

    def test_progress_cb_called_on_429(self, env):
        """On HTTP 429, progress_cb receives a 'rate limited' message before sleep."""
        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        calls: list[str] = []

        def cb(msg: str) -> None:
            calls.append(msg)

        call_count = 0

        def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return MagicMock(status_code=429)
            return _ok_response(1)

        with patch("requests.post", side_effect=mock_post), patch("time.sleep"):
            embedder = OpenAIEmbedder(max_retries=3)
            embedder.embed_batch(["code1"], progress_cb=cb)

        assert len(calls) >= 2, f"expected at least 2 retry callbacks, got {calls}"
        assert all("rate limited" in c.lower() for c in calls)
        # ASCII-only — no emoji, no non-ASCII characters
        for msg in calls:
            msg.encode("ascii")  # raises on non-ASCII
        # Message carries retry counter and wait seconds
        assert any("(1/3)" in c for c in calls)
        assert any("s " in c or c.endswith("s)") or "in 1s" in c or "in 2s" in c for c in calls)

    def test_progress_cb_called_on_timeout(self, env):
        """On Timeout, progress_cb is called before each retry."""
        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        calls: list[str] = []
        call_count = 0

        def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Timeout("boom")
            return _ok_response(1)

        with patch("requests.post", side_effect=mock_post):
            embedder = OpenAIEmbedder(max_retries=3)
            embedder.embed_batch(["code1"], progress_cb=calls.append)

        assert len(calls) == 1
        assert "timeout" in calls[0].lower()
        calls[0].encode("ascii")

    def test_progress_cb_called_on_5xx(self, env):
        """On 5xx, progress_cb is called before each retry."""
        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        calls: list[str] = []
        call_count = 0

        def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                return MagicMock(
                    status_code=503,
                    json=MagicMock(side_effect=ValueError()),
                    text="service unavailable",
                    content=b"service unavailable",
                    apparent_encoding="utf-8",
                    encoding="utf-8",
                )
            return _ok_response(1)

        with patch("requests.post", side_effect=mock_post):
            embedder = OpenAIEmbedder(max_retries=3)
            embedder.embed_batch(["code1"], progress_cb=calls.append)

        assert len(calls) == 1
        # Must include status code 503 so user can distinguish 429 vs 5xx
        assert "503" in calls[0]
        calls[0].encode("ascii")

    def test_progress_cb_called_on_connection_error(self, env):
        """Network RequestException triggers a retry callback."""
        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        calls: list[str] = []
        call_count = 0

        def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ReqConnectionError("boom")
            return _ok_response(1)

        with patch("requests.post", side_effect=mock_post):
            embedder = OpenAIEmbedder(max_retries=3)
            embedder.embed_batch(["code1"], progress_cb=calls.append)

        assert len(calls) == 1
        calls[0].encode("ascii")

    def test_no_progress_cb_unchanged_behavior(self, env):
        """Omitting progress_cb keeps existing behaviour (backward-compatible)."""
        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        call_count = 0

        def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                return MagicMock(status_code=429)
            return _ok_response(1)

        with patch("requests.post", side_effect=mock_post), patch("time.sleep"):
            embedder = OpenAIEmbedder(max_retries=3)
            result = embedder.embed_batch(["code1"])

        assert len(result) == 1

    def test_progress_cb_not_called_on_success(self, env):
        """No retries, no progress_cb invocations."""
        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        calls: list[str] = []
        with patch("requests.post", return_value=_ok_response(1)):
            embedder = OpenAIEmbedder(max_retries=3)
            embedder.embed_batch(["code1"], progress_cb=calls.append)

        assert calls == []


class TestQwen3EmbedderProgressCb:
    """Same contract for Qwen3Embedder._make_request."""

    @pytest.fixture
    def env(self, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")

    def test_progress_cb_called_on_429(self, env):
        from terrain.domains.core.embedding.qwen3_embedder import Qwen3Embedder

        calls: list[str] = []
        call_count = 0

        def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                return MagicMock(status_code=429)
            return MagicMock(
                status_code=200,
                json=lambda: {
                    "data": [{"embedding": [0.0] * 2560, "index": 0}]
                },
            )

        with patch("requests.post", side_effect=mock_post), patch("time.sleep"):
            embedder = Qwen3Embedder(max_retries=3)
            embedder.embed_batch(["code1"], progress_cb=calls.append)

        assert len(calls) == 1
        assert "rate limited" in calls[0].lower()
        assert "(1/3)" in calls[0]
        calls[0].encode("ascii")

    def test_progress_cb_called_on_timeout(self, env):
        from terrain.domains.core.embedding.qwen3_embedder import Qwen3Embedder

        calls: list[str] = []
        call_count = 0

        def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Timeout("boom")
            return MagicMock(
                status_code=200,
                json=lambda: {"data": [{"embedding": [0.0] * 2560, "index": 0}]},
            )

        with patch("requests.post", side_effect=mock_post):
            embedder = Qwen3Embedder(max_retries=3)
            embedder.embed_batch(["code1"], progress_cb=calls.append)

        assert len(calls) == 1
        assert "timeout" in calls[0].lower()

    def test_no_progress_cb_unchanged_behavior(self, env):
        from terrain.domains.core.embedding.qwen3_embedder import Qwen3Embedder

        call_count = 0

        def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                return MagicMock(status_code=429)
            return MagicMock(
                status_code=200,
                json=lambda: {"data": [{"embedding": [0.0] * 2560, "index": 0}]},
            )

        with patch("requests.post", side_effect=mock_post), patch("time.sleep"):
            embedder = Qwen3Embedder(max_retries=3)
            result = embedder.embed_batch(["code1"])

        assert len(result) == 1


class TestDummyEmbedderProgressCbBackwardCompat:
    """DummyEmbedder keeps embed_batch contract (accepts progress_cb without using it)."""

    def test_dummy_embedder_accepts_progress_cb(self):
        from terrain.domains.core.embedding.qwen3_embedder import DummyEmbedder

        calls: list[str] = []
        embedder = DummyEmbedder(dimension=8)
        result = embedder.embed_batch(["a", "b"], progress_cb=calls.append)

        assert len(result) == 2
        assert calls == []
