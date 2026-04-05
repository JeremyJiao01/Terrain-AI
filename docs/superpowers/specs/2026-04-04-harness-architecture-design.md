# Harness Architecture Design — CodeGraphWiki

**Date:** 2026-04-04
**Status:** Approved
**Scope:** Full harness system — layered architecture, test-driven refactoring, CI, contributing docs

---

## 1. Goal

Restructure `code_graph_builder/` into a strict 5-layer architecture with enforced dependency rules, comprehensive test coverage, CI automation, and agent-oriented contributing documentation.

The `npm-package/` directory is **excluded** from the layer model — it remains an independent distribution wrapper.

---

## 2. Target Directory Structure

```
code_graph_builder/
│
├── foundation/                          # L0 + L1
│   ├── types/                           # L0: Pure data definitions
│   │   ├── constants.py                 # NodeLabel, RelationshipType, SupportedLanguage enums
│   │   ├── types.py                     # BuildResult, GraphData dataclasses
│   │   └── config.py                    # Configuration dataclasses
│   ├── parsers/                         # L1: AST parsers
│   │   ├── factory.py
│   │   ├── parser_loader.py
│   │   └── language_spec.py
│   ├── services/                        # L1: Database drivers
│   │   ├── kuzu_ingestor.py
│   │   ├── memgraph_ingestor.py
│   │   └── memory_ingestor.py
│   └── utils/                           # L1: Utility functions
│       └── ...
│
├── domains/
│   ├── core/                            # L2: Core domains
│   │   ├── graph/                       # Graph building
│   │   │   ├── builder.py               # CodeGraphBuilder main API
│   │   │   └── graph_updater.py         # Graph update logic
│   │   └── embedding/                   # Vector embeddings
│   │       └── ...
│   └── upper/                           # L3: Upper domains (depend on core)
│       ├── apidoc/                      # API doc generation (depends on graph)
│       │   └── api_doc_generator.py
│       └── rag/                         # RAG pipeline (depends on embedding)
│           └── ...
│
├── entrypoints/                         # L4: Thin shells
│   ├── mcp/                             # MCP server
│   │   ├── server.py
│   │   └── tools.py
│   └── cli/                             # CLI
│       └── commands_cli.py
│
├── tests/                               # Mirrors source structure
│   ├── foundation/
│   │   ├── test_parsers.py
│   │   ├── test_services.py
│   │   └── test_utils.py
│   ├── domains/
│   │   ├── core/
│   │   │   ├── test_graph_builder.py
│   │   │   └── test_embedding.py
│   │   └── upper/
│   │       ├── test_apidoc.py
│   │       └── test_rag.py
│   └── entrypoints/
│       ├── test_mcp.py
│       └── test_cli.py
│
└── __init__.py
```

---

## 3. Dependency Rules

### Layer Definitions

| Layer | Path | May import | Must NOT import |
|-------|------|------------|-----------------|
| L0 | `foundation/types/` | stdlib + third-party only | Any project module |
| L1 | `foundation/{parsers,services,utils}/` | L0 + stdlib + third-party | L2, L3, L4 |
| L2 | `domains/core/` | L0, L1 | L3, L4, cross-domain within L2 (graph ✗→ embedding) |
| L3 | `domains/upper/` | L0, L1, L2 | L4, cross-domain within L3 (apidoc ✗→ rag) |
| L4 | `entrypoints/` | L0, L1, L2, L3 | cross-entrypoint (mcp ✗→ cli) |

### One-line summary

**Upper layers may import any lower layer. Reverse is forbidden. Same-layer cross-domain is forbidden.**

### Enforcement

`tools/dep_check.py` — a Python script that:
1. Parses all `.py` files for import statements
2. Determines each file's layer from its path
3. Checks every import against the rules above
4. Prints violations and exits non-zero if any found

Example output:
```
VIOLATION: domains/core/graph/builder.py imports domains/upper/apidoc/api_doc_generator
  Rule: L2 cannot import L3

Found 1 violation. FAILED.
```

---

## 4. Test-Driven Refactoring Process

### Principle

No file moves without passing tests. Tests are the single source of truth for correctness.

### Step 0: Establish Test Baseline

Before any refactoring, audit existing 284+ tests and fill gaps:

