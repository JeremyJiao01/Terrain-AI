# CodeGraphWiki

Code knowledge graph builder with MCP server for AI-assisted code navigation.

## Architecture

This project uses a 5-layer harness architecture (L0-L4).

All paths relative to `code_graph_builder/`.

```
L0  foundation/types/                          Pure data: constants, types, config, models
L1  foundation/{parsers,services,utils}/       Shared infra: AST parsing, DB drivers, utilities
L2  domains/core/{graph,embedding,search}/     Core domains: graph build, embeddings, search
L3  domains/upper/{apidoc,calltrace,rag,guidance}/  Upper domains: API docs, call trace, RAG, guidance
L4  entrypoints/{mcp,cli}/                     Entry points: MCP server, CLI commands
```

Rule: upper imports lower. Never reverse. Never cross-domain at same layer.

See `contributing/architecture.md` for full rules.

## Before Modifying Code

1. Read `contributing/add-feature.md` to find which files to touch.
2. Run `python tools/dep_check.py` before committing.
3. Run `python -m pytest tests/ -v` to verify.
4. Check `contributing/testing.md` Impact-Based Testing table — if your change touches listed files, run the mapped tests locally before pushing.
5. Ensure Windows compatibility — see rules below.

## Windows Compatibility

All new features and modifications MUST work on Windows. Key rules:

- Use `pathlib.Path` or `os.path.join()` for file paths — never hardcode `/` as separator
- Use `shutil` for file operations instead of shell commands
- Avoid Unix-only APIs (e.g., `os.symlink` without fallback, `fcntl`, `signal.SIGKILL`)
- Use `subprocess` with `shell=False` where possible; never assume `bash` is available
- Test file paths with spaces and non-ASCII characters
- Use `tempfile` module for temp directories — never hardcode `/tmp`
- Handle `PermissionError` and locked files (common on Windows due to antivirus / open handles)
- Use `os.environ` for environment variables — never assume Unix shell expansion

## Key Entry Points

- `terrain` CLI: `entrypoints/cli/cli.py`
- `terrain-mcp` MCP server: `entrypoints/mcp/server.py`
- Main API: `domains/core/graph/builder.py` -> `CodeGraphBuilder`

## Custom Commands

- `/ask <question>`: Ask anything about an indexed codebase — works from any directory, answers using code graph + semantic search
- `/trace <function>`: Trace complete call chain for a function — reveals entry points, callers, and blast radius
- `/code-gen <design-doc>`: Generate implementation plan from design document using MCP tools

## Build & Test

```bash
pip install -e ".[treesitter-full]"
python -m pytest tests/ -v
python tools/dep_check.py
```
