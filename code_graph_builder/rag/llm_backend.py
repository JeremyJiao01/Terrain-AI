"""LLM backend abstraction for RAG and Cypher generation.

Provides a unified interface to call LLM APIs (Moonshot/Kimi).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from loguru import logger


@dataclass
class LLMBackend:
    """Generic LLM backend that calls a chat-completion API."""

    api_key: str = ""
    model: str = "kimi-k2.5"
    base_url: str = "https://api.moonshot.cn/v1"
    temperature: float = 0.0
    max_tokens: int = 4096

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        """Send a chat completion request and return the assistant's response text."""
        try:
            import httpx
        except ImportError:
            raise ImportError(
                "httpx is required for LLM backend. "
                "Install it with: pip install httpx"
            )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
        }

        resp = httpx.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            headers=headers,
            timeout=60.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]


def create_llm_backend(**kwargs: Any) -> LLMBackend:
    """Create an LLM backend from environment variables.

    Environment variables:
        MOONSHOT_API_KEY  — API key (required)
        MOONSHOT_MODEL    — Model name (default: kimi-k2.5)
    """
    api_key = kwargs.pop("api_key", None) or os.environ.get("MOONSHOT_API_KEY", "")
    model = kwargs.pop("model", None) or os.environ.get("MOONSHOT_MODEL", "kimi-k2.5")

    if not api_key:
        logger.warning(
            "MOONSHOT_API_KEY not set — query_code_graph tool will be unavailable"
        )

    return LLMBackend(api_key=api_key, model=model, **kwargs)
