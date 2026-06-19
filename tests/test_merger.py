"""
tests/test_merger.py
Tests for core/audio/merger.py — paragraph merging with char range tracking.
All pure functions.
"""

from core.audio.merger import merge_paragraphs


class TestMergeParagraphs:
    def test_empty_transcript(self):
        assert merge_paragraphs([]) == []

    def test_single_line(self):
        result = merge_paragraphs([{"speaker": "kenji", "text": "Hello world."}])
        assert len(result) == 1
        assert result[0]["speaker"] == "kenji"
        assert result[0]["text"] == "Hello world."
        assert result[0]["line_indices"] == [0]
        assert result[0]["line_char_ranges"] == [(0, 12)]

    def test_same_speaker_merged(self):
        transcript = [
            {"speaker": "kenji", "text": "A."},
            {"speaker": "kenji", "text": "B."},
        ]
        result = merge_paragraphs(transcript)
        assert len(result) == 1
        assert result[0]["text"] == "A. B."
        assert result[0]["line_indices"] == [0, 1]

    def test_different_speakers_separate(self):
        transcript = [
            {"speaker": "kenji", "text": "Hello."},
            {"speaker": "arjun", "text": "Hi."},
        ]
        result = merge_paragraphs(transcript)
        assert len(result) == 2
        assert result[0]["speaker"] == "kenji"
        assert result[1]["speaker"] == "arjun"

    def test_speaker_case_normalized(self):
        transcript = [
            {"speaker": "KENJI", "text": "A."},
            {"speaker": "kenji", "text": "B."},
        ]
        result = merge_paragraphs(transcript)
        assert len(result) == 1  # treated as same speaker

    def test_empty_text_skipped(self):
        transcript = [
            {"speaker": "kenji", "text": "A."},
            {"speaker": "kenji", "text": ""},
            {"speaker": "kenji", "text": "B."},
        ]
        result = merge_paragraphs(transcript)
        assert len(result) == 1
        assert result[0]["text"] == "A. B."
        # Empty line skipped entirely — line indices are 0 and 2 (not 1)
        assert result[0]["line_indices"] == [0, 2]

    def test_whitespace_text_skipped(self):
        transcript = [
            {"speaker": "kenji", "text": "   "},
            {"speaker": "kenji", "text": "Hello."},
        ]
        result = merge_paragraphs(transcript)
        assert len(result) == 1
        assert result[0]["text"] == "Hello."

    def test_none_speaker_treated_as_empty(self):
        transcript = [
            {"text": "No speaker."},
            {"text": "Also no speaker."},
        ]
        result = merge_paragraphs(transcript)
        assert len(result) == 1  # both have empty speaker, merged

    def test_none_text_treated_as_empty(self):
        transcript = [
            {"speaker": "kenji", "text": None},
            {"speaker": "kenji", "text": "Real text."},
        ]
        result = merge_paragraphs(transcript)
        assert len(result) == 1
        assert result[0]["text"] == "Real text."

    def test_char_ranges_computed_correctly(self):
        transcript = [
            {"speaker": "kenji", "text": "AB"},   # len=2, range (0,2)
            {"speaker": "kenji", "text": "CDE"},   # len=3, range (3,6) -- +1 for space
            {"speaker": "kenji", "text": "F"},      # len=1, range (7,8) -- +1 for space
        ]
        result = merge_paragraphs(transcript)
        assert result[0]["text"] == "AB CDE F"
        assert result[0]["line_char_ranges"] == [(0, 2), (3, 6), (7, 8)]

    def test_alternating_speakers(self):
        transcript = [
            {"speaker": "kenji", "text": "A."},
            {"speaker": "arjun", "text": "B."},
            {"speaker": "kenji", "text": "C."},
        ]
        result = merge_paragraphs(transcript)
        assert len(result) == 3
        assert [r["speaker"] for r in result] == ["kenji", "arjun", "kenji"]

    def test_many_same_speaker_lines(self):
        transcript = [{"speaker": "kenji", "text": f"Line {i}."} for i in range(10)]
        result = merge_paragraphs(transcript)
        assert len(result) == 1
        assert result[0]["line_indices"] == list(range(10))

    def test_speaker_change_after_empty_lines(self):
        transcript = [
            {"speaker": "kenji", "text": "Hello."},
            {"speaker": "kenji", "text": ""},       # skipped
            {"speaker": "arjun", "text": "World."},
        ]
        result = merge_paragraphs(transcript)
        assert len(result) == 2
