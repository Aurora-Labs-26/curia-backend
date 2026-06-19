"""
tests/test_splitter.py
Unit tests for core/audio/splitter.py — pure text splitting + metadata remapping.
No mocks needed: all functions are pure.
"""

import pytest

from core.audio.splitter import (
    _split_at_sentences,
    _hard_split,
    _assign_line_metadata_to_chunk,
    split_into_segments,
)


# ---------------------------------------------------------------------------
# _hard_split
# ---------------------------------------------------------------------------


class TestHardSplit:
    def test_short_text_single_chunk(self):
        assert _hard_split("hello world", 100) == ["hello world"]

    def test_splits_on_word_boundary(self):
        text = "aaa bbb ccc ddd"
        chunks = _hard_split(text, 8)
        assert all(len(c) <= 8 for c in chunks)
        assert " ".join(chunks) == text

    def test_empty_text(self):
        assert _hard_split("", 100) == []

    def test_single_word(self):
        assert _hard_split("hello", 100) == ["hello"]


# ---------------------------------------------------------------------------
# _split_at_sentences
# ---------------------------------------------------------------------------


class TestSplitAtSentences:
    def test_single_short_sentence(self):
        assert _split_at_sentences("Hello world.", 100) == ["Hello world."]

    def test_multiple_sentences_fit(self):
        text = "First. Second. Third."
        result = _split_at_sentences(text, 100)
        assert result == ["First. Second. Third."]

    def test_splits_at_sentence_boundary(self):
        text = "First sentence. Second sentence. Third sentence."
        result = _split_at_sentences(text, 30)
        assert len(result) >= 2
        # Ensure no chunk exceeds limit
        for chunk in result:
            assert len(chunk) <= 30

    def test_single_long_sentence_falls_back_to_hard_split(self):
        text = "word " * 50  # no sentence punctuation
        result = _split_at_sentences(text.strip(), 20)
        assert len(result) > 1
        for chunk in result:
            assert len(chunk) <= 20

    def test_sentence_longer_than_max_uses_hard_split(self):
        text = "Short. " + "x " * 100 + "end. Final."
        result = _split_at_sentences(text, 30)
        assert len(result) > 1

    def test_empty_text(self):
        assert _split_at_sentences("", 100) == []

    def test_exclamation_and_question_splits(self):
        text = "Really? Yes! Okay."
        result = _split_at_sentences(text, 10)
        assert len(result) >= 2


# ---------------------------------------------------------------------------
# _assign_line_metadata_to_chunk
# ---------------------------------------------------------------------------


class TestAssignLineMetadata:
    def test_chunk_within_single_line(self):
        indices, ranges = _assign_line_metadata_to_chunk(
            chunk="hello",
            chunk_start=0,
            line_indices=[0],
            line_char_ranges=[(0, 10)],
        )
        assert indices == [0]
        assert ranges == [(0, 5)]

    def test_chunk_spans_multiple_lines(self):
        indices, ranges = _assign_line_metadata_to_chunk(
            chunk="x" * 20,
            chunk_start=5,
            line_indices=[0, 1, 2],
            line_char_ranges=[(0, 10), (10, 20), (20, 30)],
        )
        assert 0 in indices
        assert 1 in indices
        assert 2 in indices

    def test_no_overlap(self):
        indices, ranges = _assign_line_metadata_to_chunk(
            chunk="hi",
            chunk_start=100,
            line_indices=[0],
            line_char_ranges=[(0, 10)],
        )
        assert indices == []
        assert ranges == []

    def test_remaps_ranges_relative_to_chunk(self):
        indices, ranges = _assign_line_metadata_to_chunk(
            chunk="world",
            chunk_start=10,
            line_indices=[0],
            line_char_ranges=[(5, 20)],
        )
        assert ranges == [(0, 5)]  # clamped to chunk boundaries


# ---------------------------------------------------------------------------
# split_into_segments
# ---------------------------------------------------------------------------


class TestSplitIntoSegments:
    def test_empty_paragraphs(self):
        assert split_into_segments([]) == []

    def test_single_paragraph(self):
        paras = [{"speaker": "Kenji", "text": "Hello world.", "line_indices": [0], "line_char_ranges": [(0, 12)]}]
        result = split_into_segments(paras)
        assert len(result) == 1
        assert result[0]["speaker"] == "kenji"
        assert result[0]["text"] == "Hello world."

    def test_same_speaker_merged(self):
        paras = [
            {"speaker": "Kenji", "text": "Part one.", "line_indices": [0], "line_char_ranges": [(0, 9)]},
            {"speaker": "Kenji", "text": "Part two.", "line_indices": [1], "line_char_ranges": [(0, 9)]},
        ]
        result = split_into_segments(paras, max_chars=200)
        assert len(result) == 1
        assert "Part one." in result[0]["text"]
        assert "Part two." in result[0]["text"]

    def test_speaker_change_flushes(self):
        paras = [
            {"speaker": "Kenji", "text": "Hello.", "line_indices": [], "line_char_ranges": []},
            {"speaker": "Arjun", "text": "Hi.", "line_indices": [], "line_char_ranges": []},
        ]
        result = split_into_segments(paras)
        assert len(result) == 2
        assert result[0]["speaker"] == "kenji"
        assert result[1]["speaker"] == "arjun"

    def test_long_text_split(self):
        long_text = ". ".join(["Sentence number " + str(i) for i in range(50)]) + "."
        paras = [{"speaker": "Kenji", "text": long_text, "line_indices": [], "line_char_ranges": []}]
        result = split_into_segments(paras, max_chars=100)
        assert len(result) > 1
        for seg in result:
            assert len(seg["text"]) <= 100

    def test_empty_text_skipped(self):
        paras = [
            {"speaker": "Kenji", "text": "", "line_indices": [], "line_char_ranges": []},
            {"speaker": "Kenji", "text": "Hello.", "line_indices": [], "line_char_ranges": []},
        ]
        result = split_into_segments(paras)
        assert len(result) == 1

    def test_missing_speaker_treated_as_empty(self):
        paras = [{"text": "No speaker.", "line_indices": [], "line_char_ranges": []}]
        result = split_into_segments(paras)
        assert len(result) == 1
        assert result[0]["speaker"] == ""

    def test_merged_exceeds_max_triggers_flush(self):
        paras = [
            {"speaker": "Kenji", "text": "A" * 60, "line_indices": [], "line_char_ranges": []},
            {"speaker": "Kenji", "text": "B" * 60, "line_indices": [], "line_char_ranges": []},
        ]
        result = split_into_segments(paras, max_chars=100)
        assert len(result) == 2