| Module | Test Target | Method |
|--------|-------------|--------|
| `constants.py`, `types.py`, `config.py` | Enum completeness, dataclass serialization | Unit test |
| `parsers/` | AST output per language | Unit test (source code → nodes/relationships) |
| `parsers/` (encoding) | **GBK/GB2312 encoded C files** — function detection with Chinese comments and string literals | Unit test with prepared `.c` test fixtures |
| `services/` | CRUD operations per backend | Integration test |
| `builder.py` | Full graph build pipeline | Integration test (sample repo → verify node/relation counts) |
| `graph_updater.py` | Graph update logic | Unit test (mock service → verify calls) |
| `embeddings/` | Embedding generation and retrieval | Integration test |
| `api_doc_generator.py` | Doc output format | Unit test (graph data → verify markdown) |
| `rag/` | RAG query pipeline | Integration test |
| `mcp/server.py` + `tools.py` | MCP tool invocation | End-to-end test |
| `commands_cli.py` | CLI command entry | End-to-end test |

**Rule:** Only add missing tests. Do not rewrite existing passing tests.

### Steps 1-N: Batch Migration

Each batch follows this exact sequence:

```
1. Confirm all tests for this batch PASS (baseline)
2. Move files to target location
3. Update import paths
4. Place compatibility shim at original location
5. Run this module's tests → MUST PASS
6. Run full test suite → MUST match baseline
7. Git commit this batch
```

**If step 5 or 6 fails → stop, fix in place, do not proceed.**

### Migration Order (bottom-up)

| Batch | Target | Source |
|-------|--------|--------|
| 1 | `foundation/types/` | `constants.py`, `types.py`, `config.py` |
| 2 | `foundation/utils/` | `utils/` |
| 3 | `foundation/parsers/` | `parsers/`, `language_spec.py`, `parser_loader.py` |
| 4 | `foundation/services/` | `services/` |
| 5 | `domains/core/graph/` | `builder.py`, `graph_updater.py` |
| 6 | `domains/core/embedding/` | `embeddings/` |
| 7 | `domains/upper/apidoc/` | `api_doc_generator.py` (from `mcp/`) |
| 8 | `domains/upper/rag/` | `rag/` |
| 9 | `entrypoints/` | `mcp/server.py`, `mcp/tools.py`, `commands_cli.py` |
| 10 | Cleanup | Reorganize `tests/`, delete all compatibility shims |

### Safety Measures

- **Git worktree isolation** — all work in a separate worktree, main branch untouched
- **Compatibility shims** — original paths forward imports to new locations; removed in batch 10 after grep confirms zero remaining references
- **Automated import scanning** — grep all old import paths before and after each batch
- **Entry point verification** — after batch 9, verify `pyproject.toml` scripts, `pip install -e .`, CLI and MCP server startup

---

## 5. CI — GitHub Actions

### File: `.github/workflows/ci.yml`

**Triggers:** push to `main`, all PRs

**Jobs:**

```yaml
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.10"
      - run: pip install -e ".[treesitter-full]"
      - run: python tools/dep_check.py
      - run: python -m pytest tests/ -v
```

**Failure policy:** Any step failure blocks merge.

### Local Commands

| Command | Purpose |
|---------|---------|
| `python tools/dep_check.py` | Check layer dependency violations |
| `python -m pytest tests/ -v` | Full test suite |
| `python -m pytest tests/foundation/ -v` | Test single layer |

---

## 6. Contributing Documentation

### File Structure

```
contributing/
├── architecture.md      # Layer model, dependency rules, directory map
├── testing.md           # Test strategy, naming, run commands
├── add-feature.md       # File checklists per scenario
└── add-language.md      # Steps to add a new language parser
```

### Style: Agent-Optimized

- Structured, rule-based, imperative
- Minimal prose, maximum precision
- Every scenario maps to exact file paths and layer rules
- Example from `add-feature.md`:

```markdown
## Adding a new parser (new language support)

Touch these files:
1. `foundation/types/constants.py` — add enum to SupportedLanguage
2. `foundation/parsers/` — add parser implementation
3. `foundation/parsers/factory.py` — register in factory
4. `tests/foundation/test_parsers.py` — add test cases

Layer rules: parser lives in L1, cannot import L2+.
```

### CLAUDE.md Update

Add to project root `CLAUDE.md`:

```markdown
## Architecture
This project uses a 5-layer harness architecture (L0-L4).
See contributing/architecture.md for layer rules.
Before modifying code, read contributing/add-feature.md.
Run `python tools/dep_check.py` before committing.
```

---

## 7. Final Verification Checklist

After all batches complete:

- [ ] `python -m pytest tests/ -v` — all pass, matches baseline
- [ ] `pip install -e .` — succeeds
- [ ] `code-graph-builder` CLI — starts correctly
- [ ] `cgb-mcp` MCP server — starts correctly
- [ ] `npm run server` — works
- [ ] `python tools/dep_check.py` — zero violations
- [ ] `grep` old import paths — zero remaining references
- [ ] All compatibility shims deleted
- [ ] `pyproject.toml` entry points updated
