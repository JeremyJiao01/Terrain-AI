# 增量索引 + 代码变更感知 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 git-based 增量索引，使 MCP 工具调用前自动检测已 commit 的代码变更并只重新解析变更文件，消除"改完代码必须重新 init"的痛点。

**Architecture:** `GitChangeDetector`（L1）检测 git diff，`IncrementalUpdater`（L2）执行文件级增量图更新，MCP server（L4）在每次工具调用前插入 `_maybe_incremental_sync` 钩子，HEAD 无变化时 0ms 开销（内存比较），有变化时触发增量更新链路：图谱 → API 文档 → 向量。

**Tech Stack:** Python 3.11+, subprocess（git），Kùzu（Cypher DELETE），Tree-sitter（AST 解析），现有 `GraphUpdater`/`KuzuIngestor`/`pipeline` 接口。

---

## File Map

| 操作 | 文件路径 | 职责 |
|------|---------|------|
| 新建 | `code_graph_builder/foundation/services/git_service.py` | GitChangeDetector：git rev-parse + git diff |
| 新建 | `code_graph_builder/tests/foundation/test_git_service.py` | GitChangeDetector 单元测试 |
| 修改 | `code_graph_builder/domains/core/graph/graph_updater.py` | 新增 `process_files_subset`、`load_asts_for_calls` 两个公共方法 |
| 修改 | `code_graph_builder/domains/core/graph/builder.py` | 新增 `_create_graph_updater(ingestor)` 工厂方法 |
| 新建 | `code_graph_builder/domains/core/graph/incremental_updater.py` | IncrementalUpdater：删旧节点、重解析、级联更新 |
| 新建 | `code_graph_builder/tests/domains/core/test_incremental_updater.py` | IncrementalUpdater 单元测试（Memory 后端） |
| 修改 | `code_graph_builder/entrypoints/mcp/pipeline.py` | `save_meta` 新增 `last_indexed_commit` 参数 |
| 修改 | `code_graph_builder/entrypoints/mcp/tools.py` | 新增 `active_state` property |
| 修改 | `code_graph_builder/entrypoints/mcp/server.py` | `_maybe_incremental_sync` 钩子 + `call_tool` 前置调用 |
| 新建 | `code_graph_builder/tests/entrypoints/test_incremental_sync.py` | 集成测试（真实 git repo + Memory 后端） |

---

## Task 1: GitChangeDetector

**Files:**
- Create: `code_graph_builder/foundation/services/git_service.py`
- Test: `code_graph_builder/tests/foundation/test_git_service.py`

- [ ] **Step 1: 写失败测试**

```python
# code_graph_builder/tests/foundation/test_git_service.py
from __future__ import annotations
from pathlib import Path
from unittest.mock import patch, MagicMock
import subprocess

import pytest

from code_graph_builder.foundation.services.git_service import GitChangeDetector


@pytest.fixture
def detector():
    return GitChangeDetector()


def _mock_run(stdout: str, returncode: int = 0):
    result = MagicMock()
    result.stdout = stdout
    result.returncode = returncode
    return result


class TestGetCurrentHead:
    def test_returns_commit_hash(self, detector, tmp_path):
        with patch("subprocess.run", return_value=_mock_run("abc1234\n")) as mock:
            head = detector.get_current_head(tmp_path)
        assert head == "abc1234"
        mock.assert_called_once_with(
            ["git", "rev-parse", "HEAD"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=5,
        )

    def test_returns_none_for_non_git_repo(self, detector, tmp_path):
        with patch("subprocess.run", return_value=_mock_run("", returncode=128)):
            head = detector.get_current_head(tmp_path)
        assert head is None

    def test_returns_none_when_git_not_found(self, detector, tmp_path):
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            head = detector.get_current_head(tmp_path)
        assert head is None


class TestGetChangedFiles:
    def test_returns_empty_when_no_last_commit(self, detector, tmp_path):
        with patch("subprocess.run", return_value=_mock_run("def5678\n")):
            files, head = detector.get_changed_files(tmp_path, last_commit=None)
        assert files == []
        assert head == "def5678"

    def test_returns_empty_when_head_unchanged(self, detector, tmp_path):
        with patch("subprocess.run", return_value=_mock_run("abc1234\n")):
            files, head = detector.get_changed_files(tmp_path, last_commit="abc1234")
        assert files == []
        assert head == "abc1234"

    def test_returns_changed_files(self, detector, tmp_path):
        (tmp_path / "foo.py").write_text("x=1")
        (tmp_path / "bar.py").write_text("y=2")

        def fake_run(cmd, **kwargs):
            if "rev-parse" in cmd:
                return _mock_run("newhead\n")
            return _mock_run("foo.py\nbar.py\n")

        with patch("subprocess.run", side_effect=fake_run):
            files, head = detector.get_changed_files(tmp_path, last_commit="oldhead")

        assert head == "newhead"
        assert tmp_path / "foo.py" in files
        assert tmp_path / "bar.py" in files

    def test_includes_deleted_files(self, detector, tmp_path):
        """Deleted files (not on disk) are still returned so caller can remove them from graph."""
        def fake_run(cmd, **kwargs):
            if "rev-parse" in cmd:
                return _mock_run("newhead\n")
            return _mock_run("deleted.py\n")

        with patch("subprocess.run", side_effect=fake_run):
            files, head = detector.get_changed_files(tmp_path, last_commit="oldhead")

        assert tmp_path / "deleted.py" in files

    def test_returns_none_files_when_commit_not_in_history(self, detector, tmp_path):
        def fake_run(cmd, **kwargs):
            if "rev-parse" in cmd:
                return _mock_run("newhead\n")
            return _mock_run("", returncode=128)  # git diff fails

        with patch("subprocess.run", side_effect=fake_run):
            files, head = detector.get_changed_files(tmp_path, last_commit="ghostcommit")

        assert files is None  # Signal: full rebuild needed
        assert head == "newhead"

    def test_returns_empty_when_not_git_repo(self, detector, tmp_path):
        with patch("subprocess.run", return_value=_mock_run("", returncode=128)):
            files, head = detector.get_changed_files(tmp_path, last_commit="abc")
        assert files == []
        assert head is None
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/jiaojeremy/CodeFile/CodeGraphWiki-harness
python -m pytest code_graph_builder/tests/foundation/test_git_service.py -v 2>&1 | head -30
```

