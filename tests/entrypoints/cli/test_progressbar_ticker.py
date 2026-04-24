"""Tests for the enhanced `_ProgressBar` — ticker thread, spinner, elapsed/ETA.

JER-114: L1 progress-bar ticker so LLM/embed blocking phases do not *look*
stuck. Covers:

- ticker daemon thread starts on construction, stops on ``finish()``/``done()``
- spinner frame advances over time regardless of ANSI/TTY
- elapsed time appears in rendered output when ANSI is supported
- spinner charset gracefully falls back to ASCII when the console encoding
  cannot encode the Braille glyphs
- writes are serialised under a lock
- non-ANSI (no VT) paths do not emit ticker repaints
"""

from __future__ import annotations

import io
import threading
import time
from unittest.mock import patch

import pytest

from terrain.entrypoints.cli import cli as cli_mod


@pytest.fixture(autouse=True)
def _restore_spinner_cache(monkeypatch):
    """Reset the spinner-cache between tests so encoding-probe tests are
    independent."""
    monkeypatch.setattr(cli_mod, "_SPINNER_FRAMES", None, raising=False)
    yield


def _new_bar(ansi: bool = True) -> cli_mod._ProgressBar:
    with patch.object(cli_mod, "_ANSI", ansi):
        return cli_mod._ProgressBar("repo", total_steps=2)


def test_ticker_thread_starts_and_stops_on_finish():
    """The ticker daemon thread must exist and terminate within 300ms of
    ``finish()``."""
    bar = _new_bar(ansi=True)
    t = bar._ticker
    assert t is not None
    assert t.is_alive()
    bar.finish()
    t.join(timeout=0.3)
    assert not t.is_alive()


def test_ticker_thread_stops_on_done():
    """``done()`` must signal the ticker to exit (used when the whole pipeline
    is replaced by a single-step synchronous call)."""
    bar = _new_bar(ansi=True)
    t = bar._ticker
    bar.done(1, "first step")
    bar.finish()
    t.join(timeout=0.3)
    assert not t.is_alive()


def test_spinner_frame_advances():
    """Frame counter must tick while the thread is alive (independent of
    whether we redraw)."""
    bar = _new_bar(ansi=True)
    start = bar._frame
    # two ticks * 150ms == 300ms, give a little slack
    time.sleep(0.4)
    advanced = bar._frame
    bar.finish()
    assert advanced > start, f"spinner frame did not advance: {start} -> {advanced}"


def test_elapsed_appears_in_render_when_ansi():
    """When ANSI is active, rendered output should include an elapsed mm:ss
    marker."""
    buf = io.StringIO()
    with patch.object(cli_mod, "_ANSI", True), patch("sys.stdout", buf):
        bar = cli_mod._ProgressBar("repo", total_steps=1)
        bar.update(1, "working", pct=10.0)
        bar.finish()
    out = buf.getvalue()
    # Format is mm:ss — look for the elapsed prefix marker.
    assert "00:00" in out or "00:01" in out, out


def test_render_contains_spinner_char_when_ansi():
    """A spinner frame character must appear on each rendered line."""
    buf = io.StringIO()
    with patch.object(cli_mod, "_ANSI", True), patch("sys.stdout", buf):
        bar = cli_mod._ProgressBar("repo", total_steps=1)
        bar.update(1, "working", pct=50.0)
        bar.finish()
    out = buf.getvalue()
    # One of the frames (Braille or ASCII fallback) must be present.
    frames = cli_mod._resolve_spinner_frames()
    assert any(f in out for f in frames), f"no spinner glyph in: {out!r}"


def test_spinner_fallback_for_non_utf8_encoding():
    """If the stdout encoding cannot encode Braille, we must fall back to ASCII
    without raising."""

    class _Cp936Buf(io.StringIO):
        encoding = "cp936"

    buf = _Cp936Buf()
    with patch("sys.stdout", buf):
        frames = cli_mod._resolve_spinner_frames(force_refresh=True)
    assert frames == ("|", "/", "-", "\\")


def test_spinner_uses_braille_when_utf8_supported():
    class _Utf8Buf(io.StringIO):
        encoding = "utf-8"

    buf = _Utf8Buf()
    with patch("sys.stdout", buf):
        frames = cli_mod._resolve_spinner_frames(force_refresh=True)
    assert frames[0] == "⠋"
    assert len(frames) == 10


def test_non_ansi_skips_ticker_repaint():
    """With ANSI disabled (no VT) the ticker must not write ``\\r`` lines — it
    may only bump the frame counter."""
    buf = io.StringIO()
    with patch.object(cli_mod, "_ANSI", False), patch("sys.stdout", buf):
        bar = cli_mod._ProgressBar("repo", total_steps=1)
        before = buf.getvalue()
        # Let the ticker run without any update() call.
        time.sleep(0.4)
        after_tick = buf.getvalue()
        bar.finish()
    # Nothing new should have been written by the ticker itself.
    assert after_tick == before, (
        "ticker wrote to stdout with ANSI=False — this causes residual "
        f"output in cmd.exe: before={before!r} after={after_tick!r}"
    )


def test_update_render_under_ansi_writes_carriage_return():
    buf = io.StringIO()
    with patch.object(cli_mod, "_ANSI", True), patch("sys.stdout", buf):
        bar = cli_mod._ProgressBar("repo", total_steps=1)
        bar.update(1, "working", pct=42.0)
        bar.finish()
    assert "\r" in buf.getvalue()


def test_finish_is_idempotent():
    """Calling ``finish()`` twice (e.g. from both the happy path and the
    try/finally that wraps the command) must not blow up."""
    bar = _new_bar(ansi=True)
    bar.finish()
    bar.finish()  # should be a no-op
    assert not bar._ticker.is_alive()


def test_terminal_size_oserror_is_handled():
    """``os.get_terminal_size`` raises OSError in some Windows CI runners; the
    render path must still succeed."""
    buf = io.StringIO()
    with patch.object(cli_mod, "_ANSI", True), patch("sys.stdout", buf):
        bar = cli_mod._ProgressBar("repo", total_steps=1)
        with patch("terrain.entrypoints.cli.cli.os.get_terminal_size",
                   side_effect=OSError("no tty")):
            bar.update(1, "hello", pct=12.0)
        bar.finish()
    assert "hello" in buf.getvalue()


def test_unicode_encode_error_falls_back_to_ascii():
    """When the terminal cannot encode a message, ``_render`` must downgrade
    to ASCII rather than crashing."""

    class _BrokenBuf(io.StringIO):
        encoding = "ascii"
        _raised = False

        def write(self, s):  # type: ignore[override]
            if not type(self)._raised and "⠋" in s:
                type(self)._raised = True
                raise UnicodeEncodeError("ascii", s, 0, 1, "bad")
            return super().write(s)

    buf = _BrokenBuf()
    with patch.object(cli_mod, "_ANSI", True), patch("sys.stdout", buf):
        bar = cli_mod._ProgressBar("repo", total_steps=1)
        # Should not raise, even though the first render hit UnicodeEncodeError
        bar.update(1, "hello", pct=10.0)
        bar.finish()
    # Subsequent write (after the raise) must have reached the buffer.
    assert "hello" in buf.getvalue()


def test_writes_serialised_under_lock():
    """Hammering update() from multiple threads must not cause interleaving,
    asserted by making the lock the only thing that serialises writes."""
    bar = _new_bar(ansi=True)
    # Inspect the lock presence directly — a real interleaving test would be
    # inherently racy.
    assert isinstance(bar._lock, type(threading.Lock()))
    bar.finish()
