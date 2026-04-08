"""Command-line interface for Code Graph Builder.

Examples:
    # Scan a repository with Kùzu backend
    $ code-graph-builder scan /path/to/repo --backend kuzu --db-path ./graph.db

    # Scan with specific exclusions
    $ code-graph-builder scan /path/to/repo --exclude tests,docs --exclude-pattern "*.md"

    # Query the graph
    $ code-graph-builder query "MATCH (f:Function) RETURN f.name LIMIT 5" --db-path ./graph.db

    # Export to JSON
    $ code-graph-builder export /path/to/repo --output ./output.json

    # Use configuration file
    $ code-graph-builder scan /path/to/repo --config ./config.yaml
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
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


class _ProgressBar:
    """Single-line progress bar that overwrites itself in place.

    Usage:
        bar = _ProgressBar("graph", total_steps=4)
        bar.update(1, "Scanning files...", pct=30.0)
        bar.update(1, "Scanning files...", pct=80.0)
        bar.done(1, "Graph built: 1234 nodes")
        bar.finish()   # after all steps
    """

    BAR_WIDTH = 24

    def __init__(self, repo_name: str, total_steps: int) -> None:
        self._total = total_steps
        self._last_pct: float = -1.0
        print(f"\nIndexing  {_c('1', repo_name)}  ({total_steps} steps)\n")

    def _render(self, step: int, msg: str, pct: float) -> None:
        filled = int(self.BAR_WIDTH * pct / 100)
        if _ANSI:
            bar = _c("32", "█" * filled) + _c("2", "░" * (self.BAR_WIDTH - filled))
            pct_str = _c("1", f"{pct:3.0f}%")
            step_str = _c("2", f"{step}/{self._total}")
        else:
            bar = "#" * filled + "-" * (self.BAR_WIDTH - filled)
            pct_str = f"{pct:3.0f}%"
            step_str = f"{step}/{self._total}"

        # Truncate message to fit terminal width
        try:
            term_w = os.get_terminal_size().columns
        except OSError:
            term_w = 80
        prefix = f"  [{bar}] {pct_str}  step {step_str}  "
        max_msg = max(10, term_w - len(prefix) - 2)
        display_msg = msg[:max_msg] + "…" if len(msg) > max_msg else msg

        line = f"{prefix}{display_msg}"
        if _ANSI:
            sys.stdout.write(f"\r\033[K{line}")
        else:
            sys.stdout.write(f"\r{line}")
        sys.stdout.flush()

    def update(self, step: int, msg: str, pct: float) -> None:
        """Update the in-place progress line."""
        if pct <= self._last_pct and pct > 0:
            return
        self._last_pct = pct
        self._render(step, msg, pct)

    def done(self, step: int, msg: str) -> None:
        """Mark a step as complete — prints a finalised line and moves to next line."""
        self._last_pct = -1.0
        self._render(step, msg, 100.0)
        if _ANSI:
            label = _c("32", "✓")
        else:
            label = "done"
        sys.stdout.write(f"  {label}\n")
        sys.stdout.flush()

    def finish(self) -> None:
        """Print the completion summary line."""
        print()

from loguru import logger

from code_graph_builder import __version__
from code_graph_builder.domains.core.graph.builder import CodeGraphBuilder
from code_graph_builder.foundation.types.config import (
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
        sys.stderr,
        level=level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    )


def load_config_file(config_path: str | Path) -> dict[str, Any]:
    """Load configuration from YAML or JSON file."""
    config_path = Path(config_path)

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    if config_path.suffix in (".yaml", ".yml"):
        try:
            import yaml

            with open(config_path) as f:
                return yaml.safe_load(f)
        except ImportError:
            raise ImportError("PyYAML is required for YAML config files. Install with: pip install pyyaml")
    elif config_path.suffix == ".json":
        with open(config_path) as f:
            return json.load(f)
    else:
        raise ValueError(f"Unsupported config file format: {config_path.suffix}")


def create_builder_from_args(args: argparse.Namespace) -> CodeGraphBuilder:
    """Create CodeGraphBuilder from command-line arguments."""
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
    return CodeGraphBuilder(
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
            with open(args.output, "w") as f:
                json.dump(data, f, indent=2, default=str)
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

        builder = CodeGraphBuilder(
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

        with open(output_path, "w") as f:
            json.dump(data, f, indent=2, default=str)

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
        os.environ.get("CGB_WORKSPACE", Path.home() / ".code-graph-builder")
    ).expanduser().resolve()


def _load_repos(ws: Path) -> list[dict]:
    """Return all indexed repos, sorted by name, with 'active' flag set."""
    active_file = ws / "active.txt"
    active_name = active_file.read_text(encoding="utf-8").strip() if active_file.exists() else ""

    repos: list[dict] = []
    if not ws.exists():
        return repos
    for child in sorted(ws.iterdir()):
        if not child.is_dir():
            continue
        meta_file = child / "meta.json"
        if not meta_file.exists():
            continue
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        repos.append({
            "artifact_dir": child,
            "name": meta.get("repo_name", child.name),
            "path": meta.get("repo_path", "unknown"),
            "indexed_at": meta.get("indexed_at", "unknown"),
            "active": child.name == active_name,
        })
    return repos


def _interactive_select(repos: list[dict]) -> int | None:
    """Arrow-key interactive repo selector. Returns selected index or None on cancel.

    Uses termios/tty on Unix and msvcrt on Windows.
    Falls back to numbered input when neither raw-mode nor ANSI are available.
    """
    current = next((i for i, r in enumerate(repos) if r["active"]), 0)

    def render(selected: int) -> None:
        if _ANSI:
            if getattr(render, "_drawn", False):
                sys.stdout.write("\033[u")  # restore saved cursor position
            else:
                sys.stdout.write("\033[s")  # save cursor position before first draw
            render._drawn = True  # type: ignore[attr-defined]
            for i, r in enumerate(repos):
                marker = _c("1;32", "▶") if i == selected else " "
                active_tag = f"  {_c('33', '(active)')}" if r["active"] else ""
                sys.stdout.write(f"\r\033[2K  {marker} {r['name']}{active_tag}\r\n")
        else:
            # No cursor movement — reprint with a plain marker
            for i, r in enumerate(repos):
                marker = "> " if i == selected else "  "
                active_tag = "  (active)" if r["active"] else ""
                print(f"  {marker}{r['name']}{active_tag}")
        sys.stdout.flush()

    sys.stdout.write("\n")
    render(current)

    if platform.system() == "Windows":
        try:
            import msvcrt  # type: ignore[import]

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
            # msvcrt unavailable — fall back to numbered selection
            return _numbered_select(repos)
    else:
        try:
            import tty
            import termios

            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
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


def cmd_status(_args: argparse.Namespace) -> int:
    """Show the currently active repository."""
    ws = _get_workspace_root()
    repos = _load_repos(ws)
    total = len(repos)

    active = next((r for r in repos if r["active"]), None)

    # ── Workspace ──
    print()
    print(f"  workspace  {_c('2', str(ws))}")

    # ── Repository ──
    if active is None:
        print(f"  active     {_c('33', '(none)')}   {total} repos indexed  —  cgb repo to select")
    else:
        cwd = Path.cwd().resolve()
        linked = next((r for r in repos if Path(r["path"]).resolve() == cwd), None)

        if linked and linked["active"]:
            print(f"  here       {_c('32', active['name'])}   {cwd}")
        elif linked:
            print(f"  here       {_c('33', linked['name'])}   {cwd}   (not active — cgb repo to switch)")
        else:
            print(f"  here       {_c('2', 'not indexed')}   {cwd}   (cgb index to add)")

        print(f"  active     {active['name']}   {active['path']}")

    # ── LLM ──
    llm = _detect_llm_info()
    if llm:
        print(f"  llm        {_c('32', llm['model'])}   {llm['provider']}   {llm['base_url']}")
    else:
        print(f"  llm        {_c('33', '(not configured)')}   — cgb config --llm-model <model> to set")

    # ── Embedding ──
    embed = _detect_embed_info()
    if embed:
        print(f"  embedding  {_c('32', embed['model'])}   {embed['provider']}   {embed['base_url']}")
    else:
        print(f"  embedding  {_c('33', '(not configured)')}   — cgb config --embed-model <model> to set")

    # ── Version ──
    print(f"  version    cgb {__version__}")
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
        "# code-graph-builder configuration",
        "# Managed by cgb config / setup wizard. Edit freely.",
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

    def render(initial: bool = False) -> None:
        if _ANSI:
            # Save cursor before first draw; restore before each redraw.
            # This avoids counting lines (which breaks across cooked/raw mode).
            sys.stdout.write("\033[s" if initial else "\033[u")
        for i, opt in enumerate(options):
            if _ANSI:
                marker = _c("1;36", "◉") if i == cursor else _c("2", "○")
                label = _c("1;36", opt) if i == cursor else opt
                sys.stdout.write(f"\r\033[2K{prefix}{marker} {label}\r\n")
            else:
                marker = "> " if i == cursor else "  "
                print(f"{prefix}{marker}{opt}")
        sys.stdout.flush()

    render(initial=True)

    raw_ok = sys.stdin.isatty()
    if raw_ok and platform.system() == "Windows":
        try:
            import msvcrt
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
                    render()
            raw_ok = False  # reached only if loop breaks unexpectedly
        except ImportError:
            raw_ok = False
    elif raw_ok:
        try:
            import tty, termios
            fd = sys.stdin.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
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
                        render()
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
        except (ImportError, AttributeError):
            raw_ok = False

    if not raw_ok:
        # Fallback: numbered input
        print()
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
    print("* = active  |  Switch with: cgb repo")
    return 0


def _rename_repo(artifact_dir: Path, new_name: str) -> None:
    """Update repo_name in meta.json."""
    meta_file = artifact_dir / "meta.json"
    if not meta_file.exists():
        return
    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    meta["repo_name"] = new_name
    meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2))


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
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
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
    from code_graph_builder.entrypoints.mcp.pipeline import artifact_dir_for

    target_dir = artifact_dir_for(ws, repo_path)

    if target_dir == artifact_dir:
        # Same hash — just update meta.json in place
        _link_update_meta(artifact_dir, repo_path)
    elif target_dir.exists() and (target_dir / "graph.db").exists():
        # Target already has data — just update its meta
        _link_update_meta(target_dir, repo_path)
        artifact_dir = target_dir
    else:
        # Create new dir with symlinks (or copies on Windows) pointing to source
        target_dir.mkdir(parents=True, exist_ok=True)
        _link_artifacts(artifact_dir, target_dir)
        _link_update_meta(target_dir, repo_path, source_dir=artifact_dir)
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
            existing = json.loads(meta_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    meta = {
        **existing,
        "repo_path": repo_path.as_posix(),
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
    from code_graph_builder.domains.core.graph.incremental_updater import (
        IncrementalUpdater, INCREMENTAL_FILE_LIMIT,
    )
    from code_graph_builder.entrypoints.mcp.pipeline import (
        artifact_dir_for,
        build_vector_index,
        generate_api_docs_step,
        save_meta,
    )
    from code_graph_builder.foundation.services.git_service import GitChangeDetector
    from code_graph_builder.foundation.services.kuzu_service import KuzuIngestor

    artifact_dir = artifact_dir_for(ws, repo_path)
    db_path = artifact_dir / "graph.db"
    vectors_path = artifact_dir / "vectors.pkl"
    meta_file = artifact_dir / "meta.json"

    if not db_path.exists():
        print(f"{_c('33', 'WARN')} No existing index found. Running full rebuild instead.")
        args.incremental = False
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
        args.incremental = False
        return cmd_index(args)

    if not changed_files:
        print(f"{_c('32', '✓')} No changes since last index. Already up to date.")
        return 0

    if len(changed_files) > INCREMENTAL_FILE_LIMIT:
        print(
            f"{_c('33', 'WARN')} Too many changed files ({len(changed_files)} > {INCREMENTAL_FILE_LIMIT}). "
            f"Running full rebuild."
        )
        args.incremental = False
        return cmd_index(args)

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


def cmd_index(args: argparse.Namespace) -> int:
    """Run the full indexing pipeline on a repository."""
    from code_graph_builder.examples.generate_wiki import MAX_PAGES_COMPREHENSIVE, MAX_PAGES_CONCISE
    from code_graph_builder.entrypoints.mcp.pipeline import (
        artifact_dir_for,
        build_graph,
        build_vector_index,
        generate_api_docs_step,
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
    if getattr(args, "incremental", False):
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
    rebuild = True  # Default: always rebuild
    backend = args.backend
    comprehensive = args.mode != "concise"
    max_pages = MAX_PAGES_COMPREHENSIVE if comprehensive else MAX_PAGES_CONCISE

    total_steps = 4
    if skip_embed:
        total_steps = 2
    elif skip_wiki:
        total_steps = 3

    step_label = "graph -> api-docs"
    if not skip_embed:
        step_label += " -> embeddings"
    if not skip_wiki:
        step_label += " -> wiki"

    artifact_dir = artifact_dir_for(ws, repo_path)
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
        from code_graph_builder.foundation.services.git_service import GitChangeDetector
        _head = GitChangeDetector().get_current_head(repo_path)
        save_meta(artifact_dir, repo_path, 0, last_indexed_commit=_head, repo_name=custom_name)
        ws_root = _get_workspace_root()
        (ws_root / "active.txt").write_text(artifact_dir.name, encoding="utf-8")

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
            print("Run: cgb list")
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
    active_name = active_file.read_text(encoding="utf-8").strip() if active_file.exists() else ""

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
    from code_graph_builder.entrypoints.mcp.pipeline import (
        build_graph,
        build_vector_index,
        generate_api_docs_step,
        run_wiki_generation,
        save_meta,
    )
    from code_graph_builder.examples.generate_wiki import MAX_PAGES_COMPREHENSIVE

    ws = _get_workspace_root()
    active_file = ws / "active.txt"
    if not active_file.exists():
        print("No active repository. Run: cgb repo")
        return 1

    artifact_dir = ws / active_file.read_text(encoding="utf-8").strip()
    meta_file = artifact_dir / "meta.json"
    if not meta_file.exists():
        print("Active repository has no metadata. Run: cgb index <path>")
        return 1

    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    repo_path = Path(meta["repo_path"]).resolve()
    db_path = artifact_dir / "graph.db"
    vectors_path = artifact_dir / "vectors.pkl"
    wiki_dir = artifact_dir / "wiki"

    step = args.step  # graph | api | embed | wiki | None (all)
    run_all = step is None
    backend = args.backend

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
                from code_graph_builder.foundation.services.kuzu_service import KuzuIngestor
                ingestor = KuzuIngestor(db_path)
                ingestor.__enter__()
                builder = ingestor
            generate_api_docs_step(
                builder, artifact_dir, rebuild=True,
                progress_cb=lambda msg, pct: progress("api", msg, pct),
            )
            bar.done(steps_to_run.index("api") + 1, last_msg[0] or "API docs generated")

        vector_store = embedder = func_map = None

        if run_all or step == "embed":
            if builder is None:
                from code_graph_builder.foundation.services.kuzu_service import KuzuIngestor
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
                    print(f"{_c('31', 'ERROR')} No embeddings found. Run: cgb rebuild --step embed")
                    return 1
            if builder is None:
                from code_graph_builder.foundation.services.kuzu_service import KuzuIngestor
                ingestor = KuzuIngestor(db_path)
                ingestor.__enter__()
                builder = ingestor
            if embedder is None:
                from code_graph_builder.domains.core.embedding.qwen3_embedder import create_embedder
                embedder = create_embedder()
            if func_map is None:
                func_map = {}
            _, page_count = run_wiki_generation(
                builder=builder,
                repo_path=repo_path,
                output_dir=wiki_dir,
                max_pages=MAX_PAGES_COMPREHENSIVE,
                rebuild=True,
                comprehensive=True,
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


def _load_vector_store_simple(vectors_path: Path):
    """Load vector store from pickle, minimal version for rebuild."""
    import pickle
    if not vectors_path.exists():
        return None
    try:
        from code_graph_builder.domains.core.embedding.vector_store import MemoryVectorStore, VectorRecord
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

        builder = CodeGraphBuilder(
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
    """Execute the setup command — add cgb Scripts dir to Windows user PATH."""
    if platform.system() != "Windows":
        print("This command is only needed on Windows.")
        print("On Linux/macOS, cgb is available in PATH automatically after pip install.")
        return 0

    scripts_dir = Path(sys.executable).parent / "Scripts"
    if not (scripts_dir / "cgb.exe").exists():
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


def main() -> int:
    """Main entry point for CLI."""
    # Load exclusively from workspace .env — single source of truth.
    # reload_env() also removes config keys absent from .env so stale shell
    # exports don't silently override the current configuration.
    from code_graph_builder.foundation.utils.settings import reload_env
    reload_env()

    prog = "cgb"
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Code knowledge graph builder — index, explore, and navigate any codebase.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Workspace commands:
  cgb status                    show active repository
  cgb list                      list all indexed repositories
  cgb repo                      interactively switch repository (↑/↓)

Indexing commands:
  cgb index [path]              full pipeline: graph → api-docs → embeddings
  cgb index . --wiki            index with wiki generation
  cgb rebuild                   rebuild graph → api-docs → embeddings
  cgb rebuild --wiki            rebuild all steps including wiki
  cgb rebuild --step embed      rebuild only embeddings
  cgb clean                     interactively remove an indexed repo
  cgb clean myrepo              remove specific repo by name

Low-level commands:
  cgb scan /path --db-path ./graph.db
  cgb query "MATCH (f:Function) RETURN f.name LIMIT 5" --db-path ./graph.db
  cgb export /path --output ./graph.json --build
  cgb stats --db-path ./graph.db

Run 'cgb <command> --help' for details on any command.

Windows:
  cgb setup                     add cgb to user PATH (run once after install)
        """,
        add_help=False,
    )
    parser.add_argument("-h", "--help", action="store_true", help="Show this help message")

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
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
        help="Show the currently active repository",
        description="Display info about the currently active CodeGraphWiki repository.",
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
        "--incremental", "-i",
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
    index_parser.set_defaults(func=cmd_index)

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
        help="Specific step to rebuild (default: all steps)",
    )
    rebuild_parser.add_argument(
        "--wiki",
        action="store_true",
        help="Include wiki generation (disabled by default)",
    )
    rebuild_parser.add_argument(
        "--backend",
        choices=["kuzu", "memgraph", "memory"],
        default="kuzu",
        help="Storage backend (default: kuzu)",
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

    # setup command (Windows PATH helper)
    setup_parser = subparsers.add_parser(
        "setup",
        help="Add cgb to the system PATH (Windows only)",
        description="Add the cgb executable directory to the current user's PATH on Windows.",
    )
    setup_parser.set_defaults(func=cmd_setup)

    args = parser.parse_args()

    if getattr(args, "help", False) or not args.command:
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
