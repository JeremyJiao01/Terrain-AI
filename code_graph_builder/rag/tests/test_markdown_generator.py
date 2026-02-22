"""Tests for markdown generator."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from code_graph_builder.rag.markdown_generator import (
    AnalysisResult,
    MarkdownGenerator,
    SourceReference,
    create_source_reference_from_context,
    format_code_block,
)
from code_graph_builder.rag.prompt_templates import CodeContext


class TestSourceReference:
    """Tests for SourceReference."""

    def test_basic_creation(self):
        """Test basic creation."""
        ref = SourceReference(
            name="foo",
            qualified_name="test.foo",
            file_path="test.py",
            line_start=10,
            line_end=20,
            entity_type="Function",
        )
        assert ref.name == "foo"
        assert ref.qualified_name == "test.foo"

    def test_format_link(self):
        """Test link formatting."""
        ref = SourceReference(
            name="foo",
            qualified_name="test.foo",
            file_path="test.py",
            line_start=10,
            line_end=20,
        )
        link = ref.format_link()
        assert "[test.foo]" in link
        assert "test.py:10-20" in link

    def test_format_link_single_line(self):
        """Test link formatting for single line."""
        ref = SourceReference(
            name="foo",
            qualified_name="test.foo",
            file_path="test.py",
            line_start=10,
            line_end=10,
        )
        link = ref.format_link()
        assert "test.py:10" in link
        assert "-10" not in link

    def test_to_dict(self):
        """Test conversion to dictionary."""
        ref = SourceReference(
            name="foo",
            qualified_name="test.foo",
            file_path="test.py",
        )
        data = ref.to_dict()
        assert data["name"] == "foo"
        assert data["qualified_name"] == "test.foo"


class TestAnalysisResult:
    """Tests for AnalysisResult."""

    def test_basic_creation(self):
        """Test basic creation."""
        result = AnalysisResult(
            query="Test query",
            response="Test response",
        )
        assert result.query == "Test query"
        assert result.response == "Test response"
        assert result.sources == []

    def test_with_sources(self):
        """Test creation with sources."""
        sources = [
            SourceReference(name="foo", qualified_name="test.foo", file_path="test.py"),
        ]
        result = AnalysisResult(
            query="Test",
            response="Response",
            sources=sources,
            metadata={"key": "value"},
        )
        assert len(result.sources) == 1
        assert result.metadata["key"] == "value"

    def test_to_dict(self):
        """Test conversion to dictionary."""
        result = AnalysisResult(query="Test", response="Response")
        data = result.to_dict()
        assert data["query"] == "Test"
        assert data["response"] == "Response"
        assert "timestamp" in data


class TestMarkdownGenerator:
    """Tests for MarkdownGenerator."""

    def test_default_init(self):
        """Test default initialization."""
        gen = MarkdownGenerator()
        assert gen.include_toc is True
        assert gen.include_timestamp is True
        assert gen.include_metadata is True

    def test_custom_init(self):
        """Test custom initialization."""
        gen = MarkdownGenerator(
            include_toc=False,
            include_timestamp=False,
            include_metadata=False,
        )
        assert gen.include_toc is False

    def test_generate_analysis_doc(self):
        """Test generating analysis document."""
        gen = MarkdownGenerator()
        result = AnalysisResult(
            query="What does this do?",
            response="It does something.",
            sources=[
                SourceReference(
                    name="foo",
                    qualified_name="test.foo",
                    file_path="test.py",
                    entity_type="Function",
                ),
            ],
        )
        doc = gen.generate_analysis_doc("Test Analysis", result)
        assert "# Test Analysis" in doc
        assert "## Query" in doc
        assert "What does this do?" in doc
        assert "## Analysis" in doc
        assert "It does something." in doc
        assert "## Sources" in doc
        assert "test.foo" in doc

    def test_generate_analysis_doc_no_toc(self):
        """Test generating document without TOC."""
        gen = MarkdownGenerator(include_toc=False)
        result = AnalysisResult(query="Test", response="Response")
        doc = gen.generate_analysis_doc("Test", result)
        assert "## Table of Contents" not in doc

    def test_generate_code_documentation(self):
        """Test generating code documentation."""
        gen = MarkdownGenerator()
        ctx = CodeContext(
            source_code="def foo(): pass",
            file_path="test.py",
            qualified_name="test.foo",
            entity_type="Function",
        )
        doc = gen.generate_code_documentation(ctx, "This is a test function.")
        assert "# test.foo" in doc
        assert "**Type:** Function" in doc
        assert "**File:** `test.py`" in doc
        assert "This is a test function." in doc
        assert "## Source Code" in doc

    def test_generate_comparison_doc(self):
        """Test generating comparison document."""
        gen = MarkdownGenerator()
        contexts = [
            CodeContext(source_code="def foo(): pass", qualified_name="foo"),
            CodeContext(source_code="def bar(): pass", qualified_name="bar"),
        ]
        doc = gen.generate_comparison_doc(
            title="Comparison",
            query="Compare these",
            contexts=contexts,
            analysis="They are similar.",
        )
        assert "# Comparison" in doc
        assert "**Query:** Compare these" in doc
        assert "## Comparison Analysis" in doc
        assert "## Compared Entities" in doc

    def test_save_document(self):
        """Test saving document."""
        gen = MarkdownGenerator()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = gen.save_document("# Test", f"{tmpdir}/test.md")
            assert path.exists()
            assert path.read_text() == "# Test"

    def test_save_document_creates_dirs(self):
        """Test saving document creates directories."""
        gen = MarkdownGenerator()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = gen.save_document("# Test", f"{tmpdir}/nested/dir/test.md")
            assert path.exists()


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    def test_create_source_reference_from_context(self):
        """Test creating source reference from context."""
        ctx = CodeContext(
            source_code="def foo(): pass",
            file_path="test.py",
            qualified_name="test.foo",
            entity_type="Function",
        )
        ref = create_source_reference_from_context(ctx)
        assert isinstance(ref, SourceReference)
        assert ref.name == "foo"
        assert ref.qualified_name == "test.foo"
        assert ref.file_path == "test.py"
        assert ref.entity_type == "Function"

    def test_format_code_block(self):
        """Test formatting code block."""
        code = "def foo(): pass"
        formatted = format_code_block(code)
        assert formatted.startswith("```python")
        assert formatted.endswith("```")
        assert "def foo(): pass" in formatted

    def test_format_code_block_custom_language(self):
        """Test formatting code block with custom language."""
        code = "console.log('test')"
        formatted = format_code_block(code, language="javascript")
        assert formatted.startswith("```javascript")
