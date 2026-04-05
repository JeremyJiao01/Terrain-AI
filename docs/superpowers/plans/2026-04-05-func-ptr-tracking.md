# C/C++ 函数指针追踪增强 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect `struct.field = func` assignment patterns in C/C++ and generate indirect CALLS edges with metadata.

**Architecture:** New Tree-sitter query captures `assignment_expression` with `field_expression` LHS + `identifier` RHS. `CallProcessor` gets a new method that processes these captures, validates RHS against function registry, and emits `CALLS` edges with `indirect=True` property. `CallResolver` gains a `func_ptr_map` for resolving `obj.field()` calls to their assigned targets.

**Tech Stack:** Tree-sitter (C/C++ grammars), existing CallProcessor/CallResolver/GraphUpdater pipeline.

---

### Task 1: Add constants for func_ptr_assign query

**Files:**
- Modify: `code_graph_builder/foundation/types/constants.py:417-435`

- [ ] **Step 1: Add query key and capture constants**

In `constants.py`, after `QUERY_MACROS = "macros"` (line 426), add:

```python
QUERY_FUNC_PTR_ASSIGN = "func_ptr_assign"
```

After `CAPTURE_MACRO = "macro"` (line 435), add:

```python
CAPTURE_ASSIGN = "assign"
CAPTURE_LHS = "lhs"
CAPTURE_FIELD = "field"
CAPTURE_RHS = "rhs"
```

- [ ] **Step 2: Commit**

```bash
git add code_graph_builder/foundation/types/constants.py
git commit -m "feat: add func_ptr_assign query constants"
```

---

### Task 2: Add func_ptr_assign query to C/C++ language spec

**Files:**
- Modify: `code_graph_builder/foundation/parsers/language_spec.py`
- Modify: `code_graph_builder/foundation/parsers/parser_loader.py`

- [ ] **Step 1: Read language_spec.py to find C/C++ spec definitions**

Read the file to locate the C and C++ `LanguageSpec` definitions and understand the `custom_queries` or query override pattern.

- [ ] **Step 2: Add func_ptr_assign query string to language_spec.py**

Add a constant near the C/C++ language spec section:

```python
C_FUNC_PTR_ASSIGN_QUERY = """
(assignment_expression
  left: (field_expression
    field: (field_identifier) @field)
  right: (identifier) @rhs) @assign
"""
```

This single query captures both `.` and `->` access patterns (verified via AST: both produce `field_expression` with `field_identifier` child).

- [ ] **Step 3: Register the query in C and C++ specs**

Add `func_ptr_assign_query` to both the C and C++ `LanguageSpec` instances. The exact mechanism depends on how `language_spec.py` handles custom queries — if it uses a dict, add the key; if it uses named fields on the dataclass, add the field.

- [ ] **Step 4: Update parser_loader.py to compile the new query**

In `parser_loader.py`, where language queries are compiled, add handling for `QUERY_FUNC_PTR_ASSIGN`. The query should be compiled for C and C++ only:

```python
func_ptr_query_str = spec.func_ptr_assign_query  # or however the spec stores it
if func_ptr_query_str:
    queries[cs.QUERY_FUNC_PTR_ASSIGN] = language.query(func_ptr_query_str)
```

- [ ] **Step 5: Verify query compiles**

```bash
python3 -c "
import tree_sitter_c as tsc
from tree_sitter import Language
lang = Language(tsc.language())
q = lang.query('(assignment_expression left: (field_expression field: (field_identifier) @field) right: (identifier) @rhs) @assign')
print('Query compiled OK, pattern count:', len(q.patterns))
"
```

Expected: `Query compiled OK, pattern count: 1`

- [ ] **Step 6: Commit**

```bash
git add code_graph_builder/foundation/parsers/language_spec.py code_graph_builder/foundation/parsers/parser_loader.py
git commit -m "feat: add func_ptr_assign tree-sitter query for C/C++"
```

---

### Task 3: Add func_ptr_map to CallResolver

**Files:**
- Modify: `code_graph_builder/foundation/parsers/call_resolver.py`
- Test: `code_graph_builder/tests/foundation/parsers/test_func_ptr_detection.py`

