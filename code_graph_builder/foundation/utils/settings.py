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


def reload_env(workspace: Path | None = None) -> dict[str, list[str]]:
    """Hot-reload configuration from ``.env`` files and ``settings.json``.

    Unlike :func:`load_settings` (which uses ``setdefault``), this function
    **overwrites** existing environment variables so that changed values in
    ``.env`` or ``settings.json`` take effect immediately.

    Args:
        workspace: Workspace directory (default: ``~/.code-graph-builder``).

    Returns:
        A dict summarising what changed::

            {"updated": ["KEY1", ...], "removed": ["KEY2", ...]}
    """
    from dotenv import dotenv_values

    ws = workspace or Path(
        os.environ.get("CGB_WORKSPACE", Path.home() / ".code-graph-builder")
    )
    ws = ws.expanduser()

    # ── Collect all config-managed keys ──────────────────────────────
    # CGB_WORKSPACE is intentionally excluded — it is a runtime path
    # parameter that should not be altered by config reload.
    _CONFIG_KEYS = frozenset({
        "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL",
        "LITELLM_API_KEY", "LITELLM_BASE_URL", "LITELLM_MODEL",
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
        "MOONSHOT_API_KEY", "MOONSHOT_MODEL",
        "DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL",
        "EMBEDDING_API_KEY", "EMBEDDING_BASE_URL", "EMBEDDING_MODEL",
        "EMBEDDING_PROVIDER", "EMBED_API_KEY", "EMBED_BASE_URL", "EMBED_MODEL",
    })

    # Snapshot old values
    old_vals: dict[str, str | None] = {k: os.environ.get(k) for k in _CONFIG_KEYS}

    # ── Read fresh values (same priority as startup) ─────────────────
    # workspace .env  →  local .env  →  settings.json (lowest priority)
    new_vals: dict[str, str] = {}

    ws_env = ws / ".env"
    if ws_env.exists():
        for k, v in dotenv_values(ws_env).items():
            if v is not None:
                new_vals.setdefault(k, v)

    local_env = Path(".env")
    if local_env.exists():
        for k, v in dotenv_values(local_env).items():
            if v is not None:
                new_vals.setdefault(k, v)

    settings_file = SETTINGS_PATH
    if settings_file.exists():
        try:
            data = json.loads(settings_file.read_text(encoding="utf-8"))
            env_block = data.get("env") if isinstance(data, dict) else None
            if isinstance(env_block, dict):
                for k, v in env_block.items():
                    if isinstance(v, str):
                        new_vals.setdefault(k, v)
        except (json.JSONDecodeError, OSError):
            pass

    # ── Apply changes to os.environ ──────────────────────────────────
    updated: list[str] = []
    removed: list[str] = []

    for key in _CONFIG_KEYS:
        new_val = new_vals.get(key)
        old_val = old_vals.get(key)

        if new_val is not None:
            if old_val != new_val:
                os.environ[key] = new_val
                updated.append(key)
        else:
            # Key no longer in any config source → remove from env
            if key in os.environ:
                del os.environ[key]
                removed.append(key)

    # if updated:
    #     logger.info(f"Config reloaded — updated: {', '.join(updated)}")
    # if removed:
    #     logger.info(f"Config reloaded — removed: {', '.join(removed)}")
    # if not updated and not removed:
    #     logger.info("Config reloaded — no changes detected")

    return {"updated": updated, "removed": removed}