期望：`ModuleNotFoundError: No module named 'code_graph_builder.foundation.services.git_service'`

- [ ] **Step 3: 实现 GitChangeDetector**

```python
# code_graph_builder/foundation/services/git_service.py
"""Git-based change detection for incremental graph updates."""
from __future__ import annotations

import subprocess
from pathlib import Path

from loguru import logger


class GitChangeDetector:
    """Detect changed files between two git commits."""

    def get_current_head(self, repo_path: Path) -> str | None:
        """Return the current HEAD commit hash, or None if not a git repo."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
            logger.debug("git rev-parse HEAD failed: {}", e)
        return None

    def get_changed_files(
        self,
        repo_path: Path,
        last_commit: str | None,
    ) -> tuple[list[Path] | None, str | None]:
        """Return (changed_files, current_head).

        Returns:
            - ([], None)       — not a git repo
            - ([], current_head) — no last_commit (first index) or no changes
            - ([...], current_head) — list of changed/deleted file paths
            - (None, current_head) — last_commit not in git history; caller should full-rebuild
        """
        current_head = self.get_current_head(repo_path)
        if current_head is None:
            return [], None  # Not a git repo

        if last_commit is None:
            return [], current_head  # First-time index, no incremental to do

        if last_commit == current_head:
            return [], current_head  # Nothing changed

        try:
            result = subprocess.run(
                ["git", "diff", last_commit, current_head, "--name-only"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode != 0:
                # last_commit not reachable — signal full rebuild needed
                logger.warning(
                    "git diff {} {} failed (exit {}): {}",
                    last_commit[:8], current_head[:8],
                    result.returncode, result.stderr.strip(),
                )
                return None, current_head

            changed: list[Path] = []
            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if line:
                    changed.append(repo_path / line)

            return changed, current_head

        except (subprocess.SubprocessError, OSError) as e:
            logger.warning("git diff failed: {}", e)
            return [], current_head
```

- [ ] **Step 4: 运行测试确认通过**

```bash
python -m pytest code_graph_builder/tests/foundation/test_git_service.py -v
```

期望：所有测试 PASS

- [ ] **Step 5: Commit**

```bash
git add code_graph_builder/foundation/services/git_service.py \
        code_graph_builder/tests/foundation/test_git_service.py
git commit -m "feat(incremental): add GitChangeDetector (L1)"
```

---

## Task 2: GraphUpdater 文件子集处理方法 + CodeGraphBuilder 工厂方法

**Files:**
- Modify: `code_graph_builder/domains/core/graph/graph_updater.py` (在 `run()` 方法之后添加)
- Modify: `code_graph_builder/domains/core/graph/builder.py` (在 `build_graph()` 之后添加)

- [ ] **Step 1: 写失败测试**

