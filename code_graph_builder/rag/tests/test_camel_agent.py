"""Tests for CAMEL agent."""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from code_graph_builder.rag.camel_agent import (
    CamelAgent,
    CamelAgentResponse,
    MultiAgentRAG,
    create_camel_agent,
)
from code_graph_builder.rag.kimi_client import ChatResponse


class TestCamelAgentResponse:
    """Tests for CamelAgentResponse."""

    def test_creation(self):
        """Test basic creation."""
        response = CamelAgentResponse(
            content="Test response",
            metadata={"key": "value"},
            role="Analyst",
        )
        assert response.content == "Test response"
        assert response.metadata["key"] == "value"
        assert response.role == "Analyst"

    def test_default_role(self):
        """Test default role."""
        response = CamelAgentResponse(content="Test")
        assert response.role == "agent"


class TestCamelAgent:
    """Tests for CamelAgent."""

    @patch("code_graph_builder.rag.camel_agent.create_kimi_client")
    def test_init(self, mock_create_client):
        """Test initialization."""
        mock_client = Mock()
        mock_create_client.return_value = mock_client

        agent = CamelAgent(
            role="Code Analyst",
            goal="Analyze code",
            backstory="Expert programmer",
        )
        assert agent.role == "Code Analyst"
        assert agent.goal == "Analyze code"
        assert agent.backstory == "Expert programmer"
        assert "Code Analyst" in agent.system_prompt

    @patch("code_graph_builder.rag.camel_agent.create_kimi_client")
    def test_build_system_prompt(self, mock_create_client):
        """Test system prompt building."""
        mock_client = Mock()
        mock_create_client.return_value = mock_client

        agent = CamelAgent(
            role="Tester",
            goal="Test things",
            backstory="Testing expert",
        )
        prompt = agent.system_prompt
        assert "Tester" in prompt
        assert "Test things" in prompt
        assert "Testing expert" in prompt

    @patch("code_graph_builder.rag.camel_agent.create_kimi_client")
    def test_analyze(self, mock_create_client):
        """Test analyze method."""
        mock_client = Mock()
        mock_client.chat_with_messages.return_value = ChatResponse(
            content="Analysis result",
            usage={},
            model="kimi-k2.5",
            finish_reason="stop",
        )
        mock_create_client.return_value = mock_client

        agent = CamelAgent(
            role="Analyst",
            goal="Analyze",
            backstory="Expert",
            kimi_client=mock_client,
        )
        response = agent.analyze("Review this code", code="def foo(): pass")

        assert isinstance(response, CamelAgentResponse)
        assert response.content == "Analysis result"
        assert response.role == "Analyst"

    @patch("code_graph_builder.rag.camel_agent.create_kimi_client")
    def test_analyze_error(self, mock_create_client):
        """Test analyze method with error."""
        mock_client = Mock()
        mock_client.chat_with_messages.side_effect = Exception("API error")
        mock_create_client.return_value = mock_client

        agent = CamelAgent(
            role="Analyst",
            goal="Analyze",
            backstory="Expert",
            kimi_client=mock_client,
        )
        response = agent.analyze("Review this")

        assert "Error" in response.content
        assert "error" in response.metadata

    @patch("code_graph_builder.rag.camel_agent.create_kimi_client")
    def test_review_code_general(self, mock_create_client):
        """Test general code review."""
        mock_client = Mock()
        mock_client.chat_with_messages.return_value = ChatResponse(
            content="Review result",
            usage={},
            model="kimi-k2.5",
            finish_reason="stop",
        )
        mock_create_client.return_value = mock_client

        agent = CamelAgent(
            role="Reviewer",
            goal="Review code",
            backstory="Code reviewer",
            kimi_client=mock_client,
        )
        response = agent.review_code("def foo(): pass", review_type="general")

        assert isinstance(response, CamelAgentResponse)
        mock_client.chat_with_messages.assert_called_once()

    @patch("code_graph_builder.rag.camel_agent.create_kimi_client")
    def test_review_code_security(self, mock_create_client):
        """Test security code review."""
        mock_client = Mock()
        mock_client.chat_with_messages.return_value = ChatResponse(
            content="Security review",
            usage={},
            model="kimi-k2.5",
            finish_reason="stop",
        )
        mock_create_client.return_value = mock_client

        agent = CamelAgent(
            role="Security Expert",
            goal="Find vulnerabilities",
            backstory="Security specialist",
            kimi_client=mock_client,
        )
        response = agent.review_code("def foo(): pass", review_type="security")

        assert isinstance(response, CamelAgentResponse)
        call_args = mock_client.chat_with_messages.call_args
        messages = call_args[0][0]
        assert any("security" in msg["content"].lower() for msg in messages)

    @patch("code_graph_builder.rag.camel_agent.create_kimi_client")
    def test_explain_code_brief(self, mock_create_client):
        """Test brief code explanation."""
        mock_client = Mock()
        mock_client.chat_with_messages.return_value = ChatResponse(
            content="Brief explanation",
            usage={},
            model="kimi-k2.5",
            finish_reason="stop",
        )
        mock_create_client.return_value = mock_client

        agent = CamelAgent(
            role="Explainer",
            goal="Explain code",
            backstory="Teacher",
            kimi_client=mock_client,
        )
        response = agent.explain_code("def foo(): pass", detail_level="brief")

        assert isinstance(response, CamelAgentResponse)
        call_args = mock_client.chat_with_messages.call_args
        messages = call_args[0][0]
        assert any("brief" in msg["content"].lower() for msg in messages)

    @patch("code_graph_builder.rag.camel_agent.create_kimi_client")
    def test_suggest_improvements(self, mock_create_client):
        """Test improvement suggestions."""
        mock_client = Mock()
        mock_client.chat_with_messages.return_value = ChatResponse(
            content="Suggestions",
            usage={},
            model="kimi-k2.5",
            finish_reason="stop",
        )
        mock_create_client.return_value = mock_client

        agent = CamelAgent(
            role="Improver",
            goal="Suggest improvements",
            backstory="Optimizer",
            kimi_client=mock_client,
        )
        response = agent.suggest_improvements(
            "def foo(): pass",
            focus_areas=["readability", "performance"],
        )

        assert isinstance(response, CamelAgentResponse)
        call_args = mock_client.chat_with_messages.call_args
        messages = call_args[0][0]
        assert any("readability" in msg["content"] for msg in messages)

    @patch("code_graph_builder.rag.camel_agent.create_kimi_client")
    def test_answer_question(self, mock_create_client):
        """Test answering questions."""
        mock_client = Mock()
        mock_client.chat_with_messages.return_value = ChatResponse(
            content="Answer",
            usage={},
            model="kimi-k2.5",
            finish_reason="stop",
        )
        mock_create_client.return_value = mock_client

        agent = CamelAgent(
            role="Helper",
            goal="Answer questions",
            backstory="Assistant",
            kimi_client=mock_client,
        )
        response = agent.answer_question("What is this?", code_context="def foo(): pass")

        assert isinstance(response, CamelAgentResponse)
        assert response.content == "Answer"


