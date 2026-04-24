"""JER-101 — ``terrain link`` schema v2 + N:1 (``linked_repos``).

Prior to v2, ``terrain link`` only wrote the *new* artifact's meta.json.
The authoritative (``source``) artifact never learned about its consumers,
so the data model couldn't express N repos sharing 1 database.

Schema v2 adds two fields:

* ``source_artifact`` — on every *linked-target* meta, the ``name`` of the
  authoritative artifact directory. Reverse pointer only.
* ``linked_repos``    — only on the *source* (authoritative) meta. List of
  ``{repo_path, repo_name, artifact_dir, linked_at}`` keyed by
  ``artifact_dir``. Idempotent under repeated ``terrain link`` calls.

``register_link`` writes both sides. ``migrate_meta_to_v2`` lazily upgrades
pre-v2 workspaces the first time CLI ``list``/``status`` or MCP
``list_repositories`` touches them.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from terrain.foundation.utils.paths import normalize_repo_path

SCHEMA_VERSION = 2


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _read_meta(meta_file: Path) -> dict[str, Any] | None:
    """Return parsed meta, or None if missing/corrupt."""
    if not meta_file.exists():
        return None
    try:
        return json.loads(meta_file.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None


def _atomic_write_meta(meta_file: Path, meta: dict[str, Any]) -> None:
    """Write meta.json atomically (tmp + rename)."""
    payload = json.dumps(meta, ensure_ascii=False, indent=2)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".meta.", suffix=".tmp", dir=str(meta_file.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_path, meta_file)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _steps_for(artifact_dir: Path) -> dict[str, bool]:
    return {
        "graph": (artifact_dir / "graph.db").exists(),
        "api_docs": (artifact_dir / "api_docs" / "index.md").exists(),
        "embeddings": (artifact_dir / "vectors.pkl").exists(),
        "wiki": (artifact_dir / "wiki" / "index.md").exists(),
    }


# ---------------------------------------------------------------------------
# register_link — the single code path both CLI and MCP drive through
# ---------------------------------------------------------------------------

def register_link(
    ws: Path,
    *,
    source_dir: Path,
    target_dir: Path,
    repo_path: Any,
) -> None:
    """Link *target_dir* to *source_dir* for *repo_path*.

    * Writes ``target_dir/meta.json`` with ``source_artifact``,
      ``schema_version=2``, and never carries ``linked_repos`` over.
    * Upserts the corresponding entry in ``source_dir/meta.json``'s
      ``linked_repos`` list (keyed by ``artifact_dir``).

    The *ws* argument is accepted for future use (workspace-scoped logging
    or locks) and kept in the signature to match the PR-2 plan.
    """
    del ws  # reserved

    now = datetime.now().isoformat()
    canonical = normalize_repo_path(repo_path)
    repo_name = Path(str(repo_path)).name or "root"

    # ── Target side ───────────────────────────────────────────────────
    target_meta_file = target_dir / "meta.json"
    target_meta: dict[str, Any] = _read_meta(target_meta_file) or {}
    # Target meta must never claim authority over other links.
    target_meta.pop("linked_repos", None)

    target_meta.update({
        "schema_version": SCHEMA_VERSION,
        "source_artifact": source_dir.name,
        "linked_from": str(source_dir),  # kept for backward-compat readers
        "repo_path": canonical,
        "repo_name": repo_name,
        "linked_at": now,
        "steps": _steps_for(target_dir),
    })
    target_meta.setdefault("indexed_at", now)
    _atomic_write_meta(target_meta_file, target_meta)

    # ── Source side ───────────────────────────────────────────────────
    source_meta_file = source_dir / "meta.json"
    source_meta: dict[str, Any] = _read_meta(source_meta_file) or {}

    linked_repos = list(source_meta.get("linked_repos") or [])
    entry = {
        "repo_path": canonical,
        "repo_name": repo_name,
        "artifact_dir": target_dir.name,
        "linked_at": now,
    }
    replaced = False
    for i, existing in enumerate(linked_repos):
        if existing.get("artifact_dir") == target_dir.name:
            # Preserve original linked_at to keep the write idempotent.
            entry["linked_at"] = existing.get("linked_at", now)
            linked_repos[i] = entry
            replaced = True
            break
    if not replaced:
        linked_repos.append(entry)

    source_meta["schema_version"] = SCHEMA_VERSION
    source_meta["linked_repos"] = linked_repos
    _atomic_write_meta(source_meta_file, source_meta)


# ---------------------------------------------------------------------------
# migrate_meta_to_v2 — lazy upgrade for pre-v2 workspaces
# ---------------------------------------------------------------------------

def _source_artifact_for(meta: dict[str, Any]) -> str | None:
    """Extract the source artifact dir name from a v1 meta, if any."""
    if meta.get("source_artifact"):
        return str(meta["source_artifact"])
    for key in ("linked_from", "linked_to"):
        raw = meta.get(key)
        if raw:
            # legacy ``str(Path)`` — take the basename.
            return Path(str(raw)).name
    return None


def migrate_meta_to_v2(artifact_dir: Path, ws: Path) -> None:
    """Idempotently migrate ``artifact_dir/meta.json`` to schema v2.

    Cheap no-op when already v2. Writes atomically. Any failure to parse
    or write meta is swallowed — migration is best-effort; it must never
    break a read path.

    .. note::

       When migrating many repos at once, prefer :func:`batch_migrate_to_v2`
       which builds the link map once (O(n)) instead of scanning siblings
       per-repo (O(n²)).
    """
    meta_file = artifact_dir / "meta.json"
    meta = _read_meta(meta_file)
    if meta is None:
        return
    if meta.get("schema_version", 1) >= SCHEMA_VERSION:
        return

    # If this dir is itself a link target, just record source_artifact.
    pointer = _source_artifact_for(meta)
    if pointer:
        meta["source_artifact"] = pointer
        meta["schema_version"] = SCHEMA_VERSION
        try:
            _atomic_write_meta(meta_file, meta)
        except OSError:
            pass
        return

    # Otherwise it might be an authoritative source — reverse-scan siblings.
    linked_repos: list[dict[str, Any]] = []
    if ws.exists():
        self_name = artifact_dir.name
        for child in sorted(ws.iterdir()):
            if not child.is_dir() or child.name == self_name:
                continue
            child_meta = _read_meta(child / "meta.json")
            if child_meta is None:
                continue
            ptr = _source_artifact_for(child_meta)
            if ptr != self_name:
                continue
            linked_repos.append({
                "repo_path": normalize_repo_path(
                    child_meta.get("repo_path", child.name)
                ),
                "repo_name": child_meta.get("repo_name", child.name),
                "artifact_dir": child.name,
                "linked_at": child_meta.get(
                    "linked_at", child_meta.get("indexed_at", "")
                ),
            })

    meta["schema_version"] = SCHEMA_VERSION
    if linked_repos:
        meta["linked_repos"] = linked_repos
    try:
        _atomic_write_meta(meta_file, meta)
    except OSError:
        pass


def batch_migrate_to_v2(ws: Path) -> None:
    """Migrate all repos in *ws* to schema v2 in a single O(n) pass.

    Builds the source→targets link map once, then writes each meta.json
    at most once. Much faster than calling :func:`migrate_meta_to_v2`
    per-repo when the workspace contains many repositories.
    """
    if not ws.exists():
        return

    # Pass 1: read all metas and identify which need migration
    repo_metas: dict[str, dict[str, Any]] = {}   # dir_name → meta
    needs_migration: list[str] = []

    for child in sorted(ws.iterdir()):
        if not child.is_dir():
            continue
        meta = _read_meta(child / "meta.json")
        if meta is None:
            continue
        repo_metas[child.name] = meta
        if meta.get("schema_version", 1) < SCHEMA_VERSION:
            needs_migration.append(child.name)

    if not needs_migration:
        return

    # Pass 2: build reverse link map  source_name → [target entries]
    link_map: dict[str, list[dict[str, Any]]] = {}
    for dir_name, meta in repo_metas.items():
        ptr = _source_artifact_for(meta)
        if ptr and ptr in repo_metas:
            link_map.setdefault(ptr, []).append({
                "repo_path": normalize_repo_path(
                    meta.get("repo_path", dir_name)
                ),
                "repo_name": meta.get("repo_name", dir_name),
                "artifact_dir": dir_name,
                "linked_at": meta.get(
                    "linked_at", meta.get("indexed_at", "")
                ),
            })

    # Pass 3: write updated metas
    for dir_name in needs_migration:
        meta = repo_metas[dir_name]
        pointer = _source_artifact_for(meta)
        if pointer:
            meta["source_artifact"] = pointer
        else:
            targets = link_map.get(dir_name)
            if targets:
                meta["linked_repos"] = targets
        meta["schema_version"] = SCHEMA_VERSION
        try:
            _atomic_write_meta(ws / dir_name / "meta.json", meta)
        except OSError:
            pass


__all__ = [
    "SCHEMA_VERSION",
    "register_link",
    "migrate_meta_to_v2",
    "batch_migrate_to_v2",
]
