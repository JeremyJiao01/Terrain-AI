"""Tests for prompt templates."""

from __future__ import annotations

import pytest

from code_graph_builder.domains.upper.rag.prompt_templates import (
    CodeAnalysisPrompts,
    CodeContext,
    RAGPrompts,
    create_code_context,
    get_default_prompts,
)


class TestCodeContext:
    """Tests for CodeContext."""

    def test_basic_creation(self):
        """Test basic context creation."""
        ctx = CodeContext(
            source_code="def foo(): pass",
            file_path="test.py",
            qualified_name="test.foo",
            entity_type="Function",
        )
        assert ctx.source_code == "def foo(): pass"
        assert ctx.file_path == "test.py"
        assert ctx.qualified_name == "test.foo"
        assert ctx.entity_type == "Function"

    def test_format_context(self):
        """Test context formatting."""
        ctx = CodeContext(
            source_code="def foo(): pass",
            file_path="test.py",
            qualified_name="test.foo",
            entity_type="Function",
            docstring="Test function",
            callers=["caller1", "caller2"],
            callees=["callee1"],
        )
        formatted = ctx.format_context()
        assert "Entity: test.foo" in formatted
        assert "Type: Function" in formatted
        assert "File: test.py" in formatted
        assert "Documentation:" in formatted
        assert "def foo(): pass" in formatted
        assert "Called By:" in formatted
        assert "Calls:" in formatted

    def test_format_context_minimal(self):
        """Test context formatting with minimal data."""
        ctx = CodeContext(source_code="x = 1")
        formatted = ctx.format_context()
        assert "Source Code:" in formatted
        assert "x = 1" in formatted


class TestCodeAnalysisPrompts:
    """Tests for CodeAnalysisPrompts."""

    def test_get_system_prompt(self):
        """Test getting system prompt."""
        prompts = CodeAnalysisPrompts()
        system = prompts.get_system_prompt()
        assert "expert code analyst" in system.lower()
        assert len(system) > 0

    def test_format_explain_prompt(self):
        """Test formatting explain prompt."""
        prompts = CodeAnalysisPrompts()
        ctx = CodeContext(source_code="def foo(): pass")
        prompt = prompts.format_explain_prompt(ctx)
        assert "explain" in prompt.lower()
        assert "def foo(): pass" in prompt

    def test_format_query_prompt(self):
        """Test formatting query prompt."""
        prompts = CodeAnalysisPrompts()
        ctx = CodeContext(source_code="def foo(): pass")
        prompt = prompts.format_query_prompt("What does this do?", ctx)
        assert "What does this do?" in prompt
        assert "def foo(): pass" in prompt

    def test_format_documentation_prompt(self):
        """Test formatting documentation prompt."""
        prompts = CodeAnalysisPrompts()
        ctx = CodeContext(source_code="def foo(): pass")
        prompt = prompts.format_documentation_prompt(ctx)
        assert "documentation" in prompt.lower()

    def test_format_architecture_prompt(self):
        """Test formatting architecture prompt."""
        prompts = CodeAnalysisPrompts()
        ctx = CodeContext(source_code="class Foo: pass")
        prompt = prompts.format_architecture_prompt(ctx)
        assert "architecture" in prompt.lower()

    def test_format_summary_prompt(self):
        """Test formatting summary prompt."""
        prompts = CodeAnalysisPrompts()
        ctx = CodeContext(source_code="def foo(): pass")
        prompt = prompts.format_summary_prompt(ctx)
        assert "summary" in prompt.lower()

    def test_format_multi_context_prompt(self):
        """Test formatting multi-context prompt."""
        prompts = CodeAnalysisPrompts()
        contexts = [
            CodeContext(source_code="def foo(): pass"),
            CodeContext(source_code="def bar(): pass"),
        ]
        prompt = prompts.format_multi_context_prompt("Compare these", contexts)
        assert "Compare these" in prompt
        assert "Context 1" in prompt
        assert "Context 2" in prompt


class TestRAGPrompts:
    """Tests for RAGPrompts."""

    def test_format_rag_query_with_contexts(self):
        """Test formatting RAG query with contexts."""
        prompts = RAGPrompts()
        contexts = [
            CodeContext(
                source_code="def foo(): pass",
                qualified_name="test.foo",
            ),
        ]
        system, user = prompts.format_rag_query("Explain this", contexts)
        assert len(system) > 0
        assert "Explain this" in user
        assert "test.foo" in user

    def test_format_rag_query_no_contexts(self):
        """Test formatting RAG query with no contexts."""
        prompts = RAGPrompts()
        system, user = prompts.format_rag_query("Explain this", [])
        assert "No relevant code" in user


class TestConvenienceFunctions:
    """Tests for convenience functions."""

    def test_get_default_prompts(self):
        """Test getting default prompts."""
        prompts = get_default_prompts()
        assert isinstance(prompts, RAGPrompts)

    def test_create_code_context(self):
        """Test creating code context."""
        ctx = create_code_context(
            source_code="def foo(): pass",
            file_path="test.py",
            qualified_name="test.foo",
        )
        assert isinstance(ctx, CodeContext)
        assert ctx.source_code == "def foo(): pass"
