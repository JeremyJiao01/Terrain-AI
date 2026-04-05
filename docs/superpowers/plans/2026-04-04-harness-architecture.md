# Harness Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure `code_graph_builder/` into a strict 5-layer architecture with dependency enforcement, test-driven migration, CI, and agent-oriented contributing docs.

**Architecture:** Bottom-up migration in 10 batches. Each batch: confirm test baseline → move files → fix imports → place shim → run tests → commit. All work in a git worktree. A `tools/dep_check.py` script enforces layer rules. GitHub Actions CI runs dep-check + pytest on every push/PR.

**Tech Stack:** Python 3.10+, pytest, GitHub Actions, Tree-sitter, ast module (for dep_check)

**Design Spec:** `docs/superpowers/specs/2026-04-04-harness-architecture-design.md`

---

## Revised Target Directory Structure

Based on full codebase analysis, the complete target structure is:

```
code_graph_builder/
├── foundation/                          # L0 + L1
│   ├── types/                           # L0: Pure data definitions
│   │   ├── __init__.py
│   │   ├── constants.py                 # ← root constants.py
│   │   ├── types.py                     # ← root types.py
│   │   ├── config.py                    # ← root config.py
│   │   └── models.py                    # ← root models.py (LanguageSpec)
│   ├── parsers/                         # L1: AST parsers
│   │   ├── __init__.py
│   │   ├── factory.py                   # ← parsers/factory.py
│   │   ├── call_processor.py            # ← parsers/call_processor.py
│   │   ├── call_resolver.py             # ← parsers/call_resolver.py
│   │   ├── definition_processor.py      # ← parsers/definition_processor.py
│   │   ├── import_processor.py          # ← parsers/import_processor.py
│   │   ├── structure_processor.py       # ← parsers/structure_processor.py
│   │   ├── type_inference.py            # ← parsers/type_inference.py
│   │   ├── utils.py                     # ← parsers/utils.py
│   │   ├── parser_loader.py             # ← root parser_loader.py
│   │   └── language_spec.py             # ← root language_spec.py
│   ├── services/                        # L1: Database drivers
│   │   ├── __init__.py                  # ← services/__init__.py
│   │   ├── graph_service.py             # ← services/graph_service.py
│   │   ├── kuzu_service.py              # ← services/kuzu_service.py
│   │   └── memory_service.py            # ← services/memory_service.py
│   └── utils/                           # L1: Utilities
│       ├── __init__.py                  # ← utils/__init__.py
│       ├── encoding.py                  # ← utils/encoding.py
│       ├── path_utils.py                # ← utils/path_utils.py
│       └── settings.py                  # ← root settings.py
│
├── domains/
│   ├── core/                            # L2: Core domains
│   │   ├── graph/                       # Graph building
│   │   │   ├── __init__.py
│   │   │   ├── builder.py               # ← root builder.py
│   │   │   └── graph_updater.py         # ← root graph_updater.py
│   │   ├── embedding/                   # Vector embeddings
│   │   │   ├── __init__.py              # ← embeddings/__init__.py
│   │   │   ├── qwen3_embedder.py        # ← embeddings/qwen3_embedder.py
│   │   │   └── vector_store.py          # ← embeddings/vector_store.py
│   │   └── search/                      # Search tools
│   │       ├── __init__.py
│   │       ├── graph_query.py           # ← tools/graph_query.py
│   │       └── semantic_search.py       # ← tools/semantic_search.py
│   └── upper/                           # L3: Upper domains
│       ├── apidoc/                      # API doc generation
│       │   ├── __init__.py
│       │   └── api_doc_generator.py     # ← mcp/api_doc_generator.py
│       ├── rag/                         # RAG pipeline
│       │   ├── __init__.py              # ← rag/__init__.py
│       │   ├── camel_agent.py           # ← rag/camel_agent.py
│       │   ├── client.py               # ← rag/client.py
│       │   ├── config.py               # ← rag/config.py
│       │   ├── cypher_generator.py      # ← rag/cypher_generator.py
│       │   ├── llm_backend.py           # ← rag/llm_backend.py
│       │   ├── markdown_generator.py    # ← rag/markdown_generator.py
│       │   ├── prompt_templates.py      # ← rag/prompt_templates.py
│       │   └── rag_engine.py            # ← rag/rag_engine.py
│       └── guidance/                    # AI guidance agents
│           ├── __init__.py              # ← guidance/__init__.py
│           ├── agent.py                 # ← guidance/agent.py
│           ├── prompts.py               # ← guidance/prompts.py
│           └── toolset.py               # ← guidance/toolset.py
│
├── entrypoints/                         # L4: Thin shells
│   ├── mcp/                             # MCP server
│   │   ├── __init__.py                  # ← mcp/__init__.py
│   │   ├── server.py                    # ← mcp/server.py
│   │   ├── tools.py                     # ← mcp/tools.py
│   │   ├── pipeline.py                  # ← mcp/pipeline.py
│   │   └── file_editor.py              # ← mcp/file_editor.py
│   └── cli/                             # CLI
│       ├── __init__.py
│       ├── cli.py                       # ← root cli.py
│       ├── cgb_cli.py                   # ← root cgb_cli.py
│       └── commands_cli.py              # ← root commands_cli.py
│
├── tests/                               # Mirrors source structure
│   ├── __init__.py
│   ├── foundation/
│   │   ├── __init__.py
│   │   ├── test_types.py               # constants, types, config, models
│   │   ├── test_parsers.py             # AST parsing + GBK/GB2312 encoding
│   │   ├── test_services.py            # DB backends
│   │   └── test_utils.py               # encoding, path_utils, settings
│   ├── domains/
│   │   ├── __init__.py
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   ├── test_graph_builder.py   # builder + graph_updater
│   │   │   ├── test_embedding.py       # qwen3_embedder + vector_store
│   │   │   └── test_search.py          # graph_query + semantic_search
│   │   └── upper/
│   │       ├── __init__.py
│   │       ├── test_apidoc.py          # api_doc_generator
│   │       ├── test_rag.py             # RAG engine + sub-modules
│   │       └── test_guidance.py        # guidance agents
│   └── entrypoints/
│       ├── __init__.py
│       ├── test_mcp.py                 # MCP protocol + user flow
│       └── test_cli.py                 # CLI commands
│
├── examples/                            # Outside layer model — unchanged
│
└── __init__.py                          # Updated package exports
```

**Excluded from layers:** `npm-package/`, `examples/`, `docs/`

---

## Task 0: Create Git Worktree

**Files:**
- No file changes

- [ ] **Step 1: Create worktree branch**

```bash
cd /Users/jiaojeremy/CodeFile/CodeGraphWiki
git worktree add ../CodeGraphWiki-harness -b harness/layer-restructure
cd ../CodeGraphWiki-harness
```

- [ ] **Step 2: Verify worktree is clean**

```bash
git status
python -m pytest code_graph_builder/tests/ -v --tb=short 2>&1 | tail -20
```

Record the test baseline (number of passed/failed/skipped). All subsequent batches must match this baseline.

- [ ] **Step 3: Commit**

No commit needed — worktree setup only.

---

## Task 1: Write dep_check.py

This tool is needed from the start to validate each batch as it lands.

**Files:**
- Create: `tools/dep_check.py`
- Create: `tests/test_dep_check.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_dep_check.py`:

