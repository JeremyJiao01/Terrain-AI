"""File-level incremental graph updates driven by git diff."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger

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
            logger.warning("delete {} nodes: {}", label, e)


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
        logger.warning("delete CALLS from callers: {}", e)


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
        6. Flush all pending writes.

    Note: API docs and vector index regeneration are NOT performed here (L4 concern).
    The L4 caller (_maybe_incremental_sync) handles the cascade after run() returns.
    """

    def run(
        self,
        changed_files: list[Path],
        repo_path: Path,
        db_path: Path,
    ) -> IncrementalResult:
        """Execute incremental graph update and return statistics.

        Note: API docs and vector index regeneration are NOT performed here.
        The L4 caller is responsible for cascade updates after this returns.
        """
        t0 = time.monotonic()

        from code_graph_builder.domains.core.graph.builder import CodeGraphBuilder
        from code_graph_builder.foundation.services.kuzu_service import KuzuIngestor

        # Separate existing files from deleted ones
        existing_files = [f for f in changed_files if f.is_file()]
        all_rel_paths = [str(f.relative_to(repo_path)) for f in changed_files]

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

        # Eagerly load parsers before touching the graph so a parser failure
        # does not leave the graph in an inconsistent (partially-deleted) state.
        builder._load_parsers()

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
