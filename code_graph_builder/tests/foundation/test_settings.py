"""Tests for ~/.claude/settings.json loader."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from code_graph_builder.foundation.utils.settings import load_settings


class TestLoadSettings:
    """Test load_settings with various file states."""

    def test_missing_file_returns_empty(self, tmp_path: Path):
        result = load_settings(tmp_path / "nonexistent.json")
        assert result == {}

    def test_valid_env_block_injects_vars(self, tmp_path: Path, monkeypatch):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "env": {
                "LLM_API_KEY": "sk-test-key",
                "LLM_BASE_URL": "https://test.example.com/v1",
            }
        }))

        # Ensure these are not in the environment
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.delenv("LLM_BASE_URL", raising=False)

        result = load_settings(settings_file)

        assert result["env"]["LLM_API_KEY"] == "sk-test-key"
        assert os.environ["LLM_API_KEY"] == "sk-test-key"
        assert os.environ["LLM_BASE_URL"] == "https://test.example.com/v1"

    def test_env_var_takes_precedence(self, tmp_path: Path, monkeypatch):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "env": {
                "LLM_API_KEY": "sk-from-settings",
            }
        }))

        # Pre-set the env var — should NOT be overwritten
        monkeypatch.setenv("LLM_API_KEY", "sk-from-env")

        load_settings(settings_file)

        assert os.environ["LLM_API_KEY"] == "sk-from-env"

    def test_malformed_json_returns_empty(self, tmp_path: Path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text("{ not valid json }")

        result = load_settings(settings_file)
        assert result == {}

    def test_non_dict_root_returns_empty(self, tmp_path: Path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps([1, 2, 3]))

        result = load_settings(settings_file)
        assert result == {}

    def test_no_env_block_is_fine(self, tmp_path: Path):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({"other_key": "value"}))

        result = load_settings(settings_file)
        assert result == {"other_key": "value"}

    def test_non_string_values_skipped(self, tmp_path: Path, monkeypatch):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "env": {
                "GOOD_KEY": "good-value",
                "BAD_KEY": 12345,
                "ALSO_BAD": None,
            }
        }))

        monkeypatch.delenv("GOOD_KEY", raising=False)
        monkeypatch.delenv("BAD_KEY", raising=False)
        monkeypatch.delenv("ALSO_BAD", raising=False)

        load_settings(settings_file)

        assert os.environ.get("GOOD_KEY") == "good-value"
        assert "BAD_KEY" not in os.environ
        assert "ALSO_BAD" not in os.environ

    def test_embedding_config(self, tmp_path: Path, monkeypatch):
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "env": {
                "DASHSCOPE_API_KEY": "sk-dash-test",
                "DASHSCOPE_BASE_URL": "https://custom.dashscope.com/api/v1",
            }
        }))

        monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
        monkeypatch.delenv("DASHSCOPE_BASE_URL", raising=False)

        load_settings(settings_file)

        assert os.environ["DASHSCOPE_API_KEY"] == "sk-dash-test"
        assert os.environ["DASHSCOPE_BASE_URL"] == "https://custom.dashscope.com/api/v1"

    def test_full_config(self, tmp_path: Path, monkeypatch):
        """Test a realistic full configuration."""
        settings_file = tmp_path / "settings.json"
        settings_file.write_text(json.dumps({
            "env": {
                "LLM_API_KEY": "sk-llm",
                "LLM_BASE_URL": "https://api.openai.com/v1",
                "LLM_MODEL": "gpt-4o",
                "DASHSCOPE_API_KEY": "sk-dash",
                "DASHSCOPE_BASE_URL": "https://dashscope.aliyuncs.com/api/v1",
            }
        }))

        for key in ["LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL",
                     "DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL"]:
            monkeypatch.delenv(key, raising=False)

        result = load_settings(settings_file)

        assert os.environ["LLM_API_KEY"] == "sk-llm"
        assert os.environ["LLM_MODEL"] == "gpt-4o"
        assert os.environ["DASHSCOPE_API_KEY"] == "sk-dash"
        assert len(result["env"]) == 5