```python
"""Tests for the layer dependency checker."""
import pytest
from pathlib import Path
import tempfile
import os
import sys

# Add project root to path so we can import the tool
sys.path.insert(0, str(Path(__file__).parent.parent))
from tools.dep_check import classify_layer, check_import, scan_file, RULES


class TestClassifyLayer:
    def test_foundation_types(self):
        assert classify_layer("code_graph_builder/foundation/types/constants.py") == "L0"

    def test_foundation_parsers(self):
        assert classify_layer("code_graph_builder/foundation/parsers/factory.py") == "L1"

    def test_foundation_services(self):
        assert classify_layer("code_graph_builder/foundation/services/kuzu_service.py") == "L1"

    def test_foundation_utils(self):
        assert classify_layer("code_graph_builder/foundation/utils/encoding.py") == "L1"

    def test_domains_core(self):
        assert classify_layer("code_graph_builder/domains/core/graph/builder.py") == "L2"

    def test_domains_upper(self):
        assert classify_layer("code_graph_builder/domains/upper/rag/rag_engine.py") == "L3"

    def test_entrypoints(self):
        assert classify_layer("code_graph_builder/entrypoints/mcp/server.py") == "L4"

    def test_unknown(self):
        assert classify_layer("code_graph_builder/something_else.py") is None


class TestCheckImport:
    def test_l0_cannot_import_l1(self):
        result = check_import(
            file_path="code_graph_builder/foundation/types/constants.py",
            imported_module="code_graph_builder.foundation.parsers.factory",
        )
        assert result is not None  # violation
        assert "L0 cannot import L1" in result

    def test_l1_can_import_l0(self):
        result = check_import(
            file_path="code_graph_builder/foundation/parsers/factory.py",
            imported_module="code_graph_builder.foundation.types.constants",
        )
        assert result is None  # no violation

    def test_l2_cannot_import_l3(self):
        result = check_import(
            file_path="code_graph_builder/domains/core/graph/builder.py",
            imported_module="code_graph_builder.domains.upper.rag.rag_engine",
        )
        assert result is not None
        assert "L2 cannot import L3" in result

    def test_l2_cross_domain_forbidden(self):
        result = check_import(
            file_path="code_graph_builder/domains/core/graph/builder.py",
            imported_module="code_graph_builder.domains.core.embedding.vector_store",
        )
        assert result is not None
        assert "cross-domain" in result.lower()

    def test_l3_cross_domain_forbidden(self):
        result = check_import(
            file_path="code_graph_builder/domains/upper/apidoc/api_doc_generator.py",
            imported_module="code_graph_builder.domains.upper.rag.rag_engine",
        )
        assert result is not None
        assert "cross-domain" in result.lower()

    def test_l4_can_import_all_lower(self):
        for module in [
            "code_graph_builder.foundation.types.constants",
            "code_graph_builder.foundation.parsers.factory",
            "code_graph_builder.domains.core.graph.builder",
            "code_graph_builder.domains.upper.rag.rag_engine",
        ]:
            result = check_import(
                file_path="code_graph_builder/entrypoints/mcp/tools.py",
                imported_module=module,
            )
            assert result is None, f"L4 should be able to import {module}"

    def test_l4_cross_entrypoint_forbidden(self):
        result = check_import(
            file_path="code_graph_builder/entrypoints/mcp/server.py",
            imported_module="code_graph_builder.entrypoints.cli.commands_cli",
        )
        assert result is not None
        assert "cross-" in result.lower()

    def test_stdlib_always_allowed(self):
        result = check_import(
            file_path="code_graph_builder/foundation/types/constants.py",
            imported_module="os",
        )
        assert result is None

    def test_third_party_always_allowed(self):
        result = check_import(
            file_path="code_graph_builder/foundation/types/constants.py",
            imported_module="loguru",
        )
        assert result is None


class TestScanFile:
    def test_detects_violation(self, tmp_path):
        # Create a file that violates L0 → L1 rule
        f = tmp_path / "violation.py"
        f.write_text("from code_graph_builder.foundation.parsers.factory import ProcessorFactory\n")
        violations = scan_file(
            str(f),
            file_layer_path="code_graph_builder/foundation/types/violation.py",
        )
        assert len(violations) == 1

    def test_clean_file(self, tmp_path):
        f = tmp_path / "clean.py"
        f.write_text("from code_graph_builder.foundation.types.constants import NodeLabel\nimport os\n")
        violations = scan_file(
            str(f),
            file_layer_path="code_graph_builder/foundation/parsers/clean.py",
        )
        assert len(violations) == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_dep_check.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'tools.dep_check'`

- [ ] **Step 3: Write dep_check.py**

Create `tools/__init__.py` (empty) and `tools/dep_check.py`:

```python
#!/usr/bin/env python3
"""Layer dependency checker for code_graph_builder.

Enforces the 5-layer architecture:
  L0  foundation/types/        → stdlib + third-party only
  L1  foundation/{parsers,services,utils}/  → L0
  L2  domains/core/            → L0, L1 (no cross-domain)
  L3  domains/upper/           → L0, L1, L2 (no cross-domain)
  L4  entrypoints/             → L0, L1, L2, L3 (no cross-entrypoint)

Usage: python tools/dep_check.py [path]
  Defaults to scanning code_graph_builder/ from the repo root.
  Exits 0 if clean, 1 if violations found.
"""

import ast
import sys
from pathlib import Path

PKG = "code_graph_builder"

# Layer classification based on path segments after PKG
LAYER_MAP = [
    ("foundation/types/", "L0"),
    ("foundation/parsers/", "L1"),
    ("foundation/services/", "L1"),
    ("foundation/utils/", "L1"),
    ("domains/core/", "L2"),
    ("domains/upper/", "L3"),
    ("entrypoints/", "L4"),
]

# Allowed import directions: layer → set of layers it may import
RULES = {
    "L0": set(),          # L0 cannot import any project module
    "L1": {"L0"},
    "L2": {"L0", "L1"},
    "L3": {"L0", "L1", "L2"},
    "L4": {"L0", "L1", "L2", "L3"},
}

LAYER_ORDER = {"L0": 0, "L1": 1, "L2": 2, "L3": 3, "L4": 4}


def classify_layer(file_path: str) -> str | None:
    """Determine which layer a file belongs to based on its path."""
    # Normalize to use forward slashes and find the package-relative path
    normalized = file_path.replace("\\", "/")
    idx = normalized.find(f"{PKG}/")
    if idx == -1:
        return None
    relative = normalized[idx + len(PKG) + 1:]

    for prefix, layer in LAYER_MAP:
        if relative.startswith(prefix):
            return layer
    return None


def _get_domain(file_path: str) -> str | None:
    """Extract the domain name for cross-domain checks.

    For L2: domains/core/<domain>/...  → returns <domain>
    For L3: domains/upper/<domain>/... → returns <domain>
    For L4: entrypoints/<domain>/...   → returns <domain>
    """
    normalized = file_path.replace("\\", "/")
    idx = normalized.find(f"{PKG}/")
    if idx == -1:
        return None
    relative = normalized[idx + len(PKG) + 1:]

    for prefix in ("domains/core/", "domains/upper/", "entrypoints/"):
        if relative.startswith(prefix):
            rest = relative[len(prefix):]
            parts = rest.split("/")
            if parts:
                return parts[0]
    return None


def _module_to_path_prefix(module: str) -> str | None:
    """Convert a dotted module path to a slash path prefix for layer lookup."""
    if not module.startswith(PKG):
        return None
    rest = module[len(PKG) + 1:]  # strip 'code_graph_builder.'
    return rest.replace(".", "/")


def check_import(file_path: str, imported_module: str) -> str | None:
    """Check if a single import violates layer rules.

    Returns a violation message string, or None if the import is allowed.
    """
    # Skip non-project imports (stdlib, third-party)
    if not imported_module.startswith(PKG + "."):
        return None

    source_layer = classify_layer(file_path)
    if source_layer is None:
        return None  # file outside layer structure, skip

    # Determine target layer from the imported module
    import_path_prefix = _module_to_path_prefix(imported_module)
    if import_path_prefix is None:
        return None

    # Try to classify the imported module by building a fake path
    target_fake_path = f"{PKG}/{import_path_prefix}.py"
    target_layer = classify_layer(target_fake_path)
    if target_layer is None:
        # Try as a package (directory)
        target_fake_path = f"{PKG}/{import_path_prefix}/__init__.py"
        target_layer = classify_layer(target_fake_path)
    if target_layer is None:
        return None  # can't determine target layer, skip

    # Rule 1: Check layer direction
    if target_layer not in RULES[source_layer] and target_layer != source_layer:
        return (
            f"VIOLATION: {file_path} imports {imported_module}\n"
            f"  Rule: {source_layer} cannot import {target_layer}"
        )

    # Rule 2: Same-layer cross-domain check (L2, L3, L4)
    if source_layer == target_layer and source_layer in ("L2", "L3", "L4"):
        source_domain = _get_domain(file_path)
        target_domain = _get_domain(target_fake_path)
        if source_domain and target_domain and source_domain != target_domain:
            label = {
                "L2": "cross-domain within L2",
                "L3": "cross-domain within L3",
                "L4": "cross-entrypoint within L4",
            }[source_layer]
            return (
                f"VIOLATION: {file_path} imports {imported_module}\n"
                f"  Rule: {label} ({source_domain} -> {target_domain})"
            )

    return None


def scan_file(file_path: str, file_layer_path: str | None = None) -> list[str]:
    """Scan a Python file for import violations.

    Args:
        file_path: Actual path on disk to read.
        file_layer_path: Path used for layer classification (defaults to file_path).

    Returns:
        List of violation message strings.
    """
    layer_path = file_layer_path or file_path
    try:
        source = Path(file_path).read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=file_path)
    except (SyntaxError, UnicodeDecodeError):
        return []

    violations = []
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]

        for mod in modules:
            result = check_import(layer_path, mod)
            if result:
                violations.append(result)

    return violations


def main(root: str | None = None) -> int:
    """Scan the entire package and report violations."""
    if root is None:
        # Default: find repo root (directory containing code_graph_builder/)
        script_dir = Path(__file__).resolve().parent
        root = str(script_dir.parent)

    pkg_dir = Path(root) / PKG
    if not pkg_dir.is_dir():
        print(f"ERROR: {pkg_dir} not found", file=sys.stderr)
        return 2

    all_violations = []
    for py_file in sorted(pkg_dir.rglob("*.py")):
        # Skip tests, examples
        rel = str(py_file.relative_to(Path(root)))
        if "/tests/" in rel or "/examples/" in rel:
            continue
        violations = scan_file(str(py_file), file_layer_path=rel)
        all_violations.extend(violations)

    if all_violations:
        for v in all_violations:
            print(v)
            print()
        print(f"Found {len(all_violations)} violation(s). FAILED.")
        return 1
    else:
        print("All imports conform to layer rules. PASSED.")
        return 0


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(main(path))
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/test_dep_check.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tools/__init__.py tools/dep_check.py tests/test_dep_check.py
git commit -m "feat: add layer dependency checker (tools/dep_check.py)"
```

