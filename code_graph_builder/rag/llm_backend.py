"""LLM backend abstraction for RAG and Cypher generation.

Provides a unified interface to call any OpenAI-compatible chat-completion API.
The provider is auto-detected from environment variables in this priority:

    1. ``LLM_API_KEY`` / ``LLM_BASE_URL`` / ``LLM_MODEL``   (generic, highest)
    2. ``OPENAI_API_KEY`` / ``OPENAI_BASE_URL`` / ``OPENAI_MODEL``
    3. ``MOONSHOT_API_KEY`` / ``MOONSHOT_MODEL``              (legacy default)

When installed as an MCP server in Claude Code, configure the environment
variables in ``settings.json`` → ``mcpServers`` → ``env``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from loguru import logger

# Provider detection order: each tuple is (key_env, base_url_env, model_env, default_base_url, default_model)
_PROVIDER_ENVS: list[tuple[str, str, str, str, str]] = [
    # Generic — user explicitly chose an LLM
    ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "https://api.openai.com/v1", "gpt-4o"),
    # OpenAI / compatible (DeepSeek, Together, etc.)
    ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "https://api.openai.com/v1", "gpt-4o"),
    # Moonshot / Kimi (legacy default)
    ("MOONSHOT_API_KEY", "LLM_BASE_URL", "MOONSHOT_MODEL", "https://api.moonshot.cn/v1", "kimi-k2.5"),
]


@dataclass
class LLMBackend:
    """Generic LLM backend that calls an OpenAI-compatible chat-completion API."""

    api_key: str = ""
    model: str = "gpt-4o"
    base_url: str = "https://api.openai.com/v1"
    temperature: float = 0.0
    max_tokens: int = 4096

    @property
    def available(self) -> bool:
        """Return *True* when an API key has been configured."""
        return bool(self.api_key)

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
    """Create an LLM backend by auto-detecting available provider env vars.

    Detection priority (first match wins):
        1. ``LLM_API_KEY``      — generic override
        2. ``OPENAI_API_KEY``   — OpenAI or any compatible endpoint
        3. ``MOONSHOT_API_KEY`` — Moonshot / Kimi (legacy)

    Any of these can be overridden by passing explicit keyword arguments
    (``api_key``, ``base_url``, ``model``).
    """
    explicit_key = kwargs.pop("api_key", None)
    explicit_url = kwargs.pop("base_url", None)
    explicit_model = kwargs.pop("model", None)

    # Walk providers until we find one with a key
    detected_key = ""
    detected_url = ""
    detected_model = ""
    detected_provider = ""

    for key_env, url_env, model_env, default_url, default_model in _PROVIDER_ENVS:
        env_key = os.environ.get(key_env, "")
        if env_key:
            detected_key = env_key
            detected_url = os.environ.get(url_env, default_url)
            detected_model = os.environ.get(model_env, default_model)
            detected_provider = key_env
            break

    api_key = explicit_key or detected_key
    base_url = explicit_url or detected_url or "https://api.openai.com/v1"
    model = explicit_model or detected_model or "gpt-4o"

    if api_key:
        logger.info(
            f"LLM backend: model={model}, base_url={base_url} "
            f"(detected via {detected_provider or 'explicit kwargs'})"
        )
    else:
        logger.warning(
            "No LLM API key found in environment. "
            "Set one of: LLM_API_KEY, OPENAI_API_KEY, or MOONSHOT_API_KEY. "
            "Tools that require LLM (query_code_graph, wiki generation) will be unavailable."
        )

    return LLMBackend(api_key=api_key, base_url=base_url, model=model, **kwargs)
