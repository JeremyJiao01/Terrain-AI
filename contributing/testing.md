# Testing

## Test Structure

Tests mirror the source layout:

```
tests/
  foundation/        # L0 + L1 tests
  domains/
    core/            # L2 tests (graph, embedding, search)
    upper/           # L3 tests (apidoc, rag, guidance)
  entrypoints/       # L4 tests (mcp, cli)
```

Current flat test directory: `code_graph_builder/tests/`

## Naming Conventions

- File: `test_<module>.py`
- Class: `Test<Feature>`
- Method: `test_<behavior>`

Example: `test_builder.py` / `TestGraphBuild` / `test_resolves_cross_file_calls`

## Run Commands

```bash
# Full suite
python -m pytest code_graph_builder/tests/ -v

# Single file
python -m pytest code_graph_builder/tests/test_basic.py -v

# Single test
python -m pytest code_graph_builder/tests/test_basic.py::TestBasic::test_build -v

# By keyword
python -m pytest code_graph_builder/tests/ -k "embedding" -v
```

## Test Types

| Type | Scope | Example |
|------|-------|---------|
| Unit | Single function/class, no I/O | `test_basic.py` |
| Integration | Multiple modules, real DB | `test_step1_graph_build.py`, `test_integration_semantic.py` |
| End-to-end | Full pipeline or MCP protocol | `test_mcp_protocol.py`, `test_mcp_user_flow.py` |

## GBK/GB2312 Encoding

Some test fixtures use GBK or GB2312 encoded files. If tests fail with encoding errors on your system:

- Ensure your locale supports these encodings.
- The `code_graph_builder/utils/encoding.py` module handles detection and conversion.
- Related tests verify that non-UTF-8 files are parsed correctly.

## Before Submitting Checklist

1. `python tools/dep_check.py` -- zero violations.
2. `python -m pytest code_graph_builder/tests/ -v` -- all tests pass.
3. No new imports that violate layer rules (see `contributing/architecture.md`).
