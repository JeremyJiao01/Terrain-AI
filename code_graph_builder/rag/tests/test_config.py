"""Tests for RAG configuration."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from code_graph_builder.rag.config import (
    MoonshotConfig,
    OutputConfig,
    RAGConfig,
    RetrievalConfig,
)


class TestMoonshotConfig:
    """Tests for MoonshotConfig."""

    def test_default_values(self):
        """Test default configuration values."""
        config = MoonshotConfig(api_key="sk-test")
        assert config.model == "kimi-k2.5"
        assert config.base_url == "https://api.moonshot.cn/v1"
        assert config.max_tokens == 4096
        assert config.temperature == 0.7
        assert config.timeout == 120

    def test_custom_values(self):
        """Test custom configuration values."""
        config = MoonshotConfig(
            api_key="sk-test",
            model="kimi-k2.5",
            base_url="https://custom.api.com",
            max_tokens=2048,
            temperature=0.5,
            timeout=60,
        )
        assert config.model == "kimi-k2.5"
        assert config.base_url == "https://custom.api.com"
        assert config.max_tokens == 2048
        assert config.temperature == 0.5
        assert config.timeout == 60

    def test_api_key_from_env(self, monkeypatch):
        """Test loading API key from environment."""
        monkeypatch.setenv("MOONSHOT_API_KEY", "sk-from-env")
        config = MoonshotConfig()
        assert config.api_key == "sk-from-env"

    def test_validate_missing_api_key(self):
        """Test validation fails with missing API key."""
        config = MoonshotConfig(api_key=None)
        with pytest.raises(ValueError, match="API key is required"):
            config.validate()

    def test_validate_invalid_api_key_format(self):
        """Test validation fails with invalid API key format."""
        config = MoonshotConfig(api_key="invalid-key")
        with pytest.raises(ValueError, match="start with 'sk-'"):
            config.validate()

    def test_validate_invalid_temperature(self):
        """Test validation fails with invalid temperature."""
        config = MoonshotConfig(api_key="sk-test", temperature=3.0)
        with pytest.raises(ValueError, match="between 0 and 2"):
            config.validate()

    def test_to_dict(self):
        """Test conversion to dictionary."""
        config = MoonshotConfig(api_key="sk-test")
        data = config.to_dict()
        assert data["model"] == "kimi-k2.5"
        assert data["api_key"] == "sk-test"
        assert "base_url" in data


class TestRetrievalConfig:
    """Tests for RetrievalConfig."""

    def test_default_values(self):
        """Test default configuration values."""
        config = RetrievalConfig()
        assert config.semantic_top_k == 10
        assert config.graph_max_depth == 2
        assert config.include_callers is True
        assert config.include_callees is True
        assert config.include_related is True
        assert config.max_context_tokens == 8000
        assert config.code_chunk_size == 2000

    def test_custom_values(self):
        """Test custom configuration values."""
        config = RetrievalConfig(
            semantic_top_k=20,
            graph_max_depth=3,
            include_callers=False,
            include_callees=False,
            include_related=False,
        )
        assert config.semantic_top_k == 20
        assert config.graph_max_depth == 3
        assert config.include_callers is False

    def test_to_dict(self):
        """Test conversion to dictionary."""
        config = RetrievalConfig()
        data = config.to_dict()
        assert "semantic_top_k" in data
        assert "graph_max_depth" in data


class TestOutputConfig:
    """Tests for OutputConfig."""

    def test_default_values(self):
        """Test default configuration values."""
        config = OutputConfig()
        assert config.format == "markdown"
        assert config.include_source_links is True
        assert config.include_code_snippets is True
        assert isinstance(config.output_dir, Path)

    def test_custom_output_dir(self):
        """Test custom output directory."""
        config = OutputConfig(output_dir="/custom/path")
        assert isinstance(config.output_dir, Path)
        assert str(config.output_dir) == "/custom/path"

    def test_to_dict(self):
        """Test conversion to dictionary."""
        config = OutputConfig()
        data = config.to_dict()
        assert data["format"] == "markdown"
        assert "output_dir" in data


class TestRAGConfig:
    """Tests for RAGConfig."""

    def test_default_values(self):
        """Test default configuration values."""
        config = RAGConfig(moonshot=MoonshotConfig(api_key="sk-test"))
        assert isinstance(config.moonshot, MoonshotConfig)
        assert isinstance(config.retrieval, RetrievalConfig)
        assert isinstance(config.output, OutputConfig)
        assert config.verbose is False

    def test_from_env(self, monkeypatch):
        """Test creating config from environment variables."""
        monkeypatch.setenv("MOONSHOT_API_KEY", "sk-env-key")
        monkeypatch.setenv("MOONSHOT_MODEL", "kimi-k2.5")
        monkeypatch.setenv("RAG_SEMANTIC_TOP_K", "15")
        monkeypatch.setenv("RAG_OUTPUT_FORMAT", "json")
        monkeypatch.setenv("RAG_VERBOSE", "true")

        config = RAGConfig.from_env()
        assert config.moonshot.api_key == "sk-env-key"
        assert config.moonshot.model == "kimi-k2.5"
        assert config.retrieval.semantic_top_k == 15
        assert config.output.format == "json"
        assert config.verbose is True

    def test_validate(self):
        """Test configuration validation."""
        config = RAGConfig(moonshot=MoonshotConfig(api_key="sk-test"))
        config.validate()  # Should not raise

    def test_to_dict(self):
        """Test conversion to dictionary."""
        config = RAGConfig(moonshot=MoonshotConfig(api_key="sk-test"))
        data = config.to_dict()
        assert "moonshot" in data
        assert "retrieval" in data
        assert "output" in data
        assert "verbose" in data
