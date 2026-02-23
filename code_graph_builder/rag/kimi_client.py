"""Kimi k2.5 API client for RAG.

This module provides a client for interacting with Moonshot's Kimi k2.5 model
via the OpenAI-compatible API.

Examples:
    >>> from code_graph_builder.rag.kimi_client import KimiClient
    >>> client = KimiClient(api_key="sk-xxxxx")
    >>> response = client.chat("Explain this code", context="def foo(): pass")
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests
from loguru import logger


@dataclass
class ChatResponse:
    """Response from chat completion.

    Attributes:
        content: Generated text content
        usage: Token usage information
        model: Model used for generation
        finish_reason: Reason for completion finish
    """

    content: str
    usage: dict[str, int]
    model: str
    finish_reason: str


class KimiClient:
    """Client for Kimi k2.5 API.

    Provides a simple interface for chat completions with the Kimi k2.5 model.

    Args:
        api_key: Moonshot API key
        model: Model name (default: kimi-k2.5)
        base_url: API base URL
        max_tokens: Maximum tokens for generation
        temperature: Sampling temperature
        timeout: Request timeout in seconds

    Examples:
        >>> client = KimiClient(api_key="sk-xxxxx")
        >>> response = client.chat(
        ...     query="What does this function do?",
        ...     context="def add(a, b): return a + b"
        ... )
        >>> print(response.content)
    """

    DEFAULT_MODEL = "kimi-k2.5"
    DEFAULT_BASE_URL = "https://api.moonshot.cn/v1"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        timeout: int = 300,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout

        if not self.api_key:
            raise ValueError(
                "Moonshot API key is required. "
                "Set MOONSHOT_API_KEY environment variable or pass api_key."
            )

        logger.info(f"Initialized KimiClient with model: {self.model}")

    def _get_headers(self) -> dict[str, str]:
        """Get API request headers."""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def chat(
        self,
        query: str,
        context: str | None = None,
        system_prompt: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ChatResponse:
        """Send a chat completion request.

        Args:
            query: User query
            context: Optional context to include
            system_prompt: Optional system prompt
            max_tokens: Override max tokens
            temperature: Override temperature

        Returns:
            ChatResponse with generated content

        Raises:
            RuntimeError: If API request fails
        """
        messages = []

        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        if context:
            content = f"Context:\n{context}\n\nQuery: {query}"
        else:
            content = query

        messages.append({"role": "user", "content": content})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": temperature or self.temperature,
        }

        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=self._get_headers(),
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()

            choice = data["choices"][0]
            return ChatResponse(
                content=choice["message"]["content"],
                usage=data.get("usage", {}),
                model=data.get("model", self.model),
                finish_reason=choice.get("finish_reason", "unknown"),
            )

        except requests.exceptions.HTTPError as e:
            logger.error(f"HTTP error: {e}")
            try:
                error_data = e.response.json() if e.response else {}
                error_msg = error_data.get("error", {}).get("message", str(e))
            except Exception:
                error_msg = str(e)
            raise RuntimeError(f"API request failed: {error_msg}")

        except requests.exceptions.Timeout:
            logger.error("Request timeout")
            raise RuntimeError(f"API request timeout after {self.timeout}s")

        except Exception as e:
            logger.error(f"Request failed: {e}")
            raise RuntimeError(f"API request failed: {e}")

    def chat_with_messages(
        self,
        messages: list[dict[str, str]],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> ChatResponse:
        """Send a chat completion request with raw messages.

        Args:
            messages: List of message dicts with 'role' and 'content'
            max_tokens: Override max tokens
            temperature: Override temperature

        Returns:
            ChatResponse with generated content
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": temperature or self.temperature,
        }

        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=self._get_headers(),
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()

            choice = data["choices"][0]
            return ChatResponse(
                content=choice["message"]["content"],
                usage=data.get("usage", {}),
                model=data.get("model", self.model),
                finish_reason=choice.get("finish_reason", "unknown"),
            )

        except Exception as e:
            logger.error(f"Request failed: {e}")
            raise RuntimeError(f"API request failed: {e}")

    def health_check(self) -> bool:
        """Check if API is accessible.

        Returns:
            True if healthy, False otherwise
        """
        try:
            response = requests.get(
                f"{self.base_url}/models",
                headers=self._get_headers(),
                timeout=10,
            )
            return response.status_code == 200
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False


def create_kimi_client(
    api_key: str | None = None,
    model: str = "kimi-k2.5",
    **kwargs: Any,
) -> KimiClient:
    """Factory function to create KimiClient.

    Args:
        api_key: Moonshot API key (or from MOONSHOT_API_KEY env var)
        model: Model name
        **kwargs: Additional arguments for KimiClient

    Returns:
        Configured KimiClient
    """
    return KimiClient(api_key=api_key, model=model, **kwargs)
