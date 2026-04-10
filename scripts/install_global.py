#!/usr/bin/env python3
"""Install CodeGraphWiki custom commands globally for Claude Code.

This script:
1. pip-installs the terrain package (editable or normal)
2. Creates ~/.claude/commands/code-graph/
3. Copies terrain_cli.py (the entry point) into that directory
4. Copies all .md command files into that directory
5. (Optional) Interactively configures LLM/Embedding API keys in
   ~/.claude/settings.json

After running this, /repo-init, /code-search, etc. will be available in
Claude Code from any project directory.

Usage:
    python3 scripts/install_global.py [--editable] [--skip-config]

Options:
    --editable      Install package in editable mode (pip install -e .)
                    Recommended for development so code changes take effect
                    immediately without re-installing.
    --skip-config   Skip the interactive API configuration step.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

# Resolve paths
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
COMMANDS_SRC = PROJECT_ROOT / ".claude" / "commands"
CLI_WRAPPER = PROJECT_ROOT / "terrain" / "terrain_cli.py"

TARGET_DIR = Path.home() / ".claude" / "commands" / "code-graph"
SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

# API config prompts: (env_var_name, display_label, is_required)
_CONFIG_PROMPTS = [
    ("LLM_API_KEY", "LLM API Key (for wiki generation & Cypher queries)", True),
    ("LLM_BASE_URL", "LLM Base URL", False),
    ("LLM_MODEL", "LLM Model name", False),
    ("EMBEDDING_PROVIDER", "Embedding provider: qwen3 or openai (auto-detected if empty)", False),
    ("DASHSCOPE_API_KEY", "DashScope API Key (for Qwen3 embeddings)", False),
    ("DASHSCOPE_BASE_URL", "DashScope Base URL", False),
    ("EMBEDDING_API_KEY", "OpenAI Embedding API Key (if using OpenAI embeddings)", False),
    ("EMBEDDING_BASE_URL", "OpenAI Embedding Base URL", False),
    ("EMBEDDING_MODEL", "Embedding model name (e.g. text-embedding-3-small)", False),
]


def _configure_settings() -> None:
    """Interactive API key configuration, writes to ~/.claude/settings.json."""
    print("\n[4/4] API Configuration")
    print("  CodeGraphWiki needs LLM and Embedding API keys to work.")
    print("  Keys will be saved to ~/.claude/settings.json")
    print("  (Press Enter to skip any field)\n")

    # Load existing settings
    existing: dict = {}
    if SETTINGS_PATH.exists():
        try:
            existing = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    env_block: dict = existing.get("env", {})
    changed = False

    for var_name, label, required in _CONFIG_PROMPTS:
        current = env_block.get(var_name, "")
        if current:
            masked = current[:4] + "..." + current[-4:] if len(current) > 12 else "***"
            prompt_text = f"  {label}\n    [{var_name}] (current: {masked}): "
        else:
            marker = " *" if required else ""
            prompt_text = f"  {label}{marker}\n    [{var_name}]: "

        try:
            value = input(prompt_text).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Configuration cancelled.")
            return

        if value:
            env_block[var_name] = value
            changed = True

    if changed:
        existing["env"] = env_block
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"\n  Settings saved to {SETTINGS_PATH}")
    else:
        print("\n  No changes made to settings.")


def main():
    editable = "--editable" in sys.argv or "-e" in sys.argv
    skip_config = "--skip-config" in sys.argv

    total_steps = 3 if skip_config else 4

    # Step 1: pip install the package
    mode_label = "editable" if editable else "normal"
    print(f"[1/{total_steps}] Installing terrain ({mode_label} mode)...")

    cmd = [sys.executable, "-m", "pip", "install"]
    if editable:
        cmd.append("-e")
    cmd.append(str(PROJECT_ROOT))

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"ERROR: pip install failed:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    print("  Done.")

    # Step 2: Create target directory
    print(f"[2/{total_steps}] Creating {TARGET_DIR} ...")
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    print("  Done.")

    # Step 3: Copy files
    print(f"[3/{total_steps}] Copying command files...")

    # Copy cgb_cli.py
    dest_cli = TARGET_DIR / "terrain_cli.py"
    shutil.copy2(CLI_WRAPPER, dest_cli)
    print(f"  Copied terrain_cli.py -> {dest_cli}")

    # Copy all .md command files
    count = 0
    for md_file in sorted(COMMANDS_SRC.glob("*.md")):
        dest = TARGET_DIR / md_file.name
        shutil.copy2(md_file, dest)
        count += 1
        print(f"  Copied {md_file.name}")

    print(f"\n  {count} commands installed to {TARGET_DIR}")

    # Step 4: Interactive config (optional)
    if not skip_config:
        _configure_settings()

    print("\n=== Installation complete ===")
    print("You can now use the following commands from any project in Claude Code:")
    print("  /ask <question>     — Ask anything about an indexed codebase")
    print("  /trace <function>   — Trace complete call chain for a function")
    print("  /code-gen <design>  — Generate implementation plan from design document")
    if skip_config:
        print("\nTo configure API keys later, run:")
        print(f"  python3 {Path(__file__).resolve()}")


if __name__ == "__main__":
    main()
