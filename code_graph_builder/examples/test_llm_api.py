#!/usr/bin/env python3
"""Test LLM and Embedding API connectivity using the .env from npx setup wizard.

Reads ``~/.code-graph-builder/.env`` (written by ``npx code-graph-builder --setup``)
and runs connectivity tests against both the LLM chat-completion endpoint and
the embedding endpoint.

Uses the same request style as the production code:
  - requests (not httpx)
  - Authentication header (not Bearer)
  - verify=False, top_p=0.9, stream=False

Usage:
    python -m code_graph_builder.examples.test_llm_api
"""

from __future__ import annotations

import os
import sys
import time
import warnings
from pathlib import Path

import requests
from dotenv import load_dotenv
from loguru import logger

# Suppress SSL warnings (consistent with llm_backend.py / qwen3_embedder.py)
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

# ---------------------------------------------------------------------------
# 1. Load .env
# ---------------------------------------------------------------------------
_NPX_ENV = Path.home() / ".code-graph-builder" / ".env"
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

# Import after env is loaded so auto-detection picks up the keys
from code_graph_builder.rag.llm_backend import create_llm_backend  # noqa: E402


# ---------------------------------------------------------------------------
# 2. LLM Tests
# ---------------------------------------------------------------------------

def test_llm_connection() -> bool:
    """Send requests to verify the LLM endpoint works. Returns True on success."""
    logger.info("=" * 55)
    logger.info("  LLM Chat-Completion Tests")
    logger.info("=" * 55)

    backend = create_llm_backend()

    if not backend.available:
        logger.error(
            "No LLM API key detected. Please configure one of:\n"
            "  LLM_API_KEY, LITELLM_API_KEY, OPENAI_API_KEY, or MOONSHOT_API_KEY\n"
            "Run `npx code-graph-builder --setup` to configure interactively."
        )
        return False

    logger.info(f"  base_url : {backend.base_url}")
    logger.info(f"  model    : {backend.model}")
    logger.info(f"  api_key  : {backend.api_key[:6]}****{backend.api_key[-4:]}")
    logger.info(f"  auth     : Authentication header")
    logger.info(f"  verify   : False")
    logger.info("")

    # --- Test 1: basic chat (requests + Authentication + top_p + stream=False) ---
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
        return False

    # --- Test 2: raw requests call (to double-check headers) ---
    logger.info("")
    logger.info("── Test 2: Raw requests verification ──")
    headers = {
        "Authentication": backend.api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "model": backend.model,
        "messages": [{"role": "user", "content": "Reply with just 'OK'."}],
        "temperature": 1.0,
        "max_tokens": 32,
        "top_p": 0.9,
        "stream": False,
    }

    t0 = time.perf_counter()
    try:
        resp = requests.post(
            f"{backend.base_url}/chat/completions",
            json=payload,
            headers=headers,
            timeout=60.0,
            verify=False,
        )
        elapsed = time.perf_counter() - t0
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"].get("content", "")
        logger.success(f"Raw response ({elapsed:.2f}s): {content}")
        logger.info(f"  Status code : {resp.status_code}")
        logger.info(f"  Model used  : {data.get('model', 'N/A')}")
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        logger.error(f"Raw request failed after {elapsed:.2f}s: {exc}")
        return False

    # --- Test 3: tool calling ---
    logger.info("")
    logger.info("── Test 3: Tool / function calling ──")
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

    return True


# ---------------------------------------------------------------------------
# 3. Embedding Tests
# ---------------------------------------------------------------------------

