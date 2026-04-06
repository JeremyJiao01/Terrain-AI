# code_graph_builder/tests/entrypoints/test_incremental_sync.py
from __future__ import annotations
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def reset_cached_head():
    from code_graph_builder.entrypoints.mcp import server as srv
    yield
    srv._cached_head = None


class TestMaybeIncrementalSync:
    """Unit tests for _maybe_incremental_sync using mocked dependencies."""

    def _make_registry(self, tmp_path: Path, last_commit: str | None = "old123") -> MagicMock:
        registry = MagicMock()
        registry.active_state = (tmp_path / "repo", tmp_path / "artifacts")
        # Create minimal artifact dir with meta.json and graph.db
        (tmp_path / "artifacts").mkdir()
        (tmp_path / "artifacts" / "graph.db").touch()
        if last_commit:
            (tmp_path / "artifacts" / "meta.json").write_text(
                json.dumps({"last_indexed_commit": last_commit})
            )
        return registry

    @pytest.mark.asyncio
    async def test_no_op_when_head_unchanged(self, tmp_path):
        from code_graph_builder.entrypoints.mcp import server as srv

        registry = self._make_registry(tmp_path, last_commit="abc1234")
        srv._cached_head = "abc1234"

        with patch(
            "code_graph_builder.foundation.services.git_service.GitChangeDetector.get_current_head",
            return_value="abc1234",
        ):
            await srv._maybe_incremental_sync(registry)
        # No incremental updater calls
        assert srv._cached_head == "abc1234"

    @pytest.mark.asyncio
    async def test_calls_incremental_updater_when_head_changes(self, tmp_path):
        from code_graph_builder.entrypoints.mcp import server as srv

        registry = self._make_registry(tmp_path, last_commit="old123")
        srv._cached_head = None

        mock_result = MagicMock(files_reindexed=2, callers_reindexed=0, duration_ms=50.0)

        def fake_get_current_head(self_inner, repo_path):
            return "new456"

        def fake_get_changed_files(self_inner, repo_path, last_commit):
            fake_file = tmp_path / "repo" / "foo.py"
            fake_file.parent.mkdir(exist_ok=True)
            fake_file.write_text("def f(): pass")
            return [fake_file], "new456"

        with (
            patch(
                "code_graph_builder.foundation.services.git_service.GitChangeDetector.get_current_head",
                fake_get_current_head,
            ),
            patch(
                "code_graph_builder.foundation.services.git_service.GitChangeDetector.get_changed_files",
                fake_get_changed_files,
            ),
            patch(
                "code_graph_builder.domains.core.graph.incremental_updater.IncrementalUpdater.run",
                return_value=mock_result,
            ),
        ):
            await srv._maybe_incremental_sync(registry)

        assert srv._cached_head == "new456"

    @pytest.mark.asyncio
    async def test_no_op_when_no_active_repo(self, tmp_path):
        from code_graph_builder.entrypoints.mcp import server as srv

        registry = MagicMock()
        registry.active_state = None
        srv._cached_head = None

        # Should not raise
        await srv._maybe_incremental_sync(registry)

    @pytest.mark.asyncio
    async def test_no_op_when_not_git_repo(self, tmp_path):
        from code_graph_builder.entrypoints.mcp import server as srv

        registry = self._make_registry(tmp_path)
        srv._cached_head = None

        with patch(
            "code_graph_builder.foundation.services.git_service.GitChangeDetector.get_current_head",
            return_value=None,
        ):
            await srv._maybe_incremental_sync(registry)
        # _cached_head stays None (no git = no-op)
        assert srv._cached_head is None
