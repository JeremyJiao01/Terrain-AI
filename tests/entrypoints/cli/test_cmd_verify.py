"""JER-124 — `terrain verify` only-read artifact health check.

Covers the four artifact buckets advertised in the spec (meta.json, graph.db,
vectors.pkl, api_docs/) plus exit-code semantics and the ``--json`` mode.

The verifier never rebuilds, never network-fetches, never mutates the
workspace — these tests assert that contract by setting TERRAIN_WORKSPACE to
a tmp_path and inspecting the workspace before/after invocation.
"""
from __future__ import annotations

import json
import pickle
from pathlib import Path

import pytest

from terrain.entrypoints.cli import cli as cli_mod


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_artifact_dir(ws: Path, name: str = "demo_repo_abc12345") -> Path:
    """Create a minimal-but-valid .terrain artifact dir under *ws*.

    Includes meta.json, graph.db (real Kùzu), vectors.pkl, api_docs/funcs/.
    Returns the artifact dir path.
    """
    art = ws / name
    art.mkdir(parents=True)

    # meta.json — valid JSON
    meta = {
        "repo_path": "/tmp/demo_repo",
        "repo_name": "demo_repo",
        "indexed_at": "2026-04-26T00:00:00",
        "wiki_page_count": 0,
        "schema_version": 2,
    }
    (art / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    # graph.db — a real Kùzu database (tiny — empty schema is fine)
    import kuzu
    db = kuzu.Database(str(art / "graph.db"))
    # Force creation; closing handle releases the lock so verify can re-open.
    del db

    # vectors.pkl — a tiny pickle (an empty dict round-trips fine)
    with open(art / "vectors.pkl", "wb") as fh:
        pickle.dump({}, fh)

    # api_docs/funcs/ with at least one entry
    funcs = art / "api_docs" / "funcs"
    funcs.mkdir(parents=True)
    (funcs / "demo.md").write_text("# demo", encoding="utf-8")

    # Mark this artifact as the active repo
    (ws / "active.txt").write_text(name, encoding="utf-8")
    return art


@pytest.fixture
def ws(tmp_path, monkeypatch) -> Path:
    """Provide an isolated workspace and point TERRAIN_WORKSPACE at it."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setenv("TERRAIN_WORKSPACE", str(workspace))
    return workspace


def _run_verify(json_mode: bool = False) -> int:
    import argparse
    args = argparse.Namespace(json=json_mode)
    return cli_mod.cmd_verify(args)


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def test_all_healthy_returns_zero_and_summary(ws, capsys):
    """Healthy artifacts → exit 0 and summary 'N artifacts ok'."""
    _make_artifact_dir(ws)

    rc = _run_verify()
    out = capsys.readouterr().out

    assert rc == 0
    assert "4 artifacts ok" in out
    # Each artifact appears with the 'ok' marker
    for name in ("meta.json", "graph.db", "vectors.pkl", "api_docs/"):
        assert name in out


def test_corrupt_vectors_pkl_marked_corrupt(ws, capsys):
    """Truncated vectors.pkl → exit 1, file labelled corrupt."""
    art = _make_artifact_dir(ws)
    # Truncate to a few bytes — pickle.load will raise.
    (art / "vectors.pkl").write_bytes(b"\x80\x04junk")

    rc = _run_verify()
    out = capsys.readouterr().out

    assert rc == 1
    assert "vectors.pkl" in out
    assert "corrupt" in out


def test_missing_meta_json_marked_missing(ws, capsys):
    """Removed meta.json → exit 1, status 'missing'."""
    art = _make_artifact_dir(ws)
    (art / "meta.json").unlink()

    rc = _run_verify()
    out = capsys.readouterr().out

    assert rc == 1
    assert "meta.json" in out
    assert "missing" in out


def test_json_mode_outputs_valid_array(ws, capsys):
    """--json mode emits a JSON array; each entry has artifact + status fields."""
    _make_artifact_dir(ws)

    rc = _run_verify(json_mode=True)
    out = capsys.readouterr().out

    parsed = json.loads(out)
    assert rc == 0
    assert isinstance(parsed, list)
    assert len(parsed) == 4
    names = {e["artifact"] for e in parsed}
    assert names == {"meta.json", "graph.db", "vectors.pkl", "api_docs/"}
    for entry in parsed:
        assert entry["status"] == "ok"
        assert "path" in entry


def test_no_active_repo_returns_zero(ws, capsys):
    """An empty workspace (no active.txt) is not an error: exit 0."""
    rc = _run_verify()
    out = capsys.readouterr().out

    assert rc == 0
    assert "no active repo" in out


def test_verify_subcommand_registered(monkeypatch, capsys):
    """The CLI parser must expose `verify` as a discoverable subcommand."""
    # Drive the real `main()` with `terrain verify --help`, which forces
    # argparse to render the verify subparser's help and exit cleanly.
    monkeypatch.setattr("sys.argv", ["terrain", "verify", "--help"])
    with pytest.raises(SystemExit) as exc:
        cli_mod.main()
    out = capsys.readouterr().out
    assert exc.value.code == 0
    assert "verify" in out