---

## Task 2: Establish Test Baseline (Step 0)

Audit existing tests and add missing coverage before any file moves.

**Files:**
- Modify: `code_graph_builder/tests/` (audit existing)
- Create: `code_graph_builder/tests/test_encoding_parsing.py` (GBK/GB2312)
- Create: `code_graph_builder/tests/fixtures/` (test fixtures)

- [ ] **Step 1: Run existing tests, record baseline**

```bash
python -m pytest code_graph_builder/tests/ -v --tb=short 2>&1 | tee /tmp/test_baseline.txt
echo "---"
python -m pytest code_graph_builder/tests/ -v --tb=short 2>&1 | grep -E "passed|failed|error|skipped" | tail -5
```

Save this output — it is the baseline that every batch must match.

- [ ] **Step 2: Create GBK/GB2312 test fixtures**

Create `code_graph_builder/tests/fixtures/` directory and add GBK-encoded C test files:

```bash
mkdir -p code_graph_builder/tests/fixtures
```

Create `code_graph_builder/tests/fixtures/create_gbk_fixtures.py` (a helper script to generate properly encoded test files):

```python
#!/usr/bin/env python3
"""Generate GBK and GB2312 encoded C test fixture files."""
from pathlib import Path

FIXTURES_DIR = Path(__file__).parent

# C source with Chinese comments and string literals
C_SOURCE_GBK = """\
#include <stdio.h>

/* 这是一个GBK编码的C文件 */

// 计算两个数的和
int add(int a, int b) {
    return a + b;
}

/* 打印欢迎消息 */
void print_welcome(const char* name) {
    printf("欢迎 %s\\n", name);
}

// 结构体：用户信息
struct UserInfo {
    char name[64];
    int age;
};

// 获取用户年龄
int get_age(struct UserInfo* user) {
    return user->age;
}
"""

C_SOURCE_GB2312 = """\
#include <stdlib.h>

/* GB2312编码测试文件 */

// 分配内存缓冲区
void* alloc_buffer(int size) {
    return malloc(size);
}

// 释放内存缓冲区
void free_buffer(void* ptr) {
    free(ptr);
}
"""

def main():
    # Write GBK encoded file
    gbk_path = FIXTURES_DIR / "test_gbk.c"
    gbk_path.write_bytes(C_SOURCE_GBK.encode("gbk"))
    print(f"Created {gbk_path}")

    # Write GB2312 encoded file
    gb2312_path = FIXTURES_DIR / "test_gb2312.c"
    gb2312_path.write_bytes(C_SOURCE_GB2312.encode("gb2312"))
    print(f"Created {gb2312_path}")

    # Write UTF-8 reference (same content for comparison)
    utf8_path = FIXTURES_DIR / "test_utf8.c"
    utf8_path.write_text(C_SOURCE_GBK, encoding="utf-8")
    print(f"Created {utf8_path}")


if __name__ == "__main__":
    main()
```

Run it to generate fixtures:

```bash
python code_graph_builder/tests/fixtures/create_gbk_fixtures.py
```

- [ ] **Step 3: Write GBK/GB2312 parsing test**

Create `code_graph_builder/tests/test_encoding_parsing.py`:

