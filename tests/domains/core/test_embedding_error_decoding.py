"""Tests for embedding API error message decoding.

Covers the failure modes uncovered in JER-111: when an embedding provider
returns an error body that contains Chinese (or any non-ASCII) text, the
user-visible exception message must preserve the original text and must NOT
contain the literal substring "NoneType" (which was the tell-tale sign of
the bugs being fixed).

Scenarios covered:
    1. 4xx JSON body shaped ``{"error": null, "message": "中文报错"}``
    2. 4xx JSON body shaped ``{"code": "X", "msg": "中文报错"}``
    3. 4xx JSON body shaped ``{"error": {"message": "中文报错"}}``
    4. 4xx JSON body shaped ``{"error": "中文字符串"}`` (error is a string)
    5. HTTP 200 but body ``{"data": null, "message": "中文限流"}``
    6. 4xx non-JSON body with UTF-8 Chinese bytes and no charset header
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest


CHINESE_ERROR = "输入参数不合法，超过最大长度限制"
CHINESE_RATE_LIMIT = "服务繁忙，请稍后重试"


def _make_response(
    status_code: int,
    json_body: Any = None,
    text_bytes: bytes | None = None,
    encoding: str | None = None,
    apparent_encoding: str | None = None,
) -> MagicMock:
    """Build a mock ``requests.Response``.

    If ``json_body`` is provided, ``response.json()`` returns it. Otherwise
    ``response.json()`` raises ValueError (simulating non-JSON body).
    """
    mock = MagicMock()
    mock.status_code = status_code

    if json_body is not None:
        mock.json = lambda: json_body
        # response.text still needs to work as a fallback path
        if text_bytes is not None:
            mock.text = text_bytes.decode(encoding or "utf-8", errors="replace")
        else:
            import json as _json
            mock.text = _json.dumps(json_body, ensure_ascii=False)
    else:
        mock.json = MagicMock(side_effect=ValueError("not JSON"))
        if text_bytes is not None:
            # Simulate requests behavior: response.text uses response.encoding
            # to decode response.content. If encoding is wrong (e.g. ISO-8859-1
            # when bytes are UTF-8), text will be mojibake.
            mock.content = text_bytes
            mock.encoding = encoding
            mock.apparent_encoding = apparent_encoding or "utf-8"
            # Use PropertyMock-like behavior via a property on the mock
            def _get_text():
                enc = mock.encoding or "ISO-8859-1"
                return text_bytes.decode(enc, errors="replace")
            # property doesn't work on MagicMock directly, so we compute on read
            type(mock).text = property(lambda self: _get_text())
        else:
            mock.text = ""

    return mock


@pytest.fixture
def embed_env(monkeypatch):
    monkeypatch.setenv("EMBED_API_KEY", "sk-test")
    monkeypatch.setenv("EMBED_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("EMBED_MODEL", "text-embedding-v4")


@pytest.fixture
def dashscope_env(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://example.invalid/v1")


class TestOpenAIEmbedderErrorDecoding:
    """OpenAIEmbedder must surface Chinese error messages, not 'NoneType'."""

    def test_error_null_with_top_level_message(self, embed_env):
        """``{"error": null, "message": "中文"}`` — ``error`` is None, must not crash."""
        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        body = {"error": None, "message": CHINESE_ERROR}
        with patch("requests.post") as mock_post:
            mock_post.return_value = _make_response(400, json_body=body)
            embedder = OpenAIEmbedder(max_retries=1)
            with pytest.raises(RuntimeError) as exc_info:
                embedder.embed_code("x")

        msg = str(exc_info.value)
        assert CHINESE_ERROR in msg
        assert "NoneType" not in msg

    def test_error_string_top_level(self, embed_env):
        """``{"error": "中文字符串"}`` — error is str, not dict."""
        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        body = {"error": CHINESE_ERROR}
        with patch("requests.post") as mock_post:
            mock_post.return_value = _make_response(400, json_body=body)
            embedder = OpenAIEmbedder(max_retries=1)
            with pytest.raises(RuntimeError) as exc_info:
                embedder.embed_code("x")

        msg = str(exc_info.value)
        assert CHINESE_ERROR in msg
        assert "NoneType" not in msg

    def test_code_msg_structure(self, embed_env):
        """``{"code": "...", "msg": "中文"}`` — aliyun-style shape."""
        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        body = {"code": "InvalidParameter", "msg": CHINESE_ERROR}
        with patch("requests.post") as mock_post:
            mock_post.return_value = _make_response(400, json_body=body)
            embedder = OpenAIEmbedder(max_retries=1)
            with pytest.raises(RuntimeError) as exc_info:
                embedder.embed_code("x")

        msg = str(exc_info.value)
        assert CHINESE_ERROR in msg
        assert "NoneType" not in msg

    def test_nested_error_message(self, embed_env):
        """``{"error": {"message": "中文"}}`` — standard OpenAI shape (happy path)."""
        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        body = {"error": {"message": CHINESE_ERROR, "type": "invalid_request"}}
        with patch("requests.post") as mock_post:
            mock_post.return_value = _make_response(400, json_body=body)
            embedder = OpenAIEmbedder(max_retries=1)
            with pytest.raises(RuntimeError) as exc_info:
                embedder.embed_code("x")

        msg = str(exc_info.value)
        assert CHINESE_ERROR in msg
        assert "NoneType" not in msg

    def test_http_200_body_data_null(self, embed_env):
        """HTTP 200 with ``{"data": null, "message": "中文"}`` — must not crash
        with ``'NoneType' object is not iterable``."""
        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        body = {"data": None, "message": CHINESE_RATE_LIMIT}
        with patch("requests.post") as mock_post:
            mock_post.return_value = _make_response(200, json_body=body)
            embedder = OpenAIEmbedder(max_retries=1)
            with pytest.raises(RuntimeError) as exc_info:
                embedder.embed_code("x")

        msg = str(exc_info.value)
        assert CHINESE_RATE_LIMIT in msg
        assert "NoneType" not in msg

    def test_non_json_utf8_body(self, embed_env):
        """Non-JSON body with UTF-8 Chinese bytes, no charset header — must decode correctly."""
        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        text_bytes = CHINESE_ERROR.encode("utf-8")
        with patch("requests.post") as mock_post:
            mock_post.return_value = _make_response(
                400,
                json_body=None,
                text_bytes=text_bytes,
                encoding="ISO-8859-1",  # wrong encoding (requests default)
                apparent_encoding="utf-8",
            )
            embedder = OpenAIEmbedder(max_retries=1)
            with pytest.raises(RuntimeError) as exc_info:
                embedder.embed_code("x")

        msg = str(exc_info.value)
        assert CHINESE_ERROR in msg
        assert "NoneType" not in msg


class TestQwen3EmbedderErrorDecoding:
    """Qwen3Embedder must surface Chinese error messages, not 'NoneType'."""

    def test_error_null_with_top_level_message(self, dashscope_env):
        from terrain.domains.core.embedding.qwen3_embedder import Qwen3Embedder

        body = {"error": None, "message": CHINESE_ERROR}
        with patch("requests.post") as mock_post:
            mock_post.return_value = _make_response(400, json_body=body)
            embedder = Qwen3Embedder(max_retries=1)
            with pytest.raises(RuntimeError) as exc_info:
                embedder.embed_code("x")

        msg = str(exc_info.value)
        assert CHINESE_ERROR in msg
        assert "NoneType" not in msg

    def test_code_msg_structure(self, dashscope_env):
        from terrain.domains.core.embedding.qwen3_embedder import Qwen3Embedder

        body = {"code": "InvalidParameter", "msg": CHINESE_ERROR}
        with patch("requests.post") as mock_post:
            mock_post.return_value = _make_response(400, json_body=body)
            embedder = Qwen3Embedder(max_retries=1)
            with pytest.raises(RuntimeError) as exc_info:
                embedder.embed_code("x")

        msg = str(exc_info.value)
        assert CHINESE_ERROR in msg
        assert "NoneType" not in msg

    def test_nested_error_message(self, dashscope_env):
        from terrain.domains.core.embedding.qwen3_embedder import Qwen3Embedder

        body = {"error": {"message": CHINESE_ERROR}}
        with patch("requests.post") as mock_post:
            mock_post.return_value = _make_response(400, json_body=body)
            embedder = Qwen3Embedder(max_retries=1)
            with pytest.raises(RuntimeError) as exc_info:
                embedder.embed_code("x")

        msg = str(exc_info.value)
        assert CHINESE_ERROR in msg
        assert "NoneType" not in msg

    def test_http_200_body_data_null(self, dashscope_env):
        from terrain.domains.core.embedding.qwen3_embedder import Qwen3Embedder

        body = {"data": None, "message": CHINESE_RATE_LIMIT}
        with patch("requests.post") as mock_post:
            mock_post.return_value = _make_response(200, json_body=body)
            embedder = Qwen3Embedder(max_retries=1)
            with pytest.raises(RuntimeError) as exc_info:
                embedder.embed_code("x")

        msg = str(exc_info.value)
        assert CHINESE_RATE_LIMIT in msg
        assert "NoneType" not in msg


class TestEmbedBatchErrorPropagation:
    """embed_batch must propagate the Chinese error text from the underlying call."""

    def test_batch_preserves_chinese_error(self, embed_env):
        """When a batch fails with a Chinese error, the wrapped RuntimeError must include it."""
        from terrain.domains.core.embedding.qwen3_embedder import OpenAIEmbedder

        body = {"error": None, "message": CHINESE_ERROR}
        with patch("requests.post") as mock_post:
            mock_post.return_value = _make_response(400, json_body=body)
            embedder = OpenAIEmbedder(max_retries=1, batch_size=5)
            with pytest.raises(RuntimeError) as exc_info:
                embedder.embed_batch(["a", "b", "c"])

        msg = str(exc_info.value)
        assert CHINESE_ERROR in msg
        assert "NoneType" not in msg