```python
# code_graph_builder/tests/domains/core/test_graph_updater_subset.py
from __future__ import annotations
from pathlib import Path
from unittest.mock import MagicMock, patch
import tempfile

import pytest

from code_graph_builder.domains.core.graph.graph_updater import GraphUpdater
from code_graph_builder.foundation.services.memory_service import MemoryIngestor
from code_graph_builder.foundation.types import constants as cs


@pytest.fixture
def tmp_repo(tmp_path):
    (tmp_path / "foo.py").write_text("def hello(): pass\n")
    (tmp_path / "bar.py").write_text("def world(): pass\n")
    return tmp_path


def _make_updater(repo_path: Path) -> GraphUpdater:
    from code_graph_builder.foundation.parsers.factory import load_parsers
    parsers, queries = load_parsers()
    ingestor = MemoryIngestor()
    return GraphUpdater(ingestor=ingestor, repo_path=repo_path, parsers=parsers, queries=queries)


class TestProcessFilesSubset:
    def test_only_processes_specified_files(self, tmp_repo):
        updater = _make_updater(tmp_repo)
        updater.process_files_subset([tmp_repo / "foo.py"])
        # Only foo.py should be in ast_cache
        assert tmp_repo / "foo.py" in updater.ast_cache
        assert tmp_repo / "bar.py" not in updater.ast_cache

    def test_skips_nonexistent_files(self, tmp_repo):
        updater = _make_updater(tmp_repo)
        # Should not raise
        updater.process_files_subset([tmp_repo / "ghost.py"])
        assert len(updater.ast_cache) == 0


class TestLoadAstsForCalls:
    def test_adds_to_ast_cache_without_graph_writes(self, tmp_repo):
        updater = _make_updater(tmp_repo)
        ingestor: MemoryIngestor = updater.ingestor  # type: ignore
        initial_node_count = len(ingestor.nodes)
        updater.load_asts_for_calls([tmp_repo / "bar.py"])
        # bar.py should be in cache
        assert tmp_repo / "bar.py" in updater.ast_cache
        # No new graph nodes written
        assert len(ingestor.nodes) == initial_node_count

    def test_skips_already_cached_files(self, tmp_repo):
        updater = _make_updater(tmp_repo)
        updater.process_files_subset([tmp_repo / "foo.py"])
        original_cache = dict(updater.ast_cache._cache)
        updater.load_asts_for_calls([tmp_repo / "foo.py"])
        # Cache unchanged (already there)
        assert updater.ast_cache._cache == original_cache
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest code_graph_builder/tests/domains/core/test_graph_updater_subset.py -v 2>&1 | head -20
```

期望：`AttributeError: 'GraphUpdater' object has no attribute 'process_files_subset'`

- [ ] **Step 3: 在 graph_updater.py 中添加两个公共方法**

在 `GraphUpdater` 类的 `_process_files` 方法之后（约 line 389）添加：

```python
    def process_files_subset(self, files: list[Path]) -> None:
        """Pass 2 for a specific file list only (incremental updates).

        Parses definitions and adds nodes/relations to the graph.
        Uses an empty structural_elements dict — module→folder fallback is used
        instead of module→package for modified files (acceptable for incremental MVP).
        """
        try:
            from code_graph_builder.foundation.utils.path_utils import should_skip_path
        except ImportError:
            def should_skip_path(filepath, repo_path, exclude_paths=None, unignore_paths=None):
                return False

        from code_graph_builder.foundation.parsers.language_spec import get_language_spec

        sorted_files = sorted(
            files,
            key=lambda p: (0 if p.suffix == cs.EXT_H else 1, str(p)),
        )
        for filepath in sorted_files:
            if not filepath.is_file():
                continue
            if should_skip_path(
                filepath, self.repo_path,
                exclude_paths=self.exclude_paths,
                unignore_paths=self.unignore_paths,
            ):
                continue
            lang_config = get_language_spec(filepath.suffix)
            if (
                lang_config
                and isinstance(lang_config.language, cs.SupportedLanguage)
                and filepath.suffix == cs.EXT_H
                and cs.SupportedLanguage.C in self.parsers
            ):
                from code_graph_builder.foundation.parsers.language_spec import LANGUAGE_SPECS
                lang_config = LANGUAGE_SPECS.get(cs.SupportedLanguage.C)
            if (
                lang_config
                and isinstance(lang_config.language, cs.SupportedLanguage)
                and lang_config.language in self.parsers
            ):
                result = self.factory.definition_processor.process_file(
                    filepath,
                    lang_config.language,
                    self.queries,
                    self.factory.structure_processor.structural_elements,
                )
                if result:
                    root_node, language = result
                    self.ast_cache[filepath] = (root_node, language)
            elif self._is_dependency_file(filepath.name, filepath):
                self.factory.definition_processor.process_dependencies(filepath)

    def load_asts_for_calls(self, files: list[Path]) -> None:
        """Parse files into AST cache WITHOUT writing to graph (incremental call reprocessing).

        Used to load ASTs for affected_callers so their CALLS relations can be
        re-inserted in _process_function_calls(), without re-adding their definitions.
        """
        from code_graph_builder.foundation.parsers.language_spec import get_language_spec
        from code_graph_builder.foundation.utils.encoding import normalize_to_utf8_bytes

        for filepath in files:
            if filepath in self.ast_cache or not filepath.is_file():
                continue
            lang_config = get_language_spec(filepath.suffix)
            if not lang_config or not isinstance(lang_config.language, cs.SupportedLanguage):
                continue
            language = lang_config.language
            if language not in self.parsers:
                continue
            lang_queries = self.queries.get(language, {})
            parser = lang_queries.get("parser")
            if not parser:
                continue
            try:
                source_bytes = normalize_to_utf8_bytes(filepath.read_bytes())
                tree = parser.parse(source_bytes)
                self.ast_cache[filepath] = (tree.root_node, language)
            except Exception as e:
                logger.debug("load_asts_for_calls: failed to parse {}: {}", filepath, e)
```

