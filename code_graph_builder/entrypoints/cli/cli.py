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
from pathlib import Path
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
                sys.stdout.write(f"\033[{len(repos)}A")
            render._drawn = True  # type: ignore[attr-defined]
            for i, r in enumerate(repos):
                marker = _c("1;32", "▶") if i == selected else " "
                active_tag = f"  {_c('33', '(active)')}" if r["active"] else ""
                sys.stdout.write(f"\r  {marker} {r['name']}{active_tag}\n")
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
                        sys.stdout.write("\n")
                        sys.stdout.flush()
                        return current
                    if ch in ("\x03", "q"):
                        sys.stdout.write("\n")
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


def cmd_status(_args: argparse.Namespace) -> int:
    """Show the currently active repository."""
    ws = _get_workspace_root()
    repos = _load_repos(ws)
    total = len(repos)

    active = next((r for r in repos if r["active"]), None)
    if active is None:
        print(f"active  (none)   {total} repos indexed  —  cgb repo to select")
        return 1

    cwd = Path.cwd().resolve()
    linked = next((r for r in repos if Path(r["path"]).resolve() == cwd), None)

    if linked and linked["active"]:
        print(_c("32", f"here    {active['name']}   {cwd}"))
    elif linked:
        print(_c("33", f"here    {linked['name']}   {cwd}   (not active — cgb repo to switch)"))
    else:
        print(_c("2", f"here    not indexed   {cwd}   (cgb index to add)"))

    print(f"active  {active['name']}   {active['path']}")

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
    return 0


# ---------------------------------------------------------------------------
# index
# ---------------------------------------------------------------------------

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

    skip_embed = args.no_embed
    skip_wiki = args.no_wiki or skip_embed
    rebuild = args.rebuild
    backend = args.backend
    comprehensive = args.mode != "concise"
    max_pages = MAX_PAGES_COMPREHENSIVE if comprehensive else MAX_PAGES_CONCISE

    total_steps = 4
    if skip_embed:
        total_steps = 2
    elif skip_wiki:
        total_steps = 3

    step_label = "graph → api-docs"
    if not skip_embed:
        step_label += " → embeddings"
    if not skip_wiki:
        step_label += " → wiki"

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
        save_meta(artifact_dir, repo_path, page_count)
        ws_root = _get_workspace_root()
        (ws_root / "active.txt").write_text(artifact_dir.name, encoding="utf-8")

        print(f"{_c('32', '✓')} Done   {repo_path.name}   active repo set")
        if not skip_wiki:
            print(f"  wiki pages: {page_count}")
        return 0

    except Exception as exc:
        sys.stdout.write("\n")
        print(f"{_c('31', 'ERROR')} Indexing failed: {exc}")
        if args.verbose:
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

    steps_to_run = (
        ["graph", "api", "embed", "wiki"] if run_all else
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

        if run_all or step == "wiki":
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


def main() -> int:
    """Main entry point for CLI."""
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
  cgb index [path]              full pipeline: graph → api-docs → embeddings → wiki
  cgb index . --no-wiki         index without wiki generation
  cgb rebuild                   rebuild all steps for active repo
  cgb rebuild --step embed      rebuild only embeddings
  cgb clean                     interactively remove an indexed repo
  cgb clean myrepo              remove specific repo by name

Low-level commands:
  cgb scan /path --db-path ./graph.db
  cgb query "MATCH (f:Function) RETURN f.name LIMIT 5" --db-path ./graph.db
  cgb export /path --output ./graph.json --build
  cgb stats --db-path ./graph.db

Run 'cgb <command> --help' for details on any command.
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
        "--rebuild",
        action="store_true",
        help="Force rebuild even if artifacts exist",
    )
    index_parser.add_argument(
        "--no-embed",
        action="store_true",
        help="Skip embedding generation (also skips wiki)",
    )
    index_parser.add_argument(
        "--no-wiki",
        action="store_true",
        help="Skip wiki generation",
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

    args = parser.parse_args()

    if getattr(args, "help", False) or not args.command:
        parser.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
