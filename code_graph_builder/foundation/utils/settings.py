"""Configuration loader for code-graph-builder.

All configuration is read exclusively from the workspace ``.env`` file
(``~/.code-graph-builder/.env`` by default, overridable via
``CGB_WORKSPACE``).  No other files (local ``.env``, ``settings.json``,
etc.) are consulted so that the workspace ``.env`` is the single source
of truth.
"""

from __future__ import annotations

import os
from pathlib import Path

from loguru import logger

# Kept for backward-compatibility imports only — not used internally.
SETTINGS_PATH = Path.home() / ".claude" / "settings.json"


def load_settings(path: Path | None = None) -> dict:
    """No-op stub retained for backward compatibility.

    Configuration is now loaded exclusively from the workspace ``.env``
    file via :func:`reload_env`.  This function returns an empty dict
    without touching ``os.environ`` or reading any file.
    """
    return {}


def refresh_env() -> None:
    """Lightweight re-read of the workspace ``.env`` file.

    Called before each LLM / embedding factory invocation so that edits
    to the workspace ``.env`` take effect immediately in long-running
    processes (MCP server) without a restart.

    Uses mtime-based fast path: filesystem is only touched when the file
    has changed since the last call.
    """
    ws_env = Path(
        os.environ.get("CGB_WORKSPACE", Path.home() / ".code-graph-builder")
    ).expanduser() / ".env"

    # Fast path: skip if workspace .env hasn't changed since last check
    try:
        mtime = ws_env.stat().st_mtime if ws_env.exists() else 0.0
    except OSError:
        mtime = 0.0

    last = getattr(refresh_env, "_last_mtime", -1.0)
    if mtime == last:
        return
    refresh_env._last_mtime = mtime  # type: ignore[attr-defined]

    # Reload — stale keys absent from .env are also removed so shell
    # leftovers don't silently override the current configuration.
    try:
        reload_env()
    except Exception:
        pass  # graceful degradation if reload fails


def reload_env(workspace: Path | None = None) -> dict[str, list[str]]:
    """Hot-reload configuration from the workspace ``.env`` file.

    Overwrites existing environment variables with values from the
    workspace ``.env``, and **removes** any config-managed keys that are
    no longer present in the file — preventing stale shell exports or
    values injected by other tools from silently overriding config.

    Args:
        workspace: Workspace directory (default: ``~/.code-graph-builder``).

    Returns:
        ``{"updated": [...], "removed": [...]}``
    """
    from dotenv import dotenv_values

    ws = workspace or Path(
        os.environ.get("CGB_WORKSPACE", Path.home() / ".code-graph-builder")
    )
    ws = ws.expanduser()

    # All config-managed keys.  CGB_WORKSPACE is intentionally excluded —
    # it is a bootstrap parameter set before this function runs.
    _CONFIG_KEYS = frozenset({
        "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL",
        "LITELLM_API_KEY", "LITELLM_BASE_URL", "LITELLM_MODEL",
        "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
        "MOONSHOT_API_KEY", "MOONSHOT_MODEL",
        "DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL",
        "EMBEDDING_API_KEY", "EMBEDDING_BASE_URL", "EMBEDDING_MODEL",
        "EMBEDDING_PROVIDER", "EMBED_API_KEY", "EMBED_BASE_URL", "EMBED_MODEL",
        "CGB_DEBUG",
    })

    # Snapshot current values
    old_vals: dict[str, str | None] = {k: os.environ.get(k) for k in _CONFIG_KEYS}

    # Read only from workspace .env — single source of truth
    new_vals: dict[str, str] = {}
    ws_env = ws / ".env"
    if ws_env.exists():
        for k, v in dotenv_values(ws_env).items():
            if v is not None:
                new_vals[k] = v

    # Apply: update present keys, remove absent ones
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
            if key in os.environ:
                del os.environ[key]
                removed.append(key)

    return {"updated": updated, "removed": removed}
