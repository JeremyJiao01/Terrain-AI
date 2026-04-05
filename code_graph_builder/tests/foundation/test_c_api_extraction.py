"""Tests for C language API interface extraction.

Tests cover:
- Function extraction with visibility (public/static/extern)
- Struct/union/enum member extraction
- Typedef extraction
- Macro extraction
- Header declaration tracking for visibility resolution
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _make_builder(project_path: Path):
    """Create a CodeGraphBuilder with a project-specific DB path."""
    from code_graph_builder.domains.core.graph.builder import CodeGraphBuilder

    db_path = project_path / "test_graph.db"
    return CodeGraphBuilder(
        str(project_path),
        backend_config={"db_path": str(db_path)},
    )


@pytest.fixture
def c_project_with_header(tmp_path: Path) -> Path:
    """Create a C project with header and source files."""
    project_path = tmp_path / "c_api_project"
    project_path.mkdir()

    # Create a Makefile to be recognized as a C package
    (project_path / "Makefile").write_text("all:\n\tgcc -o main main.c\n")

    # Header file declaring public API
    (project_path / "api.h").write_text(
        """\
#ifndef API_H
#define API_H

typedef int error_code;
typedef struct point Point;

struct point {
    int x;
    int y;
};

enum color {
    RED,
    GREEN,
    BLUE
};

union value {
    int i;
    float f;
    char c;
};

#define MAX_SIZE 1024
#define VERSION "1.0.0"

int api_init(void);
void api_cleanup(void);
int api_process(const char *input, int len);

#endif
"""
    )

    # Source file with implementations
    (project_path / "api.c").write_text(
        """\
#include "api.h"

static int _internal_helper(int x) {
    return x * 2;
}

int api_init(void) {
    return _internal_helper(0);
}

void api_cleanup(void) {
    // cleanup
}

int api_process(const char *input, int len) {
    return _internal_helper(len);
}

void undeclared_extern_func(void) {
    // This function has external linkage but is not in a header
}
"""
    )

    return project_path


@pytest.fixture
def c_struct_project(tmp_path: Path) -> Path:
    """Create a C project focused on struct/union/enum definitions."""
    project_path = tmp_path / "c_struct_project"
    project_path.mkdir()

    (project_path / "types.h").write_text(
        """\
#ifndef TYPES_H
#define TYPES_H

typedef unsigned long size_t_alias;
typedef int (*callback_fn)(int, int);

struct config {
    int width;
    int height;
    char *name;
    float ratio;
};

enum log_level {
    LOG_DEBUG,
    LOG_INFO,
    LOG_WARN,
    LOG_ERROR
};

union data {
    int integer;
    double floating;
    char string[32];
};

#define MAX_BUFSIZE 4096
#define MIN(a, b) ((a) < (b) ? (a) : (b))

