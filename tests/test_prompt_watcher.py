"""
tests/test_prompt_watcher.py
Tests for core/prompt_watcher.py — file hash tracking and change detection.

Verifies:
  - _hash_file returns MD5 hex for existing files
  - _hash_file returns "" for missing files
  - init_prompt_hashes populates internal state
  - check_prompt_changes detects new, modified, and unchanged files
  - check_prompt_changes returns list of changed task names
"""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _reset_hashes():
    """Clear module-level state between tests."""
    import core.prompt_watcher as pw
    pw._hashes.clear()
    yield
    pw._hashes.clear()


class TestHashFile:

    def test_returns_hex_for_existing_file(self, tmp_path):
        from core.prompt_watcher import _hash_file
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        result = _hash_file(f)
        assert isinstance(result, str)
        assert len(result) == 32

    def test_returns_empty_for_missing_file(self, tmp_path):
        from core.prompt_watcher import _hash_file
        result = _hash_file(tmp_path / "nonexistent.txt")
        assert result == ""

    def test_different_content_different_hash(self, tmp_path):
        from core.prompt_watcher import _hash_file
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("content one")
        f2.write_text("content two")
        assert _hash_file(f1) != _hash_file(f2)

    def test_same_content_same_hash(self, tmp_path):
        from core.prompt_watcher import _hash_file
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("identical")
        f2.write_text("identical")
        assert _hash_file(f1) == _hash_file(f2)


class TestInitPromptHashes:

    def test_tracks_existing_files(self, tmp_path):
        import core.prompt_watcher as pw
        (tmp_path / "outline.txt").write_text("outline prompt")
        (tmp_path / "transcript.txt").write_text("transcript prompt")
        pw.init_prompt_hashes(tmp_path)
        assert "outline" in pw._hashes
        assert "transcript" in pw._hashes
        assert len(pw._hashes) == 2

    def test_ignores_non_txt_files(self, tmp_path):
        import core.prompt_watcher as pw
        (tmp_path / "outline.txt").write_text("prompt")
        (tmp_path / "notes.md").write_text("not a prompt")
        (tmp_path / "script.py").write_text("not a prompt")
        pw.init_prompt_hashes(tmp_path)
        assert list(pw._hashes.keys()) == ["outline"]

    def test_empty_dir(self, tmp_path):
        import core.prompt_watcher as pw
        pw.init_prompt_hashes(tmp_path)
        assert pw._hashes == {}


class TestCheckPromptChanges:

    def test_detects_modified_file(self, tmp_path):
        import core.prompt_watcher as pw
        f = tmp_path / "outline.txt"
        f.write_text("version 1")
        pw.init_prompt_hashes(tmp_path)
        f.write_text("version 2")
        changed = pw.check_prompt_changes(tmp_path)
        assert changed == ["outline"]

    def test_no_changes_returns_empty(self, tmp_path):
        import core.prompt_watcher as pw
        (tmp_path / "outline.txt").write_text("stable")
        pw.init_prompt_hashes(tmp_path)
        changed = pw.check_prompt_changes(tmp_path)
        assert changed == []

    def test_new_file_not_flagged_as_change(self, tmp_path):
        import core.prompt_watcher as pw
        pw.init_prompt_hashes(tmp_path)
        (tmp_path / "new_prompt.txt").write_text("brand new")
        changed = pw.check_prompt_changes(tmp_path)
        assert changed == []
        assert "new_prompt" in pw._hashes

    def test_multiple_changes(self, tmp_path):
        import core.prompt_watcher as pw
        (tmp_path / "a.txt").write_text("v1")
        (tmp_path / "b.txt").write_text("v1")
        (tmp_path / "c.txt").write_text("v1")
        pw.init_prompt_hashes(tmp_path)
        (tmp_path / "a.txt").write_text("v2")
        (tmp_path / "c.txt").write_text("v2")
        changed = pw.check_prompt_changes(tmp_path)
        assert sorted(changed) == ["a", "c"]
