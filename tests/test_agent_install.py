# tests/test_agent_install.py
from pathlib import Path
import pytest

AGENT_INSTALL = Path(__file__).parent.parent / "AGENT_INSTALL.md"


@pytest.fixture
def content():
    return AGENT_INSTALL.read_text(encoding="utf-8")


def test_file_exists():
    assert AGENT_INSTALL.exists(), "AGENT_INSTALL.md not found at repo root"


def test_all_blocks_present(content):
    for block in ["Block 1", "Block 2", "Block 3", "Block 4", "Block 4.5",
                  "Block 5", "Block 6", "Block 7"]:
        assert block in content, f"Missing {block}"


def test_windows_python_command(content):
    assert "py -3.11" in content


def test_macos_linux_python_command(content):
    assert "python3.11" in content


def test_windows_mcp_command(content):
    assert "cmd /c npx" in content


def test_windows_paths(content):
    assert "%USERPROFILE%" in content
    assert "%APPDATA%" in content


def test_mirror_sources(content):
    assert "pypi.tuna.tsinghua.edu.cn" in content
    assert "mirrors.aliyun.com" in content
    assert "pypi.douban.com" in content


def test_python_strict_version(content):
    assert "3.11" in content
    assert "not supported" in content.lower() or "stop" in content.lower()


def test_llm_api_test_present(content):
    assert "chat/completions" in content
    assert "Reply with OK" in content


def test_embedding_api_test_present(content):
    assert "/embeddings" in content
    assert '"hello"' in content


def test_terrain_env_path(content):
    assert "~/.terrain/.env" in content


def test_slash_command_paths(content):
    assert "~/.claude/commands/terrain.md" in content
    assert "~/.config/opencode/command/terrain.md" in content


def test_validation_commands(content):
    assert "terrain --version" in content
    assert "terrain status" in content