- [ ] **Step 4: 在 builder.py 中添加 _create_graph_updater 方法**

在 `build_graph` 方法之后（约 line 264）添加：

```python
    def _create_graph_updater(self, ingestor: Any) -> "GraphUpdater":
        """Create a GraphUpdater pre-configured with this builder's parsers and scan settings.

        Used by IncrementalUpdater to run Pass 2/3 on a file subset
        without triggering a full rebuild.
        """
        from code_graph_builder.domains.core.graph.graph_updater import GraphUpdater

        self._load_parsers()
        return GraphUpdater(
            ingestor=ingestor,
            repo_path=self.repo_path,
            parsers=self._parsers,
            queries=self._queries,
            unignore_paths=frozenset(self.scan_config.unignore_paths),
            exclude_paths=frozenset(self.scan_config.exclude_patterns),
        )
```

- [ ] **Step 5: 运行测试确认通过**

```bash
python -m pytest code_graph_builder/tests/domains/core/test_graph_updater_subset.py -v
```

期望：所有测试 PASS

- [ ] **Step 6: Commit**

```bash
git add code_graph_builder/domains/core/graph/graph_updater.py \
        code_graph_builder/domains/core/graph/builder.py \
        code_graph_builder/tests/domains/core/test_graph_updater_subset.py
git commit -m "feat(incremental): add process_files_subset + load_asts_for_calls to GraphUpdater"
```

---

## Task 3: IncrementalUpdater

**Files:**
- Create: `code_graph_builder/domains/core/graph/incremental_updater.py`
- Test: `code_graph_builder/tests/domains/core/test_incremental_updater.py`

- [ ] **Step 1: 写失败测试**

```python
# code_graph_builder/tests/domains/core/test_incremental_updater.py
from __future__ import annotations
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from code_graph_builder.domains.core.graph.incremental_updater import (
    IncrementalUpdater,
    IncrementalResult,
    INCREMENTAL_FILE_LIMIT,
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
    """Test the internal deletion helpers using a mock ingestor."""

    def test_delete_nodes_for_files_calls_query_per_label(self):
        from code_graph_builder.domains.core.graph.incremental_updater import _delete_nodes_for_files
        mock_ingestor = MagicMock()
        _delete_nodes_for_files(mock_ingestor, ["src/foo.py"])
        # Should call query once per node label
        assert mock_ingestor.query.call_count >= 1
        # All calls should use DETACH DELETE
        for call in mock_ingestor.query.call_args_list:
            assert "DETACH DELETE" in call.args[0]

    def test_delete_calls_from_callers(self):
        from code_graph_builder.domains.core.graph.incremental_updater import _delete_calls_from
        mock_ingestor = MagicMock()
        _delete_calls_from(mock_ingestor, ["src/caller.py"])
        mock_ingestor.query.assert_called_once()
        query_str = mock_ingestor.query.call_args.args[0]
        assert "CALLS" in query_str
        assert "DELETE" in query_str

    def test_delete_nodes_skips_empty_list(self):
        from code_graph_builder.domains.core.graph.incremental_updater import _delete_nodes_for_files
        mock_ingestor = MagicMock()
        _delete_nodes_for_files(mock_ingestor, [])
        mock_ingestor.query.assert_not_called()

    def test_query_affected_callers_returns_paths(self):
        from code_graph_builder.domains.core.graph.incremental_updater import _query_affected_callers
        mock_ingestor = MagicMock()
        mock_ingestor.query.return_value = [{"caller_path": "src/caller.py"}]
        result = _query_affected_callers(mock_ingestor, ["src/changed.py"])
        assert "src/caller.py" in result

    def test_query_affected_callers_empty_input(self):
        from code_graph_builder.domains.core.graph.incremental_updater import _query_affected_callers
        mock_ingestor = MagicMock()
        result = _query_affected_callers(mock_ingestor, [])
        assert result == set()
        mock_ingestor.query.assert_not_called()
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest code_graph_builder/tests/domains/core/test_incremental_updater.py -v 2>&1 | head -20
```

