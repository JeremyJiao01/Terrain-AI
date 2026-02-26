#!/usr/bin/env python3
"""Install CodeGraphWiki custom commands globally for Claude Code.

This script:
1. pip-installs the code_graph_builder package (editable or normal)
2. Creates ~/.claude/commands/code-graph/
3. Copies cgb_cli.py (the entry point) into that directory
4. Copies all .md command files into that directory

After running this, /repo-init, /code-search, etc. will be available in
Claude Code from any project directory.

Usage:
    python3 scripts/install_global.py [--editable]

Options:
    --editable   Install package in editable mode (pip install -e .)
                 Recommended for development so code changes take effect
                 immediately without re-installing.
"""

import shutil
import subprocess
import sys
from pathlib import Path

# Resolve paths
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
COMMANDS_SRC = PROJECT_ROOT / ".claude" / "commands"
CLI_WRAPPER = PROJECT_ROOT / "code_graph_builder" / "cgb_cli.py"

TARGET_DIR = Path.home() / ".claude" / "commands" / "code-graph"


def main():
    editable = "--editable" in sys.argv or "-e" in sys.argv

    # Step 1: pip install the package
    mode_flag = "-e" if editable else ""
    mode_label = "editable" if editable else "normal"
    print(f"[1/3] Installing code_graph_builder ({mode_label} mode)...")

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
    print(f"[2/3] Creating {TARGET_DIR} ...")
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    print("  Done.")

    # Step 3: Copy files
    print("[3/3] Copying command files...")

    # Copy cgb_cli.py
    dest_cli = TARGET_DIR / "cgb_cli.py"
    shutil.copy2(CLI_WRAPPER, dest_cli)
    print(f"  Copied cgb_cli.py → {dest_cli}")

    # Copy all .md command files
    count = 0
    for md_file in sorted(COMMANDS_SRC.glob("*.md")):
        dest = TARGET_DIR / md_file.name
        shutil.copy2(md_file, dest)
        count += 1
        print(f"  Copied {md_file.name}")

    print(f"\n=== Done: {count} commands installed to {TARGET_DIR} ===")
    print("\nYou can now use /repo-init, /code-search, etc. from any project in Claude Code.")


if __name__ == "__main__":
    main()
