"""Tests for RAG module.

Tests the RAG engine, CAMEL agent integration, and end-to-end workflows.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ..rag import RAGConfig, create_rag_engine
from ..rag.camel_agent import CamelAgent, create_camel_agent
from ..rag.config import MoonshotConfig, RetrievalConfig
from ..rag.kimi_client import ChatResponse, KimiClient, create_kimi_client
from ..rag.markdown_generator import AnalysisResult, MarkdownGenerator, SourceReference
from ..rag.prompt_templates import CodeAnalysisPrompts, CodeContext, RAGPrompts


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_api_key() -> str:
    """Mock API key for testing."""
    return "sk-test-mock-key-12345"


@pytest.fixture
def moonshot_config(mock_api_key: str) -> MoonshotConfig:
    """Create Moonshot config for testing."""
    return MoonshotConfig(
        api_key=mock_api_key,
        model="kimi-k2.5",
        max_tokens=1024,
        temperature=0.5,
    )


@pytest.fixture
def rag_config(mock_api_key: str) -> RAGConfig:
    """Create RAG config for testing."""
    return RAGConfig(
        moonshot=MoonshotConfig(api_key=mock_api_key),
        retrieval=RetrievalConfig(semantic_top_k=5),
    )


@pytest.fixture
def sample_code_context() -> CodeContext:
    """Create sample code context."""
    return CodeContext(
        source_code="def add(a, b):\n    return a + b",
        file_path="src/math.py",
        qualified_name="math.add",
        entity_type="Function",
        docstring="Add two numbers.",
        callers=["math.calculate"],
        callees=[],
    )


@pytest.fixture
def mock_kimi_response() -> ChatResponse:
    """Create mock Kimi response."""
    return ChatResponse(
        content="This function adds two numbers together.",
        usage={"prompt_tokens": 50, "completion_tokens": 20, "total_tokens": 70},
        model="kimi-k2.5",
        finish_reason="stop",
    )


# =============================================================================
# Config Tests
# =============================================================================


class TestMoonshotConfig:
    """Test MoonshotConfig."""

    def test_config_creation(self, mock_api_key: str) -> None:
        """Test creating config."""
        config = MoonshotConfig(api_key=mock_api_key)
        assert config.api_key == mock_api_key
        assert config.model == "kimi-k2.5"
        assert config.base_url == "https://api.moonshot.cn/v1"

    def test_config_from_env(self, monkeypatch) -> None:
        """Test loading config from environment."""
        monkeypatch.setenv("MOONSHOT_API_KEY", "sk-env-key")
        monkeypatch.setenv("MOONSHOT_MODEL", "kimi-k2-turbo")

        config = MoonshotConfig()
        assert config.api_key == "sk-env-key"
        # Note: model defaults to kimi-k2.5 unless explicitly provided to __init__
        # The environment variable is only used by RAGConfig.from_env()

    def test_config_validation(self) -> None:
        """Test config validation."""
        config = MoonshotConfig(api_key="sk-valid")
        config.validate()  # Should not raise

        with pytest.raises(ValueError, match="API key is required"):
            invalid_config = MoonshotConfig(api_key=None)
            invalid_config.validate()

        with pytest.raises(ValueError, match="format is invalid"):
            invalid_config = MoonshotConfig(api_key="invalid")
            invalid_config.validate()

    def test_config_to_dict(self, mock_api_key: str) -> None:
        """Test config serialization."""
        config = MoonshotConfig(api_key=mock_api_key)
        data = config.to_dict()
        assert data["api_key"] == mock_api_key
        assert data["model"] == "kimi-k2.5"


class TestRAGConfig:
    """Test RAGConfig."""

    def test_config_creation(self, mock_api_key: str) -> None:
        """Test creating RAG config."""
        config = RAGConfig(
            moonshot=MoonshotConfig(api_key=mock_api_key),
        )
        assert config.moonshot.api_key == mock_api_key
        assert config.retrieval.semantic_top_k == 10

    def test_config_from_env(self, monkeypatch) -> None:
        """Test loading RAG config from environment."""
        monkeypatch.setenv("MOONSHOT_API_KEY", "sk-env-key")
        monkeypatch.setenv("RAG_SEMANTIC_TOP_K", "15")
        monkeypatch.setenv("RAG_VERBOSE", "true")

        config = RAGConfig.from_env()
        assert config.moonshot.api_key == "sk-env-key"
        assert config.retrieval.semantic_top_k == 15
        assert config.verbose is True


# =============================================================================
# KimiClient Tests
# =============================================================================


class TestKimiClient:
    """Test KimiClient."""

    def test_client_creation(self, mock_api_key: str) -> None:
        """Test creating client."""
        client = KimiClient(api_key=mock_api_key)
        assert client.api_key == mock_api_key
        assert client.model == "kimi-k2.5"

    def test_client_missing_api_key(self) -> None:
        """Test client creation without API key."""
        with pytest.raises(ValueError, match="API key is required"):
            KimiClient(api_key=None)

    @patch("requests.post")
    def test_chat_request(self, mock_post, mock_api_key: str) -> None:
        """Test chat request."""
        mock_post.return_value.json.return_value = {
            "choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "model": "kimi-k2.5",
        }
        mock_post.return_value.raise_for_status = MagicMock()

        client = KimiClient(api_key=mock_api_key)
        response = client.chat(query="Hello")

        assert response.content == "Hello"
        assert response.model == "kimi-k2.5"
        mock_post.assert_called_once()


# =============================================================================
# Prompt Templates Tests
# =============================================================================


class TestCodeContext:
    """Test CodeContext."""

    def test_context_creation(self) -> None:
        """Test creating context."""
        context = CodeContext(
            source_code="def foo(): pass",
            file_path="test.py",
            qualified_name="test.foo",
        )
        assert context.source_code == "def foo(): pass"
        assert context.file_path == "test.py"

    def test_context_formatting(self, sample_code_context: CodeContext) -> None:
        """Test context formatting."""
        formatted = sample_code_context.format_context()
        assert "Entity: math.add" in formatted
        assert "Type: Function" in formatted
        assert "def add(a, b):" in formatted
        assert "Called By:" in formatted


class TestCodeAnalysisPrompts:
    """Test CodeAnalysisPrompts."""

    def test_system_prompt(self) -> None:
        """Test getting system prompt."""
        prompts = CodeAnalysisPrompts()
        system = prompts.get_system_prompt()
        assert "expert code analyst" in system.lower()

    def test_format_explain_prompt(self, sample_code_context: CodeContext) -> None:
        """Test formatting explain prompt."""
        prompts = CodeAnalysisPrompts()
        prompt = prompts.format_explain_prompt(sample_code_context)
        assert "explain the following code" in prompt.lower()
        assert "def add(a, b):" in prompt

    def test_format_query_prompt(self, sample_code_context: CodeContext) -> None:
        """Test formatting query prompt."""
        prompts = CodeAnalysisPrompts()
        prompt = prompts.format_query_prompt(
            query="What does this do?",
            context=sample_code_context,
        )
        assert "What does this do?" in prompt
        assert "def add(a, b):" in prompt


class TestRAGPrompts:
    """Test RAGPrompts."""

    def test_format_rag_query(self, sample_code_context: CodeContext) -> None:
        """Test formatting RAG query."""
        prompts = RAGPrompts()
        system, user = prompts.format_rag_query(
            query="Explain this function",
            contexts=[sample_code_context],
        )
        assert "expert code analyst" in system.lower()
        assert "Explain this function" in user
        assert "math.add" in user

    def test_format_rag_query_no_results(self) -> None:
        """Test formatting RAG query with no results."""
        prompts = RAGPrompts()
        system, user = prompts.format_rag_query(
            query="Explain this",
            contexts=[],
        )
        assert "No relevant code" in user


# =============================================================================
# Markdown Generator Tests
# =============================================================================


class TestMarkdownGenerator:
    """Test MarkdownGenerator."""

    def test_generate_analysis_doc(self) -> None:
        """Test generating analysis document."""
        generator = MarkdownGenerator()
        result = AnalysisResult(
            query="What is this?",
            response="This is a test.",
            sources=[SourceReference(
                name="test",
                qualified_name="module.test",
                file_path="test.py",
            )],
        )
        markdown = generator.generate_analysis_doc("Test Analysis", result)
        assert "# Test Analysis" in markdown
        assert "What is this?" in markdown
        assert "This is a test." in markdown
        assert "module.test" in markdown

    def test_generate_code_documentation(
        self,
        sample_code_context: CodeContext,
    ) -> None:
        """Test generating code documentation."""
        generator = MarkdownGenerator()
        analysis = "This function adds numbers."
        markdown = generator.generate_code_documentation(
            sample_code_context,
            analysis,
        )
        assert "# math.add" in markdown
        assert "This function adds numbers." in markdown
        assert "def add(a, b):" in markdown


# =============================================================================
# CamelRAGAgent Tests
# =============================================================================


class TestCamelAgent:
    """Test CamelAgent."""

    def test_agent_creation(self) -> None:
        """Test creating agent."""
        with patch.object(KimiClient, "__init__", return_value=None):
            agent = CamelAgent(
                role="Code Analyst",
                goal="Analyze code",
                backstory="Expert programmer",
            )
            assert agent.role == "Code Analyst"
            assert agent.goal == "Analyze code"

    @patch.object(KimiClient, "chat_with_messages")
    def test_analyze(self, mock_chat) -> None:
        """Test code analysis."""
        mock_chat.return_value = ChatResponse(
            content="This adds two numbers.",
            usage={},
            model="kimi-k2.5",
            finish_reason="stop",
        )

        agent = CamelAgent(
            role="Code Analyst",
            goal="Analyze code",
            backstory="Expert programmer",
            kimi_client=KimiClient(api_key="sk-test"),
        )

        response = agent.analyze("Explain this function", code="def add(a, b): return a + b")
        assert "adds two numbers" in response.content

    def test_explain_code(self) -> None:
        """Test code explanation."""
        with patch.object(KimiClient, "chat_with_messages") as mock_chat:
            mock_chat.return_value = ChatResponse(
                content="This function adds two numbers.",
                usage={},
                model="kimi-k2.5",
                finish_reason="stop",
            )

            agent = CamelAgent(
                role="Code Analyst",
                goal="Analyze code",
                backstory="Expert programmer",
                kimi_client=KimiClient(api_key="sk-test"),
            )

            response = agent.explain_code("def add(a, b): return a + b")
            assert "adds two numbers" in response.content

    def test_review_code(self) -> None:
        """Test code review."""
        with patch.object(KimiClient, "chat_with_messages") as mock_chat:
            mock_chat.return_value = ChatResponse(
                content="Code looks good.",
                usage={},
                model="kimi-k2.5",
                finish_reason="stop",
            )

            agent = CamelAgent(
                role="Code Reviewer",
                goal="Review code",
                backstory="Senior engineer",
                kimi_client=KimiClient(api_key="sk-test"),
            )

            response = agent.review_code("def foo(): pass", review_type="general")
            assert "good" in response.content.lower() or "Error" not in response.content


# =============================================================================
# Factory Function Tests
# =============================================================================


def test_create_kimi_client(mock_api_key: str) -> None:
    """Test create_kimi_client factory."""
    with patch.object(KimiClient, "__init__", return_value=None):
        client = create_kimi_client(api_key=mock_api_key)
        assert isinstance(client, KimiClient)


def test_create_camel_agent() -> None:
    """Test create_camel_agent factory."""
    with patch.object(CamelAgent, "__init__", return_value=None) as mock_init:
        mock_init.return_value = None
        agent = create_camel_agent(
            role="Analyst",
            goal="Analyze",
            backstory="Expert",
        )
        assert isinstance(agent, CamelAgent)