class TestMultiAgentRAG:
    """Tests for MultiAgentRAG."""

    @patch("code_graph_builder.rag.camel_agent.CamelAgent")
    def test_init(self, mock_agent_class):
        """Test initialization."""
        mock_rag_engine = Mock()
        mock_agent_instance = Mock()
        mock_agent_class.return_value = mock_agent_instance

        multi_agent = MultiAgentRAG(mock_rag_engine)

        assert multi_agent.rag_engine == mock_rag_engine
        # Should create 4 agents
        assert mock_agent_class.call_count == 4

    @patch("code_graph_builder.rag.camel_agent.CamelAgent")
    def test_analyze(self, mock_agent_class):
        """Test multi-agent analysis."""
        mock_rag_engine = Mock()
        mock_rag_engine.query.return_value = Mock(
            contexts=[Mock(format_context=Mock(return_value="context"))],
        )

        mock_agent = Mock()
        mock_agent.analyze.return_value = CamelAgentResponse(content="Analysis")
        mock_agent_class.return_value = mock_agent

        multi_agent = MultiAgentRAG(mock_rag_engine)
        results = multi_agent.analyze("Test query", analysis_types=["architecture"])

        assert "architecture" in results
        mock_rag_engine.query.assert_called_once_with("Test query")

    @patch("code_graph_builder.rag.camel_agent.CamelAgent")
    def test_comprehensive_review(self, mock_agent_class):
        """Test comprehensive review."""
        mock_rag_engine = Mock()
        mock_rag_engine.explain_code.return_value = Mock(
            contexts=[Mock(source_code="def foo(): pass")],
        )

        mock_agent = Mock()
        mock_agent.analyze.return_value = CamelAgentResponse(content="Analysis")
        mock_agent.review_code.return_value = CamelAgentResponse(content="Review")
        mock_agent.explain_code.return_value = CamelAgentResponse(content="Explanation")
        mock_agent_class.return_value = mock_agent

        multi_agent = MultiAgentRAG(mock_rag_engine)
        results = multi_agent.comprehensive_review("test.foo")

        assert "architecture" in results
        assert "security" in results
        assert "performance" in results
        assert "documentation" in results


class TestCreateCamelAgent:
    """Tests for create_camel_agent factory function."""

    @patch("code_graph_builder.rag.camel_agent.create_kimi_client")
    def test_create_agent(self, mock_create_client):
        """Test creating agent."""
        mock_client = Mock()
        mock_create_client.return_value = mock_client

        agent = create_camel_agent(
            role="Tester",
            goal="Test things",
            backstory="Testing expert",
        )

        assert isinstance(agent, CamelAgent)
        assert agent.role == "Tester"
