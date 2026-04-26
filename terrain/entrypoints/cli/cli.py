"""Command-line interface for Terrain.

Examples:
    # Scan a repository with Kùzu backend
    $ terrain scan /path/to/repo --backend kuzu --db-path ./graph.db

    # Scan with specific exclusions
    $ terrain scan /path/to/repo --exclude tests,docs --exclude-pattern "*.md"

    # Query the graph
    $ terrain query "MATCH (f:Function) RETURN f.name LIMIT 5" --db-path ./graph.db

    # Export to JSON
    $ terrain export /path/to/repo --output ./output.json

    # Use configuration file
    $ terrain scan /path/to/repo --config ./config.yaml
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import sys
import threading
import time
from pathlib import Path, PurePath
from typing import Any


# ---------------------------------------------------------------------------
# ANSI colour helpers — degrade gracefully on Windows without VT support
# ---------------------------------------------------------------------------

def _init_ansi() -> bool:
    """Return True if ANSI escape codes are supported on this terminal."""
    if not sys.stdout.isatty():
        return False
    if platform.system() == "Windows":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_ulong()
            kernel32.GetConsoleMode(handle, ctypes.byref(mode))
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            return bool(kernel32.SetConsoleMode(handle, mode.value | 0x0004))
        except Exception:
            return False
    return True


_ANSI = _init_ansi()


def _c(code: str, text: str) -> str:
    """Wrap *text* in an ANSI SGR sequence, or return plain text if unsupported."""
    if not _ANSI:
        return text
    return f"\033[{code}m{text}\033[0m"


# ---------------------------------------------------------------------------
# Spinner frames — Braille for UTF-8 terminals, ASCII fallback for cp936 etc.
# ---------------------------------------------------------------------------

_SPINNER_BRAILLE: tuple[str, ...] = (
    "⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏",
)
_SPINNER_ASCII: tuple[str, ...] = ("|", "/", "-", "\\")
_SPINNER_FRAMES: tuple[str, ...] | None = None

# Active progress bar — set by _ProgressBar.__init__, cleared by finish().
# Used by _ProgressAwareStderr to coordinate stderr writes with the progress line.
_ACTIVE_BAR: "_ProgressBar | None" = None


class _ProgressAwareStderr:
    """sys.stderr drop-in that prevents loguru from breaking the progress bar.

    When a log line arrives while a progress bar is active:
      1. Erase the current progress line  (\\r\\033[K on stdout)
      2. Write the log content to the real stderr
      3. Redraw the progress bar on stdout

    This keeps log messages visible without causing "screen flood" from the
    ticker re-rendering on successive blank lines.
    """

    def __init__(self, real_stderr) -> None:
        self._real = real_stderr

    def write(self, data: str) -> int:
        bar = _ACTIVE_BAR
        if _ANSI and bar is not None and not bar._finished and bar._last_step > 0:
            with bar._lock:
                sys.stdout.write("\r\033[K")
                sys.stdout.flush()
                result = self._real.write(data)
                self._real.flush()
                if data.endswith("\n"):
                    bar._render_locked(bar._last_step, bar._last_msg, bar._last_pct)
                    sys.stdout.flush()
            return result
        return self._real.write(data)

    def flush(self) -> None:
        self._real.flush()

    def __getattr__(self, name: str):
        return getattr(self._real, name)


def _resolve_spinner_frames(force_refresh: bool = False) -> tuple[str, ...]:
    """Return the spinner character set for the current stdout encoding.

    Probes whether the Braille set round-trips through the console encoding.
    Non-UTF-8 Windows code pages (cp936, cp437, cp850) fall back to ASCII.
    """
    global _SPINNER_FRAMES
    if _SPINNER_FRAMES is not None and not force_refresh:
        return _SPINNER_FRAMES
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        "".join(_SPINNER_BRAILLE).encode(encoding)
        frames = _SPINNER_BRAILLE
    except (UnicodeEncodeError, LookupError):
        frames = _SPINNER_ASCII
    _SPINNER_FRAMES = frames
    return frames


def _fmt_mmss(seconds: float) -> str:
    if seconds < 0 or seconds != seconds:  # NaN check
        return "--:--"
    s = int(seconds)
    return f"{s // 60:02d}:{s % 60:02d}"


class _ProgressBar:
    """Single-line progress bar that overwrites itself in place.

    Runs a 150ms daemon ticker so the spinner keeps spinning and the elapsed
    clock keeps advancing during blocking phases (LLM / embedding), which
    otherwise make the bar *look* stuck.

    Usage:
        bar = _ProgressBar("graph", total_steps=4)
        bar.update(1, "Scanning files...", pct=30.0)
        bar.update(1, "Scanning files...", pct=80.0)
        bar.done(1, "Graph built: 1234 nodes")
        bar.finish()   # after all steps — also wire into try/finally
    """

    BAR_WIDTH = 24
    _TICK_INTERVAL = 0.15  # seconds

    def __init__(self, repo_name: str, total_steps: int) -> None:
        self._total = total_steps
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._frame = 0
        self._started_at = time.monotonic()
        # Samples of (elapsed_seconds, pct) for ETA linear-regression.
        self._pct_history: list[tuple[float, float]] = []
        self._last_step = 0
        self._last_msg = ""
        self._last_pct: float = -1.0
        self._finished = False
        self._ascii_only = False  # flips to True after a UnicodeEncodeError
        self._frames = _resolve_spinner_frames()
        print(f"\nIndexing  {_c('1', repo_name)}  ({total_steps} steps)\n")
        # Start the ticker even when ANSI is disabled: it still advances the
        # frame counter, so that the first `update()` renders a non-zero frame
        # and elapsed/ETA numbers are fresh.
        self._ticker = threading.Thread(
            target=self._tick_loop, name="progressbar-ticker", daemon=True,
        )
        self._ticker.start()
        # Intercept sys.stderr so that loguru (and any other stderr writer)
        # coordinates with the progress line instead of flooding the screen.
        global _ACTIVE_BAR
        self._saved_stderr = sys.stderr
        sys.stderr = _ProgressAwareStderr(sys.stderr)
        _ACTIVE_BAR = self

    # -- internals ------------------------------------------------------

    def _tick_loop(self) -> None:
        while not self._stop.wait(self._TICK_INTERVAL):
            with self._lock:
                self._frame = (self._frame + 1) % max(len(self._frames), 1)
                # In non-ANSI terminals we cannot clear the line, so repainting
                # with \r leaves residual text in cmd.exe. Just bump the frame
                # and let the next update() / done() call render normally.
                if _ANSI and self._last_step > 0 and not self._finished:
                    self._render_locked(self._last_step, self._last_msg, self._last_pct)

    def _eta_seconds(self, pct: float) -> float:
        """Estimate ETA from recent progress samples. Returns NaN if unknown."""
        if pct <= 0 or pct >= 100:
            return float("nan")
        # Use the oldest kept sample against the latest: slope = Δpct / Δt.
        if not self._pct_history:
            return float("nan")
        t0, p0 = self._pct_history[0]
        t1 = time.monotonic() - self._started_at
        dp = pct - p0
        dt = t1 - t0
        if dp <= 0 or dt <= 0:
            return float("nan")
        remaining_pct = 100.0 - pct
        return remaining_pct * dt / dp

    def _render_locked(self, step: int, msg: str, pct: float) -> None:
        """Render the current line. MUST be called with ``self._lock`` held."""
        filled = int(self.BAR_WIDTH * max(0.0, min(100.0, pct)) / 100)
        spinner = self._frames[self._frame % len(self._frames)]
        elapsed = time.monotonic() - self._started_at
        eta = self._eta_seconds(pct)
        if _ANSI:
            bar = _c("32", "█" * filled) + _c("2", "░" * (self.BAR_WIDTH - filled))
            pct_str = _c("1", f"{pct:3.0f}%")
            step_str = _c("2", f"{step}/{self._total}")
        else:
            bar = "#" * filled + "-" * (self.BAR_WIDTH - filled)
            pct_str = f"{pct:3.0f}%"
            step_str = f"{step}/{self._total}"

        try:
            term_w = os.get_terminal_size().columns
        except OSError:
            term_w = 80
        prefix = (
            f"  {spinner} [{bar}] {pct_str}  step {step_str}  "
            f"{_fmt_mmss(elapsed)} / ETA {_fmt_mmss(eta)}  "
        )
        # Visible prefix width ≈ 63 + len(step_str) ≈ 66–68 chars.
        # Using 70 ensures the full line never exceeds term_w and avoids the
        # wrap-then-\r\033[K residual-line flooding bug.
        max_msg = max(10, term_w - 70)
        display_msg = msg[:max_msg] + "…" if len(msg) > max_msg else msg

        line = f"{prefix}{display_msg}"
        if _ANSI:
            payload = f"\r\033[K{line}"
        else:
            payload = f"\r{line}"
        try:
            sys.stdout.write(payload)
            sys.stdout.flush()
        except UnicodeEncodeError:
            # Console encoding (e.g. cp437) can't render Braille / Unicode
            # ellipsis. Permanently downgrade to ASCII-safe output.
            self._ascii_only = True
            self._frames = _SPINNER_ASCII
            ascii_msg = msg.encode("ascii", "replace").decode("ascii")
            ascii_line = f"  {self._frames[self._frame % len(self._frames)]} [{bar}] {pct_str}  step {step_str}  {_fmt_mmss(elapsed)} / ETA {_fmt_mmss(eta)}  {ascii_msg[:max_msg]}"
            if _ANSI:
                ascii_payload = f"\r\033[K{ascii_line}"
            else:
                ascii_payload = f"\r{ascii_line}"
            try:
                sys.stdout.write(ascii_payload)
                sys.stdout.flush()
            except Exception:
                pass

    # -- public API -----------------------------------------------------

    def update(self, step: int, msg: str, pct: float) -> None:
        """Update the in-place progress line."""
        with self._lock:
            if pct <= self._last_pct and pct > 0:
                # Still refresh the remembered msg so the ticker shows it.
                self._last_msg = msg
                self._last_step = step
                return
            self._last_pct = pct
            self._last_msg = msg
            self._last_step = step
            # Keep a short history for ETA regression (cap at 16 samples).
            self._pct_history.append((time.monotonic() - self._started_at, pct))
            if len(self._pct_history) > 16:
                self._pct_history.pop(0)
            self._render_locked(step, msg, pct)

    def done(self, step: int, msg: str) -> None:
        """Mark a step as complete — prints a finalised line and moves to next line."""
        with self._lock:
            self._last_pct = -1.0
            self._last_step = step
            self._last_msg = msg
            self._pct_history.clear()
            self._render_locked(step, msg, 100.0)
            if _ANSI:
                label = _c("32", "✓")
            else:
                label = "done"
            try:
                sys.stdout.write(f"  {label}\n")
                sys.stdout.flush()
            except UnicodeEncodeError:
                sys.stdout.write("  done\n")
                sys.stdout.flush()

    def finish(self) -> None:
        """Stop the ticker and print the completion summary line.

        Idempotent — safe to call from both the happy path and a try/finally
        cleanup wrapper around the top-level command.
        """
        if self._finished:
            return
        self._stop.set()
        with self._lock:
            # Restore sys.stderr before setting _finished so that any in-flight
            # loguru write that's waiting on the lock sees the restored stream.
            global _ACTIVE_BAR
            _ACTIVE_BAR = None
            sys.stderr = self._saved_stderr
            self._finished = True
            try:
                sys.stdout.write("\n")
                sys.stdout.flush()
            except Exception:
                pass
        # Join is best-effort; the daemon thread won't block process exit.
        self._ticker.join(timeout=0.5)

from loguru import logger

# Replace loguru's built-in stderr handler with a proxy that always delegates
# to the *current* sys.stderr.  This lets _ProgressBar swap sys.stderr at
# runtime to coordinate log output with the progress line.
class _StderrProxy:
    def write(self, msg: str) -> int:
        return sys.stderr.write(msg)
    def flush(self) -> None:
        try:
            sys.stderr.flush()
        except Exception:
            pass
    def __getattr__(self, name: str):
        return getattr(sys.stderr, name)

_STDERR_PROXY = _StderrProxy()

logger.remove()
logger.add(
    _STDERR_PROXY,
    level="INFO",
    format=(
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan>"
        " - <level>{message}</level>"
    ),
    colorize=True,
)

from terrain import __version__
from terrain.domains.core.graph.builder import TerrainBuilder
from terrain.entrypoints.mcp.tools import _resolve_artifact_dir
from terrain.foundation.types.config import (
    KuzuConfig,
    MemgraphConfig,
    MemoryConfig,
    OutputConfig,
    ScanConfig,
)


def setup_logging(verbose: bool = False) -> None:
    """Setup logging configuration."""
    level = "DEBUG" if verbose else "INFO"
    logger.remove()
    logger.add(
        _STDERR_PROXY,
        level=level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan>"
            " - <level>{message}</level>"
        ),
        colorize=True,
    )


def load_config_file(config_path: str | Path) -> dict[str, Any]:
    """Load configuration from YAML or JSON file."""
    config_path = Path(config_path)

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    if config_path.suffix in (".yaml", ".yml"):
        try:
            import yaml

            with open(config_path, encoding="utf-8") as f:
                return yaml.safe_load(f)
        except ImportError:
            raise ImportError("PyYAML is required for YAML config files. Install with: pip install pyyaml")
    elif config_path.suffix == ".json":
        with open(config_path, encoding="utf-8") as f:
            return json.load(f)
    else:
        raise ValueError(f"Unsupported config file format: {config_path.suffix}")


def create_builder_from_args(args: argparse.Namespace) -> TerrainBuilder:
    """Create TerrainBuilder from command-line arguments."""
    # Load config file if specified
    config = {}
    if hasattr(args, 'config') and args.config:
        config = load_config_file(args.config)

    # Determine backend
    backend = getattr(args, 'backend', None) or config.get("backend", "kuzu")

    # Build backend config
    backend_config = config.get("backend_config", {})
    if backend == "kuzu":
        if getattr(args, 'db_path', None):
            backend_config["db_path"] = args.db_path
        if getattr(args, 'batch_size', None):
            backend_config["batch_size"] = args.batch_size
    elif backend == "memgraph":
        if getattr(args, 'host', None):
            backend_config["host"] = args.host
        if getattr(args, 'port', None):
            backend_config["port"] = args.port
        if getattr(args, 'username', None):
            backend_config["username"] = args.username
        if getattr(args, 'password', None):
            backend_config["password"] = args.password
        if getattr(args, 'batch_size', None):
            backend_config["batch_size"] = args.batch_size

    # Build scan config
    scan_config = config.get("scan_config", {})
    if getattr(args, 'exclude', None):
        # Parse comma-separated exclusions
        exclude_set = set()
        for item in args.exclude:
            exclude_set.update(item.split(","))
        scan_config["exclude_patterns"] = exclude_set
    if getattr(args, 'exclude_pattern', None):
        scan_config.setdefault("exclude_patterns", set()).update(args.exclude_pattern)
    if getattr(args, 'language', None):
        scan_config["include_languages"] = set(args.language.split(","))
    if getattr(args, 'max_file_size', None):
        scan_config["max_file_size"] = args.max_file_size

    # Create builder
    return TerrainBuilder(
        repo_path=args.repo_path,
        backend=backend,
        backend_config=backend_config,
        scan_config=scan_config,
    )


def cmd_scan(args: argparse.Namespace) -> int:
    """Execute the scan command."""
    setup_logging(args.verbose)

    try:
        logger.info(f"Starting scan of: {args.repo_path}")
        logger.info(f"Backend: {args.backend or 'kuzu'}")

        builder = create_builder_from_args(args)

        # Build the graph
        result = builder.build_graph(clean=args.clean)

        print()
        print("=" * 60)
        print("SCAN COMPLETE")
        print("=" * 60)
        print(f"Repository: {result.project_name}")
        print(f"Nodes created: {result.nodes_created}")
        print(f"Relationships created: {result.relationships_created}")
        print(f"Functions found: {result.functions_found}")
        print(f"Classes found: {result.classes_found}")
        print(f"Files processed: {result.files_processed}")

        if args.backend == "kuzu" or (not args.backend and args.db_path):
            db_path = args.db_path or f"./{Path(args.repo_path).name}_graph.db"
            print(f"Database saved to: {db_path}")

        # Export to JSON if requested
        if args.output:
            logger.info(f"Exporting to: {args.output}")
            data = builder.export_graph()
            with open(args.output, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, default=str, ensure_ascii=False)
            print(f"Exported to: {args.output}")

        return 0

    except Exception as e:
        logger.error(f"Scan failed: {e}")
        if args.verbose:
            import traceback

            traceback.print_exc()
        return 1


def cmd_query(args: argparse.Namespace) -> int:
    """Execute the query command."""
    setup_logging(args.verbose)

    try:
        # Determine backend from args
        backend = args.backend or "kuzu"
        backend_config = {"db_path": args.db_path} if args.db_path else {}

        builder = TerrainBuilder(
            repo_path=args.repo_path or ".",
            backend=backend,
            backend_config=backend_config,
        )

        logger.info(f"Executing query: {args.cypher_query}")
        results = builder.query(args.cypher_query)

        print()
        print("=" * 60)
        print("QUERY RESULTS")
        print("=" * 60)
        print(f"Query: {args.cypher_query}")
        print(f"Results: {len(results)}")
        print()

        if results:
            # Print as table
            if args.format == "table":
                headers = list(results[0].keys())
                # Calculate column widths
                widths = {h: len(h) for h in headers}
                for row in results:
                    for h in headers:
                        widths[h] = max(widths[h], len(str(row.get(h, ""))))

                # Print header
                header_line = " | ".join(h.ljust(widths[h]) for h in headers)
                print(header_line)
                print("-" * len(header_line))

                # Print rows
                for row in results:
                    print(" | ".join(str(row.get(h, "")).ljust(widths[h]) for h in headers))
            else:
                # JSON format
                for i, row in enumerate(results, 1):
                    print(f"{i}. {json.dumps(row, default=str)}")
        else:
            print("No results found.")

        return 0

    except Exception as e:
        logger.error(f"Query failed: {e}")
        if args.verbose:
            import traceback

            traceback.print_exc()
        return 1


def cmd_export(args: argparse.Namespace) -> int:
    """Execute the export command."""
    setup_logging(args.verbose)

    try:
        builder = create_builder_from_args(args)

        logger.info(f"Exporting graph from: {args.repo_path}")

        # Build if not already built
        if args.build:
            logger.info("Building graph first...")
            builder.build_graph(clean=args.clean)

        data = builder.export_graph()

        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str, ensure_ascii=False)

        print()
        print("=" * 60)
        print("EXPORT COMPLETE")
        print("=" * 60)
        print(f"Output: {output_path.absolute()}")
        print(f"Nodes: {len(data.get('nodes', []))}")
        print(f"Relationships: {len(data.get('relationships', []))}")

        return 0

    except Exception as e:
        logger.error(f"Export failed: {e}")
        if args.verbose:
            import traceback

            traceback.print_exc()
        return 1


def _get_workspace_root() -> Path:
    return Path(
        os.environ.get("TERRAIN_WORKSPACE", Path.home() / ".terrain")
    ).expanduser().resolve()


def _load_repos(ws: Path) -> list[dict]:
    """Return all indexed repos, sorted by name, with 'active' flag set."""
    from terrain.entrypoints.link_ops import batch_migrate_to_v2
    from terrain.foundation.utils.paths import normalize_repo_path

    active_file = ws / "active.txt"
    active_name = active_file.read_text(encoding="utf-8", errors="replace").strip() if active_file.exists() else ""

    repos: list[dict] = []
    if not ws.exists():
        return repos

    # JER-101: batch v1 → v2 migration in a single O(n) pass.
    batch_migrate_to_v2(ws)

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
        resolved = _resolve_artifact_dir(child)
        raw_path = meta.get("repo_path", "unknown")
        try:
            path_display = normalize_repo_path(raw_path) if raw_path != "unknown" else raw_path
        except (TypeError, ValueError):
            path_display = raw_path
        repos.append({
            "artifact_dir": resolved,
            "name": meta.get("repo_name", child.name),
            "path": path_display,
            "indexed_at": meta.get("indexed_at", "unknown"),
            "active": child.name == active_name,
        })
    return repos


def _get_repo_status_entries(ws: "Path") -> list[dict]:
    from terrain.entrypoints.link_ops import batch_migrate_to_v2
    from terrain.foundation.services.workspace_service import get_repo_status_entries
    batch_migrate_to_v2(ws)
    return get_repo_status_entries(ws)


def _interactive_select(repos: list[dict]) -> int | None:
    """Arrow-key interactive repo selector. Returns selected index or None on cancel.

    Uses termios/tty on Unix and msvcrt on Windows.
    Falls back to numbered input when neither raw-mode nor ANSI are available.
    """
    current = next((i for i, r in enumerate(repos) if r["active"]), 0)
    drawn = False

    def render(selected: int) -> None:
        nonlocal drawn
        if _ANSI:
            if drawn:
                sys.stdout.write(f"\033[{len(repos)}A")
            for i, r in enumerate(repos):
                marker = _c("1;32", "▶") if i == selected else " "
                active_tag = f"  {_c('33', '(active)')}" if r["active"] else ""
                sys.stdout.write(f"\r\033[2K  {marker} {r['name']}{active_tag}\r\n")
            drawn = True
        else:
            for i, r in enumerate(repos):
                marker = "> " if i == selected else "  "
                active_tag = "  (active)" if r["active"] else ""
                print(f"  {marker}{r['name']}{active_tag}")
        sys.stdout.flush()

    sys.stdout.write("\n")

    if platform.system() == "Windows":
        try:
            import msvcrt  # type: ignore[import]
            render(current)
            while True:
                ch = msvcrt.getwch()
                if ch in ("\r", "\n"):
                    sys.stdout.write("\n")
                    return current
                if ch in ("\x03", "q"):
                    sys.stdout.write("\n")
                    return None
                if ch in ("\x00", "\xe0"):   # special key prefix
                    ch2 = msvcrt.getwch()
                    if ch2 == "H" and current > 0:        # up arrow
                        current -= 1
                    elif ch2 == "P" and current < len(repos) - 1:  # down arrow
                        current += 1
                    if not _ANSI:
                        print()  # blank line between redraws in plain mode
                    render(current)
        except ImportError:
            return _numbered_select(repos)
    elif sys.stdin.isatty() and _ANSI:
        try:
            import tty
            import termios

            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                render(current)       # initial render inside raw mode
                while True:
                    ch = sys.stdin.read(1)
                    if ch in ("\r", "\n"):
                        sys.stdout.write("\r\n")
                        sys.stdout.flush()
                        return current
                    if ch in ("\x03", "q"):
                        sys.stdout.write("\r\n")
                        sys.stdout.flush()
                        return None
                    if ch == "\x1b":
                        seq = sys.stdin.read(2)
                        if seq == "[A" and current > 0:
                            current -= 1
                        elif seq == "[B" and current < len(repos) - 1:
                            current += 1
                        render(current)
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except (ImportError, AttributeError):
            return _numbered_select(repos)
    else:
        return _numbered_select(repos)

    return current


def _numbered_select(repos: list[dict]) -> int | None:
    """Plain-text numbered fallback for non-interactive terminals."""
    for i, r in enumerate(repos):
        active_tag = "  (active)" if r["active"] else ""
        print(f"  {i + 1}. {r['name']}{active_tag}")
    try:
        raw = input(f"\nEnter number [1-{len(repos)}], q to cancel: ").strip()
        if raw.lower() == "q":
            return None
        idx = int(raw) - 1
        if 0 <= idx < len(repos):
            return idx
        print("Invalid selection.")
        return None
    except (ValueError, EOFError):
        return None


def _detect_llm_info() -> dict[str, str]:
    """Detect current LLM configuration from environment."""
    providers = [
        ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "Generic"),
        ("LITELLM_API_KEY", "LITELLM_BASE_URL", "LITELLM_MODEL", "LiteLLM"),
        ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL", "OpenAI"),
        ("MOONSHOT_API_KEY", "LLM_BASE_URL", "MOONSHOT_MODEL", "Moonshot"),
    ]
    for key_env, url_env, model_env, name in providers:
        api_key = os.environ.get(key_env, "")
        if api_key:
            return {
                "provider": name,
                "model": os.environ.get(model_env, "(default)"),
                "base_url": os.environ.get(url_env, "(default)"),
                "api_key": api_key[:4] + "****" + api_key[-4:] if len(api_key) >= 8 else "****",
            }
    return {}


def _detect_embed_info() -> dict[str, str]:
    """Detect current embedding configuration from environment."""
    provider = os.environ.get("EMBEDDING_PROVIDER", "").lower()
    # Try explicit EMBED_* keys first
    for key_env, url_env, model_env, name in [
        ("EMBED_API_KEY", "EMBED_BASE_URL", "EMBED_MODEL", "Custom"),
        ("EMBEDDING_API_KEY", "EMBEDDING_BASE_URL", "EMBEDDING_MODEL", "OpenAI-compatible"),
        ("DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL", "EMBED_MODEL", "Qwen3/DashScope"),
    ]:
        api_key = os.environ.get(key_env, "")
        if api_key:
            detected_name = name
            if provider:
                detected_name = provider.capitalize()
            return {
                "provider": detected_name,
                "model": os.environ.get(model_env, "(default)"),
                "base_url": os.environ.get(url_env, "(default)"),
                "api_key": api_key[:4] + "****" + api_key[-4:] if len(api_key) >= 8 else "****",
            }
    # Fallback: check if OPENAI_API_KEY or LLM_API_KEY can serve as embedding key
    for key_env, name in [("OPENAI_API_KEY", "OpenAI"), ("LLM_API_KEY", "Generic")]:
        api_key = os.environ.get(key_env, "")
        if api_key:
            return {
                "provider": name + " (shared)",
                "model": os.environ.get("EMBED_MODEL", "(default)"),
                "base_url": os.environ.get("EMBED_BASE_URL", "(default)"),
                "api_key": api_key[:4] + "****" + api_key[-4:] if len(api_key) >= 8 else "****",
            }
    return {}


def cmd_status(args: argparse.Namespace) -> int:
    """Show workspace status and staleness of indexed repositories."""
    ws = _get_workspace_root()
    env_path = ws / ".env"

    # ── Handle --json: output machine-readable repo status ──
    if getattr(args, "json", False):
        entries = _get_repo_status_entries(ws)
        print(json.dumps(entries, ensure_ascii=False, indent=2))
        return 0

    # ── Handle --debug toggle ──
    toggle = getattr(args, "debug", None)
    if toggle is not None:
        env_data = _load_env_file(env_path)
        if toggle:
            env_data["CGB_DEBUG"] = "1"
            _save_env_file(env_path, env_data)
            print(f"\n  {_c('32', 'Debug mode enabled')}  — MCP server will log to {ws / 'debug.log'}")
            print(f"  Restart the MCP server for the change to take effect.\n")
        else:
            env_data.pop("CGB_DEBUG", None)
            _save_env_file(env_path, env_data)
            print(f"\n  {_c('33', 'Debug mode disabled')}\n")
        return 0

    repos = _load_repos(ws)
    total = len(repos)

    active = next((r for r in repos if r["active"]), None)

    # ── Workspace ──
    print()
    print(f"  workspace  {_c('2', str(ws))}")

    # ── Repository ──
    if active is None:
        print(f"  active     {_c('33', '(none)')}   {total} repos indexed  —  terrain repo to select")
    else:
        cwd = Path.cwd().resolve()
        linked = next((r for r in repos if Path(r["path"]).resolve() == cwd), None)

        if linked and linked["active"]:
            print(f"  here       {_c('32', active['name'])}   {cwd}")
        elif linked:
            print(f"  here       {_c('33', linked['name'])}   {cwd}   (not active — terrain repo to switch)")
        else:
            print(f"  here       {_c('2', 'not indexed')}   {cwd}   (terrain index to add)")

        print(f"  active     {active['name']}   {active['path']}")

    # ── LLM ──
    llm = _detect_llm_info()
    if llm:
        print(f"  llm        {_c('32', llm['model'])}   {llm['provider']}   {llm['base_url']}")
    else:
        print(f"  llm        {_c('33', '(not configured)')}   — terrain config --llm-model <model> to set")

    # ── Embedding ──
    embed = _detect_embed_info()
    if embed:
        print(f"  embedding  {_c('32', embed['model'])}   {embed['provider']}   {embed['base_url']}")
    else:
        print(f"  embedding  {_c('33', '(not configured)')}   — terrain config --embed-model <model> to set")

    # ── Language parsers ──
    _CORE_LANGS: list[tuple[str, str]] = [
        ("python",     "tree_sitter_python"),
        ("javascript", "tree_sitter_javascript"),
        ("typescript", "tree_sitter_typescript"),
        ("c",          "tree_sitter_c"),
        ("c++",        "tree_sitter_cpp"),
    ]
    _EXTRA_LANGS: list[tuple[str, str]] = [
        ("rust",  "tree_sitter_rust"),
        ("go",    "tree_sitter_go"),
        ("java",  "tree_sitter_java"),
        ("lua",   "tree_sitter_lua"),
        ("scala", "tree_sitter_scala"),
    ]
    available_langs: list[str] = []
    missing_langs: list[str] = []
    for lang, module in _CORE_LANGS + _EXTRA_LANGS:
        if importlib.util.find_spec(module) is not None:
            available_langs.append(lang)
        else:
            missing_langs.append(lang)

    if missing_langs:
        print(
            f"  parsers    {_c('32', ' '.join(available_langs))}"
            f"   {_c('33', '(missing: ' + ' '.join(missing_langs) + ')')}"
        )
        print(f"             {_c('2', 'Add: npx terrain-ai@latest --setup')}")
    else:
        print(f"  parsers    {_c('32', ' '.join(available_langs))}")

    # ── Debug ──
    env_data = _load_env_file(env_path)
    debug_val = env_data.get("CGB_DEBUG", "").strip().lower()
    debug_on = debug_val in ("1", "true", "yes")
    if debug_on:
        debug_log = ws / "debug.log"
        size = ""
        if debug_log.exists():
            mb = debug_log.stat().st_size / (1024 * 1024)
            size = f"   {mb:.1f} MB"
        print(f"  debug      {_c('32', 'ON')}{size}   {debug_log}")
    else:
        print(f"  debug      {_c('2', 'OFF')}   — terrain status --debug on to enable")

    # ── Repos staleness ──
    repo_entries = _get_repo_status_entries(ws)
    if repo_entries:
        print()
        _STATUS_ICONS = {
            "up-to-date": _c("32", "✅") if _ANSI else "ok ",
            "stale":      _c("33", "⚠️") if _ANSI else "!! ",
            "unknown":    _c("2",  "❓") if _ANSI else "?  ",
        }
        _STATUS_LABELS = {
            "up-to-date": _c("32", "up-to-date"),
            "stale":      _c("33", "stale"),
            "unknown":    _c("2",  "unknown"),
        }
        name_w = max(len(e["name"]) for e in repo_entries)
        for entry in repo_entries:
            icon = _STATUS_ICONS[entry["status"]]
            label = _STATUS_LABELS[entry["status"]]
            commits = entry["commits_since"]
            indexed_at = entry["indexed_at"]
            if commits is None:
                detail = "no git repo detected"
            elif commits == 0:
                detail = f"0 commits since last index"
            else:
                detail = f"{commits} commit{'s' if commits != 1 else ''} since last index"
            name_padded = entry["name"].ljust(name_w)
            print(f"  {name_padded}  {icon}  {label:<12}  ({detail})")
    elif ws.exists():
        print(f"  repos      {_c('2', '(none indexed)')}")

    # ── Version ──
    print(f"  version    terrain-ai {__version__}")
    print()

    return 0


# ---------------------------------------------------------------------------
# config — view / modify LLM and embedding configuration
# ---------------------------------------------------------------------------

def _load_env_file(env_path: Path) -> dict[str, str]:
    """Parse a .env file into a dict."""
    if not env_path.exists():
        return {}
    result: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        eq = stripped.find("=")
        if eq == -1:
            continue
        key = stripped[:eq].strip()
        val = stripped[eq + 1:].strip()
        # Strip surrounding quotes
        if len(val) >= 2 and ((val[0] == '"' and val[-1] == '"') or (val[0] == "'" and val[-1] == "'")):
            val = val[1:-1]
        result[key] = val
    return result


def _save_env_file(env_path: Path, data: dict[str, str]) -> None:
    """Write a dict as a .env file, preserving non-empty values only."""
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# terrain configuration",
        "# Managed by terrain config / setup wizard. Edit freely.",
        "",
    ]
    for key, val in data.items():
        if val:
            lines.append(f"{key}={val}")
    lines.append("")
    env_path.write_text("\n".join(lines), encoding="utf-8")


def _mask(s: str) -> str:
    """Mask an API key for display: sk-ab****ef."""
    if not s or len(s) < 8:
        return "****" if s else "(not set)"
    return s[:4] + "****" + s[-4:]


# ── Tree-drawing characters (matches npx --setup style) ──────────────
_T_SIDE   = "│"
_T_BRANCH = "├─"
_T_LAST   = "╰─"
_T_DOT    = "●"
_T_OK     = "✓"
_T_WARN   = "⚠"


def _select_menu(options: list[str], prefix: str = "  ") -> int | None:
    """Arrow-key single-select menu.  Returns index or None on Ctrl-C/q."""
    cursor = 0
    drawn = False

    def _render_ansi() -> None:
        """Render menu items using ANSI escapes.  All calls MUST happen in the
        same terminal mode (raw) so that ``\\033[{N}A`` line-counting is
        consistent.  Each line uses ``\\r\\n`` (explicit CR+LF) because raw
        mode disables ONLCR."""
        nonlocal drawn
        if drawn:
            sys.stdout.write(f"\033[{len(options)}A")
        for i, opt in enumerate(options):
            marker = _c("1;36", "◉") if i == cursor else _c("2", "○")
            label = _c("1;36", opt) if i == cursor else opt
            sys.stdout.write(f"\r\033[2K{prefix}{marker} {label}\r\n")
        drawn = True
        sys.stdout.flush()

    # ── Try interactive raw-mode input ────────────────────────────────
    raw_ok = sys.stdin.isatty() and _ANSI

    if raw_ok and platform.system() == "Windows":
        try:
            import msvcrt
            _render_ansi()          # initial render (Windows: no OPOST issue)
            while True:
                ch = msvcrt.getwch()
                if ch in ("\r", "\n"):
                    sys.stdout.write("\n")
                    return cursor
                if ch in ("\x03", "q"):
                    sys.stdout.write("\n")
                    return None
                if ch in ("\x00", "\xe0"):
                    ch2 = msvcrt.getwch()
                    if ch2 == "H":
                        cursor = (cursor - 1) % len(options)
                    elif ch2 == "P":
                        cursor = (cursor + 1) % len(options)
                    _render_ansi()
        except ImportError:
            raw_ok = False

    elif raw_ok:
        try:
            import tty, termios
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                _render_ansi()      # initial render *inside* raw mode
                while True:
                    ch = sys.stdin.read(1)
                    if ch in ("\r", "\n"):
                        sys.stdout.write("\r\n")
                        sys.stdout.flush()
                        return cursor
                    if ch in ("\x03", "q"):
                        sys.stdout.write("\r\n")
                        sys.stdout.flush()
                        return None
                    if ch == "\x1b":
                        seq = sys.stdin.read(2)
                        if seq == "[A":
                            cursor = (cursor - 1) % len(options)
                        elif seq == "[B":
                            cursor = (cursor + 1) % len(options)
                        _render_ansi()
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except (ImportError, AttributeError):
            raw_ok = False

    if not raw_ok:
        # Fallback: numbered input
        for i, opt in enumerate(options):
            print(f"{prefix}  {i + 1}) {opt}")
        try:
            choice = input(f"{prefix}  Enter number: ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                return idx
        except (ValueError, EOFError, KeyboardInterrupt):
            pass
        return None

    return None


def _prompt(label: str, default: str = "") -> str:
    """Prompt for input with an optional default shown in brackets."""
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"  {_T_SIDE}  {label}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return default
    return val or default


# LLM / Embedding provider presets (name, base_url, default_model)
_LLM_PROVIDERS = [
    ("OpenAI / compatible",  "https://api.openai.com/v1",       "gpt-4o"),
    ("DeepSeek",             "https://api.deepseek.com/v1",     "deepseek-chat"),
    ("Moonshot / Kimi",      "https://api.moonshot.cn/v1",      "kimi-k2.5"),
    ("LiteLLM proxy",        "http://localhost:4000/v1",         "gpt-4o"),
    ("Custom endpoint",      "",                                 ""),
]

_EMBED_PROVIDERS = [
    ("DashScope / Qwen",     "https://dashscope.aliyuncs.com/api/v1", "text-embedding-v4"),
    ("OpenAI Embeddings",    "https://api.openai.com/v1",              "text-embedding-3-small"),
    ("Custom endpoint",      "",                                        ""),
]


def cmd_config(args: argparse.Namespace) -> int:
    """View or modify LLM / embedding configuration."""
    ws = _get_workspace_root()
    env_path = ws / ".env"
    env_data = _load_env_file(env_path)

    # ── Non-interactive mode: apply CLI flags directly ────────────────
    has_flags = any(
        getattr(args, a, None) is not None
        for a in ("llm_model", "llm_base_url", "llm_api_key",
                   "embed_model", "embed_base_url", "embed_api_key")
    )
    if has_flags:
        changes: list[str] = []
        for attr, env_key in [
            ("llm_model",    "LLM_MODEL"),
            ("llm_base_url", "LLM_BASE_URL"),
            ("llm_api_key",  "LLM_API_KEY"),
            ("embed_model",  "EMBED_MODEL"),
            ("embed_base_url", "EMBED_BASE_URL"),
            ("embed_api_key",  "EMBED_API_KEY"),
        ]:
            val = getattr(args, attr, None)
            if val is not None:
                env_data[env_key] = val
                os.environ[env_key] = val
                display = "****" if "api_key" in attr else val
                changes.append(f"{env_key}={display}")
        _save_env_file(env_path, env_data)
        print()
        print(f"  {_c('32', 'Configuration updated')}  ({env_path})")
        print()
        for c in changes:
            print(f"    {_c('32', _T_OK)} {c}")
        print()
        return 0

    # ── Interactive mode ──────────────────────────────────────────────
    llm_info = _detect_llm_info()
    embed_info = _detect_embed_info()

    print()
    print(f"  {_T_DOT} {_c('1', 'Current Configuration')}  ({env_path})")
    print(f"  {_T_SIDE}")

    # Show current LLM
    if llm_info:
        print(f"  {_T_BRANCH} LLM:       {_c('32', llm_info['model'])}  {_c('2', llm_info['provider'])}")
        print(f"  {_T_SIDE}             base_url  {llm_info['base_url']}")
        print(f"  {_T_SIDE}             api_key   {llm_info['api_key']}")
    else:
        print(f"  {_T_BRANCH} LLM:       {_c('33', '(not configured)')}")

    print(f"  {_T_SIDE}")

    # Show current Embedding
    if embed_info:
        print(f"  {_T_LAST} Embedding: {_c('32', embed_info['model'])}  {_c('2', embed_info['provider'])}")
        print(f"               base_url  {embed_info['base_url']}")
        print(f"               api_key   {embed_info['api_key']}")
    else:
        print(f"  {_T_LAST} Embedding: {_c('33', '(not configured)')}")

    print()

    # ── Ask what to configure ─────────────────────────────────────────
    menu_options = [
        "Configure LLM",
        "Configure Embedding",
        "Configure both",
        "Exit (no changes)",
    ]
    print(f"  {_T_DOT} What would you like to configure?")
    print(f"  {_T_SIDE}  Use ↑↓ to navigate, Enter to confirm")
    print(f"  {_T_SIDE}")

    choice = _select_menu(menu_options, prefix=f"  {_T_SIDE}  ")
    if choice is None or choice == 3:
        print(f"  {_T_LAST} No changes.")
        print()
        return 0

    configure_llm = choice in (0, 2)
    configure_embed = choice in (1, 2)
    any_saved = False

    # ── LLM Configuration ────────────────────────────────────────────
    if configure_llm:
        print()
        print(f"  {_T_DOT} LLM Provider")
        print(f"  {_T_SIDE}")
        if llm_info:
            print(f"  {_T_SIDE}  Current: {_mask(env_data.get('LLM_API_KEY', ''))} → {env_data.get('LLM_BASE_URL', '(default)')}")
            print(f"  {_T_SIDE}")

        provider_names = [p[0] for p in _LLM_PROVIDERS] + ["Skip"]
        print(f"  {_T_SIDE}  Select provider:")
        print(f"  {_T_SIDE}")

        llm_choice = _select_menu(provider_names, prefix=f"  {_T_SIDE}  ")

        if llm_choice is not None and llm_choice < len(_LLM_PROVIDERS):
            pname, purl, pmodel = _LLM_PROVIDERS[llm_choice]
            # Pre-fill with existing values or provider defaults
            cur_url = env_data.get("LLM_BASE_URL", purl) or purl
            cur_model = env_data.get("LLM_MODEL", pmodel) or pmodel
            cur_key = env_data.get("LLM_API_KEY", "")

            print(f"  {_T_SIDE}")
            new_key = _prompt("API Key", _mask(cur_key) if cur_key else "")
            # If user entered the masked version, keep old key
            if new_key == _mask(cur_key):
                new_key = cur_key

            if not purl:
                print(f"  {_T_SIDE}  {_c('2', 'Use an OpenAI-compatible base URL ending in /v1')}")
                print(f"  {_T_SIDE}  {_c('2', 'e.g. https://your-api-host.com/v1')}")
            new_url = _prompt("Base URL", cur_url if purl else "")
            new_model = _prompt("Model", cur_model if pmodel else "")

            if new_key and new_key != "****":
                env_data["LLM_API_KEY"] = new_key
            if new_url:
                env_data["LLM_BASE_URL"] = new_url
            if new_model:
                env_data["LLM_MODEL"] = new_model

            key_display = _mask(env_data.get("LLM_API_KEY", ""))
            print(f"  {_T_SIDE}")
            print(f"  {_T_LAST} {_c('32', _T_OK)} {pname} / {env_data.get('LLM_MODEL', '?')}  (key: {key_display})")
            any_saved = True
        else:
            print(f"  {_T_LAST} {_T_WARN} Skipped")

    # ── Embedding Configuration ──────────────────────────────────────
    if configure_embed:
        print()
        print(f"  {_T_DOT} Embedding Provider")
        print(f"  {_T_SIDE}")
        if embed_info:
            embed_key_display = env_data.get("EMBED_API_KEY", "") or env_data.get("DASHSCOPE_API_KEY", "")
            print(f"  {_T_SIDE}  Current: {_mask(embed_key_display)} → {env_data.get('EMBED_BASE_URL', env_data.get('DASHSCOPE_BASE_URL', '(default)'))}")
            print(f"  {_T_SIDE}")

        provider_names = [p[0] for p in _EMBED_PROVIDERS] + ["Skip"]
        print(f"  {_T_SIDE}  Select provider:")
        print(f"  {_T_SIDE}")

        embed_choice = _select_menu(provider_names, prefix=f"  {_T_SIDE}  ")

        if embed_choice is not None and embed_choice < len(_EMBED_PROVIDERS):
            ename, eurl, emodel = _EMBED_PROVIDERS[embed_choice]
            # For DashScope, use DASHSCOPE_* keys
            if embed_choice == 0:
                key_env, url_env = "DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL"
            else:
                key_env, url_env = "EMBED_API_KEY", "EMBED_BASE_URL"

            cur_url = env_data.get(url_env, eurl) or eurl
            cur_model = env_data.get("EMBED_MODEL", emodel) or emodel
            cur_key = env_data.get(key_env, "")

            print(f"  {_T_SIDE}")
            new_key = _prompt("API Key", _mask(cur_key) if cur_key else "")
            if new_key == _mask(cur_key):
                new_key = cur_key

            if not eurl:
                print(f"  {_T_SIDE}  {_c('2', 'Use an OpenAI-compatible base URL ending in /v1')}")
                print(f"  {_T_SIDE}  {_c('2', 'e.g. https://your-api-host.com/v1')}")
            new_url = _prompt("Base URL", cur_url if eurl else "")
            new_model = _prompt("Model", cur_model if emodel else "")

            if new_key and new_key != "****":
                env_data[key_env] = new_key
            if new_url:
                env_data[url_env] = new_url
            if new_model:
                env_data["EMBED_MODEL"] = new_model

            key_display = _mask(env_data.get(key_env, ""))
            print(f"  {_T_SIDE}")
            print(f"  {_T_LAST} {_c('32', _T_OK)} {ename} / {env_data.get('EMBED_MODEL', '?')}  (key: {key_display})")
            any_saved = True
        else:
            print(f"  {_T_LAST} {_T_WARN} Skipped")

    # ── Save ─────────────────────────────────────────────────────────
    if any_saved:
        _save_env_file(env_path, env_data)
        # Also update os.environ for immediate effect in this process
        for key, val in env_data.items():
            if val:
                os.environ[key] = val
        print()
        print(f"  {_T_DOT} {_c('32', 'Configuration saved')}")
        print(f"  {_T_SIDE}")
        print(f"  {_T_BRANCH} File: {env_path}")
        llm_model = env_data.get("LLM_MODEL", "")
        embed_model = env_data.get("EMBED_MODEL", "")
        if llm_model:
            print(f"  {_T_BRANCH} LLM:       {llm_model}  →  {env_data.get('LLM_BASE_URL', '?')}")
        if embed_model:
            print(f"  {_T_BRANCH} Embedding: {embed_model}  →  {env_data.get('EMBED_BASE_URL', env_data.get('DASHSCOPE_BASE_URL', '?'))}")
        print(f"  {_T_LAST} {_c('2', 'Restart MCP server or call reload_config for changes to take effect.')}")
        print()
    else:
        print()
        print(f"  No changes made.")
        print()

    return 0


def cmd_list(_args: argparse.Namespace) -> int:
    """List all indexed repositories in the workspace."""
    ws = _get_workspace_root()
    repos = _load_repos(ws)

    print()
    print("=" * 60)
    print(f"INDEXED REPOSITORIES  ({len(repos)} total)")
    print(f"workspace: {ws}")
    print("=" * 60)
    if not repos:
        print("No repositories indexed yet.")
        return 0

    for r in repos:
        marker = "* " if r["active"] else "  "
        print(f"{marker}{r['name']}")
        print(f"    path:       {r['path']}")
        print(f"    indexed_at: {r['indexed_at']}")
    print()
    print("* = active  |  Switch with: terrain repo")
    return 0


def _rename_repo(artifact_dir: Path, new_name: str) -> None:
    """Update repo_name in meta.json."""
    meta_file = artifact_dir / "meta.json"
    if not meta_file.exists():
        return
    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return
    meta["repo_name"] = new_name
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_repo(_args: argparse.Namespace) -> int:
    """Interactively select and switch the active repository."""
    ws = _get_workspace_root()
    repos = _load_repos(ws)

    if not repos:
        print("No repositories indexed yet.")
        return 1

    print(f"Select repository  ({len(repos)} indexed)  ↑/↓ to move, Enter to confirm, q to cancel")

    idx = _interactive_select(repos)
    if idx is None:
        print("Cancelled.")
        return 0

    target = repos[idx]
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "active.txt").write_text(target["artifact_dir"].name, encoding="utf-8")
    print(f"Switched to: {target['name']}")
    print(f"Path: {target['path']}")

    # Offer rename after switching
    try:
        rename_input = input(f"Rename [{target['name']}] (Enter to keep): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        rename_input = ""
    if rename_input and rename_input != target["name"]:
        _rename_repo(target["artifact_dir"], rename_input)
        print(f"Renamed to: {rename_input}")

    return 0


# ---------------------------------------------------------------------------
# link — associate a local repo path with an existing artifact database
# ---------------------------------------------------------------------------

def _parse_repo_path(raw: str) -> tuple[Path, bool]:
    """Parse a repo path string, handling Windows paths on non-Windows systems.

    Handles paths copied from Windows File Explorer address bar, e.g.:
        ``C:\\Users\\john\\project``  ``D:\\work\\myrepo``

    Returns:
        (path, is_remote) — *is_remote* is True when the path belongs to a
        different OS (e.g. a Windows path on Linux) and cannot be validated
        locally.
    """
    import re
    from pathlib import PureWindowsPath

    cleaned = raw.strip().strip('"').strip("'")

    # Detect Windows absolute path:  X:\... or X:/...
    is_win_path = bool(re.match(r'^[A-Za-z]:[/\\]', cleaned))

    if is_win_path and platform.system() != "Windows":
        # Running on Linux/macOS but got a Windows path — use PureWindowsPath
        # so .as_posix() / .name / .anchor work correctly.
        return PureWindowsPath(cleaned), True  # type: ignore[return-value]

    # Native path — resolve normally
    return Path(cleaned).resolve(), False


def cmd_link(args: argparse.Namespace) -> int:
    """Link a local repository to an existing artifact database."""
    import shutil

    ws = _get_workspace_root()
    repo_path, is_remote = _parse_repo_path(args.repo_path)

    if not is_remote and not repo_path.exists():
        print(f"  {_c('31', 'ERROR')} Path does not exist: {repo_path}")
        return 1
    if is_remote:
        print(f"  {_c('2', 'note')}  Remote path (Windows) — skipping local existence check.")

    # ── Discover candidate artifact dirs in workspace ────────────────
    # A candidate is any dir with meta.json (and ideally graph.db)
    candidates: list[dict] = []
    if ws.exists():
        for child in sorted(ws.iterdir()):
            if not child.is_dir():
                continue
            meta_file = child / "meta.json"
            # Also accept dirs with graph.db but no meta.json (raw copy from others)
            has_db = (child / "graph.db").exists()
            has_meta = meta_file.exists()
            if not has_db and not has_meta:
                continue

            meta: dict = {}
            if has_meta:
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8", errors="replace"))
                except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                    pass

            candidates.append({
                "artifact_dir": child,
                "dir_name": child.name,
                "repo_name": meta.get("repo_name", child.name),
                "repo_path": meta.get("repo_path", "(unset)"),
                "has_graph": has_db,
                "has_vectors": (child / "vectors.pkl").exists(),
                "has_api_docs": (child / "api_docs" / "index.md").exists(),
                "has_wiki": (child / "wiki" / "index.md").exists(),
                "meta": meta,
            })

    if not candidates:
        print(f"  {_c('31', 'ERROR')} No artifact directories found in workspace: {ws}")
        print(f"  Copy an artifact directory (with graph.db) into {ws} first.")
        return 1

    # ── Select which artifact to link ─────────────────────────────────
    db_name = getattr(args, "db", None)
    selected: dict | None = None

    if db_name:
        # Match by dir name or repo_name
        for c in candidates:
            if c["dir_name"] == db_name or c["repo_name"] == db_name:
                selected = c
                break
        if selected is None:
            print(f"  {_c('31', 'ERROR')} Artifact '{db_name}' not found in workspace.")
            print(f"  Available: {', '.join(c['dir_name'] for c in candidates)}")
            return 1
    elif len(candidates) == 1:
        selected = candidates[0]
    else:
        # Interactive selection
        print()
        print(f"  {_T_DOT} {_c('1', 'Select artifact database to link')}")
        print(f"  {_T_SIDE}")
        print(f"  {_T_SIDE}  Link target: {_c('36', str(repo_path))}")
        print(f"  {_T_SIDE}")

        options: list[str] = []
        for c in candidates:
            parts = [c["dir_name"]]
            if c["repo_path"] != "(unset)":
                parts.append(f"← {c['repo_path']}")
            artifacts = []
            if c["has_graph"]:
                artifacts.append("graph")
            if c["has_vectors"]:
                artifacts.append("vectors")
            if c["has_api_docs"]:
                artifacts.append("api-docs")
            if c["has_wiki"]:
                artifacts.append("wiki")
            if artifacts:
                parts.append(f"[{', '.join(artifacts)}]")
            options.append("  ".join(parts))

        idx = _select_menu(options, prefix=f"  {_T_SIDE}  ")
        if idx is None:
            print(f"  {_T_LAST} Cancelled.")
            print()
            return 0
        selected = candidates[idx]

    artifact_dir = selected["artifact_dir"]

    # ── Decide: update-in-place vs. create-new ───────────────────────
    # If the user's repo path differs from the current artifact dir's
    # hash-based name, we create a new dir and symlink/copy from the old.
    from terrain.entrypoints.link_ops import register_link
    from terrain.entrypoints.mcp.pipeline import artifact_dir_for
    from terrain.foundation.utils.paths import normalize_repo_path

    target_dir = artifact_dir_for(ws, repo_path)
    canonical_new = normalize_repo_path(repo_path)

    if target_dir == artifact_dir:
        # Same hash — either a benign re-link or a hash collision to a
        # *different* logical repo. JER-101: refuse overwriting a
        # recorded repo_path with a different one.
        existing_raw = selected.get("meta", {}).get("repo_path")
        if existing_raw and existing_raw != "(unset)":
            try:
                existing_canonical = normalize_repo_path(existing_raw)
            except (TypeError, ValueError):
                existing_canonical = str(existing_raw)
            if existing_canonical != canonical_new:
                print(f"  {_c('31', 'ERROR')} artifact dir {artifact_dir.name} "
                      f"already links a different repo: {existing_canonical}")
                print(f"  New repo_path {canonical_new} would overwrite it.")
                print(f"  Pick a different --db target or remove the existing "
                      f"artifact dir before re-linking.")
                return 1
        _link_update_meta(artifact_dir, repo_path)
    elif target_dir.exists() and (target_dir / "graph.db").exists():
        # Target already has data — just update its meta
        _link_update_meta(target_dir, repo_path)
        artifact_dir = target_dir
    else:
        # Create new dir with symlinks (or copies on Windows) pointing to source
        target_dir.mkdir(parents=True, exist_ok=True)
        _link_artifacts(artifact_dir, target_dir)
        register_link(ws, source_dir=artifact_dir, target_dir=target_dir,
                      repo_path=repo_path)
        artifact_dir = target_dir

    # ── Set as active ─────────────────────────────────────────────────
    (ws / "active.txt").write_text(artifact_dir.name, encoding="utf-8")

    # ── Summary ───────────────────────────────────────────────────────
    print()
    print(f"  {_T_DOT} {_c('32', 'Repository linked successfully')}")
    print(f"  {_T_SIDE}")
    print(f"  {_T_BRANCH} repo       {repo_path}")
    print(f"  {_T_BRANCH} artifact   {artifact_dir}")
    parts = []
    if (artifact_dir / "graph.db").exists():
        parts.append("graph")
    if (artifact_dir / "vectors.pkl").exists():
        parts.append("vectors")
    if (artifact_dir / "api_docs" / "index.md").exists():
        parts.append("api-docs")
    if (artifact_dir / "wiki" / "index.md").exists():
        parts.append("wiki")
    print(f"  {_T_BRANCH} data       {', '.join(parts) if parts else '(none)'}")
    print(f"  {_T_LAST} active     {_c('32', 'yes')}")
    print()

    return 0


def _link_update_meta(artifact_dir: Path, repo_path: "Path | PurePath",
                      source_dir: Path | None = None) -> None:
    """Create or update meta.json to point to the given repo_path."""
    from datetime import datetime

    meta_file = artifact_dir / "meta.json"
    existing: dict = {}
    if meta_file.exists():
        try:
            existing = json.loads(meta_file.read_text(encoding="utf-8", errors="replace"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            pass

    from terrain.entrypoints.link_ops import SCHEMA_VERSION
    from terrain.foundation.utils.paths import normalize_repo_path

    meta = {
        **existing,
        "schema_version": SCHEMA_VERSION,
        "repo_path": normalize_repo_path(repo_path),
        "repo_name": repo_path.name or "root",
        "linked_at": datetime.now().isoformat(),
        "steps": {
            "graph": (artifact_dir / "graph.db").exists(),
            "api_docs": (artifact_dir / "api_docs" / "index.md").exists(),
            "embeddings": (artifact_dir / "vectors.pkl").exists(),
            "wiki": (artifact_dir / "wiki" / "index.md").exists(),
        },
    }
    if source_dir is not None:
        meta["linked_from"] = str(source_dir)
    # Preserve indexed_at if it already exists; otherwise set it
    if "indexed_at" not in meta:
        meta["indexed_at"] = meta["linked_at"]

    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _link_artifacts(source_dir: Path, target_dir: Path) -> None:
    """Create symlinks (or copies on Windows) from source artifacts into target."""
    import shutil

    artifact_names = ["graph.db", "api_docs", "vectors.pkl", "wiki"]
    for name in artifact_names:
        src = source_dir / name
        dst = target_dir / name
        if not src.exists():
            continue
        if dst.exists() or dst.is_symlink():
            continue  # Don't overwrite existing data
        try:
            # Prefer symlinks for efficiency
            dst.symlink_to(src)
        except OSError:
            # Fallback: copy (Windows without developer mode, etc.)
            if src.is_dir():
                shutil.copytree(str(src), str(dst))
            else:
                shutil.copy2(str(src), str(dst))


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------

def _run_incremental_index(args: argparse.Namespace, repo_path: Path, ws: Path) -> int:
    """Run incremental (git-diff-based) index update."""
    from terrain.domains.core.graph.incremental_updater import (
        IncrementalUpdater, INCREMENTAL_FILE_LIMIT,
    )
    from terrain.entrypoints.mcp.pipeline import (
        artifact_dir_for,
        build_vector_index,
        enhance_api_docs_step,
        generate_api_docs_step,
        generate_descriptions_step,
        save_meta,
    )
    from terrain.foundation.services.git_service import GitChangeDetector
    from terrain.foundation.services.kuzu_service import KuzuIngestor

    artifact_dir = artifact_dir_for(ws, repo_path)
    db_path = artifact_dir / "graph.db"
    vectors_path = artifact_dir / "vectors.pkl"
    meta_file = artifact_dir / "meta.json"

    if not db_path.exists():
        print(f"{_c('33', 'WARN')} No existing index found. Running full rebuild instead.")
        args.update = False
        return cmd_index(args)

    # Detect changed files
    detector = GitChangeDetector()
    last_commit = None
    if meta_file.exists():
        import json as _json
        try:
            last_commit = _json.loads(
                meta_file.read_text(encoding="utf-8", errors="replace")
            ).get("last_indexed_commit")
        except Exception:
            pass

    changed_files, current_head = detector.get_changed_files(repo_path, last_commit)

    if changed_files is None:
        print(f"{_c('33', 'WARN')} Cannot determine changes (git history mismatch). Running full rebuild.")
        args.update = False
        return cmd_index(args)

    if not changed_files:
        print(f"{_c('32', '✓')} No changes since last index. Already up to date.")
        return 0

    if len(changed_files) > INCREMENTAL_FILE_LIMIT:
        print(
            f"{_c('33', 'WARN')} Too many changed files ({len(changed_files)} > {INCREMENTAL_FILE_LIMIT}). "
            f"Running full rebuild."
        )
        args.update = False
        return cmd_index(args)

    skip_llm = getattr(args, "no_llm", False)
    print(f"  {_c('36', 'incremental')} {len(changed_files)} changed file(s)")

    try:
        result = IncrementalUpdater().run(changed_files, repo_path, db_path)
        print(
            f"  {_c('32', '✓')} Graph updated: {result.files_reindexed} files, "
            f"{result.callers_reindexed} callers in {result.duration_ms:.0f}ms"
        )

        # Cascade: regenerate API docs
        ro_ingestor = KuzuIngestor(db_path, read_only=True)
        with ro_ingestor:
            generate_api_docs_step(
                ro_ingestor, artifact_dir, rebuild=True, repo_path=repo_path,
            )
        print(f"  {_c('32', '✓')} API docs regenerated")

        # Cascade: LLM enhancement
        if not skip_llm:
            desc_result = generate_descriptions_step(
                artifact_dir=artifact_dir,
                repo_path=repo_path,
            )
            desc_count = desc_result.get("generated_count", 0)
            if desc_count > 0:
                print(f"  {_c('32', '✓')} LLM descriptions generated ({desc_count} functions)")

            enhance_result = enhance_api_docs_step(
                artifact_dir=artifact_dir,
            )
            enhance_count = enhance_result.get("generated_count", 0)
            if enhance_count > 0:
                print(f"  {_c('32', '✓')} Module summaries generated ({enhance_count} modules)")

        # Cascade: rebuild embeddings
        if not args.no_embed and vectors_path.exists():
            build_vector_index(
                None, repo_path, vectors_path, rebuild=True,
            )
            print(f"  {_c('32', '✓')} Embeddings rebuilt")

        save_meta(artifact_dir, repo_path, 0, last_indexed_commit=current_head)
        ws_root = _get_workspace_root()
        (ws_root / "active.txt").write_text(artifact_dir.name, encoding="utf-8")

        print(f"{_c('32', '✓')} Incremental update complete")
        return 0

    except Exception as exc:
        sys.stdout.write("\n")
        print(f"{_c('31', 'ERROR')} Incremental update failed: {exc}")
        if getattr(args, "verbose", False):
            import traceback
            traceback.print_exc()
        return 1


def _resolve_index_artifact_dir(
    repo_path: Path, ws: Path, output: str | None = None, interactive: bool = True,
) -> Path:
    """Resolve the artifact directory for ``terrain index`` output.

    Args:
        repo_path: The repository being indexed.
        ws: Workspace root directory.
        output: "local" for .terrain/, "workspace" for workspace, None for interactive.
        interactive: If True and output is None, show menu. Otherwise default to local.

    Returns:
        The artifact directory path.
    """
    from terrain.entrypoints.mcp.pipeline import artifact_dir_for

    if output == "local":
        return repo_path / ".terrain"
    if output == "workspace":
        return artifact_dir_for(ws, repo_path)

    # output is None — interactive or default
    if not interactive:
        return repo_path / ".terrain"

    ws_dir = artifact_dir_for(ws, repo_path)
    options = [
        f".terrain/  (repo-local, shareable via git)",
        f"{ws_dir}  (workspace)",
    ]
    print()
    print(f"  {_T_DOT} {_c('1', 'Output destination')}")
    print(f"  {_T_SIDE}  Use ↑↓ to navigate, Enter to confirm")
    print(f"  {_T_SIDE}")
    choice = _select_menu(options, prefix=f"  {_T_SIDE}  ")
    if choice is None or choice == 0:
        return repo_path / ".terrain"
    return ws_dir


def cmd_sync(args: argparse.Namespace) -> int:
    """Incremental update for an indexed repository."""
    ws = _get_workspace_root()

    repo_path_arg = getattr(args, "repo_path", None)
    if repo_path_arg:
        repo_path = Path(repo_path_arg).resolve()
        if not repo_path.exists():
            print(f"ERROR: Path does not exist: {repo_path}")
            return 1
        sync_args = argparse.Namespace(
            repo_path=str(repo_path),
            update=True,
            no_embed=False,
            wiki=False,
            mode="comprehensive",
            backend="kuzu",
            output=None,
            no_llm=getattr(args, "no_llm", False),
            verbose=getattr(args, "verbose", False),
        )
        return cmd_index(sync_args)

    # Interactive mode: pick from indexed repos
    entries = _get_repo_status_entries(ws)
    if not entries:
        print(f"  {_c('33', 'WARN')} No indexed repositories found.")
        print("  Run: terrain index <path>")
        return 0

    # Sort: most stale first, then unknown, then up-to-date; alpha within groups
    def _sort_key(e: dict):
        c = e["commits_since"]
        if c is None:
            return (1, 0, e["name"])
        if c == 0:
            return (2, 0, e["name"])
        return (0, -c, e["name"])

    entries.sort(key=_sort_key)

    GREEN  = "\033[32m"
    YELLOW = "\033[33m"
    DIM    = "\033[2m"
    RESET  = "\033[0m"

    labels: list[str] = []
    for e in entries:
        date = (e["indexed_at"] or "")[:10] or "?"
        c = e["commits_since"]
        if c is None:
            stale_tag = f"{DIM}commits unknown{RESET}"
        elif c == 0:
            stale_tag = f"{GREEN}up to date{RESET}"
        else:
            stale_tag = f"{YELLOW}{c} commit{'s' if c != 1 else ''} behind{RESET}"
        labels.append(f"{e['name']}  {DIM}{date}{RESET}  {stale_tag}")

    print()
    print(f"  {_T_BRANCH} ↑↓ navigate · Enter confirm · q cancel")
    print()

    choice = _select_menu(labels, prefix="  ")
    print()

    if choice is None:
        print("  Cancelled.")
        return 0

    selected = entries[choice]
    repo_path_str = selected["path"]
    if not repo_path_str or not Path(repo_path_str).is_dir():
        print(f"  {_c('31', 'ERROR')} Repository path not found: {repo_path_str or '(unknown)'}")
        print("  The repository may have been moved. Run: terrain index <new-path>")
        return 1

    sync_args = argparse.Namespace(
        repo_path=repo_path_str,
        update=True,
        no_embed=False,
        wiki=False,
        mode="comprehensive",
        backend="kuzu",
        output=None,
        no_llm=getattr(args, "no_llm", False),
        verbose=getattr(args, "verbose", False),
    )
    return cmd_index(sync_args)


def cmd_index(args: argparse.Namespace) -> int:
    """Run the full indexing pipeline on a repository."""
    from terrain.examples.generate_wiki import MAX_PAGES_COMPREHENSIVE, MAX_PAGES_CONCISE
    from terrain.entrypoints.mcp.pipeline import (
        artifact_dir_for,
        build_graph,
        build_vector_index,
        enhance_api_docs_step,
        generate_api_docs_step,
        generate_descriptions_step,
        run_wiki_generation,
        save_meta,
    )

    repo_path = Path(args.repo_path).resolve()
    if not repo_path.exists():
        print(f"ERROR: Path does not exist: {repo_path}")
        return 1

    ws = _get_workspace_root()
    ws.mkdir(parents=True, exist_ok=True)

    # Dispatch to incremental update if requested
    if getattr(args, "update", False):
        return _run_incremental_index(args, repo_path, ws)

    # Prompt for a display name (default: directory name)
    default_name = repo_path.name
    try:
        user_input = input(f"Database name [{default_name}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        user_input = ""
    custom_name = user_input if user_input else default_name

    skip_embed = args.no_embed
    skip_wiki = not getattr(args, "wiki", False) or skip_embed
    skip_llm = getattr(args, "no_llm", False)
    rebuild = True  # Default: always rebuild
    backend = args.backend
    comprehensive = args.mode != "concise"
    max_pages = MAX_PAGES_COMPREHENSIVE if comprehensive else MAX_PAGES_CONCISE

    total_steps = 4
    if skip_embed:
        total_steps = 2
    elif skip_wiki:
        total_steps = 3
    # LLM steps (2b/2c) are sub-steps, don't change total_steps count

    step_label = "graph -> api-docs"
    if not skip_llm:
        step_label += " (+ LLM)"
    if not skip_embed:
        step_label += " -> embeddings"
    if not skip_wiki:
        step_label += " -> wiki"

    output_flag = getattr(args, "output", None)
    artifact_dir = _resolve_index_artifact_dir(
        repo_path, ws, output=output_flag, interactive=sys.stdin.isatty(),
    )
    artifact_dir.mkdir(parents=True, exist_ok=True)
    db_path = artifact_dir / "graph.db"
    vectors_path = artifact_dir / "vectors.pkl"
    wiki_dir = artifact_dir / "wiki"

    bar = _ProgressBar(repo_path.name, total_steps)
    last_msg: list[str] = [""]

    def progress(step: int, msg: str, pct: float = 0.0) -> None:
        last_msg[0] = msg
        if pct >= 100.0:
            bar.done(step, msg)
        else:
            bar.update(step, msg, pct)

    try:
        builder = build_graph(
            repo_path, db_path, rebuild,
            progress_cb=lambda msg, pct: progress(1, msg, pct),
            backend=backend,
        )
        bar.done(1, last_msg[0] or "Graph built")

        generate_api_docs_step(
            builder, artifact_dir, rebuild,
            progress_cb=lambda msg, pct: progress(2, msg, pct),
        )
        bar.done(2, last_msg[0] or "API docs generated")

        # Save meta after graph+api-docs so the repo is discoverable even if
        # embedding is skipped or fails later.
        from terrain.foundation.services.git_service import GitChangeDetector
        _head = GitChangeDetector().get_current_head(repo_path)
        save_meta(artifact_dir, repo_path, 0, last_indexed_commit=_head, repo_name=custom_name)
        ws_root = _get_workspace_root()
        if artifact_dir == repo_path / ".terrain":
            from terrain.entrypoints.mcp.pipeline import artifact_dir_for
            ws_stub = artifact_dir_for(ws_root, repo_path)
            ws_stub.mkdir(parents=True, exist_ok=True)
            save_meta(ws_stub, repo_path, 0, last_indexed_commit=_head, repo_name=custom_name)
            (ws_root / "active.txt").write_text(ws_stub.name, encoding="utf-8")
        else:
            (ws_root / "active.txt").write_text(artifact_dir.name, encoding="utf-8")

        # LLM enhancement steps (2b/2c)
        if not skip_llm:
            bar.update(2, "LLM description generation...", 0.0)
            desc_result = generate_descriptions_step(
                artifact_dir=artifact_dir,
                repo_path=repo_path,
                progress_cb=lambda msg, pct: progress(2, msg, pct),
            )
            desc_count = desc_result.get("generated_count", 0)
            if desc_count > 0:
                bar.done(2, f"LLM descriptions: {desc_count} functions")
            else:
                bar.done(2, "LLM descriptions: skipped (no LLM or no TODOs)")

            bar.update(2, "LLM module enhancement...", 0.0)
            enhance_result = enhance_api_docs_step(
                artifact_dir=artifact_dir,
                progress_cb=lambda msg, pct: progress(2, msg, pct),
            )
            enhance_count = enhance_result.get("generated_count", 0)
            if enhance_count > 0:
                bar.done(2, f"Module enhancement: {enhance_count} modules")
            else:
                bar.done(2, "Module enhancement: skipped (no LLM or no modules)")

        page_count = 0
        if not skip_embed:
            vector_store, embedder, func_map = build_vector_index(
                builder, repo_path, vectors_path, rebuild,
                progress_cb=lambda msg, pct: progress(3, msg, pct),
            )
            bar.done(3, last_msg[0] or "Embeddings built")

            if not skip_wiki:
                _, page_count = run_wiki_generation(
                    builder=builder,
                    repo_path=repo_path,
                    output_dir=wiki_dir,
                    max_pages=max_pages,
                    rebuild=rebuild,
                    comprehensive=comprehensive,
                    vector_store=vector_store,
                    embedder=embedder,
                    func_map=func_map,
                    progress_cb=lambda msg, pct: progress(4, msg, pct),
                )
                bar.done(4, last_msg[0] or f"Wiki generated ({page_count} pages)")
            else:
                bar.done(3, "Wiki skipped (--no-wiki)")
        else:
            bar.done(2, "Embeddings skipped (--no-embed)")

        bar.finish()
        # Update meta with final page_count (active.txt already written above).
        _head = GitChangeDetector().get_current_head(repo_path)
        save_meta(artifact_dir, repo_path, page_count, last_indexed_commit=_head, repo_name=custom_name)
        if artifact_dir == repo_path / ".terrain":
            from terrain.entrypoints.mcp.pipeline import artifact_dir_for
            ws_stub = artifact_dir_for(ws_root, repo_path)
            save_meta(ws_stub, repo_path, page_count, last_indexed_commit=_head, repo_name=custom_name)

        print(f"{_c('32', '✓')} Done   {custom_name}   active repo set")
        if not skip_wiki:
            print(f"  wiki pages: {page_count}")
        return 0

    except Exception as exc:
        sys.stdout.write("\n")
        print(f"{_c('31', 'ERROR')} Indexing failed: {exc}")
        if getattr(args, "verbose", False):
            import traceback
            traceback.print_exc()
        return 1
    finally:
        # Stop the ticker even on KeyboardInterrupt / SystemExit — otherwise
        # the daemon may print one last frame on top of subsequent logs.
        bar.finish()


# ---------------------------------------------------------------------------
# clean
# ---------------------------------------------------------------------------

def cmd_clean(args: argparse.Namespace) -> int:
    """Remove indexed data for a repository from the workspace."""
    import shutil

    ws = _get_workspace_root()
    repos = _load_repos(ws)

    if not repos:
        print("No repositories indexed yet.")
        return 0

    # Determine target(s)
    if args.all:
        targets = repos
        print(f"This will delete all {len(targets)} indexed repositories from the workspace.")
    elif args.repo_name:
        name = args.repo_name
        targets = [r for r in repos if r["name"] == name or r["artifact_dir"].name == name]
        if not targets:
            print(f"Repository not found: {name}")
            print("Run: terrain list")
            return 1
    else:
        # Interactive selection
        print(f"Select repository to clean  ({len(repos)} indexed)  ↑/↓, Enter, q to cancel")
        idx = _interactive_select(repos)
        if idx is None:
            print("Cancelled.")
            return 0
        targets = [repos[idx]]

    for r in targets:
        print(f"  {r['name']}  ({r['artifact_dir']})")

    confirm = input("\nDelete? [y/N] ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return 0

    active_file = ws / "active.txt"
    active_name = active_file.read_text(encoding="utf-8", errors="replace").strip() if active_file.exists() else ""

    for r in targets:
        shutil.rmtree(r["artifact_dir"], ignore_errors=True)
        print(f"Removed: {r['name']}")
        if r["artifact_dir"].name == active_name:
            active_file.unlink(missing_ok=True)
            print("  (was active — cleared active repo)")

    return 0


# ---------------------------------------------------------------------------
# rebuild
# ---------------------------------------------------------------------------

def cmd_rebuild(args: argparse.Namespace) -> int:
    """Re-run one or more pipeline steps for the active repository."""
    from terrain.entrypoints.mcp.pipeline import (
        build_graph,
        build_vector_index,
        enhance_api_docs_step,
        generate_api_docs_step,
        generate_descriptions_step,
        run_wiki_generation,
        save_meta,
    )
    from terrain.examples.generate_wiki import MAX_PAGES_COMPREHENSIVE, MAX_PAGES_CONCISE

    ws = _get_workspace_root()
    active_file = ws / "active.txt"
    if not active_file.exists():
        print("No active repository. Run: terrain repo")
        return 1

    artifact_dir = ws / active_file.read_text(encoding="utf-8", errors="replace").strip()
    meta_file = artifact_dir / "meta.json"
    if not meta_file.exists():
        print("Active repository has no metadata. Run: terrain index <path>")
        return 1

    meta = json.loads(meta_file.read_text(encoding="utf-8", errors="replace"))
    repo_path = Path(meta["repo_path"]).resolve()
    db_path = artifact_dir / "graph.db"
    vectors_path = artifact_dir / "vectors.pkl"
    wiki_dir = artifact_dir / "wiki"

    step = args.step  # graph | api | embed | wiki | None (all)
    run_all = step is None
    skip_llm = getattr(args, "no_llm", False)
    backend = args.backend
    wiki_mode = getattr(args, "mode", "comprehensive")

    include_wiki = getattr(args, "wiki", False) or step == "wiki"
    steps_to_run = (
        ["graph", "api", "embed", "wiki"] if run_all and include_wiki else
        ["graph", "api", "embed"] if run_all else
        ["graph", "api"] if step == "graph" else [step]
    )
    bar = _ProgressBar(repo_path.name, len(steps_to_run))
    last_msg: list[str] = [""]

    def progress(label: str, msg: str, pct: float = 0.0) -> None:
        last_msg[0] = msg
        idx = steps_to_run.index(label) + 1 if label in steps_to_run else 1
        if pct >= 100.0:
            bar.done(idx, msg)
        else:
            bar.update(idx, msg, pct)

    try:
        builder = None

        if run_all or step == "graph":
            builder = build_graph(
                repo_path, db_path, rebuild=True,
                progress_cb=lambda msg, pct: progress("graph", msg, pct),
                backend=backend,
            )
            bar.done(steps_to_run.index("graph") + 1, last_msg[0] or "Graph built")

        if run_all or step in ("api", "graph"):
            if builder is None:
                if not db_path.exists():
                    print(f"{_c('31', 'ERROR')} No graph found. Run first: terrain rebuild --step graph")
                    return 1
                from terrain.foundation.services.kuzu_service import KuzuIngestor
                ingestor = KuzuIngestor(db_path)
                ingestor.__enter__()
                builder = ingestor
            generate_api_docs_step(
                builder, artifact_dir, rebuild=True,
                progress_cb=lambda msg, pct: progress("api", msg, pct),
            )
            bar.done(steps_to_run.index("api") + 1, last_msg[0] or "API docs generated")

            # LLM enhancement after API docs
            if not skip_llm:
                desc_result = generate_descriptions_step(
                    artifact_dir=artifact_dir,
                    repo_path=repo_path,
                    progress_cb=lambda msg, pct: progress("api", msg, pct),
                )
                desc_count = desc_result.get("generated_count", 0)
                if desc_count > 0:
                    bar.done(steps_to_run.index("api") + 1, f"LLM descriptions: {desc_count} functions")

                enhance_result = enhance_api_docs_step(
                    artifact_dir=artifact_dir,
                    progress_cb=lambda msg, pct: progress("api", msg, pct),
                )
                enhance_count = enhance_result.get("generated_count", 0)
                if enhance_count > 0:
                    bar.done(steps_to_run.index("api") + 1, f"Module enhancement: {enhance_count} modules")

        vector_store = embedder = func_map = None

        if run_all or step == "embed":
            if builder is None:
                if not db_path.exists():
                    print(f"{_c('31', 'ERROR')} No graph found. Run first: terrain rebuild --step graph")
                    return 1
                from terrain.foundation.services.kuzu_service import KuzuIngestor
                ingestor = KuzuIngestor(db_path)
                ingestor.__enter__()
                builder = ingestor
            vector_store, embedder, func_map = build_vector_index(
                builder, repo_path, vectors_path, rebuild=True,
                progress_cb=lambda msg, pct: progress("embed", msg, pct),
            )
            bar.done(steps_to_run.index("embed") + 1, last_msg[0] or "Embeddings built")

        if include_wiki:
            if vector_store is None:
                vector_store = _load_vector_store_simple(vectors_path)
                if vector_store is None:
                    print(f"{_c('31', 'ERROR')} No embeddings found. Run: terrain rebuild --step embed")
                    return 1
            if builder is None:
                from terrain.foundation.services.kuzu_service import KuzuIngestor
                ingestor = KuzuIngestor(db_path)
                ingestor.__enter__()
                builder = ingestor
            if embedder is None:
                from terrain.domains.core.embedding.qwen3_embedder import create_embedder
                embedder = create_embedder()
            if func_map is None:
                func_map = {}
            is_comprehensive = wiki_mode == "comprehensive"
            _, page_count = run_wiki_generation(
                builder=builder,
                repo_path=repo_path,
                output_dir=wiki_dir,
                max_pages=MAX_PAGES_COMPREHENSIVE if is_comprehensive else MAX_PAGES_CONCISE,
                rebuild=True,
                comprehensive=is_comprehensive,
                vector_store=vector_store,
                embedder=embedder,
                func_map=func_map,
                progress_cb=lambda msg, pct: progress("wiki", msg, pct),
            )
            save_meta(artifact_dir, repo_path, page_count)
            bar.done(steps_to_run.index("wiki") + 1, last_msg[0] or f"Wiki generated ({page_count} pages)")

        bar.finish()
        print(f"{_c('32', '✓')} Done   {repo_path.name}")
        return 0

    except Exception as exc:
        sys.stdout.write("\n")
        print(f"{_c('31', 'ERROR')} Rebuild failed: {exc}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1
    finally:
        bar.finish()


def _load_vector_store_simple(vectors_path: Path):
    """Load vector store from pickle, minimal version for rebuild."""
    import pickle
    if not vectors_path.exists():
        return None
    try:
        from terrain.domains.core.embedding.vector_store import MemoryVectorStore, VectorRecord
        with open(vectors_path, "rb") as fh:
            data = pickle.load(fh)
        if isinstance(data, dict) and "vector_store" in data:
            store = data["vector_store"]
            if isinstance(store, MemoryVectorStore):
                return store
        if isinstance(data, list) and data and isinstance(data[0], VectorRecord):
            dim = len(data[0].embedding)
            store = MemoryVectorStore(dimension=dim)
            store.store_embeddings_batch(data)
            return store
    except Exception:
        pass
    return None


def cmd_stats(args: argparse.Namespace) -> int:
    """Execute the stats command."""
    setup_logging(args.verbose)

    try:
        backend = args.backend or "kuzu"
        backend_config = {"db_path": args.db_path} if args.db_path else {}

        builder = TerrainBuilder(
            repo_path=args.repo_path or ".",
            backend=backend,
            backend_config=backend_config,
        )

        stats = builder.get_statistics()

        print()
        print("=" * 60)
        print("GRAPH STATISTICS")
        print("=" * 60)
        print(f"Total nodes: {stats.get('total_nodes', 0)}")
        print(f"Total relationships: {stats.get('total_relationships', 0)}")
        print()

        node_labels = stats.get("node_labels", {})
        if node_labels:
            print("Node types:")
            for label, count in sorted(node_labels.items(), key=lambda x: -x[1]):
                label_str = str(label)
                print(f"  {label_str:20s}: {count:5d}")
            print()

        rel_types = stats.get("relationship_types", {})
        if rel_types:
            print("Relationship types:")
            for rel_type, count in sorted(rel_types.items(), key=lambda x: -x[1]):
                rel_str = str(rel_type)
                print(f"  {rel_str:20s}: {count:5d}")

        return 0

    except Exception as e:
        logger.error(f"Stats failed: {e}")
        if args.verbose:
            import traceback

            traceback.print_exc()
        return 1


def cmd_setup(args: argparse.Namespace) -> int:
    """Execute the setup wizard (configure API keys, workspace, MCP registration).

    On Windows: also adds Python Scripts dir to user PATH.
    On all platforms: delegates to the npm setup wizard via npx.
    """
    import shutil
    import subprocess

    npx = shutil.which("npx")
    if npx:
        result = subprocess.run(
            [npx, "terrain-ai@latest", "setup"],
            shell=False,
        )
        return result.returncode

    # npx not available — fall back to Windows PATH fix if applicable
    if platform.system() != "Windows":
        print("terrain setup requires Node.js / npx to run the setup wizard.")
        print("Install Node.js (https://nodejs.org) and re-run: terrain setup")
        print()
        print("Alternatively, run the wizard directly:")
        print("  npx terrain-ai@latest setup")
        return 1

    scripts_dir = Path(sys.executable).parent / "Scripts"
    if not (scripts_dir / "terrain.exe").exists():
        # Fallback: same directory as python.exe (e.g. in virtual envs)
        scripts_dir = Path(sys.executable).parent

    scripts_str = str(scripts_dir)

    try:
        import winreg  # type: ignore[import]

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            "Environment",
            0,
            winreg.KEY_READ | winreg.KEY_WRITE,
        )
        try:
            current_path, _ = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current_path = ""

        path_parts = [p for p in current_path.split(";") if p]
        if scripts_str.lower() not in [p.lower() for p in path_parts]:
            path_parts.append(scripts_str)
            winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, ";".join(path_parts))
            print(f"Added to user PATH: {scripts_str}")
            print("Restart your terminal (or log out and back in) for the change to take effect.")
        else:
            print(f"Already in PATH: {scripts_str}")

        winreg.CloseKey(key)

        # Notify running processes that the environment changed
        try:
            import ctypes
            ctypes.windll.user32.SendMessageTimeoutW(  # type: ignore[attr-defined]
                0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, None
            )
        except Exception:
            pass

        return 0

    except Exception as exc:
        print(f"Failed to update PATH: {exc}")
        print(f"Please manually add the following directory to your PATH:\n  {scripts_str}")
        return 1


# ---------------------------------------------------------------------------
# update — check and install updates for npm and PyPI packages
# ---------------------------------------------------------------------------

def _semver_cmp(a: str, b: str) -> int:
    """Compare two semver strings. Returns 1 if a > b, -1 if a < b, 0 if equal."""
    def _parts(v: str) -> list[int]:
        return [int(x) for x in (v or "0").split(".")[:3]]
    for va, vb in zip(_parts(a), _parts(b)):
        if va > vb:
            return 1
        if va < vb:
            return -1
    return 0


def _find_pip() -> list[str] | None:
    """Return a pip invocation list, e.g. ['pip3'] or ['python3', '-m', 'pip']."""
    import shutil
    import subprocess
    import sys as _sys

    candidates = ["pip3", "pip"] if _sys.platform != "win32" else ["pip", "pip3"]
    for p in candidates:
        if shutil.which(p):
            try:
                subprocess.run(
                    [p, "--version"],
                    check=True, capture_output=True, timeout=8,
                )
                return [p]
            except Exception:
                pass
    # Fallback: python -m pip
    py = _sys.executable
    try:
        subprocess.run(
            [py, "-m", "pip", "--version"],
            check=True, capture_output=True, timeout=8,
        )
        return [py, "-m", "pip"]
    except Exception:
        return None


def cmd_update(args: argparse.Namespace) -> int:
    """Check and install updates for the terrain-ai npm and PyPI packages."""
    import importlib.metadata
    import json
    import shutil
    import subprocess
    import urllib.error
    import urllib.request

    check_only: bool = getattr(args, "check_only", False)
    skip_npm: bool = getattr(args, "skip_npm", False)
    skip_pip: bool = getattr(args, "skip_pip", False)

    OK   = _c("32", "✓")
    FAIL = _c("31", "✗")
    WARN = _c("33", "⚠")
    UP   = _c("36", "↑")
    DIM  = lambda t: _c("2", t)  # noqa: E731

    print()

    # ── Python / PyPI ─────────────────────────────────────────────────────────
    local_py: str | None = None
    latest_py: str | None = None
    pip_cmd: list[str] | None = None
    py_updated = False
    py_error: str | None = None

    if not skip_pip:
        print(f"  {DIM('Checking PyPI …')}", end="", flush=True)

        try:
            local_py = importlib.metadata.version("terrain-ai")
        except importlib.metadata.PackageNotFoundError:
            pass

        try:
            with urllib.request.urlopen(
                "https://pypi.org/pypi/terrain-ai/json", timeout=10
            ) as resp:
                data = json.loads(resp.read())
            latest_py = data["info"]["version"]
        except (urllib.error.URLError, KeyError, Exception) as exc:
            py_error = f"Could not reach PyPI: {exc}"

        print(f"\r\033[K", end="")  # clear the "Checking…" line

        if py_error:
            print(f"  {WARN} PyPI  {DIM(py_error)}")
        elif local_py is None:
            print(f"  {WARN} PyPI  terrain-ai not installed via pip")
        elif latest_py is None:
            print(f"  {WARN} PyPI  current {local_py}  (could not fetch latest)")
        elif _semver_cmp(latest_py, local_py) <= 0:
            print(f"  {OK} PyPI  terrain-ai {_c('32', local_py)}  (up to date)")
        else:
            # Update available
            arrow = f"{_c('33', local_py)} → {_c('32', latest_py)}"
            if check_only:
                print(f"  {UP} PyPI  terrain-ai {arrow}  {DIM('(update available)')}")
            else:
                print(f"  {UP} PyPI  terrain-ai {arrow}  — installing …", flush=True)
                pip_cmd = _find_pip()
                if pip_cmd is None:
                    print(f"  {FAIL} PyPI  pip not found; cannot update automatically.")
                    print(f"       Run: pip install --upgrade terrain-ai[treesitter-full]")
                else:
                    try:
                        cmd = [
                            *pip_cmd, "install",
                            "--upgrade", "--prefer-binary",
                            "--no-cache-dir", "--force-reinstall",
                            "terrain-ai[treesitter-full]",
                        ]
                        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
                        py_updated = True
                        print(f"\r\033[K  {OK} PyPI  terrain-ai {arrow}  updated ✓")
                    except subprocess.CalledProcessError as exc:
                        err_tail = (exc.stderr or b"").decode(errors="replace").strip()
                        err_tail = err_tail[-200:] if err_tail else "(no output)"
                        print(f"\r\033[K  {FAIL} PyPI  update failed: {err_tail}")
                    except subprocess.TimeoutExpired:
                        print(f"\r\033[K  {FAIL} PyPI  update timed out (>5 min)")

    # ── npm ───────────────────────────────────────────────────────────────────
    local_npm: str | None = None
    latest_npm: str | None = None
    npm_updated = False
    npm_error: str | None = None

    if not skip_npm and shutil.which("npm"):
        print(f"  {DIM('Checking npm …')}", end="", flush=True)

        # Local version: ask npm what's globally installed
        try:
            result = subprocess.run(
                ["npm", "list", "-g", "--depth=0", "--json"],
                capture_output=True, timeout=15, text=True,
            )
            info = json.loads(result.stdout or "{}")
            deps = info.get("dependencies", {})
            if "terrain-ai" in deps:
                local_npm = deps["terrain-ai"].get("version")
        except Exception:
            pass

        # Latest version from registry
        try:
            result = subprocess.run(
                ["npm", "view", "terrain-ai", "version"],
                capture_output=True, timeout=15, text=True, check=True,
            )
            latest_npm = result.stdout.strip() or None
        except Exception as exc:
            npm_error = str(exc)

        print(f"\r\033[K", end="")  # clear "Checking…"

        if npm_error and local_npm is None:
            print(f"  {WARN} npm   could not reach registry: {npm_error}")
        elif local_npm is None:
            print(f"  {DIM('  npm   terrain-ai not installed globally — skipping')}")
        elif latest_npm is None:
            print(f"  {WARN} npm   current {local_npm}  (could not fetch latest)")
        elif _semver_cmp(latest_npm, local_npm) <= 0:
            print(f"  {OK} npm   terrain-ai {_c('32', local_npm)}  (up to date)")
        else:
            arrow = f"{_c('33', local_npm)} → {_c('32', latest_npm)}"
            if check_only:
                print(f"  {UP} npm   terrain-ai {arrow}  {DIM('(update available)')}")
            else:
                print(f"  {UP} npm   terrain-ai {arrow}  — installing …", flush=True)
                try:
                    subprocess.run(
                        ["npm", "install", "-g", "terrain-ai@latest"],
                        check=True, capture_output=True, timeout=120,
                    )
                    npm_updated = True
                    print(f"\r\033[K  {OK} npm   terrain-ai {arrow}  updated ✓")
                except subprocess.CalledProcessError as exc:
                    err_tail = (exc.stderr or b"").decode(errors="replace").strip()
                    err_tail = err_tail[-200:] if err_tail else "(no output)"
                    print(f"\r\033[K  {FAIL} npm   update failed: {err_tail}")
                except subprocess.TimeoutExpired:
                    print(f"\r\033[K  {FAIL} npm   update timed out (>2 min)")
    elif not skip_npm:
        print(f"  {DIM('  npm   not found — skipping npm update')}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    if check_only:
        if (local_py and latest_py and _semver_cmp(latest_py, local_py) > 0) or \
           (local_npm and latest_npm and _semver_cmp(latest_npm, local_npm) > 0):
            print(f"  {DIM('Run  terrain update  to install the above updates.')}")
        else:
            print(f"  {OK} Everything is up to date.")
    else:
        if py_updated or npm_updated:
            notes: list[str] = []
            if py_updated:
                notes.append("Python package updated — restart terrain / MCP server to use new version")
            if npm_updated:
                notes.append("npm package updated — restart any running terrain processes")
            for note in notes:
                print(f"  {_c('2', note)}")
        else:
            print(f"  {OK} No updates were applied.")
    print()
    return 0


def cmd_reload(args: argparse.Namespace) -> int:
    """Hot-reload .env configuration and display changes."""
    from terrain.foundation.utils.settings import reload_env

    changes = reload_env()
    updated = changes.get("updated", [])
    removed = changes.get("removed", [])

    if not updated and not removed:
        print(f"  {_c('32', 'OK')} No configuration changes detected.")
        return 0

    if updated:
        print(f"  {_c('36', 'updated')} {', '.join(updated)}")
    if removed:
        print(f"  {_c('33', 'removed')} {', '.join(removed)}")

    print(f"\n  {_c('2', 'Note: MCP server needs restart or reload_config call to pick up changes.')}")
    return 0


def main() -> int:
    """Main entry point for CLI."""
    # Load exclusively from workspace .env — single source of truth.
    # reload_env() also removes config keys absent from .env so stale shell
    # exports don't silently override the current configuration.
    from terrain.foundation.utils.settings import reload_env
    reload_env()

    prog = "terrain"
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Code knowledge graph builder — index, explore, and navigate any codebase.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Workspace commands:
  terrain status                    show active repository
  terrain list                      list all indexed repositories
  terrain repo                      interactively switch repository (↑/↓)

Indexing commands:
  terrain index [path]              full pipeline: graph → api-docs → embeddings
  terrain index . --wiki            index with wiki generation
  terrain rebuild                   rebuild graph → api-docs → embeddings
  terrain rebuild --wiki            rebuild all steps including wiki
  terrain rebuild --step embed      rebuild only embeddings
  terrain clean                     interactively remove an indexed repo
  terrain clean myrepo              remove specific repo by name

Low-level commands:
  terrain scan /path --db-path ./graph.db
  terrain query "MATCH (f:Function) RETURN f.name LIMIT 5" --db-path ./graph.db
  terrain export /path --output ./graph.json --build
  terrain stats --db-path ./graph.db

Run 'terrain <command> --help' for details on any command.

Other commands:
  terrain update                    update terrain-ai (npm + PyPI) to the latest version
  terrain update --check            check for updates without installing
  terrain setup                     run setup wizard (API keys, workspace, MCP registration)
        """,
        add_help=False,
    )
    parser.add_argument("-h", "--help", action="store_true", help="Show this help message")

    parser.add_argument(
        "--version",
        action="version",
        version=f"terrain-ai {__version__}",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose output",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Scan command
    scan_parser = subparsers.add_parser(
        "scan",
        help="Scan a repository and build the knowledge graph",
        description="Scan source code and build a knowledge graph.",
    )
    scan_parser.add_argument(
        "repo_path",
        type=str,
        help="Path to the repository to scan",
    )
    scan_parser.add_argument(
        "--backend",
        choices=["kuzu", "memgraph", "memory"],
        default="kuzu",
        help="Storage backend (default: kuzu)",
    )
    scan_parser.add_argument(
        "--db-path",
        type=str,
        help="Path to store the database (for Kùzu backend)",
    )
    scan_parser.add_argument(
        "--host",
        type=str,
        help="Memgraph host (for Memgraph backend)",
    )
    scan_parser.add_argument(
        "--port",
        type=int,
        help="Memgraph port (for Memgraph backend)",
    )
    scan_parser.add_argument(
        "--username",
        type=str,
        help="Memgraph username",
    )
    scan_parser.add_argument(
        "--password",
        type=str,
        help="Memgraph password",
    )
    scan_parser.add_argument(
        "--batch-size",
        type=int,
        help="Batch size for database writes",
    )
    scan_parser.add_argument(
        "--exclude",
        action="append",
        help="Comma-separated patterns to exclude (can be used multiple times)",
    )
    scan_parser.add_argument(
        "--exclude-pattern",
        action="append",
        help="Additional exclude pattern (can be used multiple times)",
    )
    scan_parser.add_argument(
        "--language",
        type=str,
        help="Comma-separated list of languages to include",
    )
    scan_parser.add_argument(
        "--max-file-size",
        type=int,
        help="Maximum file size in bytes",
    )
    scan_parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean existing database before scanning",
    )
    scan_parser.add_argument(
        "--output",
        "-o",
        type=str,
        help="Export graph to JSON file after scanning",
    )
    scan_parser.add_argument(
        "--config",
        "-c",
        type=str,
        help="Configuration file (YAML or JSON)",
    )
    scan_parser.set_defaults(func=cmd_scan)

    # Query command
    query_parser = subparsers.add_parser(
        "query",
        help="Execute a Cypher query against the graph",
        description="Query the knowledge graph using Cypher.",
    )
    query_parser.add_argument(
        "cypher_query",
        type=str,
        help="Cypher query to execute",
    )
    query_parser.add_argument(
        "--repo-path",
        type=str,
        help="Path to the repository (for reference)",
    )
    query_parser.add_argument(
        "--backend",
        choices=["kuzu", "memgraph", "memory"],
        default="kuzu",
        help="Storage backend (default: kuzu)",
    )
    query_parser.add_argument(
        "--db-path",
        type=str,
        help="Path to the database (for Kùzu backend)",
    )
    query_parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)",
    )
    query_parser.set_defaults(func=cmd_query)

    # Export command
    export_parser = subparsers.add_parser(
        "export",
        help="Export the graph to JSON",
        description="Export the knowledge graph to a JSON file.",
    )
    export_parser.add_argument(
        "repo_path",
        type=str,
        help="Path to the repository",
    )
    export_parser.add_argument(
        "--output",
        "-o",
        type=str,
        required=True,
        help="Output JSON file path",
    )
    export_parser.add_argument(
        "--backend",
        choices=["kuzu", "memgraph", "memory"],
        default="memory",
        help="Storage backend (default: memory)",
    )
    export_parser.add_argument(
        "--build",
        action="store_true",
        help="Build the graph before exporting",
    )
    export_parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean existing data before building",
    )
    export_parser.add_argument(
        "--exclude",
        action="append",
        help="Patterns to exclude",
    )
    export_parser.set_defaults(func=cmd_export)

    # status command
    status_parser = subparsers.add_parser(
        "status",
        help="Show workspace status and staleness of indexed repositories",
        description="Display workspace info and show which indexed repositories have stale data.",
    )
    status_parser.add_argument(
        "--debug",
        nargs="?",
        const=True,
        default=None,
        metavar="on|off",
        type=lambda v: v.lower() in ("1", "true", "yes", "on") if isinstance(v, str) else v,
        help="Toggle MCP debug logging: --debug on / --debug off (or just --debug to enable)",
    )
    status_parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output machine-readable JSON with repo staleness info",
    )
    status_parser.set_defaults(func=cmd_status)

    # config command
    config_parser = subparsers.add_parser(
        "config",
        help="View or modify LLM / embedding configuration",
        description="View current config or set LLM / embedding model, base URL, and API key.",
    )
    config_parser.add_argument("--llm-model", type=str, default=None, help="Set LLM model name (e.g. gpt-4o)")
    config_parser.add_argument("--llm-base-url", type=str, default=None, help="Set LLM API base URL")
    config_parser.add_argument("--llm-api-key", type=str, default=None, help="Set LLM API key")
    config_parser.add_argument("--embed-model", type=str, default=None, help="Set embedding model name")
    config_parser.add_argument("--embed-base-url", type=str, default=None, help="Set embedding API base URL")
    config_parser.add_argument("--embed-api-key", type=str, default=None, help="Set embedding API key")
    config_parser.set_defaults(func=cmd_config)

    # list command
    list_parser = subparsers.add_parser(
        "list",
        help="List all indexed repositories in the workspace",
        description="Show all repositories that have been indexed in the workspace.",
    )
    list_parser.set_defaults(func=cmd_list)

    # repo command
    repo_parser = subparsers.add_parser(
        "repo",
        help="Interactively switch the active repository (↑/↓ to select)",
        description="Use arrow keys to select and switch the active repository.",
    )
    repo_parser.set_defaults(func=cmd_repo)

    # index command
    index_parser = subparsers.add_parser(
        "index",
        help="Index a repository (full pipeline: graph → api-docs → embeddings → wiki)",
        description="Run the full indexing pipeline. Defaults to current directory if no path given.",
    )
    index_parser.add_argument(
        "repo_path",
        nargs="?",
        default=".",
        type=str,
        help="Path to repository (default: current directory)",
    )
    index_parser.add_argument(
        "--update", "-u",
        action="store_true",
        help="Incremental update: only reindex git-changed files (falls back to full rebuild if needed)",
    )
    index_parser.add_argument(
        "--no-embed",
        action="store_true",
        help="Skip embedding generation (also skips wiki)",
    )
    index_parser.add_argument(
        "--wiki",
        action="store_true",
        help="Generate wiki (disabled by default)",
    )
    index_parser.add_argument(
        "--mode",
        choices=["comprehensive", "concise"],
        default="comprehensive",
        help="Wiki generation mode (default: comprehensive)",
    )
    index_parser.add_argument(
        "--backend",
        choices=["kuzu", "memgraph", "memory"],
        default="kuzu",
        help="Storage backend (default: kuzu)",
    )
    index_parser.add_argument(
        "--output",
        choices=["local", "workspace"],
        default=None,
        help="Output destination: 'local' for .terrain/ in repo, 'workspace' for ~/.terrain/",
    )
    index_parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip LLM-powered description generation and module enhancement",
    )
    index_parser.set_defaults(func=cmd_index)

    # sync command
    sync_parser = subparsers.add_parser(
        "sync",
        help="Incremental update for an indexed repository",
        description="Re-index only git-changed files. Interactive repo picker when no path is given.",
    )
    sync_parser.add_argument(
        "repo_path",
        nargs="?",
        default=None,
        type=str,
        help="Path to repository (interactive picker if omitted)",
    )
    sync_parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip LLM-powered description generation",
    )
    sync_parser.set_defaults(func=cmd_sync)

    # link command
    link_parser = subparsers.add_parser(
        "link",
        help="Link a local repo to an existing artifact database",
        description=(
            "Associate a local repository path with a pre-built artifact database "
            "in the workspace. Useful when sharing indexed data between team members."
        ),
    )
    link_parser.add_argument(
        "repo_path",
        type=str,
        help="Absolute path to the local repository",
    )
    link_parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="Artifact directory name to link (interactive if omitted)",
    )
    link_parser.set_defaults(func=cmd_link)

    # clean command
    clean_parser = subparsers.add_parser(
        "clean",
        help="Remove indexed data for a repository",
        description="Delete indexed artifacts from the workspace. Interactive if no name given.",
    )
    clean_parser.add_argument(
        "repo_name",
        nargs="?",
        default=None,
        type=str,
        help="Repository name to clean (interactive if omitted)",
    )
    clean_parser.add_argument(
        "--all",
        action="store_true",
        help="Remove all indexed repositories",
    )
    clean_parser.set_defaults(func=cmd_clean)

    # rebuild command
    rebuild_parser = subparsers.add_parser(
        "rebuild",
        help="Re-run pipeline steps for the active repository",
        description="Rebuild one or all pipeline steps for the currently active repository.",
    )
    rebuild_parser.add_argument(
        "--step",
        choices=["graph", "api", "embed", "wiki"],
        default=None,
        help=(
            "Specific step to rebuild (default: all steps). "
            "graph: rebuild code graph and API docs; "
            "api: regenerate API docs only (no graph rebuild); "
            "embed: rebuild vector embeddings; "
            "wiki: regenerate wiki pages"
        ),
    )
    rebuild_parser.add_argument(
        "--wiki",
        action="store_true",
        help="Include wiki generation (disabled by default)",
    )
    rebuild_parser.add_argument(
        "--mode",
        choices=["comprehensive", "concise"],
        default="comprehensive",
        help="Wiki generation mode (default: comprehensive)",
    )
    rebuild_parser.add_argument(
        "--backend",
        choices=["kuzu", "memgraph", "memory"],
        default="kuzu",
        help="Storage backend (default: kuzu)",
    )
    rebuild_parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip LLM-powered description generation and module enhancement",
    )
    rebuild_parser.set_defaults(func=cmd_rebuild)

    # Stats command
    stats_parser = subparsers.add_parser(
        "stats",
        help="Show graph statistics",
        description="Display statistics about the knowledge graph.",
    )
    stats_parser.add_argument(
        "--repo-path",
        type=str,
        help="Path to the repository",
    )
    stats_parser.add_argument(
        "--backend",
        choices=["kuzu", "memgraph", "memory"],
        default="kuzu",
        help="Storage backend (default: kuzu)",
    )
    stats_parser.add_argument(
        "--db-path",
        type=str,
        help="Path to the database",
    )
    stats_parser.set_defaults(func=cmd_stats)

    # setup command — runs the npm setup wizard (all platforms)
    setup_parser = subparsers.add_parser(
        "setup",
        help="Run the setup wizard (configure API keys, workspace, MCP registration)",
        description="Launch the terrain setup wizard via npx. Configures API keys, workspace, and registers the MCP server with Claude.",
    )
    setup_parser.set_defaults(func=cmd_setup)

    # reload command
    reload_parser = subparsers.add_parser(
        "reload",
        help="Hot-reload .env configuration and show changes",
        description="Reload configuration from workspace .env file and display what changed.",
    )
    reload_parser.set_defaults(func=cmd_reload)

    # update command
    update_parser = subparsers.add_parser(
        "update",
        help="Check and install updates for terrain-ai (npm + PyPI)",
        description=(
            "Check for newer versions of terrain-ai on PyPI and npm, "
            "then upgrade both packages in place."
        ),
    )
    update_parser.add_argument(
        "--check",
        dest="check_only",
        action="store_true",
        help="Only report available updates; do not install anything",
    )
    update_parser.add_argument(
        "--skip-npm",
        action="store_true",
        help="Skip the npm global package update",
    )
    update_parser.add_argument(
        "--skip-pip",
        action="store_true",
        help="Skip the PyPI (pip) package update",
    )
    update_parser.set_defaults(func=cmd_update)

    args = parser.parse_args()

    if getattr(args, "help", False) or not args.command:
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