```python
"""Tests for parsing GBK/GB2312 encoded C files.

Ensures the parser correctly extracts functions, structs, and
relationships from C source files that use non-UTF-8 encodings
with Chinese comments and string literals.
"""
import pytest
from pathlib import Path

from code_graph_builder.builder import CodeGraphBuilder
from code_graph_builder.config import MemoryConfig, ScanConfig


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def memory_builder():
    """Create a CodeGraphBuilder with in-memory backend."""
    config = MemoryConfig()
    return CodeGraphBuilder(config)


class TestGBKParsing:
    """Test parsing of GBK-encoded C files."""

    def test_gbk_file_functions_detected(self, memory_builder):
        """GBK-encoded C file: all functions must be detected."""
        gbk_file = FIXTURES_DIR / "test_gbk.c"
        assert gbk_file.exists(), f"Fixture not found: {gbk_file}"

        result = memory_builder.build(
            str(FIXTURES_DIR),
            scan_config=ScanConfig(
                include_patterns=["test_gbk.c"],
            ),
        )

        # Extract function names from the graph
        functions = set()
        for node in result.graph_data.nodes:
            if hasattr(node, 'name') and hasattr(node, 'label'):
                if 'FUNCTION' in str(node.label).upper():
                    functions.add(node.name)

        expected_functions = {"add", "print_welcome", "get_age"}
        assert expected_functions.issubset(functions), (
            f"Missing functions: {expected_functions - functions}. "
            f"Found: {functions}"
        )

    def test_gbk_file_struct_detected(self, memory_builder):
        """GBK-encoded C file: struct definitions must be detected."""
        result = memory_builder.build(
            str(FIXTURES_DIR),
            scan_config=ScanConfig(
                include_patterns=["test_gbk.c"],
            ),
        )

        structs = set()
        for node in result.graph_data.nodes:
            if hasattr(node, 'name') and hasattr(node, 'label'):
                if 'CLASS' in str(node.label).upper() or 'STRUCT' in str(node.label).upper():
                    structs.add(node.name)

        assert "UserInfo" in structs, f"UserInfo struct not found. Found: {structs}"

    def test_gbk_file_no_garbled_names(self, memory_builder):
        """GBK-encoded C file: function names must not contain garbled characters."""
        result = memory_builder.build(
            str(FIXTURES_DIR),
            scan_config=ScanConfig(
                include_patterns=["test_gbk.c"],
            ),
        )

        for node in result.graph_data.nodes:
            if hasattr(node, 'name'):
                # Function/struct names should be pure ASCII identifiers
                if hasattr(node, 'label') and any(
                    kw in str(node.label).upper()
                    for kw in ('FUNCTION', 'CLASS', 'STRUCT')
                ):
                    assert node.name.isascii(), (
                        f"Garbled name detected: {node.name!r}"
                    )


class TestGB2312Parsing:
    """Test parsing of GB2312-encoded C files."""

    def test_gb2312_file_functions_detected(self, memory_builder):
        """GB2312-encoded C file: all functions must be detected."""
        gb2312_file = FIXTURES_DIR / "test_gb2312.c"
        assert gb2312_file.exists(), f"Fixture not found: {gb2312_file}"

        result = memory_builder.build(
            str(FIXTURES_DIR),
            scan_config=ScanConfig(
                include_patterns=["test_gb2312.c"],
            ),
        )

        functions = set()
        for node in result.graph_data.nodes:
            if hasattr(node, 'name') and hasattr(node, 'label'):
                if 'FUNCTION' in str(node.label).upper():
                    functions.add(node.name)

        expected_functions = {"alloc_buffer", "free_buffer"}
        assert expected_functions.issubset(functions), (
            f"Missing functions: {expected_functions - functions}. "
            f"Found: {functions}"
        )


class TestUTF8Comparison:
    """Ensure UTF-8 version produces identical parse results."""

    def test_utf8_same_functions_as_gbk(self, memory_builder):
        """UTF-8 and GBK versions of the same C file must yield identical function sets."""
        # Parse GBK version
        result_gbk = memory_builder.build(
            str(FIXTURES_DIR),
            scan_config=ScanConfig(include_patterns=["test_gbk.c"]),
        )
        funcs_gbk = {
            n.name for n in result_gbk.graph_data.nodes
            if hasattr(n, 'name') and hasattr(n, 'label')
            and 'FUNCTION' in str(n.label).upper()
        }

        # Parse UTF-8 version (need a fresh builder since Memory backend accumulates)
        builder2 = CodeGraphBuilder(MemoryConfig())
        result_utf8 = builder2.build(
            str(FIXTURES_DIR),
            scan_config=ScanConfig(include_patterns=["test_utf8.c"]),
        )
        funcs_utf8 = {
            n.name for n in result_utf8.graph_data.nodes
            if hasattr(n, 'name') and hasattr(n, 'label')
            and 'FUNCTION' in str(n.label).upper()
        }

        assert funcs_gbk == funcs_utf8, (
            f"GBK functions {funcs_gbk} != UTF-8 functions {funcs_utf8}"
        )
```

- [ ] **Step 4: Run the encoding test**

```bash
python -m pytest code_graph_builder/tests/test_encoding_parsing.py -v
```

Expected: All tests PASS. If any fail, debug and fix the parser/encoding layer before proceeding.

- [ ] **Step 5: Run full test suite to confirm baseline unchanged**

```bash
python -m pytest code_graph_builder/tests/ -v --tb=short
```

Expected: Same results as baseline from Step 1, plus the new encoding tests.

- [ ] **Step 6: Commit**

```bash
git add code_graph_builder/tests/fixtures/ code_graph_builder/tests/test_encoding_parsing.py
git commit -m "test: add GBK/GB2312 encoding test fixtures and parser tests"
```

---

## Task 3: Batch 1 — Migrate foundation/types/ (L0)

Move `constants.py`, `types.py`, `config.py`, `models.py` into `foundation/types/`.

