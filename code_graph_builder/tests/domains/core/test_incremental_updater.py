from __future__ import annotations
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from code_graph_builder.domains.core.graph.incremental_updater import (
    IncrementalUpdater,
    IncrementalResult,
    INCREMENTAL_FILE_LIMIT,
    _delete_nodes_for_files,
    _delete_calls_from,
    _query_affected_callers,
)


class TestIncrementalResult:
    def test_dataclass_fields(self):
        r = IncrementalResult(files_reindexed=3, callers_reindexed=1, duration_ms=42.0)
        assert r.files_reindexed == 3
        assert r.callers_reindexed == 1
        assert r.duration_ms == 42.0


class TestIncrementalFileLimit:
    def test_default_limit(self):
        assert INCREMENTAL_FILE_LIMIT == 50


class TestDeleteHelpers:
    def test_delete_nodes_for_files_calls_query_per_label(self):
        mock_ingestor = MagicMock()
        _delete_nodes_for_files(mock_ingestor, ["src/foo.py"])
        assert mock_ingestor.query.call_count >= 1
        for call in mock_ingestor.query.call_args_list:
            assert "DETACH DELETE" in call.args[0]

    def test_delete_calls_from_callers(self):
        mock_ingestor = MagicMock()
        _delete_calls_from(mock_ingestor, ["src/caller.py"])
        mock_ingestor.query.assert_called_once()
        query_str = mock_ingestor.query.call_args.args[0]
        assert "CALLS" in query_str
        assert "DELETE" in query_str

    def test_delete_nodes_skips_empty_list(self):
        mock_ingestor = MagicMock()
        _delete_nodes_for_files(mock_ingestor, [])
        mock_ingestor.query.assert_not_called()

    def test_query_affected_callers_returns_paths(self):
        mock_ingestor = MagicMock()
        mock_ingestor.query.return_value = [{"caller_path": "src/caller.py"}]
        result = _query_affected_callers(mock_ingestor, ["src/changed.py"])
        assert "src/caller.py" in result

    def test_query_affected_callers_empty_input(self):
        mock_ingestor = MagicMock()
        result = _query_affected_callers(mock_ingestor, [])
        assert result == set()
        mock_ingestor.query.assert_not_called()
