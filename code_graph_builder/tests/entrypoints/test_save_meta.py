import json
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


def test_save_meta_without_commit_and_no_existing(tmp_path):
    save_meta(tmp_path, tmp_path / "repo", wiki_page_count=0)
    meta = json.loads((tmp_path / "meta.json").read_text())
    assert "last_indexed_commit" not in meta