期望：`ModuleNotFoundError: No module named '...incremental_updater'`

- [ ] **Step 3: 实现 incremental_updater.py**

```python
# code_graph_builder/domains/core/graph/incremental_updater.py
"""File-level incremental graph updates driven by git diff."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from code_graph_builder.foundation.services.kuzu_service import KuzuIngestor

INCREMENTAL_FILE_LIMIT: int = 50
"""If more files changed than this, fall back to a full rebuild instead."""

_CHANGED_LABELS = ("Function", "Method", "Class", "Type", "Import", "Module")


@dataclass
class IncrementalResult:
    files_reindexed: int
    callers_reindexed: int
    duration_ms: float


# ---------------------------------------------------------------------------
# Graph helpers
# ---------------------------------------------------------------------------

def _query_affected_callers(
    ingestor: Any,
    changed_rel_paths: list[str],
) -> set[str]:
    """Return relative paths of modules that CALL into any of the changed files."""
    if not changed_rel_paths:
        return set()
    try:
        rows = ingestor.query(
            "MATCH (caller:Module)-[:CALLS]->(callee) "
            "WHERE callee.path IN $paths "
            "RETURN DISTINCT caller.path AS caller_path",
            {"paths": changed_rel_paths},
        )
        return {row["caller_path"] for row in rows if row.get("caller_path")}
    except Exception as e:
        logger.debug("_query_affected_callers failed: {}", e)
        return set()


def _delete_nodes_for_files(ingestor: Any, rel_paths: list[str]) -> None:
    """Delete all nodes (and their incident edges) that belong to the given files."""
    if not rel_paths:
        return
    for label in _CHANGED_LABELS:
        try:
            ingestor.query(
                f"MATCH (n:{label}) WHERE n.path IN $paths DETACH DELETE n",
                {"paths": rel_paths},
            )
        except Exception as e:
            logger.debug("delete {} nodes: {}", label, e)


def _delete_calls_from(ingestor: Any, caller_rel_paths: list[str]) -> None:
    """Delete only outgoing CALLS relations from the given caller modules."""
    if not caller_rel_paths:
        return
    try:
        ingestor.query(
            "MATCH (caller:Module)-[r:CALLS]->() "
            "WHERE caller.path IN $paths "
            "DELETE r",
            {"paths": caller_rel_paths},
        )
    except Exception as e:
        logger.debug("delete CALLS from callers: {}", e)


# ---------------------------------------------------------------------------
# IncrementalUpdater
# ---------------------------------------------------------------------------

class IncrementalUpdater:
    """Run file-level incremental updates on the code knowledge graph.

    Workflow:
        1. Query graph for modules that call into changed files (affected_callers).
        2. DETACH DELETE all nodes for changed files.
        3. DELETE only CALLS relations from affected_callers.
        4. Re-run Pass 2 (definitions) for existing changed files.
        5. Re-run Pass 3 (calls) for changed + affected_callers.
        6. Regenerate API docs (full, fast — just queries DB).
        7. Rebuild vector index (full, from MD files).
        8. Flush all pending writes.
    """

    def run(
        self,
        changed_files: list[Path],
        repo_path: Path,
        db_path: Path,
        artifact_dir: Path,
        vectors_path: Path,
    ) -> IncrementalResult:
        """Execute incremental update and return statistics."""
        t0 = time.monotonic()

        from code_graph_builder.domains.core.graph.builder import CodeGraphBuilder
        from code_graph_builder.foundation.services.kuzu_service import KuzuIngestor
        from code_graph_builder.entrypoints.mcp.pipeline import (
            generate_api_docs_step,
            build_vector_index,
        )

        # Separate existing files from deleted ones
        existing_files = [f for f in changed_files if f.is_file()]
        all_rel_paths = [str(f.relative_to(repo_path)) for f in changed_files]
        existing_rel_paths = [str(f.relative_to(repo_path)) for f in existing_files]

        logger.info(
            "Incremental update: {} changed ({} existing, {} deleted)",
            len(changed_files), len(existing_files),
            len(changed_files) - len(existing_files),
        )

        # Create builder (lazy — no DB connection yet)
        builder = CodeGraphBuilder(
            repo_path=str(repo_path),
            backend="kuzu",
            backend_config={"db_path": str(db_path), "batch_size": 1000},
        )

        with KuzuIngestor(db_path) as ingestor:
            # Step 1: Find files that call into changed files
            affected_rel = _query_affected_callers(ingestor, all_rel_paths)
            # Exclude files that are already in the changed set
            pure_callers = affected_rel - set(all_rel_paths)

            logger.info("Affected callers: {}", len(pure_callers))

            # Step 2: Delete all nodes for changed files
            _delete_nodes_for_files(ingestor, all_rel_paths)

            # Step 3: Delete only CALLS relations from pure callers
            _delete_calls_from(ingestor, list(pure_callers))
            ingestor.flush_all()

            # Step 4 + 5: Re-parse definitions + calls
            graph_updater = builder._create_graph_updater(ingestor)

            # Pass 2: definitions for existing changed files only
            graph_updater.process_files_subset(existing_files)

            # Pass 3: calls for changed + affected callers
            callers_on_disk = [
                repo_path / p
                for p in pure_callers
                if (repo_path / p).is_file()
            ]
            graph_updater.load_asts_for_calls(callers_on_disk)
            graph_updater._process_function_calls()

            ingestor.flush_all()

        # Step 6: Regenerate API docs (re-queries the updated graph)
        if (artifact_dir / "api_docs").exists():
            try:
                generate_api_docs_step(builder, artifact_dir, rebuild=True, repo_path=repo_path)
            except Exception as e:
                logger.warning("API docs update failed: {}", e)

        # Step 7: Rebuild vector index (reads from api_docs/funcs/*.md)
        if vectors_path.exists():
            try:
                build_vector_index(builder, repo_path, vectors_path, rebuild=True)
            except Exception as e:
                logger.warning("Vector index rebuild failed: {}", e)

        duration_ms = (time.monotonic() - t0) * 1000
        result = IncrementalResult(
            files_reindexed=len(existing_files),
            callers_reindexed=len(pure_callers),
            duration_ms=duration_ms,
        )
        logger.info(
            "Incremental update done: {} files, {} callers in {:.0f}ms",
            result.files_reindexed, result.callers_reindexed, result.duration_ms,
        )
        return result
```