#endif
"""
    )

    return project_path


def test_c_function_visibility_header(c_project_with_header: Path) -> None:
    """Test that functions declared in headers get 'public' visibility."""
    builder = _make_builder(c_project_with_header)
    result = builder.build_graph(clean=True)

    assert result.nodes_created > 0, "No nodes were created"

    # Query functions and their visibility
    func_query = """
    MATCH (f:Function)
    RETURN f.name AS name, f.visibility AS visibility, f.signature AS signature
    """
    functions = builder.query(func_query)

    func_map = {}
    for row in functions:
        raw = row.get("result", row)
        if isinstance(raw, (list, tuple)):
            func_map[raw[0]] = {"visibility": raw[1], "signature": raw[2]}
        elif isinstance(raw, dict):
            func_map[raw.get("name", "")] = {
                "visibility": raw.get("visibility"),
                "signature": raw.get("signature"),
            }

    # Functions declared in api.h should be "public"
    assert "api_init" in func_map, f"api_init not found. Available: {list(func_map.keys())}"
    assert func_map["api_init"]["visibility"] == "public", (
        f"api_init should be 'public', got '{func_map['api_init']['visibility']}'"
    )

    # Static function should be "static"
    assert "_internal_helper" in func_map, (
        f"_internal_helper not found. Available: {list(func_map.keys())}"
    )
    assert func_map["_internal_helper"]["visibility"] == "static", (
        f"_internal_helper should be 'static', got '{func_map['_internal_helper']['visibility']}'"
    )


def test_c_function_visibility_extern(c_project_with_header: Path) -> None:
    """Test that non-static functions not in headers get 'extern' visibility."""
    builder = _make_builder(c_project_with_header)
    result = builder.build_graph(clean=True)

    func_query = """
    MATCH (f:Function)
    WHERE f.name = 'undeclared_extern_func'
    RETURN f.name AS name, f.visibility AS visibility
    """
    functions = builder.query(func_query)

    assert len(functions) > 0, "undeclared_extern_func not found"

    raw = functions[0].get("result", functions[0])
    if isinstance(raw, (list, tuple)):
        visibility = raw[1]
    else:
        visibility = raw.get("visibility")

    assert visibility == "extern", (
        f"undeclared_extern_func should be 'extern', got '{visibility}'"
    )


def test_c_struct_member_extraction(c_struct_project: Path) -> None:
    """Test that struct members are extracted."""
    builder = _make_builder(c_struct_project)
    result = builder.build_graph(clean=True)

    class_query = """
    MATCH (c:Class)
    RETURN c.name AS name, c.kind AS kind, c.parameters AS members, c.signature AS signature
    """
    classes = builder.query(class_query)

    class_map = {}
    for row in classes:
        raw = row.get("result", row)
        if isinstance(raw, (list, tuple)):
            class_map[raw[0]] = {
                "kind": raw[1],
                "members": raw[2],
                "signature": raw[3],
            }
        elif isinstance(raw, dict):
            class_map[raw.get("name", "")] = {
                "kind": raw.get("kind"),
                "members": raw.get("members"),
                "signature": raw.get("signature"),
            }

    # Check struct
    assert "config" in class_map, f"config struct not found. Available: {list(class_map.keys())}"
    config = class_map["config"]
    assert config["kind"] == "struct", f"Expected kind 'struct', got '{config['kind']}'"
    assert config["members"] is not None, "config struct should have members"
    assert len(config["members"]) >= 3, (
        f"config struct should have at least 3 members, got {len(config['members'])}"
    )

    # Check enum
    assert "log_level" in class_map, f"log_level enum not found. Available: {list(class_map.keys())}"
    log_level = class_map["log_level"]
    assert log_level["kind"] == "enum", f"Expected kind 'enum', got '{log_level['kind']}'"
    assert log_level["members"] is not None, "log_level enum should have members"
    # Should contain LOG_DEBUG, LOG_INFO, LOG_WARN, LOG_ERROR
    assert len(log_level["members"]) == 4, (
        f"log_level enum should have 4 members, got {len(log_level['members'])}"
    )

    # Check union
    assert "data" in class_map, f"data union not found. Available: {list(class_map.keys())}"
    data = class_map["data"]
    assert data["kind"] == "union", f"Expected kind 'union', got '{data['kind']}'"


def test_c_typedef_extraction(c_struct_project: Path) -> None:
    """Test that typedef declarations are extracted as Type nodes."""
    builder = _make_builder(c_struct_project)
    result = builder.build_graph(clean=True)

    type_query = """
    MATCH (t:Type)
    RETURN t.name AS name, t.kind AS kind, t.signature AS signature
    """
    types = builder.query(type_query)

    type_map = {}
    for row in types:
        raw = row.get("result", row)
        if isinstance(raw, (list, tuple)):
            type_map[raw[0]] = {"kind": raw[1], "signature": raw[2]}
        elif isinstance(raw, dict):
            type_map[raw.get("name", "")] = {
                "kind": raw.get("kind"),
                "signature": raw.get("signature"),
            }

    assert "size_t_alias" in type_map, (
        f"size_t_alias typedef not found. Available: {list(type_map.keys())}"
    )
    assert type_map["size_t_alias"]["kind"] == "typedef"


def test_c_macro_extraction(c_struct_project: Path) -> None:
    """Test that #define macros are extracted."""
    builder = _make_builder(c_struct_project)
    result = builder.build_graph(clean=True)

    # Macros are stored as Function nodes with kind='macro'
    macro_query = """
    MATCH (f:Function)
    WHERE f.kind = 'macro'
    RETURN f.name AS name, f.signature AS signature, f.visibility AS visibility
    """
    macros = builder.query(macro_query)

    macro_names = set()
    for row in macros:
        raw = row.get("result", row)
        if isinstance(raw, (list, tuple)):
            macro_names.add(raw[0])
        elif isinstance(raw, dict):
            macro_names.add(raw.get("name"))

    assert "MAX_BUFSIZE" in macro_names, (
        f"MAX_BUFSIZE macro not found. Available: {macro_names}"
    )
    assert "MIN" in macro_names, (
        f"MIN macro not found. Available: {macro_names}"
    )


def test_c_function_signature_extraction(c_project_with_header: Path) -> None:
    """Test that C function signatures are correctly built."""
    builder = _make_builder(c_project_with_header)
    result = builder.build_graph(clean=True)

    func_query = """
    MATCH (f:Function)
    WHERE f.name = 'api_process'
    RETURN f.name AS name, f.signature AS signature, f.return_type AS return_type,
           f.parameters AS parameters
    """
    functions = builder.query(func_query)

    assert len(functions) > 0, "api_process not found"

    raw = functions[0].get("result", functions[0])
    if isinstance(raw, (list, tuple)):
        signature = raw[1]
        return_type = raw[2]
        parameters = raw[3]
    else:
        signature = raw.get("signature")
        return_type = raw.get("return_type")
        parameters = raw.get("parameters")

    assert signature is not None, "api_process should have a signature"
    assert "api_process" in signature, f"Signature should contain function name: {signature}"
    assert return_type is not None, "api_process should have a return type"


def test_c_header_declarations_tracked(c_project_with_header: Path) -> None:
    """Test that header declarations are tracked for visibility resolution."""
    builder = _make_builder(c_project_with_header)
    result = builder.build_graph(clean=True)

    # Query all functions from the .c file
    func_query = """
    MATCH (m:Module)-[:DEFINES]->(f:Function)
    RETURN m.name AS module, f.name AS name, f.visibility AS visibility
    """
    functions = builder.query(func_query)

    c_file_funcs = {}
    for row in functions:
        raw = row.get("result", row)
        if isinstance(raw, (list, tuple)):
            mod_name = raw[0]
            func_name = raw[1]
            vis = raw[2]
        elif isinstance(raw, dict):
            mod_name = raw.get("module", "")
            func_name = raw.get("name", "")
            vis = raw.get("visibility")
        else:
            continue

        if mod_name and mod_name.endswith(".c"):
            c_file_funcs[func_name] = vis

    # Functions also in header should be public
    for fname in ("api_init", "api_cleanup", "api_process"):
        if fname in c_file_funcs:
            assert c_file_funcs[fname] == "public", (
                f"{fname} in .c file should be 'public' (declared in header), "
                f"got '{c_file_funcs[fname]}'"
            )
