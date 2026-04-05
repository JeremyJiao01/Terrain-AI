"""LLM backend abstraction for RAG and Cypher generation.

Provides a unified interface to call any OpenAI-compatible chat-completion API.
The provider is auto-detected from environment variables in this priority:

    1. ``LLM_API_KEY`` / ``LLM_BASE_URL`` / ``LLM_MODEL``   (generic, highest)
    2. ``LITELLM_API_KEY`` / ``LITELLM_BASE_URL`` / ``LITELLM_MODEL`` (LiteLLM proxy)
    3. ``OPENAI_API_KEY`` / ``OPENAI_BASE_URL`` / ``OPENAI_MODEL``
    4. ``MOONSHOT_API_KEY`` / ``MOONSHOT_MODEL``              (legacy default)

When installed as an MCP server in Claude Code, configure the environment
variables in ``settings.json`` → ``mcpServers`` → ``env``.
"""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

# Suppress SSL verification warnings when verify=False is used (e.g. LiteLLM proxy)
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

# Provider detection order: each tuple is (key_env, base_url_env, model_env, default_base_url, default_model)
_PROVIDER_ENVS: list[tuple[str, str, str, str, str]] = [
    # Generic — user explicitly chose an LLM
    ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "https://api.openai.com/v1", "gpt-4o"),
    # LiteLLM proxy — OpenAI-compatible gateway for 100+ LLM providers
    ("LITELLM_API_KEY", "LITELLM_BASE_URL", "LITELLM_MODEL", "http://localhost:4000/v1", "gpt-4o"),
    # OpenAI / compatible (DeepSeek, Together, etc.)
    ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "https://api.openai.com/v1", "gpt-4o"),
    # Moonshot / Kimi (legacy default)
    ("MOONSHOT_API_KEY", "LLM_BASE_URL", "MOONSHOT_MODEL", "https://api.moonshot.cn/v1", "kimi-k2.5"),
]


@dataclass
class ToolCall:
    """A single tool invocation returned by the LLM."""

    id: str
    function_name: str
    arguments: str  # JSON-encoded string


@dataclass
class ChatMessage:
    """Structured response from a chat completion that may contain tool calls."""

    content: str | None
    tool_calls: list[ToolCall] | None
    finish_reason: str


@dataclass
class LLMBackend:
    """Generic LLM backend that calls an OpenAI-compatible chat-completion API."""

    api_key: str = ""
    model: str = "gpt-4o"
    base_url: str = "https://api.openai.com/v1"
    temperature: float = 1.0
    max_tokens: int = 4096

    @property
    def available(self) -> bool:
        """Return *True* when an API key has been configured."""
        return bool(self.api_key)

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        """Send a chat completion request and return the assistant's response text."""
        try:
            import requests
        except ImportError:
            raise ImportError(
                "requests is required for LLM backend. "
                "Install it with: pip install requests"
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
            "top_p": kwargs.get("top_p", 0.9),
            "stream": False,
        }

        resp = requests.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            headers=headers,
            timeout=60.0,
            verify=False,
        )
        resp.raise_for_status()
        data = resp.json()
        message = data["choices"][0]["message"]
        return message.get("content") or message.get("reasoning_content", "")

    def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> ChatMessage:
        """Send a chat completion with optional tool definitions.

        Returns a :class:`ChatMessage` that may contain ``tool_calls`` when the
        LLM decides to invoke one or more tools.  If *tools* is ``None`` or
        empty, behaves like :meth:`chat` but returns a structured message.
        """
        try:
            import requests
        except ImportError:
            raise ImportError(
                "requests is required for LLM backend. "
                "Install it with: pip install requests"
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
            "top_p": kwargs.get("top_p", 0.9),
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = kwargs.get("tool_choice", "auto")

        resp = requests.post(
            f"{self.base_url}/chat/completions",
            json=payload,
            headers=headers,
            timeout=kwargs.get("timeout", 120.0),
            verify=False,
        )
        resp.raise_for_status()
        data = resp.json()

        choice = data["choices"][0]
        message = choice["message"]
        finish_reason = choice.get("finish_reason", "stop")

        parsed_calls: list[ToolCall] | None = None
        raw_calls = message.get("tool_calls")
        if raw_calls:
            parsed_calls = [
                ToolCall(
                    id=tc["id"],
                    function_name=tc["function"]["name"],
                    arguments=tc["function"]["arguments"],
                )
                for tc in raw_calls
            ]

        return ChatMessage(
            content=message.get("content"),
            tool_calls=parsed_calls,
            finish_reason=finish_reason,
        )


def create_llm_backend(**kwargs: Any) -> LLMBackend:
    """Create an LLM backend by auto-detecting available provider env vars.

    Detection priority (first match wins):
        1. ``LLM_API_KEY``      — generic override
        2. ``LITELLM_API_KEY``  — LiteLLM proxy
        3. ``OPENAI_API_KEY``   — OpenAI or any compatible endpoint
        4. ``MOONSHOT_API_KEY`` — Moonshot / Kimi (legacy)

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
            "Set one of: LLM_API_KEY, LITELLM_API_KEY, OPENAI_API_KEY, or MOONSHOT_API_KEY. "
            "Tools that require LLM (query_code_graph, wiki generation) will be unavailable."
        )

    return LLMBackend(api_key=api_key, base_url=base_url, model=model, **kwargs)
