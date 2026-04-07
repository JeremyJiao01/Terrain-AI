from __future__ import annotations
from pathlib import Path

import pytest

from code_graph_builder.domains.core.graph.graph_updater import GraphUpdater
from code_graph_builder.foundation.services.memory_service import MemoryIngestor


@pytest.fixture
def tmp_repo(tmp_path):
    (tmp_path / "foo.py").write_text("def hello(): pass\n")
    (tmp_path / "bar.py").write_text("def world(): pass\n")
    return tmp_path


def _make_updater(repo_path: Path) -> GraphUpdater:
    from code_graph_builder.foundation.parsers.parser_loader import load_parsers
    parsers, queries = load_parsers()
    ingestor = MemoryIngestor()
    return GraphUpdater(ingestor=ingestor, repo_path=repo_path, parsers=parsers, queries=queries)


class TestProcessFilesSubset:
    def test_only_processes_specified_files(self, tmp_repo):
        updater = _make_updater(tmp_repo)
        updater.process_files_subset([tmp_repo / "foo.py"])
        assert tmp_repo / "foo.py" in updater.ast_cache
        assert tmp_repo / "bar.py" not in updater.ast_cache

    def test_skips_nonexistent_files(self, tmp_repo):
        updater = _make_updater(tmp_repo)
        updater.process_files_subset([tmp_repo / "ghost.py"])
        assert len(updater.ast_cache) == 0


class TestLoadAstsForCalls:
    def test_adds_to_ast_cache_without_graph_writes(self, tmp_repo):
        updater = _make_updater(tmp_repo)
        ingestor: MemoryIngestor = updater.ingestor  # type: ignore
        initial_node_count = len(ingestor.nodes)
        updater.load_asts_for_calls([tmp_repo / "bar.py"])
        assert tmp_repo / "bar.py" in updater.ast_cache
        assert len(ingestor.nodes) == initial_node_count

    def test_skips_already_cached_files(self, tmp_repo):
        updater = _make_updater(tmp_repo)
        updater.process_files_subset([tmp_repo / "foo.py"])
        # Capture what's in cache before calling load_asts_for_calls
        cached_before = set(updater.ast_cache.cache.keys())
        assert tmp_repo / "foo.py" in cached_before
        updater.load_asts_for_calls([tmp_repo / "foo.py"])
        # File should still be in cache (not evicted), and no new entries added
        assert tmp_repo / "foo.py" in updater.ast_cache
        assert set(updater.ast_cache.cache.keys()) == cached_before
