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

## Regression Guarantee

Every test added for a new feature automatically becomes part of the regression baseline.
`pytest` discovers all `test_*.py` files under `tests/` recursively, and CI runs the
full suite on every push and pull request. This means:

1. Your new tests **will** run on every future change — no extra registration needed.
2. If a later change breaks your feature, CI will catch it before merge.
3. Never delete or weaken an existing test to make a new feature pass. Fix the code instead.

## Impact-Based Testing

When your change touches files covered by specific test suites, you **must** run those tests locally before pushing. This is not optional — CI catches failures, but local testing catches them faster and avoids broken pushes.

| Changed Files | Required Tests | Command |
|---------------|---------------|---------|
| `foundation/parsers/call_processor.py` | func ptr detection | `python -m pytest code_graph_builder/tests/foundation/parsers/test_func_ptr_detection.py -v` |
| `foundation/parsers/call_resolver.py` | func ptr detection | `python -m pytest code_graph_builder/tests/foundation/parsers/test_func_ptr_detection.py -v` |
| `foundation/parsers/language_spec.py` | func ptr detection | `python -m pytest code_graph_builder/tests/foundation/parsers/test_func_ptr_detection.py -v` |
| `foundation/parsers/parser_loader.py` | func ptr detection | `python -m pytest code_graph_builder/tests/foundation/parsers/test_func_ptr_detection.py -v` |
| `foundation/types/constants.py` | func ptr + calltrace | `python -m pytest code_graph_builder/tests/foundation/parsers/test_func_ptr_detection.py code_graph_builder/tests/domains/upper/calltrace/ -v` |
| `domains/core/search/graph_query.py` | calltrace | `python -m pytest code_graph_builder/tests/domains/upper/calltrace/ -v` |
| `domains/upper/calltrace/` | calltrace | `python -m pytest code_graph_builder/tests/domains/upper/calltrace/ -v` |
| `domains/core/graph/graph_updater.py` | func ptr + graph build | `python -m pytest code_graph_builder/tests/foundation/parsers/test_func_ptr_detection.py -v` |
| `entrypoints/mcp/tools.py` | MCP protocol | `python -m pytest code_graph_builder/tests/entrypoints/ -v` |

**Rule:** When adding new test files, add a row to this table mapping source files to the new tests. This keeps the impact map current.

## Before Submitting Checklist

1. `python tools/dep_check.py` -- zero violations.
2. `python -m pytest code_graph_builder/tests/ -v` -- all tests pass.
3. No new imports that violate layer rules (see `contributing/architecture.md`).
4. If your change touches files in the Impact-Based Testing table above, verify those specific tests pass locally before pushing.