**Files:**
- Move: `code_graph_builder/constants.py` → `code_graph_builder/foundation/types/constants.py`
- Move: `code_graph_builder/types.py` → `code_graph_builder/foundation/types/types.py`
- Move: `code_graph_builder/config.py` → `code_graph_builder/foundation/types/config.py`
- Move: `code_graph_builder/models.py` → `code_graph_builder/foundation/types/models.py`
- Create: `code_graph_builder/foundation/__init__.py`
- Create: `code_graph_builder/foundation/types/__init__.py`
- Create: compatibility shims at original locations

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p code_graph_builder/foundation/types
touch code_graph_builder/foundation/__init__.py
touch code_graph_builder/foundation/types/__init__.py
```

- [ ] **Step 2: Move files**

```bash
git mv code_graph_builder/constants.py code_graph_builder/foundation/types/constants.py
git mv code_graph_builder/types.py code_graph_builder/foundation/types/types.py
git mv code_graph_builder/config.py code_graph_builder/foundation/types/config.py
git mv code_graph_builder/models.py code_graph_builder/foundation/types/models.py
```

- [ ] **Step 3: Create compatibility shims**

Create `code_graph_builder/constants.py`:
```python
# Compatibility shim — will be removed after full migration
from code_graph_builder.foundation.types.constants import *  # noqa: F401,F403
```

Create `code_graph_builder/types.py`:
```python
# Compatibility shim — will be removed after full migration
from code_graph_builder.foundation.types.types import *  # noqa: F401,F403
```

Create `code_graph_builder/config.py`:
```python
# Compatibility shim — will be removed after full migration
from code_graph_builder.foundation.types.config import *  # noqa: F401,F403
```

Create `code_graph_builder/models.py`:
```python
# Compatibility shim — will be removed after full migration
from code_graph_builder.foundation.types.models import *  # noqa: F401,F403
```

- [ ] **Step 4: Update internal imports within the moved files**

Scan the four moved files for any `from code_graph_builder.constants import` or similar cross-references, and update them to use the new `foundation.types` paths. The shims ensure external consumers still work.

```bash
# Find internal cross-references in the moved files
grep -n "from code_graph_builder\.\(constants\|types\|config\|models\)" \
  code_graph_builder/foundation/types/*.py
```

Update any matches to use `code_graph_builder.foundation.types.*` paths.

- [ ] **Step 5: Run full test suite**

```bash
python -m pytest code_graph_builder/tests/ -v --tb=short
```

Expected: All tests PASS, identical to baseline. The shims ensure nothing breaks.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: migrate constants/types/config/models to foundation/types/ (L0)"
```

---

## Task 4: Batch 2 — Migrate foundation/utils/ (L1)

Move `utils/` contents and `settings.py` into `foundation/utils/`.

**Files:**
- Move: `code_graph_builder/utils/encoding.py` → `code_graph_builder/foundation/utils/encoding.py`
- Move: `code_graph_builder/utils/path_utils.py` → `code_graph_builder/foundation/utils/path_utils.py`
- Move: `code_graph_builder/utils/__init__.py` → `code_graph_builder/foundation/utils/__init__.py`
- Move: `code_graph_builder/settings.py` → `code_graph_builder/foundation/utils/settings.py`
- Create: compatibility shims at `code_graph_builder/utils/` and `code_graph_builder/settings.py`

- [ ] **Step 1: Create directory and move files**

```bash
mkdir -p code_graph_builder/foundation/utils

# Move utils contents
git mv code_graph_builder/utils/encoding.py code_graph_builder/foundation/utils/encoding.py
git mv code_graph_builder/utils/path_utils.py code_graph_builder/foundation/utils/path_utils.py
git mv code_graph_builder/utils/__init__.py code_graph_builder/foundation/utils/__init__.py

# Move settings.py
git mv code_graph_builder/settings.py code_graph_builder/foundation/utils/settings.py
```

- [ ] **Step 2: Create compatibility shims**

Recreate `code_graph_builder/utils/__init__.py`:
```python
# Compatibility shim — will be removed after full migration
from code_graph_builder.foundation.utils import *  # noqa: F401,F403
```

Create `code_graph_builder/utils/encoding.py`:
```python
# Compatibility shim — will be removed after full migration
from code_graph_builder.foundation.utils.encoding import *  # noqa: F401,F403
```

Create `code_graph_builder/utils/path_utils.py`:
```python
# Compatibility shim — will be removed after full migration
from code_graph_builder.foundation.utils.path_utils import *  # noqa: F401,F403
```

Create `code_graph_builder/settings.py`:
```python
# Compatibility shim — will be removed after full migration
from code_graph_builder.foundation.utils.settings import *  # noqa: F401,F403
```

- [ ] **Step 3: Update internal imports in moved files**

```bash
grep -rn "from code_graph_builder\.\(utils\|settings\)" \
  code_graph_builder/foundation/utils/*.py
```

Update any internal cross-references to use new paths.

- [ ] **Step 4: Run full test suite**

```bash
python -m pytest code_graph_builder/tests/ -v --tb=short
```

Expected: All tests PASS, identical to baseline.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: migrate utils/ and settings.py to foundation/utils/ (L1)"
```

---

## Task 5: Batch 3 — Migrate foundation/parsers/ (L1)

Move `parsers/`, `parser_loader.py`, and `language_spec.py` into `foundation/parsers/`.

**Files:**
- Move: all files from `code_graph_builder/parsers/` → `code_graph_builder/foundation/parsers/`
- Move: `code_graph_builder/parser_loader.py` → `code_graph_builder/foundation/parsers/parser_loader.py`
- Move: `code_graph_builder/language_spec.py` → `code_graph_builder/foundation/parsers/language_spec.py`
- Create: compatibility shims

- [ ] **Step 1: Create directory and move files**

```bash
mkdir -p code_graph_builder/foundation/parsers

# Move all parser files
for f in code_graph_builder/parsers/*.py; do
  fname=$(basename "$f")
  git mv "$f" "code_graph_builder/foundation/parsers/$fname"
done

# Move root-level parser files
git mv code_graph_builder/parser_loader.py code_graph_builder/foundation/parsers/parser_loader.py
git mv code_graph_builder/language_spec.py code_graph_builder/foundation/parsers/language_spec.py

# Remove empty old parsers directory if git leaves it
rmdir code_graph_builder/parsers 2>/dev/null || true
```

- [ ] **Step 2: Create compatibility shims**

Recreate `code_graph_builder/parsers/__init__.py`:
```python
# Compatibility shim — will be removed after full migration
from code_graph_builder.foundation.parsers import *  # noqa: F401,F403
```

Create shims for each file in `code_graph_builder/parsers/`:
```python
# code_graph_builder/parsers/factory.py
from code_graph_builder.foundation.parsers.factory import *  # noqa: F401,F403
```

(Repeat pattern for: `call_processor.py`, `call_resolver.py`, `definition_processor.py`, `import_processor.py`, `structure_processor.py`, `type_inference.py`, `utils.py`)

Create root-level shims:
```python
# code_graph_builder/parser_loader.py
from code_graph_builder.foundation.parsers.parser_loader import *  # noqa: F401,F403
```

```python
# code_graph_builder/language_spec.py
from code_graph_builder.foundation.parsers.language_spec import *  # noqa: F401,F403
```

- [ ] **Step 3: Update internal imports in moved files**

The parser files import from each other and from L0 types. Update:

```bash
grep -rn "from code_graph_builder\.\(parsers\|parser_loader\|language_spec\|constants\|types\|models\)" \
  code_graph_builder/foundation/parsers/*.py
```

Update imports:
- `from code_graph_builder.parsers.X` → `from code_graph_builder.foundation.parsers.X`
- `from code_graph_builder.constants` → `from code_graph_builder.foundation.types.constants`
- `from code_graph_builder.types` → `from code_graph_builder.foundation.types.types`
- `from code_graph_builder.models` → `from code_graph_builder.foundation.types.models`

- [ ] **Step 4: Run GBK/GB2312 encoding tests specifically**

```bash
python -m pytest code_graph_builder/tests/test_encoding_parsing.py -v
```

Expected: All PASS — parser migration did not break encoding handling.

- [ ] **Step 5: Run full test suite**

```bash
python -m pytest code_graph_builder/tests/ -v --tb=short
```

Expected: All tests PASS, identical to baseline.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "refactor: migrate parsers/ to foundation/parsers/ (L1)"
```

---

## Task 6: Batch 4 — Migrate foundation/services/ (L1)

**Files:**
- Move: all files from `code_graph_builder/services/` → `code_graph_builder/foundation/services/`
- Create: compatibility shims

- [ ] **Step 1: Move files**

```bash
mkdir -p code_graph_builder/foundation/services
for f in code_graph_builder/services/*.py; do
  fname=$(basename "$f")
  git mv "$f" "code_graph_builder/foundation/services/$fname"
done
rmdir code_graph_builder/services 2>/dev/null || true
```

- [ ] **Step 2: Create compatibility shims**

Recreate `code_graph_builder/services/__init__.py` and per-file shims following the same pattern as Task 5 Step 2.

- [ ] **Step 3: Update internal imports in moved files**

```bash
grep -rn "from code_graph_builder\.\(services\|types\|constants\)" \
  code_graph_builder/foundation/services/*.py
```

Update to use `foundation.types.*` and `foundation.services.*` paths.

- [ ] **Step 4: Run full test suite**

```bash
python -m pytest code_graph_builder/tests/ -v --tb=short
```

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: migrate services/ to foundation/services/ (L1)"
```

---

## Task 7: Batch 5 — Migrate domains/core/graph/ (L2)

**Files:**
- Move: `code_graph_builder/builder.py` → `code_graph_builder/domains/core/graph/builder.py`
- Move: `code_graph_builder/graph_updater.py` → `code_graph_builder/domains/core/graph/graph_updater.py`
- Create: compatibility shims

- [ ] **Step 1: Create directory and move files**

```bash
mkdir -p code_graph_builder/domains/core/graph
touch code_graph_builder/domains/__init__.py
touch code_graph_builder/domains/core/__init__.py
touch code_graph_builder/domains/core/graph/__init__.py

git mv code_graph_builder/builder.py code_graph_builder/domains/core/graph/builder.py
git mv code_graph_builder/graph_updater.py code_graph_builder/domains/core/graph/graph_updater.py
```

- [ ] **Step 2: Create compatibility shims**

```python
# code_graph_builder/builder.py
from code_graph_builder.domains.core.graph.builder import *  # noqa: F401,F403
```

```python
# code_graph_builder/graph_updater.py
from code_graph_builder.domains.core.graph.graph_updater import *  # noqa: F401,F403
```

- [ ] **Step 3: Update internal imports in moved files**

`builder.py` and `graph_updater.py` import heavily from L0 and L1. Update all to use new paths:

```bash
grep -rn "from code_graph_builder\." \
  code_graph_builder/domains/core/graph/*.py
```

Update all matches to new `foundation.*` paths.

- [ ] **Step 4: Run full test suite**

```bash
python -m pytest code_graph_builder/tests/ -v --tb=short
```

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: migrate builder/graph_updater to domains/core/graph/ (L2)"
```

---

## Task 8: Batch 6 — Migrate domains/core/embedding/ and domains/core/search/ (L2)

**Files:**
- Move: `code_graph_builder/embeddings/*` → `code_graph_builder/domains/core/embedding/`
- Move: `code_graph_builder/tools/graph_query.py` → `code_graph_builder/domains/core/search/graph_query.py`
- Move: `code_graph_builder/tools/semantic_search.py` → `code_graph_builder/domains/core/search/semantic_search.py`
- Create: compatibility shims

- [ ] **Step 1: Create directories and move files**

```bash
mkdir -p code_graph_builder/domains/core/embedding
mkdir -p code_graph_builder/domains/core/search
touch code_graph_builder/domains/core/embedding/__init__.py
touch code_graph_builder/domains/core/search/__init__.py

# Move embeddings
for f in code_graph_builder/embeddings/*.py; do
  fname=$(basename "$f")
  git mv "$f" "code_graph_builder/domains/core/embedding/$fname"
done
rmdir code_graph_builder/embeddings 2>/dev/null || true

# Move tools
git mv code_graph_builder/tools/graph_query.py code_graph_builder/domains/core/search/graph_query.py
git mv code_graph_builder/tools/semantic_search.py code_graph_builder/domains/core/search/semantic_search.py
```

- [ ] **Step 2: Create compatibility shims**

Create shims at original `code_graph_builder/embeddings/` and `code_graph_builder/tools/` locations following the established pattern.

- [ ] **Step 3: Update internal imports in moved files**

```bash
grep -rn "from code_graph_builder\." \
  code_graph_builder/domains/core/embedding/*.py \
  code_graph_builder/domains/core/search/*.py
```

Update to new paths.

- [ ] **Step 4: Run full test suite**

```bash
python -m pytest code_graph_builder/tests/ -v --tb=short
```

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: migrate embeddings/ and tools/ to domains/core/ (L2)"
```

---

## Task 9: Batch 7 — Migrate domains/upper/apidoc/ (L3)

**Files:**
- Move: `code_graph_builder/mcp/api_doc_generator.py` → `code_graph_builder/domains/upper/apidoc/api_doc_generator.py`
- Create: compatibility shim

- [ ] **Step 1: Create directory and move file**

```bash
mkdir -p code_graph_builder/domains/upper/apidoc
touch code_graph_builder/domains/upper/__init__.py
touch code_graph_builder/domains/upper/apidoc/__init__.py

git mv code_graph_builder/mcp/api_doc_generator.py code_graph_builder/domains/upper/apidoc/api_doc_generator.py
```

- [ ] **Step 2: Create compatibility shim**

```python
# code_graph_builder/mcp/api_doc_generator.py
from code_graph_builder.domains.upper.apidoc.api_doc_generator import *  # noqa: F401,F403
```

- [ ] **Step 3: Update internal imports**

```bash
grep -rn "from code_graph_builder\." \
  code_graph_builder/domains/upper/apidoc/*.py
```

- [ ] **Step 4: Run full test suite**

```bash
python -m pytest code_graph_builder/tests/ -v --tb=short
```

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: migrate api_doc_generator to domains/upper/apidoc/ (L3)"
```

---

## Task 10: Batch 8 — Migrate domains/upper/rag/ and domains/upper/guidance/ (L3)

**Files:**
- Move: all `code_graph_builder/rag/*.py` (not `rag/tests/`) → `code_graph_builder/domains/upper/rag/`
- Move: all `code_graph_builder/guidance/*.py` → `code_graph_builder/domains/upper/guidance/`
- Create: compatibility shims

- [ ] **Step 1: Create directories and move files**

```bash
mkdir -p code_graph_builder/domains/upper/rag
mkdir -p code_graph_builder/domains/upper/guidance
touch code_graph_builder/domains/upper/rag/__init__.py
touch code_graph_builder/domains/upper/guidance/__init__.py

# Move RAG files (exclude tests/ subdirectory)
for f in code_graph_builder/rag/*.py; do
  fname=$(basename "$f")
  git mv "$f" "code_graph_builder/domains/upper/rag/$fname"
done

# Move guidance files
for f in code_graph_builder/guidance/*.py; do
  fname=$(basename "$f")
  git mv "$f" "code_graph_builder/domains/upper/guidance/$fname"
done

# Clean up empty directories
rmdir code_graph_builder/guidance 2>/dev/null || true
```

Note: `rag/tests/` stays in place for now — it will be reorganized in Task 12.

- [ ] **Step 2: Create compatibility shims**

Create shims at original `code_graph_builder/rag/` and `code_graph_builder/guidance/` locations.

- [ ] **Step 3: Update internal imports in moved files**

```bash
grep -rn "from code_graph_builder\." \
  code_graph_builder/domains/upper/rag/*.py \
  code_graph_builder/domains/upper/guidance/*.py
```

Key updates:
- `from code_graph_builder.rag.X` → `from code_graph_builder.domains.upper.rag.X`
- `from code_graph_builder.embeddings.X` → `from code_graph_builder.domains.core.embedding.X`
- `from code_graph_builder.tools.X` → `from code_graph_builder.domains.core.search.X`

- [ ] **Step 4: Run full test suite**

```bash
python -m pytest code_graph_builder/tests/ -v --tb=short
```

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: migrate rag/ and guidance/ to domains/upper/ (L3)"
```

---

## Task 11: Batch 9 — Migrate entrypoints/ (L4)

**Files:**
- Move: `code_graph_builder/mcp/server.py` → `code_graph_builder/entrypoints/mcp/server.py`
- Move: `code_graph_builder/mcp/tools.py` → `code_graph_builder/entrypoints/mcp/tools.py`
- Move: `code_graph_builder/mcp/pipeline.py` → `code_graph_builder/entrypoints/mcp/pipeline.py`
- Move: `code_graph_builder/mcp/file_editor.py` → `code_graph_builder/entrypoints/mcp/file_editor.py`
- Move: `code_graph_builder/mcp/__init__.py` → `code_graph_builder/entrypoints/mcp/__init__.py`
- Move: `code_graph_builder/cli.py` → `code_graph_builder/entrypoints/cli/cli.py`
- Move: `code_graph_builder/cgb_cli.py` → `code_graph_builder/entrypoints/cli/cgb_cli.py`
- Move: `code_graph_builder/commands_cli.py` → `code_graph_builder/entrypoints/cli/commands_cli.py`
- Update: `pyproject.toml` entry points

- [ ] **Step 1: Create directories and move files**

```bash
mkdir -p code_graph_builder/entrypoints/mcp
mkdir -p code_graph_builder/entrypoints/cli
touch code_graph_builder/entrypoints/__init__.py
touch code_graph_builder/entrypoints/cli/__init__.py

# Move MCP files
for f in code_graph_builder/mcp/__init__.py code_graph_builder/mcp/server.py \
         code_graph_builder/mcp/tools.py code_graph_builder/mcp/pipeline.py \
         code_graph_builder/mcp/file_editor.py; do
  fname=$(basename "$f")
  git mv "$f" "code_graph_builder/entrypoints/mcp/$fname"
done
rmdir code_graph_builder/mcp 2>/dev/null || true

# Move CLI files
git mv code_graph_builder/cli.py code_graph_builder/entrypoints/cli/cli.py
git mv code_graph_builder/cgb_cli.py code_graph_builder/entrypoints/cli/cgb_cli.py
git mv code_graph_builder/commands_cli.py code_graph_builder/entrypoints/cli/commands_cli.py
```

- [ ] **Step 2: Create compatibility shims**

Create shims at original `code_graph_builder/mcp/`, `code_graph_builder/cli.py`, etc.

Critical shim — `code_graph_builder/mcp/__init__.py`:
```python
# Compatibility shim — will be removed after full migration
from code_graph_builder.entrypoints.mcp import *  # noqa: F401,F403
```

This is critical because `pyproject.toml` entry point `cgb-mcp` points to `code_graph_builder.mcp:main`.

- [ ] **Step 3: Update pyproject.toml entry points**

Update `pyproject.toml`:

```toml
[project.scripts]
code-graph-builder = "code_graph_builder.entrypoints.cli.cli:main"
cgb-mcp = "code_graph_builder.entrypoints.mcp:main"
```

- [ ] **Step 4: Update internal imports in moved files**

```bash
grep -rn "from code_graph_builder\." \
  code_graph_builder/entrypoints/mcp/*.py \
  code_graph_builder/entrypoints/cli/*.py
```

This is the largest import update — `mcp/tools.py` imports from nearly every module. Update all to new paths.

- [ ] **Step 5: Update `code_graph_builder/__init__.py`**

Update the package root `__init__.py` to import from new locations:

```bash
grep -n "from code_graph_builder\." code_graph_builder/__init__.py
```

Update all imports to new paths (e.g., `from code_graph_builder.foundation.types.config import ...`).

- [ ] **Step 6: Verify entry points work**

```bash
pip install -e . 2>&1 | tail -5
code-graph-builder --help 2>&1 | head -5
```

Expected: Both commands succeed.

- [ ] **Step 7: Run full test suite**

```bash
python -m pytest code_graph_builder/tests/ -v --tb=short
```

Expected: All PASS.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor: migrate mcp/ and cli to entrypoints/ (L4) + update pyproject.toml"
```

---

## Task 12: Batch 10 — Reorganize Tests + Remove Shims

**Files:**
- Reorganize: `code_graph_builder/tests/` → new structure mirroring layers
- Move: `code_graph_builder/rag/tests/` → `code_graph_builder/tests/domains/upper/`
- Delete: all compatibility shims
- Verify: zero old import paths remain

- [ ] **Step 1: Create new test directory structure**

```bash
mkdir -p code_graph_builder/tests/foundation
mkdir -p code_graph_builder/tests/domains/core
mkdir -p code_graph_builder/tests/domains/upper
mkdir -p code_graph_builder/tests/entrypoints
touch code_graph_builder/tests/foundation/__init__.py
touch code_graph_builder/tests/domains/__init__.py
touch code_graph_builder/tests/domains/core/__init__.py
touch code_graph_builder/tests/domains/upper/__init__.py
touch code_graph_builder/tests/entrypoints/__init__.py
```

- [ ] **Step 2: Move test files to new locations**

Map existing tests to layers:

```bash
# Foundation tests
git mv code_graph_builder/tests/test_basic.py code_graph_builder/tests/foundation/test_types.py
git mv code_graph_builder/tests/test_settings.py code_graph_builder/tests/foundation/test_settings.py
git mv code_graph_builder/tests/test_encoding_parsing.py code_graph_builder/tests/foundation/test_encoding_parsing.py
git mv code_graph_builder/tests/test_c_api_extraction.py code_graph_builder/tests/foundation/test_c_api_extraction.py
git mv code_graph_builder/tests/test_call_resolution_scenarios.py code_graph_builder/tests/foundation/test_call_resolution.py

# Domain/core tests
git mv code_graph_builder/tests/test_step1_graph_build.py code_graph_builder/tests/domains/core/test_graph_build.py
git mv code_graph_builder/tests/test_vector_store.py code_graph_builder/tests/domains/core/test_vector_store.py
git mv code_graph_builder/tests/test_embedder.py code_graph_builder/tests/domains/core/test_embedder.py
git mv code_graph_builder/tests/test_integration_semantic.py code_graph_builder/tests/domains/core/test_semantic.py

# Domain/upper tests
git mv code_graph_builder/tests/test_step2_api_docs.py code_graph_builder/tests/domains/upper/test_api_docs.py
git mv code_graph_builder/tests/test_step3_embedding.py code_graph_builder/tests/domains/upper/test_embedding_pipeline.py
git mv code_graph_builder/tests/test_rag.py code_graph_builder/tests/domains/upper/test_rag.py
git mv code_graph_builder/tests/test_api_find.py code_graph_builder/tests/domains/upper/test_api_find.py
git mv code_graph_builder/tests/test_api_find_integration.py code_graph_builder/tests/domains/upper/test_api_find_integration.py

# Move rag/tests/ contents
for f in code_graph_builder/rag/tests/test_*.py; do
  fname=$(basename "$f")
  git mv "$f" "code_graph_builder/tests/domains/upper/$fname"
done

# Entrypoint tests
git mv code_graph_builder/tests/test_mcp_protocol.py code_graph_builder/tests/entrypoints/test_mcp_protocol.py
git mv code_graph_builder/tests/test_mcp_user_flow.py code_graph_builder/tests/entrypoints/test_mcp_user_flow.py
```

- [ ] **Step 3: Update test imports**

```bash
grep -rn "from code_graph_builder\." code_graph_builder/tests/ | grep -v __pycache__
```

Update all test imports to use new `foundation.*`, `domains.*`, `entrypoints.*` paths.

- [ ] **Step 4: Run full test suite from new locations**

```bash
python -m pytest code_graph_builder/tests/ -v --tb=short
```

Expected: All PASS.

- [ ] **Step 5: Scan for old import paths in source code**

```bash
# Check for any remaining old-style imports in non-shim source files
grep -rn "from code_graph_builder\.\(constants\|types\|config\|models\|parsers\|parser_loader\|language_spec\|services\|utils\|settings\|builder\|graph_updater\|embeddings\|tools\|rag\|guidance\|mcp\|cli\|cgb_cli\|commands_cli\)" \
  code_graph_builder/ \
  --include="*.py" \
  | grep -v "Compatibility shim" \
  | grep -v __pycache__ \
  | grep -v "/tests/" \
  | grep -v "/examples/"
```

Expected: Only shim files should match. If any non-shim source file still uses old paths, fix it.

- [ ] **Step 6: Delete all compatibility shims**

```bash
# Remove shim files (they all contain "Compatibility shim")
grep -rl "Compatibility shim" code_graph_builder/ --include="*.py" | while read f; do
  rm "$f"
  echo "Deleted shim: $f"
done

# Clean up empty directories left behind
find code_graph_builder/ -type d -empty -delete
```

- [ ] **Step 7: Run full test suite after shim removal**

```bash
python -m pytest code_graph_builder/tests/ -v --tb=short
```

Expected: All PASS. If any test fails, a direct import of the old path still exists — find and fix it.

- [ ] **Step 8: Run dep_check**

```bash
python tools/dep_check.py
```

Expected: `All imports conform to layer rules. PASSED.`

- [ ] **Step 9: Verify entry points still work**

```bash
pip install -e . 2>&1 | tail -5
code-graph-builder --help 2>&1 | head -5
```

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "refactor: reorganize tests by layer + remove all compatibility shims"
```

---

## Task 13: GitHub Actions CI

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Create CI workflow**

```bash
mkdir -p .github/workflows
```

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  check:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.10", "3.11", "3.12"]

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      - name: Install dependencies
        run: pip install -e ".[treesitter-full]"

      - name: Check layer dependencies
        run: python tools/dep_check.py

      - name: Run tests
        run: python -m pytest code_graph_builder/tests/ -v --tb=short -x
        env:
          CGB_WORKSPACE: /tmp/cgb-test
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: add GitHub Actions workflow (dep-check + pytest)"
```

---

## Task 14: Contributing Documentation

**Files:**
- Create: `contributing/architecture.md`
- Create: `contributing/testing.md`
- Create: `contributing/add-feature.md`
- Create: `contributing/add-language.md`

- [ ] **Step 1: Create architecture.md**

Create `contributing/architecture.md`:

```markdown
# Architecture

## Layer Model

```
L0  foundation/types/                Pure data: constants, types, config, models
L1  foundation/{parsers,services,utils}/  Shared infra: AST parsing, DB drivers, utilities
L2  domains/core/{graph,embedding,search}/  Core domains: graph build, embeddings, search
L3  domains/upper/{apidoc,rag,guidance}/    Upper domains: API docs, RAG, guidance agents
L4  entrypoints/{mcp,cli}/           Entry points: MCP server, CLI commands
```

## Dependency Rules

Upper layers import lower layers. Never reverse. Never cross-domain at same layer.

| Layer | May import | Must NOT import |
|-------|-----------|-----------------|
| L0 | stdlib, third-party | Any project module |
| L1 | L0 | L2, L3, L4 |
| L2 | L0, L1 | L3, L4, other L2 domains |
| L3 | L0, L1, L2 | L4, other L3 domains |
| L4 | L0, L1, L2, L3 | Other L4 entrypoints |

## Enforcement

```bash
python tools/dep_check.py
```

Runs on every CI push/PR. Violations block merge.

## Directory Map

See `docs/superpowers/specs/2026-04-04-harness-architecture-design.md` Section 2 for full file listing.
```

- [ ] **Step 2: Create testing.md**

Create `contributing/testing.md`:

```markdown
# Testing

## Structure

Tests mirror the source layout:

```
tests/
├── foundation/       L0+L1 tests
├── domains/
│   ├── core/         L2 tests
│   └── upper/        L3 tests
└── entrypoints/      L4 tests
```

## Naming

- File: `test_<module>.py`
- Class: `Test<Feature>`
- Method: `test_<behavior>`

## Running

```bash
# Full suite
python -m pytest code_graph_builder/tests/ -v

# Single layer
python -m pytest code_graph_builder/tests/foundation/ -v

# Single file
python -m pytest code_graph_builder/tests/foundation/test_encoding_parsing.py -v

# Single test
python -m pytest code_graph_builder/tests/foundation/test_encoding_parsing.py::TestGBKParsing::test_gbk_file_functions_detected -v
```

## Test Types

- **Unit tests**: No external dependencies. Mock DB/API calls.
- **Integration tests**: Require file system, may use in-memory DB.
- **End-to-end tests**: Full pipeline tests (graph build → docs → embedding).

## Encoding Tests

GBK/GB2312 encoded C files are tested in `tests/foundation/test_encoding_parsing.py`.
Fixtures live in `tests/fixtures/`. To regenerate:

```bash
python code_graph_builder/tests/fixtures/create_gbk_fixtures.py
```

## Before Submitting

```bash
python tools/dep_check.py           # Layer rules
python -m pytest code_graph_builder/tests/ -v  # All tests
```
```

- [ ] **Step 3: Create add-feature.md**

Create `contributing/add-feature.md`:

```markdown
# Adding Features

## Adding a new parser (new language)

Touch:
1. `foundation/types/constants.py` — add to `SupportedLanguage` enum
2. `foundation/parsers/language_spec.py` — add language spec
3. `foundation/parsers/factory.py` — register in `ProcessorFactory`
4. `tests/foundation/test_parsers.py` — add parse tests

Layer: L1. Cannot import L2+.

## Adding a new database backend

Touch:
1. `foundation/services/` — create `<name>_service.py` implementing `IngestorProtocol`
2. `foundation/types/config.py` — add `<Name>Config` dataclass
3. `domains/core/graph/builder.py` — register backend in builder
4. `tests/domains/core/test_graph_builder.py` — add integration tests

Layer: service is L1, builder is L2.

## Adding a new embedding model

Touch:
1. `domains/core/embedding/` — create `<name>_embedder.py` implementing `BaseEmbedder`
2. `domains/core/graph/builder.py` — register in embedder factory
3. `tests/domains/core/test_embedding.py` — add tests

Layer: L2. Can import L0, L1.

## Adding a new MCP tool

Touch:
1. `entrypoints/mcp/tools.py` — add tool handler
2. `tests/entrypoints/test_mcp_protocol.py` — add tool test

Layer: L4. Can import all lower layers.

## Adding a new CLI command

Touch:
1. `entrypoints/cli/commands_cli.py` — add command handler
2. `tests/entrypoints/test_cli.py` — add command test

Layer: L4. Can import all lower layers.

## Adding a RAG feature

Touch:
1. `domains/upper/rag/` — modify or add module
2. `tests/domains/upper/test_rag.py` — add tests

Layer: L3. Can import L0, L1, L2. Cannot import other L3 domains or L4.
```

- [ ] **Step 4: Create add-language.md**

Create `contributing/add-language.md`:

```markdown
# Adding a New Language

Step-by-step guide to add Tree-sitter parsing support for a new language.

## Prerequisites

- Tree-sitter grammar package available on PyPI (e.g., `tree-sitter-ruby`)

## Steps

### 1. Add enum value

File: `foundation/types/constants.py`

```python
class SupportedLanguage(str, Enum):
    # ... existing languages ...
    RUBY = "ruby"  # Add new language
```

### 2. Add language spec

File: `foundation/parsers/language_spec.py`

Define a `LanguageSpec` for the new language with:
- `file_extensions`: e.g., `[".rb"]`
- `function_node_types`: AST node types for functions
- `class_node_types`: AST node types for classes
- `call_node_types`: AST node types for function calls
- `import_node_types`: AST node types for imports

### 3. Register in factory

File: `foundation/parsers/factory.py`

Add the language to the `ProcessorFactory` mapping.

### 4. Add grammar dependency

File: `pyproject.toml`

Add the grammar package to optional dependencies if not core:
```toml
[project.optional-dependencies]
treesitter-full = [
    # ... existing ...
    "tree-sitter-ruby>=0.21",
]
```

### 5. Write tests

File: `tests/foundation/test_parsers.py`

Add tests with sample source code in the new language:
- Function detection
- Class detection
- Call relationship detection
- Import detection

### 6. Verify

```bash
python tools/dep_check.py
python -m pytest code_graph_builder/tests/foundation/ -v
```
```

- [ ] **Step 5: Commit**

```bash
git add contributing/
git commit -m "docs: add contributing guides (architecture, testing, add-feature, add-language)"
```

---

## Task 15: CLAUDE.md

**Files:**
- Create: `CLAUDE.md`

- [ ] **Step 1: Create CLAUDE.md**

Create `CLAUDE.md` at project root:

```markdown
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

## Key Entry Points

- `code-graph-builder` CLI: `entrypoints/cli/cli.py`
- `cgb-mcp` MCP server: `entrypoints/mcp/server.py`
- Main API: `domains/core/graph/builder.py` → `CodeGraphBuilder`

## Build & Test

```bash
pip install -e ".[treesitter-full]"
python -m pytest code_graph_builder/tests/ -v
python tools/dep_check.py
```
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add CLAUDE.md with architecture overview for agents"
```

---

## Task 16: Final Verification

**Files:**
- No new files

- [ ] **Step 1: Full test suite**

```bash
python -m pytest code_graph_builder/tests/ -v --tb=short
```

Compare with baseline from Task 2 Step 1. Must have same pass/fail/skip counts (test names will differ due to reorganization).

- [ ] **Step 2: Dependency check**

```bash
python tools/dep_check.py
```

Expected: `All imports conform to layer rules. PASSED.`

- [ ] **Step 3: Entry point verification**

```bash
pip install -e . 2>&1 | tail -5
code-graph-builder --help 2>&1 | head -5
```

- [ ] **Step 4: npm verification**

```bash
cd /Users/jiaojeremy/CodeFile/CodeGraphWiki
npm run server 2>&1 | head -10
```

(Ctrl+C to stop after verifying it starts)

- [ ] **Step 5: Grep for any remaining old imports**

```bash
grep -rn "from code_graph_builder\.\(constants\|types\|config\|models\|builder\|graph_updater\|parser_loader\|language_spec\|settings\)" \
  code_graph_builder/ --include="*.py" | grep -v __pycache__ | grep -v /examples/
```

Expected: Zero results (or only `__init__.py` re-exports if intentionally kept).

- [ ] **Step 6: Commit final state (if any fixes were needed)**

```bash
git add -A
git status
# Only commit if there are changes
git diff --cached --quiet || git commit -m "fix: final verification fixes"
```

---

## Summary

| Task | Batch | What |
|------|-------|------|
| 0 | — | Create git worktree |
| 1 | — | Write dep_check.py |
| 2 | 0 | Establish test baseline + GBK/GB2312 tests |
| 3 | 1 | Migrate foundation/types/ (L0) |
| 4 | 2 | Migrate foundation/utils/ (L1) |
| 5 | 3 | Migrate foundation/parsers/ (L1) |
| 6 | 4 | Migrate foundation/services/ (L1) |
| 7 | 5 | Migrate domains/core/graph/ (L2) |
| 8 | 6 | Migrate domains/core/embedding/ + search/ (L2) |
| 9 | 7 | Migrate domains/upper/apidoc/ (L3) |
| 10 | 8 | Migrate domains/upper/rag/ + guidance/ (L3) |
| 11 | 9 | Migrate entrypoints/ (L4) + update pyproject.toml |
| 12 | 10 | Reorganize tests + remove shims |
| 13 | — | GitHub Actions CI |
| 14 | — | Contributing documentation |
| 15 | — | CLAUDE.md |
| 16 | — | Final verification |
