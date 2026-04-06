# CodeGraphWiki

Code knowledge graph builder with MCP server for AI-assisted code navigation.

## Architecture

This project uses a 5-layer harness architecture (L0-L4).

```
L0  foundation/types/           Pure data definitions
L1  foundation/{parsers,services,utils}/  Shared infrastructure
L2  domains/core/               Core domains (graph, embedding, search)
L3  domains/upper/              Upper domains (apidoc, rag, guidance)
L4  entrypoints/                Entry points (mcp, cli)
```

Rule: upper imports lower. Never reverse. Never cross-domain at same layer.

See `contributing/architecture.md` for full rules.

## Before Modifying Code

1. Read `contributing/add-feature.md` to find which files to touch.
2. Run `python tools/dep_check.py` before committing.
3. Run `python -m pytest code_graph_builder/tests/ -v` to verify.
4. Check `contributing/testing.md` Impact-Based Testing table — if your change touches listed files, run the mapped tests locally before pushing.

## Key Entry Points

- `code-graph-builder` CLI: `entrypoints/cli/cli.py`
- `cgb-mcp` MCP server: `entrypoints/mcp/server.py`
- Main API: `domains/core/graph/builder.py` -> `CodeGraphBuilder`

## Custom Commands

- `/code-gen <design-doc>`: Generate implementation plan from design document using MCP tools

## Build & Test

```bash
pip install -e ".[treesitter-full]"
python -m pytest code_graph_builder/tests/ -v
python tools/dep_check.py
```