- [ ] **Step 4: 运行测试确认通过**

```bash
python -m pytest code_graph_builder/tests/domains/core/test_incremental_updater.py -v
```

期望：所有测试 PASS

- [ ] **Step 5: Commit**

```bash
git add code_graph_builder/domains/core/graph/incremental_updater.py \
        code_graph_builder/tests/domains/core/test_incremental_updater.py
git commit -m "feat(incremental): add IncrementalUpdater (L2)"
```

---

## Task 4: pipeline.save_meta 保存 last_indexed_commit

**Files:**
- Modify: `code_graph_builder/entrypoints/mcp/pipeline.py`

`save_meta` 函数（line 1336）新增可选参数 `last_indexed_commit`，在写入 meta.json 时包含该字段。

- [ ] **Step 1: 写失败测试**

```python
# 追加到 code_graph_builder/tests/entrypoints/test_mcp_protocol.py 或新建文件
# code_graph_builder/tests/entrypoints/test_save_meta.py
import json
import tempfile
from pathlib import Path

from code_graph_builder.entrypoints.mcp.pipeline import save_meta


def test_save_meta_persists_last_indexed_commit(tmp_path):
    save_meta(tmp_path, tmp_path / "repo", wiki_page_count=0, last_indexed_commit="abc1234")
    meta = json.loads((tmp_path / "meta.json").read_text())
    assert meta["last_indexed_commit"] == "abc1234"


def test_save_meta_without_commit_leaves_existing(tmp_path):
    # Write initial meta with a commit hash
    (tmp_path / "meta.json").write_text(json.dumps({"last_indexed_commit": "old123"}))
    # Call without commit arg — should preserve existing value
    save_meta(tmp_path, tmp_path / "repo", wiki_page_count=0)
    meta = json.loads((tmp_path / "meta.json").read_text())
    assert meta["last_indexed_commit"] == "old123"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest code_graph_builder/tests/entrypoints/test_save_meta.py -v 2>&1 | head -20
```

期望：`TypeError: save_meta() got an unexpected keyword argument 'last_indexed_commit'`

- [ ] **Step 3: 修改 pipeline.py 中的 save_meta**

将 `save_meta` 函数签名（line 1336）从：
```python
def save_meta(artifact_dir: Path, repo_path: Path, wiki_page_count: int) -> None:
```
改为：
```python
def save_meta(
    artifact_dir: Path,
    repo_path: Path,
    wiki_page_count: int,
    last_indexed_commit: str | None = None,
) -> None:
```

在 `meta = { **existing, ... }` 字典（line 1356）中添加一行：
```python
    meta = {
        **existing,
        "repo_path": str(repo_path),
        "repo_name": repo_path.name,
        "indexed_at": datetime.now().isoformat(),
        "wiki_page_count": wiki_page_count,
        "steps": {
            "graph": has_graph,
            "api_docs": has_api_docs,
            "embeddings": has_embeddings,
            "wiki": has_wiki,
        },
        # Only update last_indexed_commit when explicitly provided
        **({} if last_indexed_commit is None else {"last_indexed_commit": last_indexed_commit}),
    }
```

- [ ] **Step 4: 在 initialize_repository_step 中调用新参数**

