"""Read-only workspace scanning utilities shared across entrypoints."""
from __future__ import annotations

import json
from pathlib import Path


def get_repo_status_entries(ws: Path) -> list[dict]:
    """Return metadata for every indexed repo under *ws*.

    Each entry is a dict with:
        artifact_dir  — artifact directory name (stable join key)
        name          — display name from meta.json
        path          — absolute repo path
        indexed_at    — ISO 8601 timestamp string
        status        — always "unknown" (git checks removed for speed)
        commits_since — always None
        indexed_head  — short SHA (7 chars) recorded at index time, or None
        current_head  — always None

    Note: callers are responsible for running any workspace migrations
    (e.g. batch_migrate_to_v2) before calling this function.
    """
    if not ws.exists():
        return []

    entries: list[dict] = []

    for child in sorted(ws.iterdir()):
        if not child.is_dir():
            continue
        meta_file = child / "meta.json"
        if not meta_file.exists():
            continue
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue

        name = meta.get("repo_name", child.name)
        repo_path_str = meta.get("repo_path", "")
        indexed_at = meta.get("indexed_at", "")
        last_indexed_commit = meta.get("last_indexed_commit") or ""

        entries.append({
            "artifact_dir": child.name,
            "name": name,
            "path": repo_path_str,
            "indexed_at": indexed_at,
            "status": "unknown",
            "commits_since": None,
            "indexed_head": last_indexed_commit[:7] if last_indexed_commit else None,
            "current_head": None,
        })

    return entries