- [ ] **Step 1: Write failing test for register_func_ptr and resolve_func_ptr_call**

Create `code_graph_builder/tests/foundation/parsers/test_func_ptr_detection.py`:

```python
"""Tests for C/C++ function pointer detection."""
from __future__ import annotations

from unittest.mock import MagicMock

from code_graph_builder.foundation.parsers.call_resolver import CallResolver


def _make_resolver() -> CallResolver:
    registry = MagicMock()
    registry.__contains__ = MagicMock(return_value=False)
    import_processor = MagicMock()
    import_processor.get_import_mapping.return_value = {}
    return CallResolver(function_registry=registry, import_processor=import_processor)


def test_register_and_resolve_func_ptr():
    resolver = _make_resolver()
    resolver.register_func_ptr("on_error", "project.pkg.handle_error")
    assert resolver.resolve_func_ptr_call("on_error") == "project.pkg.handle_error"


def test_resolve_func_ptr_call_unknown():
    resolver = _make_resolver()
    assert resolver.resolve_func_ptr_call("unknown_field") is None


def test_resolve_call_fallback_to_func_ptr():
    """When normal resolution fails, obj.field should resolve via func_ptr_map."""
    registry = MagicMock()
    registry.__contains__ = MagicMock(return_value=False)
    registry._entries = {}
    import_processor = MagicMock()
    import_processor.get_import_mapping.return_value = {}

    resolver = CallResolver(function_registry=registry, import_processor=import_processor)
    resolver.register_func_ptr("callback", "project.src.process_data")

    result = resolver.resolve_call("config.callback", "project.src.main")
    assert result == "project.src.process_data"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest code_graph_builder/tests/foundation/parsers/test_func_ptr_detection.py -v
```

Expected: FAIL — `CallResolver` has no `register_func_ptr` method.

- [ ] **Step 3: Implement func_ptr_map in CallResolver**

In `call_resolver.py`, add to `__init__`:

```python
self._func_ptr_map: dict[str, str] = {}
```

Add two new methods after `_resolve_via_registry`:

```python
def register_func_ptr(self, field_name: str, target_qn: str) -> None:
    """Register a struct field → function mapping from pointer assignment."""
    self._func_ptr_map[field_name] = target_qn

def resolve_func_ptr_call(self, field_name: str) -> str | None:
    """Resolve an indirect call through a registered function pointer field."""
    return self._func_ptr_map.get(field_name)
```

In `resolve_call`, add a fallback **before** the final `_resolve_via_registry` call. After the `_resolve_same_module` check (line 61), add:

```python
# Try function pointer resolution for obj.field patterns
if cs.SEPARATOR_DOT in call_name:
    field = call_name.rsplit(cs.SEPARATOR_DOT, 1)[-1]
    if resolved := self.resolve_func_ptr_call(field):
        return resolved
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest code_graph_builder/tests/foundation/parsers/test_func_ptr_detection.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add code_graph_builder/foundation/parsers/call_resolver.py code_graph_builder/tests/foundation/parsers/test_func_ptr_detection.py
git commit -m "feat: add func_ptr_map to CallResolver for indirect call resolution"
```

---

### Task 4: Add process_func_ptr_assignments to CallProcessor

**Files:**
- Modify: `code_graph_builder/foundation/parsers/call_processor.py`
- Test: `code_graph_builder/tests/foundation/parsers/test_func_ptr_detection.py`

- [ ] **Step 1: Write failing test for process_func_ptr_assignments**

Append to `test_func_ptr_detection.py`:

```python
import tree_sitter_c as tsc
from pathlib import Path
from tree_sitter import Language, Parser
from unittest.mock import MagicMock, call

from code_graph_builder.foundation.parsers.call_processor import CallProcessor
from code_graph_builder.foundation.types import constants as cs


def _parse_c(code: str):
    """Parse C code and return root_node."""
    lang = Language(tsc.language())
    parser = Parser(lang)
    tree = parser.parse(code.encode())
    return tree.root_node, lang


def _make_call_processor(registry_entries: set[str] | None = None):
    """Create a CallProcessor with mocked dependencies."""
    ingestor = MagicMock()
    repo_path = Path("/fake/repo")
    function_registry = MagicMock()
    entries = registry_entries or set()
    function_registry.__contains__ = lambda self, key: key in entries
    function_registry._entries = {k: "FUNCTION" for k in entries}

    import_processor = MagicMock()
    import_processor.get_import_mapping.return_value = {}

    processor = CallProcessor(
        ingestor=ingestor,
        repo_path=repo_path,
        project_name="project",
        function_registry=function_registry,
        import_processor=import_processor,
        type_inference=None,
        class_inheritance={},
    )
    return processor, ingestor


def test_dot_access_assignment():
    """config.on_error = handle_error should create indirect CALLS edge."""
    code = """
    void setup() {
        config.on_error = handle_error;
    }
    """
    root_node, lang = _parse_c(code)
    query = lang.query(
        '(assignment_expression '
        '  left: (field_expression field: (field_identifier) @field) '
        '  right: (identifier) @rhs) @assign'
    )
    queries = {cs.SupportedLanguage.C: {cs.QUERY_FUNC_PTR_ASSIGN: query}}

    registry_entries = {"project.fake.repo.handle_error"}
    processor, ingestor = _make_call_processor(registry_entries)

    processor.process_func_ptr_assignments(
        file_path=Path("/fake/repo/test.c"),
        root_node=root_node,
        language=cs.SupportedLanguage.C,
        queries=queries,
    )

    # Verify CALLS edge was created with indirect=True
    assert ingestor.ensure_relationship_batch.called
    args = ingestor.ensure_relationship_batch.call_args
    assert args[0][1] == cs.RelationshipType.CALLS
    props = args[0][3] if len(args[0]) > 3 else args[1].get("properties", {})
    assert props.get("indirect") is True
    assert props.get("via_field") == "on_error"


def test_arrow_access_assignment():
    """ptr->callback = process should create indirect CALLS edge."""
    code = """
    void init() {
        ptr->callback = process;
    }
    """
    root_node, lang = _parse_c(code)
    query = lang.query(
        '(assignment_expression '
        '  left: (field_expression field: (field_identifier) @field) '
        '  right: (identifier) @rhs) @assign'
    )
    queries = {cs.SupportedLanguage.C: {cs.QUERY_FUNC_PTR_ASSIGN: query}}

    registry_entries = {"project.fake.repo.process"}
    processor, ingestor = _make_call_processor(registry_entries)

    processor.process_func_ptr_assignments(
        file_path=Path("/fake/repo/test.c"),
        root_node=root_node,
        language=cs.SupportedLanguage.C,
        queries=queries,
    )

    assert ingestor.ensure_relationship_batch.called
    props = ingestor.ensure_relationship_batch.call_args[0][3] if len(ingestor.ensure_relationship_batch.call_args[0]) > 3 else ingestor.ensure_relationship_batch.call_args[1].get("properties", {})
    assert props.get("via_field") == "callback"


def test_rhs_not_in_registry_skipped():
    """Assignment where RHS is not a known function should not create edge."""
    code = """
    void setup() {
        config.value = some_var;
    }
    """
    root_node, lang = _parse_c(code)
    query = lang.query(
        '(assignment_expression '
        '  left: (field_expression field: (field_identifier) @field) '
        '  right: (identifier) @rhs) @assign'
    )
    queries = {cs.SupportedLanguage.C: {cs.QUERY_FUNC_PTR_ASSIGN: query}}

    processor, ingestor = _make_call_processor(registry_entries=set())

    processor.process_func_ptr_assignments(
        file_path=Path("/fake/repo/test.c"),
        root_node=root_node,
        language=cs.SupportedLanguage.C,
        queries=queries,
    )

    assert not ingestor.ensure_relationship_batch.called


def test_array_init_not_matched():
    """Array initializer should not trigger func ptr detection."""
    code = """
    void setup() {
        handler_t handlers[] = {func_a, func_b};
    }
    """
    root_node, lang = _parse_c(code)
    query = lang.query(
        '(assignment_expression '
        '  left: (field_expression field: (field_identifier) @field) '
        '  right: (identifier) @rhs) @assign'
    )
    queries = {cs.SupportedLanguage.C: {cs.QUERY_FUNC_PTR_ASSIGN: query}}

    registry_entries = {"project.fake.repo.func_a", "project.fake.repo.func_b"}
    processor, ingestor = _make_call_processor(registry_entries)

    processor.process_func_ptr_assignments(
        file_path=Path("/fake/repo/test.c"),
        root_node=root_node,
        language=cs.SupportedLanguage.C,
        queries=queries,
    )

    # Array init uses init_declarator, not assignment_expression — query won't match
    assert not ingestor.ensure_relationship_batch.called


def test_global_assignment_skipped():
    """Assignment outside any function should be skipped."""
    code = """
    config.on_error = handle_error;
    """
    root_node, lang = _parse_c(code)
    query = lang.query(
        '(assignment_expression '
        '  left: (field_expression field: (field_identifier) @field) '
        '  right: (identifier) @rhs) @assign'
    )
    queries = {cs.SupportedLanguage.C: {cs.QUERY_FUNC_PTR_ASSIGN: query}}

    registry_entries = {"project.fake.repo.handle_error"}
    processor, ingestor = _make_call_processor(registry_entries)

    processor.process_func_ptr_assignments(
        file_path=Path("/fake/repo/test.c"),
        root_node=root_node,
        language=cs.SupportedLanguage.C,
        queries=queries,
    )

    # No enclosing function → skip
    assert not ingestor.ensure_relationship_batch.called
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest code_graph_builder/tests/foundation/parsers/test_func_ptr_detection.py::test_dot_access_assignment -v
```