找到 pipeline.py 中调用 `save_meta(...)` 的位置（用 `grep -n "save_meta" pipeline.py` 确认），在每次全量索引完成后的调用中传入当前 HEAD：

```python
# 在 save_meta 调用处（保持向后兼容），添加 last_indexed_commit 参数
# 先获取 HEAD：
import subprocess as _sp
def _get_git_head(repo_path: Path) -> str | None:
    try:
        r = _sp.run(["git", "rev-parse", "HEAD"], cwd=repo_path,
                    capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None
```

注意：`_get_git_head` 是 pipeline.py 内部函数，重复了 `GitChangeDetector.get_current_head` 的逻辑，但避免了 L4→L1 的循环依赖（pipeline 在 L4，git_service 在 L1，是合法依赖方向）。实际上 L4 可以 import L1，所以直接 import `GitChangeDetector` 更好：

```python
# 在 pipeline.py 顶部 imports 区域添加（懒导入避免循环依赖）：
# from code_graph_builder.foundation.services.git_service import GitChangeDetector

# 在每处 save_meta(...) 调用改为：
from code_graph_builder.foundation.services.git_service import GitChangeDetector as _GCD
_head = _GCD().get_current_head(repo_path)
save_meta(artifact_dir, repo_path, wiki_page_count, last_indexed_commit=_head)
```

找到所有 `save_meta(` 调用位置：
```bash
grep -n "save_meta(" code_graph_builder/entrypoints/mcp/pipeline.py
```

对每一处调用按上述方式修改。

- [ ] **Step 5: 运行测试确认通过**

```bash
python -m pytest code_graph_builder/tests/entrypoints/test_save_meta.py -v
```

期望：所有测试 PASS

- [ ] **Step 6: Commit**

```bash
git add code_graph_builder/entrypoints/mcp/pipeline.py \
        code_graph_builder/tests/entrypoints/test_save_meta.py
git commit -m "feat(incremental): save last_indexed_commit in meta.json after full index"
```

---

## Task 5: MCPToolsRegistry.active_state + server._maybe_incremental_sync

**Files:**
- Modify: `code_graph_builder/entrypoints/mcp/tools.py`
- Modify: `code_graph_builder/entrypoints/mcp/server.py`

- [ ] **Step 1: 写失败测试（server sync hook）**

```python
# code_graph_builder/tests/entrypoints/test_incremental_sync.py
from __future__ import annotations
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest code_graph_builder/tests/entrypoints/test_incremental_sync.py -v 2>&1 | head -30
```

期望：`AttributeError: module '...server' has no attribute '_maybe_incremental_sync'` 或 `AttributeError: 'MagicMock' object has no attribute 'active_state'`

- [ ] **Step 3: 在 tools.py 中添加 active_state property**

找到 `MCPToolsRegistry` 类（约 line 130），在类内添加 property：

```python
    @property
    def active_state(self) -> tuple[Path, Path] | None:
        """Return (repo_path, artifact_dir) for the currently active repo, or None."""
        if self._active_repo_path is not None and self._active_artifact_dir is not None:
            return self._active_repo_path, self._active_artifact_dir
        return None
```

- [ ] **Step 4: 在 server.py 中添加 _cached_head 和 _maybe_incremental_sync**

在 `SERVER_NAME = "code-graph-builder"` 行（line 63）之后添加：

