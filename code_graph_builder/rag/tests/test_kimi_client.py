"""Tests for Kimi client."""

from __future__ import annotations

import json
from unittest.mock import Mock, patch

import pytest

from code_graph_builder.rag.kimi_client import (
    ChatResponse,
    KimiClient,
    create_kimi_client,
)


class TestChatResponse:
    """Tests for ChatResponse dataclass."""

    def test_creation(self):
        """Test basic creation."""
        response = ChatResponse(
            content="Test response",
            usage={"prompt_tokens": 10, "completion_tokens": 20},
            model="kimi-k2.5",
            finish_reason="stop",
        )
        assert response.content == "Test response"
        assert response.usage["prompt_tokens"] == 10
        assert response.model == "kimi-k2.5"


class TestKimiClient:
    """Tests for KimiClient."""

    def test_default_init(self):
        """Test default initialization."""
        client = KimiClient(api_key="sk-test")
        assert client.api_key == "sk-test"
        assert client.model == "kimi-k2.5"
        assert client.base_url == "https://api.moonshot.cn/v1"
        assert client.max_tokens == 4096

    def test_custom_init(self):
        """Test custom initialization."""
        client = KimiClient(
            api_key="sk-test",
            model="custom-model",
            base_url="https://custom.api.com/",
            max_tokens=2048,
            temperature=0.5,
            timeout=60,
        )
        assert client.model == "custom-model"
        assert client.base_url == "https://custom.api.com"  # trailing slash removed
        assert client.max_tokens == 2048
        assert client.temperature == 0.5
        assert client.timeout == 60

    def test_init_missing_api_key(self):
        """Test initialization fails without API key."""
        with pytest.raises(ValueError, match="API key is required"):
            KimiClient(api_key=None)

    def test_get_headers(self):
        """Test getting headers."""
        client = KimiClient(api_key="sk-test")
        headers = client._get_headers()
        assert headers["Authorization"] == "Bearer sk-test"
        assert headers["Content-Type"] == "application/json"

    @patch("code_graph_builder.rag.kimi_client.requests.post")
    def test_chat_success(self, mock_post):
        """Test successful chat completion."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {"content": "Test response"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            "model": "kimi-k2.5",
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        client = KimiClient(api_key="sk-test")
        response = client.chat("Hello")

        assert response.content == "Test response"
        assert response.model == "kimi-k2.5"
        mock_post.assert_called_once()

    @patch("code_graph_builder.rag.kimi_client.requests.post")
    def test_chat_with_context(self, mock_post):
        """Test chat with context."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {"content": "Response"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {},
            "model": "kimi-k2.5",
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        client = KimiClient(api_key="sk-test")
        response = client.chat(
            query="Explain",
            context="def foo(): pass",
            system_prompt="You are helpful.",
        )

        assert response.content == "Response"
        # Check that context was included in the call
        call_args = mock_post.call_args
        json_data = call_args.kwargs["json"]
        assert any("Context:" in msg["content"] for msg in json_data["messages"])

    @patch("code_graph_builder.rag.kimi_client.requests.post")
    def test_chat_http_error(self, mock_post):
        """Test chat with HTTP error."""
        from requests.exceptions import HTTPError

        mock_response = Mock()
        mock_response.json.return_value = {
            "error": {"message": "Invalid API key"}
        }
        mock_response.raise_for_status.side_effect = HTTPError(
            "401 Client Error",
            response=mock_response,
        )
        mock_post.return_value = mock_response

        client = KimiClient(api_key="sk-test")
        with pytest.raises(RuntimeError, match="API request failed"):
            client.chat("Hello")

    @patch("code_graph_builder.rag.kimi_client.requests.post")
    def test_chat_timeout(self, mock_post):
        """Test chat with timeout."""
        from requests.exceptions import Timeout

        mock_post.side_effect = Timeout("Request timed out")

        client = KimiClient(api_key="sk-test", timeout=5)
        with pytest.raises(RuntimeError, match="timeout"):
            client.chat("Hello")

    @patch("code_graph_builder.rag.kimi_client.requests.post")
    def test_chat_with_messages(self, mock_post):
        """Test chat with raw messages."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {"content": "Response"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {},
            "model": "kimi-k2.5",
        }
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response

        client = KimiClient(api_key="sk-test")
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        response = client.chat_with_messages(messages)

        assert response.content == "Response"
        call_args = mock_post.call_args
        json_data = call_args.kwargs["json"]
        assert json_data["messages"] == messages

    @patch("code_graph_builder.rag.kimi_client.requests.get")
    def test_health_check_success(self, mock_get):
        """Test successful health check."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_get.return_value = mock_response

        client = KimiClient(api_key="sk-test")
        assert client.health_check() is True

    @patch("code_graph_builder.rag.kimi_client.requests.get")
    def test_health_check_failure(self, mock_get):
        """Test failed health check."""
        mock_get.side_effect = Exception("Connection error")

        client = KimiClient(api_key="sk-test")
        assert client.health_check() is False


class TestCreateKimiClient:
    """Tests for create_kimi_client factory function."""

    def test_create_with_defaults(self):
        """Test creating client with defaults."""
        client = create_kimi_client(api_key="sk-test")
        assert isinstance(client, KimiClient)
        assert client.model == "kimi-k2.5"

    def test_create_with_custom_model(self):
        """Test creating client with custom model."""
        client = create_kimi_client(
            api_key="sk-test",
            model="custom-model",
            max_tokens=2048,
        )
        assert client.model == "custom-model"
        assert client.max_tokens == 2048