Expected: FAIL — `CallProcessor` has no `process_func_ptr_assignments` method.

- [ ] **Step 3: Implement process_func_ptr_assignments**

Add to `call_processor.py`, after `process_calls_in_file`:

```python
def process_func_ptr_assignments(
    self,
    file_path: Path,
    root_node: Node,
    language: cs.SupportedLanguage,
    queries: dict[cs.SupportedLanguage, LanguageQueries],
) -> None:
    """Detect struct field function pointer assignments and create indirect CALLS edges."""
    relative_path = file_path.relative_to(self.repo_path)

    try:
        lang_queries = queries.get(language)
        if not lang_queries:
            return

        fp_query = lang_queries.get(cs.QUERY_FUNC_PTR_ASSIGN)
        if not fp_query:
            return

        module_qn = cs.SEPARATOR_DOT.join(
            [self.project_name] + list(relative_path.with_suffix("").parts)
        )

        cursor = QueryCursor(fp_query)
        captures = cursor.captures(root_node)

        assign_nodes = captures.get(cs.CAPTURE_ASSIGN, [])
        field_nodes = captures.get(cs.CAPTURE_FIELD, [])
        rhs_nodes = captures.get(cs.CAPTURE_RHS, [])

        for i, assign_node in enumerate(assign_nodes):
            if not isinstance(assign_node, Node):
                continue
            if i >= len(field_nodes) or i >= len(rhs_nodes):
                continue

            field_name = safe_decode_text(field_nodes[i])
            rhs_name = safe_decode_text(rhs_nodes[i])
            if not field_name or not rhs_name:
                continue

            # Find enclosing function
            caller_qn = self._find_caller_function(assign_node, module_qn, language)
            if not caller_qn:
                continue

            # Resolve RHS to a known function
            resolver = self._get_call_resolver()
            target_qn = resolver.resolve_call(rhs_name, module_qn, None)
            if not target_qn:
                continue

            # Register in func_ptr_map for later call resolution
            resolver.register_func_ptr(field_name, target_qn)

            # Create indirect CALLS edge
            self.ingestor.ensure_relationship_batch(
                (cs.NodeLabel.FUNCTION, cs.KEY_QUALIFIED_NAME, caller_qn),
                cs.RelationshipType.CALLS,
                (cs.NodeLabel.FUNCTION, cs.KEY_QUALIFIED_NAME, target_qn),
                properties={"indirect": True, "via_field": field_name},
            )
            logger.debug(
                f"Created indirect CALLS: {caller_qn} -> {target_qn} via .{field_name}"
            )

    except Exception as e:
        logger.warning(f"Failed to process func ptr assignments in {file_path}: {e}")
```

- [ ] **Step 4: Run all tests**

