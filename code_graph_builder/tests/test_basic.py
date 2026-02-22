"""Basic tests for code_graph_builder."""

import pytest


def test_import():
    """Test basic imports."""
    from code_graph_builder import CodeGraphBuilder, BuildResult
    from code_graph_builder.constants import SupportedLanguage, NodeLabel
    from code_graph_builder.types import GraphData, GraphSummary

    assert CodeGraphBuilder is not None
    assert BuildResult is not None
    assert SupportedLanguage is not None
    assert NodeLabel is not None
    assert GraphData is not None
    assert GraphSummary is not None


def test_constants():
    """Test constants are defined correctly."""
    from code_graph_builder.constants import SupportedLanguage, NodeLabel, RelationshipType

    # Test SupportedLanguage enum
    assert SupportedLanguage.PYTHON.value == "python"
    assert SupportedLanguage.JS.value == "javascript"

    # Test NodeLabel enum
    assert NodeLabel.FUNCTION.value == "Function"
    assert NodeLabel.CLASS.value == "Class"

    # Test RelationshipType enum
    assert RelationshipType.CALLS.value == "CALLS"
    assert RelationshipType.DEFINES.value == "DEFINES"


def test_types():
    """Test type definitions."""
    from code_graph_builder.types import BuildResult, NodeType

    # Test BuildResult
    result = BuildResult(
        project_name="test",
        nodes_created=10,
        relationships_created=5,
        functions_found=3,
        classes_found=2,
        files_processed=1,
        errors=[],
    )
    assert result.project_name == "test"
    assert result.nodes_created == 10

    # Test NodeType
    assert NodeType.FUNCTION.value == "Function"
    assert NodeType.METHOD.value == "Method"


def test_models():
    """Test data models."""
    from code_graph_builder.models import LanguageSpec, Dependency
    from code_graph_builder.constants import SupportedLanguage

    # Test LanguageSpec
    spec = LanguageSpec(
        language=SupportedLanguage.PYTHON,
        file_extensions=(".py",),
        function_node_types=("function_definition",),
        class_node_types=("class_definition",),
        module_node_types=("module",),
    )
    assert spec.language == SupportedLanguage.PYTHON
    assert ".py" in spec.file_extensions

    # Test Dependency
    dep = Dependency(name="requests", spec=">=2.0.0")
    assert dep.name == "requests"
    assert dep.spec == ">=2.0.0"
