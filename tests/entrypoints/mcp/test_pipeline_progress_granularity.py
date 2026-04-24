"""Per-function progress callback granularity for description/embed steps.

JER-115: progress_cb should tick once per function, not once per batch.
The batch header (pre-LLM call) must include a preview of function names so
the L1 spinner has something meaningful to show while the call is in flight.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from terrain.entrypoints.mcp import pipeline


# ---------------------------------------------------------------------------
# Description step
# ---------------------------------------------------------------------------


def _write_todo_func(funcs_dir: Path, name: str) -> None:
    """Create a minimal L3 API doc with a TODO placeholder."""
    (funcs_dir / f"{name}.md").write_text(
        f"# {name}\n"
        f"- 签名: `void {name}(void)`\n"
        f"- 模块: test_module\n"
        f"\n"
        f"<!-- TODO: description -->\n"
        f"\n"
        f"## 实现\n"
        f"```c\n"
        f"void {name}(void) {{}}\n"
        f"```\n",
        encoding="utf-8",
    )


class _FakeLLMClient:
    """Returns one numbered description per batched function."""

    def chat(self, query: str, system_prompt: str, max_tokens: int, temperature: float) -> Any:
        # Count how many `[N] Module:` entries appear in the prompt.
        count = query.count("] Module:")
        lines = [f"[{i + 1}] desc-{i + 1}" for i in range(count)]
        return SimpleNamespace(content="\n".join(lines))


def test_generate_descriptions_reports_per_function(tmp_path, monkeypatch):
    """Each function handled must fire its own progress_cb (tick), not just each batch."""
    artifact_dir = tmp_path / "artifacts"
    funcs_dir = artifact_dir / "api_docs" / "funcs"
    funcs_dir.mkdir(parents=True)

    # 12 funcs across 2 batches (_DESC_BATCH_SIZE == 10).
    names = [f"func_{i:02d}" for i in range(12)]
    for n in names:
        _write_todo_func(funcs_dir, n)

    monkeypatch.setattr(pipeline, "create_llm_client", lambda: _FakeLLMClient())

    events: list[tuple[str, float]] = []

    pipeline.generate_descriptions_step(
        artifact_dir=artifact_dir,
        repo_path=tmp_path,
        progress_cb=lambda msg, pct: events.append((msg, pct)),
    )

    # A "tick" is a per-function done message: "[done/total] ✓ name".
    tick_events = [e for e in events if "✓" in e[0]]
    assert len(tick_events) == 12, (
        f"Expected one tick per function, got {len(tick_events)}: {tick_events}"
    )

    # Ticks must reference every function name, and increment monotonically.
    for i, (msg, pct) in enumerate(tick_events, start=1):
        assert f"[{i}/12]" in msg, f"tick {i} missing position marker: {msg!r}"
        assert 0 <= pct <= 100
    pcts = [pct for _, pct in tick_events]
    assert pcts == sorted(pcts), "tick percentages must be monotonically non-decreasing"
    assert pcts[-1] == pytest.approx(100.0, abs=1e-6)

    # Batch-header events should preview function names (L1 spinner needs this).
    header_events = [e for e in events if "LLM:" in e[0]]
    assert len(header_events) == 2, f"Expected 2 batch headers, got: {header_events}"
    for hdr_msg, _ in header_events:
        # Preview should contain at least one real function name prefix.
        assert "func_" in hdr_msg


def test_generate_descriptions_cjk_names_safe(tmp_path, monkeypatch):
    """CJK function names must not crash the preview/tick path (name[:18] on code points)."""
    artifact_dir = tmp_path / "artifacts"
    funcs_dir = artifact_dir / "api_docs" / "funcs"
    funcs_dir.mkdir(parents=True)

    cjk_names = ["计算平均值", "データ変換関数", "データベース接続_管理"]
    for n in cjk_names:
        _write_todo_func(funcs_dir, n)

    monkeypatch.setattr(pipeline, "create_llm_client", lambda: _FakeLLMClient())

    events: list[tuple[str, float]] = []
    result = pipeline.generate_descriptions_step(
        artifact_dir=artifact_dir,
        repo_path=tmp_path,
        progress_cb=lambda m, p: events.append((m, p)),
    )

    assert result["generated_count"] == 3
    # Each CJK name must appear in a tick.
    ticks = [m for m, _ in events if "✓" in m]
    for n in cjk_names:
        assert any(n in t for t in ticks), f"CJK name {n!r} missing from ticks: {ticks}"


# ---------------------------------------------------------------------------
# Embedding step
# ---------------------------------------------------------------------------


class _FakeEmbedder:
    def __init__(self, dim: int = 4) -> None:
        self._dim = dim

    def get_embedding_dimension(self) -> int:
        return self._dim

    def embed_batch(
        self, texts: list[str], progress_cb=None
    ) -> list[list[float]]:
        return [[float(i + 1)] * self._dim for i, _ in enumerate(texts)]


def _write_l3_for_embedding(funcs_dir: Path, name: str) -> None:
    (funcs_dir / f"{name}.md").write_text(
        f"# {name}\n"
        f"- 签名: `void {name}(void)`\n"
        f"- 模块: test_module\n"
        f"\n"
        f"> A short description for {name}.\n"
        f"\n"
        f"## 实现\n"
        f"```c\n"
        f"void {name}(void) {{}}\n"
        f"```\n",
        encoding="utf-8",
    )


def test_build_vector_index_reports_per_function(tmp_path, monkeypatch):
    artifact_dir = tmp_path / "artifacts"
    funcs_dir = artifact_dir / "api_docs" / "funcs"
    funcs_dir.mkdir(parents=True)

    # _EMBED_BATCH_SIZE is 25 → use 30 funcs to get two batches.
    names = [f"embed_fn_{i:02d}" for i in range(30)]
    for n in names:
        _write_l3_for_embedding(funcs_dir, n)

    from terrain.domains.core.embedding import qwen3_embedder as qwen_mod

    monkeypatch.setattr(qwen_mod, "create_embedder", lambda batch_size=None: _FakeEmbedder())

    vectors_path = artifact_dir / "vectors.pkl"
    events: list[tuple[str, float]] = []

    pipeline.build_vector_index(
        builder=None,
        repo_path=tmp_path,
        vectors_path=vectors_path,
        rebuild=True,
        progress_cb=lambda m, p: events.append((m, p)),
    )

    tick_events = [e for e in events if "✓" in e[0]]
    assert len(tick_events) == 30, (
        f"Expected one embed tick per function, got {len(tick_events)}"
    )
    # Percentages within embedding stay inside [16.0, 40.0] per pipeline weighting.
    for _, pct in tick_events:
        assert 16.0 <= pct <= 40.0 + 1e-6
    # Must be monotonic.
    pcts = [p for _, p in tick_events]
    assert pcts == sorted(pcts)