```bash
python3 -m pytest code_graph_builder/tests/foundation/parsers/test_func_ptr_detection.py -v
```

Expected: 8 passed (3 resolver + 5 call processor tests).

- [ ] **Step 5: Commit**

```bash
git add code_graph_builder/foundation/parsers/call_processor.py code_graph_builder/tests/foundation/parsers/test_func_ptr_detection.py
git commit -m "feat: add process_func_ptr_assignments to CallProcessor"
```

---

### Task 5: Wire into GraphUpdater

**Files:**
- Modify: `code_graph_builder/domains/core/graph/graph_updater.py`

- [ ] **Step 1: Read _process_function_calls method**

Read `graph_updater.py` lines 388-400 to see the exact call processing loop.

- [ ] **Step 2: Add func ptr processing after call processing**

In `_process_function_calls`, after the existing loop that calls `process_calls_in_file`, add a second pass for C/C++ files:

```python
def _process_function_calls(self) -> None:
    """Process function calls in all cached ASTs."""
    ast_cache_items = list(self.ast_cache.items())
    for file_path, (root_node, language) in ast_cache_items:
        self.factory.call_processor.process_calls_in_file(
            file_path, root_node, language, self.queries
        )

    # Second pass: detect C/C++ function pointer assignments
    for file_path, (root_node, language) in ast_cache_items:
        if language in (cs.SupportedLanguage.C, cs.SupportedLanguage.CPP):
            self.factory.call_processor.process_func_ptr_assignments(
                file_path, root_node, language, self.queries
            )
```

- [ ] **Step 3: Run dep_check**

```bash
python3 tools/dep_check.py
```

Expected: No layer violations.

- [ ] **Step 4: Run full test suite**

```bash
python3 -m pytest code_graph_builder/tests/ -v
```

Expected: All tests pass (including the new func_ptr tests).

- [ ] **Step 5: Commit**

```bash
git add code_graph_builder/domains/core/graph/graph_updater.py
git commit -m "feat: wire func ptr assignment detection into graph build pipeline"
```

---

### Task 6: Final integration test + verify

**Files:**
- Test: `code_graph_builder/tests/foundation/parsers/test_func_ptr_detection.py`

- [ ] **Step 1: Add integration-style test**

Append to `test_func_ptr_detection.py`:

```python
def test_multiple_assignments_same_file():
    """Multiple struct field assignments in one file create multiple edges."""
    code = """
    void init() {
        handlers.on_start = start_func;
        handlers.on_stop = stop_func;
        handlers.on_error = error_func;
    }
    """
    root_node, lang = _parse_c(code)
    query = lang.query(
        '(assignment_expression '
        '  left: (field_expression field: (field_identifier) @field) '
        '  right: (identifier) @rhs) @assign'
    )
    queries = {cs.SupportedLanguage.C: {cs.QUERY_FUNC_PTR_ASSIGN: query}}

    registry_entries = {
        "project.fake.repo.start_func",
        "project.fake.repo.stop_func",
        "project.fake.repo.error_func",
    }
    processor, ingestor = _make_call_processor(registry_entries)

    processor.process_func_ptr_assignments(
        file_path=Path("/fake/repo/test.c"),
        root_node=root_node,
        language=cs.SupportedLanguage.C,
        queries=queries,
    )

    assert ingestor.ensure_relationship_batch.call_count == 3
    fields = [
        c[1].get("properties", c[0][3] if len(c[0]) > 3 else {}).get("via_field")
        for c in ingestor.ensure_relationship_batch.call_args_list
    ]
    assert set(fields) == {"on_start", "on_stop", "on_error"}
```

- [ ] **Step 2: Run all tests**

```bash
python3 -m pytest code_graph_builder/tests/foundation/parsers/test_func_ptr_detection.py -v
```

Expected: 9 passed.

- [ ] **Step 3: Run full project test suite + dep_check**

```bash
python3 -m pytest code_graph_builder/tests/ -v && python3 tools/dep_check.py
```

Expected: All pass, no violations.

- [ ] **Step 4: Final commit**

```bash
git add code_graph_builder/tests/foundation/parsers/test_func_ptr_detection.py
git commit -m "test: add integration test for multiple func ptr assignments"
```