def test_embedding_connection() -> bool:
    """Send requests to verify the embedding endpoint works. Returns True on success."""
    logger.info("")
    logger.info("=" * 55)
    logger.info("  Embedding API Tests")
    logger.info("=" * 55)

    # Resolve API key — same fallback chain as create_embedder()
    api_key = (
        os.getenv("DASHSCOPE_API_KEY")
        or os.getenv("EMBEDDING_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("LLM_API_KEY")
    )
    base_url = (
        os.getenv("DASHSCOPE_BASE_URL")
        or os.getenv("EMBEDDING_BASE_URL")
        or os.getenv("OPENAI_BASE_URL")
        or os.getenv("LLM_BASE_URL")
    )
    embed_model = os.getenv("EMBED_MODEL") or os.getenv("EMBEDDING_MODEL") or "text-embedding-v4"

    if not api_key:
        logger.warning(
            "No embedding API key found. Skipping embedding tests.\n"
            "  Set DASHSCOPE_API_KEY, EMBEDDING_API_KEY, or OPENAI_API_KEY to enable."
        )
        return True  # not a failure, just skipped

    if not base_url:
        logger.warning("No embedding base URL found. Skipping embedding tests.")
        return True

    logger.info(f"  base_url : {base_url}")
    logger.info(f"  model    : {embed_model}")
    logger.info(f"  api_key  : {api_key[:6]}****{api_key[-4:]}")
    logger.info(f"  endpoint : {base_url}/embeddings")
    logger.info(f"  verify   : False")
    logger.info("")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # --- Test 1: single embedding ---
    logger.info("── Test 1: Single code embedding ──")
    code_snippet = "def fibonacci(n):\n    if n <= 1:\n        return n\n    return fibonacci(n-1) + fibonacci(n-2)"
    payload = {
        "model": embed_model,
        "input": [code_snippet],
    }

    t0 = time.perf_counter()
    try:
        resp = requests.post(
            f"{base_url}/embeddings",
            json=payload,
            headers=headers,
            timeout=60,
            verify=False,
        )
        elapsed = time.perf_counter() - t0
        resp.raise_for_status()
        data = resp.json()

        # OpenAI-compatible format: data[].embedding
        items = sorted(data["data"], key=lambda x: x["index"])
        embedding = items[0]["embedding"]
        logger.success(f"Embedding ({elapsed:.2f}s): {len(embedding)} dimensions")
        logger.info(f"  First 5 values: {embedding[:5]}")
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        logger.error(f"Single embedding failed after {elapsed:.2f}s: {exc}")
        return False

    # --- Test 2: batch embedding ---
    logger.info("")
    logger.info("── Test 2: Batch embedding (3 snippets) ──")
    batch_texts = [
        "def add(a, b): return a + b",
        "class Calculator:\n    def multiply(self, x, y):\n        return x * y",
        "import os\nprint(os.getcwd())",
    ]
    payload_batch = {
        "model": embed_model,
        "input": batch_texts,
    }

    t0 = time.perf_counter()
    try:
        resp = requests.post(
            f"{base_url}/embeddings",
            json=payload_batch,
            headers=headers,
            timeout=60,
            verify=False,
        )
        elapsed = time.perf_counter() - t0
        resp.raise_for_status()
        data = resp.json()

        items = sorted(data["data"], key=lambda x: x["index"])
        logger.success(f"Batch embedding ({elapsed:.2f}s): {len(items)} vectors returned")
        for i, item in enumerate(items):
            logger.info(f"  [{i}] {len(item['embedding'])} dimensions")
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        logger.error(f"Batch embedding failed after {elapsed:.2f}s: {exc}")
        return False

    # --- Test 3: via create_embedder() factory ---
    logger.info("")
    logger.info("── Test 3: create_embedder() factory ──")
    try:
        from code_graph_builder.embeddings.qwen3_embedder import create_embedder

        embedder = create_embedder()
        t0 = time.perf_counter()
        emb = embedder.embed_code("def hello(): print('world')")
        elapsed = time.perf_counter() - t0
        logger.success(f"create_embedder().embed_code ({elapsed:.2f}s): {len(emb)} dimensions")

        t0 = time.perf_counter()
        query_emb = embedder.embed_query("function that prints hello")
        elapsed = time.perf_counter() - t0
        logger.success(f"create_embedder().embed_query ({elapsed:.2f}s): {len(query_emb)} dimensions")
    except Exception as exc:
        logger.error(f"create_embedder() test failed: {exc}")
        return False

    return True


# ---------------------------------------------------------------------------
# 4. Main
# ---------------------------------------------------------------------------

def main() -> None:
    llm_ok = test_llm_connection()
    embed_ok = test_embedding_connection()

    logger.info("")
    logger.info("=" * 55)
    if llm_ok and embed_ok:
        logger.success("All tests passed!")
    else:
        if not llm_ok:
            logger.error("LLM tests FAILED")
        if not embed_ok:
            logger.error("Embedding tests FAILED")
        sys.exit(1)
    logger.info("=" * 55)


if __name__ == "__main__":
    main()
