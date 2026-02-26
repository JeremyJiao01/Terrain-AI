#!/usr/bin/env python3
"""Global CLI entry point for CodeGraphWiki custom commands.

This file lives at ~/.claude/commands/code-graph/cgb_cli.py and acts as
a thin wrapper that delegates to the installed code_graph_builder package.

Usage (called by the .md command files in this directory):
    python3 ~/.claude/commands/code-graph/cgb_cli.py <subcommand> [args...]
"""

import sys


def main():
    try:
        from code_graph_builder.commands_cli import main as cli_main
    except ImportError:
        print(
            "ERROR: code_graph_builder package is not installed.\n"
            "Run the following to install:\n"
            "  pip install /path/to/CodeGraphWiki\n"
            "Or:\n"
            "  pip install -e /path/to/CodeGraphWiki",
            file=sys.stderr,
        )
        sys.exit(1)

    cli_main()


if __name__ == "__main__":
    main()
