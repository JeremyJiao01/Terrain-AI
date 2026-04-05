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


import tree_sitter_c as tsc
from pathlib import Path
from tree_sitter import Language, Parser
from unittest.mock import MagicMock

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


def _make_func_ptr_queries(lang):
    """Build the func_ptr_assign query dict for tests."""
    query = lang.query(
        '(assignment_expression '
        '  left: (field_expression field: (field_identifier) @field) '
        '  right: (identifier) @rhs) @assign'
    )
    return {cs.SupportedLanguage.C: {cs.QUERY_FUNC_PTR_ASSIGN: query}}


def test_dot_access_assignment():
    """config.on_error = handle_error should create indirect CALLS edge."""
    code = """
    void setup() {
        config.on_error = handle_error;
    }
    """
    root_node, lang = _parse_c(code)
    queries = _make_func_ptr_queries(lang)

    registry_entries = {"project.fake.repo.test.handle_error"}
    processor, ingestor = _make_call_processor(registry_entries)

    processor.process_func_ptr_assignments(
        file_path=Path("/fake/repo/test.c"),
        root_node=root_node,
        language=cs.SupportedLanguage.C,
        queries=queries,
    )

    assert ingestor.ensure_relationship_batch.called
    call_args = ingestor.ensure_relationship_batch.call_args
    # Check relationship type is CALLS
    assert call_args[0][1] == cs.RelationshipType.CALLS
    # Check properties contain indirect=True and via_field
    props = call_args[1].get("properties") if call_args[1] else call_args[0][3]
    assert props["indirect"] is True
    assert props["via_field"] == "on_error"


def test_arrow_access_assignment():
    """ptr->callback = process should create indirect CALLS edge."""
    code = """
    void init() {
        ptr->callback = process;
    }
    """
    root_node, lang = _parse_c(code)
    queries = _make_func_ptr_queries(lang)

    registry_entries = {"project.fake.repo.test.process"}
    processor, ingestor = _make_call_processor(registry_entries)

    processor.process_func_ptr_assignments(
        file_path=Path("/fake/repo/test.c"),
        root_node=root_node,
        language=cs.SupportedLanguage.C,
        queries=queries,
    )

    assert ingestor.ensure_relationship_batch.called
    call_args = ingestor.ensure_relationship_batch.call_args
    props = call_args[1].get("properties") if call_args[1] else call_args[0][3]
    assert props["via_field"] == "callback"


def test_rhs_not_in_registry_skipped():
    """Assignment where RHS is not a known function should not create edge."""
    code = """
    void setup() {
        config.value = some_var;
    }
    """
    root_node, lang = _parse_c(code)
    queries = _make_func_ptr_queries(lang)

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
    queries = _make_func_ptr_queries(lang)

    registry_entries = {"project.fake.repo.test.func_a", "project.fake.repo.test.func_b"}
    processor, ingestor = _make_call_processor(registry_entries)

    processor.process_func_ptr_assignments(
        file_path=Path("/fake/repo/test.c"),
        root_node=root_node,
        language=cs.SupportedLanguage.C,
        queries=queries,
    )

    assert not ingestor.ensure_relationship_batch.called


def test_global_assignment_skipped():
    """Assignment outside any function should be skipped (no enclosing function)."""
    code = """
    config.on_error = handle_error;
    """
    root_node, lang = _parse_c(code)
    queries = _make_func_ptr_queries(lang)

    registry_entries = {"project.fake.repo.test.handle_error"}
    processor, ingestor = _make_call_processor(registry_entries)

    processor.process_func_ptr_assignments(
        file_path=Path("/fake/repo/test.c"),
        root_node=root_node,
        language=cs.SupportedLanguage.C,
        queries=queries,
    )

    assert not ingestor.ensure_relationship_batch.called


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
    queries = _make_func_ptr_queries(lang)

    registry_entries = {
        "project.fake.repo.test.start_func",
        "project.fake.repo.test.stop_func",
        "project.fake.repo.test.error_func",
    }
    processor, ingestor = _make_call_processor(registry_entries)

    processor.process_func_ptr_assignments(
        file_path=Path("/fake/repo/test.c"),
        root_node=root_node,
        language=cs.SupportedLanguage.C,
        queries=queries,
    )

    assert ingestor.ensure_relationship_batch.call_count == 3
    via_fields = set()
    for call_args in ingestor.ensure_relationship_batch.call_args_list:
        props = call_args[1].get("properties") if call_args[1] else call_args[0][3]
        via_fields.add(props["via_field"])
        assert props["indirect"] is True
    assert via_fields == {"on_start", "on_stop", "on_error"}