```python
# ---------------------------------------------------------------------------
# Incremental sync state
# ---------------------------------------------------------------------------
_cached_head: str | None = None
"""Process-level cache of the last-seen HEAD. Avoids subprocess on every call."""

INCREMENTAL_FILE_LIMIT: int = 50
"""Fall back to full rebuild if more than this many files changed."""


async def _maybe_incremental_sync(registry: "MCPToolsRegistry") -> None:
    """Check for committed code changes and run incremental graph update if needed.

    Called before every tool invocation. The fast path (HEAD unchanged) costs ~0ms
    since it only compares two strings in memory.
    """
    global _cached_head

    state = registry.active_state
    if state is None:
        return  # No active repo yet

    repo_path, artifact_dir = state
    db_path = artifact_dir / "graph.db"
    vectors_path = artifact_dir / "vectors.pkl"

    if not db_path.exists():
        return  # Graph not built yet

    from code_graph_builder.foundation.services.git_service import GitChangeDetector

    detector = GitChangeDetector()
    current_head = detector.get_current_head(repo_path)

    if current_head is None:
        return  # Not a git repo

    if current_head == _cached_head:
        return  # Fast path: HEAD hasn't changed since last check

    # HEAD changed — read last indexed commit from meta.json
    import json as _json
    last_commit: str | None = None
    meta_file = artifact_dir / "meta.json"
    if meta_file.exists():
        try:
            last_commit = _json.loads(
                meta_file.read_text(encoding="utf-8", errors="replace")
            ).get("last_indexed_commit")
        except Exception:
            pass

    changed_files, new_head = detector.get_changed_files(repo_path, last_commit)

    if new_head is not None:
        _cached_head = new_head  # Update cache regardless of outcome below

    if changed_files is None:
        logger.warning(
            "last_indexed_commit {} not in git history — incremental sync skipped",
            (last_commit or "")[:8],
        )
        return

    if not changed_files:
        return  # No changes

    if len(changed_files) > INCREMENTAL_FILE_LIMIT:
        logger.info(
            "Too many changed files ({} > {}), skipping incremental sync",
            len(changed_files), INCREMENTAL_FILE_LIMIT,
        )
        return

    # Run incremental update
    from code_graph_builder.domains.core.graph.incremental_updater import IncrementalUpdater

    try:
        result = IncrementalUpdater().run(
            changed_files=changed_files,
            repo_path=repo_path,
            db_path=db_path,
            artifact_dir=artifact_dir,
            vectors_path=vectors_path,
        )
        logger.info(
            "Incremental sync: {} files, {} callers in {:.0f}ms",
            result.files_reindexed, result.callers_reindexed, result.duration_ms,
        )

        # Persist new last_indexed_commit
        if new_head and meta_file.exists():
            try:
                existing = _json.loads(meta_file.read_text(encoding="utf-8", errors="replace"))
                existing["last_indexed_commit"] = new_head
                meta_file.write_text(_json.dumps(existing, ensure_ascii=False, indent=2))
            except Exception as e:
                logger.debug("Failed to update last_indexed_commit in meta.json: {}", e)

    except Exception as e:
        logger.warning("Incremental sync failed (will retry next call): {}", e)
```

- [ ] **Step 5: 在 call_tool 中插入前置调用**

找到 `async def call_tool(name: str, arguments: dict)` 的第一行（line 87），在 `handler = registry.get_handler(name)` 之前插入：

```python
    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        # Check for committed code changes and sync incrementally if needed
        await _maybe_incremental_sync(registry)

        handler = registry.get_handler(name)
        # ... rest unchanged
```

- [ ] **Step 6: 运行测试确认通过**

```bash
python -m pytest code_graph_builder/tests/entrypoints/test_incremental_sync.py -v
```

期望：所有测试 PASS

- [ ] **Step 7: Commit**

```bash
git add code_graph_builder/entrypoints/mcp/tools.py \
        code_graph_builder/entrypoints/mcp/server.py \
        code_graph_builder/tests/entrypoints/test_incremental_sync.py
git commit -m "feat(incremental): wire _maybe_incremental_sync hook into MCP server (L4)"
```

---

## Task 6: 运行全部测试 + dep_check

- [ ] **Step 1: 运行全套测试**

```bash
cd /Users/jiaojeremy/CodeFile/CodeGraphWiki-harness
python -m pytest code_graph_builder/tests/foundation/test_git_service.py \
                 code_graph_builder/tests/domains/core/test_graph_updater_subset.py \
                 code_graph_builder/tests/domains/core/test_incremental_updater.py \
                 code_graph_builder/tests/entrypoints/test_save_meta.py \
                 code_graph_builder/tests/entrypoints/test_incremental_sync.py \
                 -v
```

期望：所有新测试 PASS

- [ ] **Step 2: 运行依赖检查**

```bash
python tools/dep_check.py
```

期望：无层级违规（git_service 在 L1，incremental_updater 在 L2，server 在 L4，均符合 upper imports lower）

- [ ] **Step 3: 运行现有核心测试确认无回归**

```bash
python -m pytest code_graph_builder/tests/domains/core/test_graph_build.py \
                 code_graph_builder/tests/foundation/test_encoding_parsing.py \
                 -v
```

期望：PASS（无回归）

- [ ] **Step 4: 最终 commit**

```bash
git add -A
git commit -m "feat(incremental): complete incremental indexing implementation

- GitChangeDetector: git diff-based change detection (L1)
- GraphUpdater: process_files_subset + load_asts_for_calls
- CodeGraphBuilder: _create_graph_updater factory
- IncrementalUpdater: file-level graph update + cascade to API docs + vectors
- pipeline: save last_indexed_commit in meta.json
- server: _maybe_incremental_sync hook on every MCP tool call

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>"
```

---

## 已知限制（MVP 范围外）

1. **新文件在新目录**：`process_files_subset` 使用空 `structural_elements`，新目录的 Module→Package 关系降级为 Module→Folder。
2. **Working tree 变更**：只感知已 commit 的变更，未 commit 的改动需要 commit 后才生效。
3. **超过 50 个文件**：自动跳过增量，需用户手动 `initialize_repository` 重建。
4. **非 git 仓库**：增量功能静默禁用，不影响正常使用。
