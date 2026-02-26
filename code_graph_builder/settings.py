"""Load global configuration from ``~/.claude/settings.json``.

This module reads LLM and embedding API credentials stored in the Claude
Code settings file and injects them into ``os.environ`` via
:func:`os.environ.setdefault`.  Because ``setdefault`` is used, any values
already present in the environment (from ``.env``, MCP ``env`` block, or
shell exports) take precedence — this file acts as a *fallback* layer.

Expected JSON structure::

    {
      "env": {
        "LLM_API_KEY": "sk-...",
        "LLM_BASE_URL": "https://api.openai.com/v1",
        "LLM_MODEL": "gpt-4o",
        "DASHSCOPE_API_KEY": "sk-...",
        "DASHSCOPE_BASE_URL": "https://dashscope.aliyuncs.com/api/v1"
      }
    }

All keys inside the ``"env"`` object are injected into the process
environment.  Unknown keys are silently accepted so the file can hold
additional settings for other tools.

The function is intentionally side-effect-free when the file does not
exist or is malformed — it logs a warning and returns without error.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from loguru import logger

# Well-known settings file location
SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


def load_settings(path: Path | None = None) -> dict:
    """Read ``~/.claude/settings.json`` and inject ``env`` entries.

    Args:
        path: Override the default settings file location (useful for tests).

    Returns:
        The parsed JSON dict (or ``{}`` if the file does not exist).
    """
    settings_file = path or SETTINGS_PATH

    if not settings_file.exists():
        return {}

    try:
        data = json.loads(settings_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning(f"Failed to parse {settings_file}: {exc}")
        return {}

    if not isinstance(data, dict):
        logger.warning(f"Expected JSON object in {settings_file}, got {type(data).__name__}")
        return {}

    env_block = data.get("env")
    if isinstance(env_block, dict):
        injected = []
        for key, value in env_block.items():
            if isinstance(value, str) and key not in os.environ:
                os.environ.setdefault(key, value)
                injected.append(key)
        if injected:
            logger.info(f"Loaded from {settings_file}: {', '.join(injected)}")

    return data
