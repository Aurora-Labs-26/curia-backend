"""
tests/test_prompts_loader.py
Tests for core/prompts/loader.py — prompt file loading/saving/listing.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.prompts.loader import list_prompt_tasks, load_prompt, save_prompt, with_prompt


class TestLoadPrompt:
    def test_file_not_found(self, tmp_path):
        with patch("core.prompts.loader.PROMPTS_DIR", tmp_path):
            assert load_prompt("nonexistent") is None

    def test_empty_file(self, tmp_path):
        (tmp_path / "empty.txt").write_text("")
        with patch("core.prompts.loader.PROMPTS_DIR", tmp_path):
            assert load_prompt("empty") is None

    def test_whitespace_only(self, tmp_path):
        (tmp_path / "ws.txt").write_text("   \n  \n")
        with patch("core.prompts.loader.PROMPTS_DIR", tmp_path):
            assert load_prompt("ws") is None

    def test_valid_content(self, tmp_path):
        (tmp_path / "summary.txt").write_text("Summarize the article.\n")
        with patch("core.prompts.loader.PROMPTS_DIR", tmp_path):
            result = load_prompt("summary")
        assert result == "Summarize the article."


class TestSavePrompt:
    def test_creates_file(self, tmp_path):
        with patch("core.prompts.loader.PROMPTS_DIR", tmp_path):
            path = save_prompt("test_task", "Custom instructions")
        assert path.exists()
        assert path.read_text().strip() == "Custom instructions"

    def test_creates_dir_if_missing(self, tmp_path):
        new_dir = tmp_path / "subdir"
        with patch("core.prompts.loader.PROMPTS_DIR", new_dir):
            save_prompt("task", "Content")
        assert (new_dir / "task.txt").exists()

    def test_strips_and_adds_newline(self, tmp_path):
        with patch("core.prompts.loader.PROMPTS_DIR", tmp_path):
            save_prompt("task", "  Hello World  ")
        content = (tmp_path / "task.txt").read_text()
        assert content == "Hello World\n"


class TestWithPrompt:
    def test_no_custom_returns_original(self, tmp_path):
        with patch("core.prompts.loader.PROMPTS_DIR", tmp_path):

            class MySig:
                __doc__ = "Original docstring"

            result = with_prompt(MySig, "nonexistent")
        assert result is MySig

    def test_custom_same_as_docstring_returns_original(self, tmp_path):
        (tmp_path / "task.txt").write_text("Original docstring\n")
        with patch("core.prompts.loader.PROMPTS_DIR", tmp_path):

            class MySig:
                __doc__ = "Original docstring"

            result = with_prompt(MySig, "task")
        assert result is MySig

    def test_custom_different_returns_subclass(self, tmp_path):
        (tmp_path / "task.txt").write_text("New custom instructions\n")
        with patch("core.prompts.loader.PROMPTS_DIR", tmp_path):

            class MySig:
                __doc__ = "Original"

            result = with_prompt(MySig, "task")
        assert result is not MySig
        assert issubclass(result, MySig)
        assert result.__doc__ == "New custom instructions"

    def test_show_specific_override(self, tmp_path):
        (tmp_path / "task_clarity.txt").write_text("Clarity-specific\n")
        with patch("core.prompts.loader.PROMPTS_DIR", tmp_path):

            class MySig:
                __doc__ = "Original"

            result = with_prompt(MySig, "task", show="clarity")
        assert result.__doc__ == "Clarity-specific"

    def test_show_specific_not_found_falls_back(self, tmp_path):
        (tmp_path / "task.txt").write_text("Generic custom\n")
        with patch("core.prompts.loader.PROMPTS_DIR", tmp_path):

            class MySig:
                __doc__ = "Original"

            result = with_prompt(MySig, "task", show="nonexistent_show")
        assert result.__doc__ == "Generic custom"


class TestListPromptTasks:
    def test_empty_dir(self, tmp_path):
        with patch("core.prompts.loader.PROMPTS_DIR", tmp_path):
            assert list_prompt_tasks() == []

    def test_lists_txt_files(self, tmp_path):
        (tmp_path / "summary.txt").write_text("s")
        (tmp_path / "outline.txt").write_text("o")
        (tmp_path / "notes.md").write_text("n")  # not .txt
        with patch("core.prompts.loader.PROMPTS_DIR", tmp_path):
            result = list_prompt_tasks()
        assert result == ["outline", "summary"]  # sorted

    def test_dir_not_exists(self, tmp_path):
        missing = tmp_path / "nonexistent"
        with patch("core.prompts.loader.PROMPTS_DIR", missing):
            assert list_prompt_tasks() == []
