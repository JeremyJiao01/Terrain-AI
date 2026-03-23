#!/usr/bin/env python3
"""Test LLM API connectivity using the .env config from npx setup wizard.

Reads the configuration from ``~/.code-graph-builder/.env`` (written by
``npx code-graph-builder --setup``) and sends a simple chat-completion
request to verify the LLM endpoint is reachable.

Usage:
    python -m code_graph_builder.examples.test_llm_api

Environment variables (auto-loaded from .env):
    LLM_API_KEY / LITELLM_API_KEY / OPENAI_API_KEY / MOONSHOT_API_KEY
    LLM_BASE_URL / LITELLM_BASE_URL / OPENAI_BASE_URL
    LLM_MODEL / LITELLM_MODEL / OPENAI_MODEL / MOONSHOT_MODEL
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

# 1. Load the .env written by `npx code-graph-builder --setup`
_NPX_ENV = Path.home() / ".code-graph-builder" / ".env"
# Also try project-local .env as fallback
_LOCAL_ENV = Path(__file__).resolve().parent.parent.parent / ".env"

for env_path in (_NPX_ENV, _LOCAL_ENV):
    if env_path.exists():
        load_dotenv(env_path)
        logger.info(f"Loaded env from: {env_path}")
        break
else:
    logger.warning(
        "No .env file found. Falling back to shell environment.\n"
        f"  Expected: {_NPX_ENV}\n"
        "  Run `npx code-graph-builder --setup` to create one."
    )

# 2. Import after env is loaded so auto-detection picks up the keys
from code_graph_builder.rag.llm_backend import create_llm_backend  # noqa: E402


def test_llm_connection() -> None:
    """Send a simple request to verify the LLM endpoint works."""
    backend = create_llm_backend()

    # --- Pre-flight checks ---
    if not backend.available:
        logger.error(
            "No API key detected. Please configure one of:\n"
            "  LITELLM_API_KEY, LLM_API_KEY, OPENAI_API_KEY, or MOONSHOT_API_KEY\n"
            "Run `npx code-graph-builder --setup` to configure interactively."
        )
        sys.exit(1)

    logger.info(f"Provider base_url : {backend.base_url}")
    logger.info(f"Model             : {backend.model}")
    logger.info(f"API Key           : {backend.api_key[:6]}****{backend.api_key[-4:]}")
    logger.info("")

    # --- Test 1: simple chat completion ---
    logger.info("── Test 1: Basic chat completion ──")
    messages = [
        {"role": "system", "content": "You are a helpful assistant. Reply in one short sentence."},
        {"role": "user", "content": "Say hello and tell me which model you are."},
    ]

    t0 = time.perf_counter()
    try:
        reply = backend.chat(messages)
        elapsed = time.perf_counter() - t0
        logger.success(f"Response ({elapsed:.2f}s): {reply}")
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        logger.error(f"Request failed after {elapsed:.2f}s: {exc}")
        sys.exit(1)

    # --- Test 2: chat with tools (function calling) ---
    logger.info("")
    logger.info("── Test 2: Tool / function calling ──")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the current weather for a location.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "City name, e.g. 'Beijing'",
                        }
                    },
                    "required": ["location"],
                },
            },
        }
    ]
    messages_tool = [
        {"role": "user", "content": "What's the weather in Shanghai?"},
    ]

    t0 = time.perf_counter()
    try:
        result = backend.chat_with_tools(messages_tool, tools=tools)
        elapsed = time.perf_counter() - t0

        if result.tool_calls:
            for tc in result.tool_calls:
                logger.success(
                    f"Tool call ({elapsed:.2f}s): {tc.function_name}({tc.arguments})"
                )
        elif result.content:
            logger.success(f"Text reply ({elapsed:.2f}s): {result.content}")
        else:
            logger.warning(f"Empty response ({elapsed:.2f}s), finish_reason={result.finish_reason}")
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        logger.warning(f"Tool calling not supported or failed ({elapsed:.2f}s): {exc}")
        logger.info("This is OK — not all providers support function calling.")

    # --- Summary ---
    logger.info("")
    logger.info("=" * 50)
    logger.success("LLM connection test passed!")
    logger.info("=" * 50)


if __name__ == "__main__":
    test_llm_connection()
