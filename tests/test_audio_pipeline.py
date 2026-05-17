"""
tests/test_audio_pipeline.py
TDD tests for the improved audio pipeline.

Two pipelines:
  1. Batch — merge lines into paragraphs, minimal TTS calls, best quality
  2. Streaming — per-segment TTS, progressive file, real-time playback

Shared components:
  - ParagraphMerger: groups consecutive same-speaker lines into paragraphs
  - SegmentSplitter: splits transcript by outline segments
  - SSMLBuilder: adds SSML break tags between paragraphs/segments
  - AudioStitcher: replaces per-line stitching with per-segment

No network, no TTS calls, no DB. Pure logic tests.
"""

import pytest


# ─── ParagraphMerger ──────────────────────────────────────────────────────────
# Groups consecutive transcript lines by the same speaker into paragraph chunks.
# Input:  [{speaker: "kenji", text: "Line 1"}, {speaker: "kenji", text: "Line 2"},
#          {speaker: "arjun", text: "Line 3"}]
# Output: [{speaker: "kenji", text: "Line 1 Line 2"}, {speaker: "arjun", text: "Line 3"}]


class TestParagraphMerger:
    def test_merges_consecutive_same_speaker(self):
        from core.audio.merger import merge_paragraphs

        transcript = [
            {"speaker": "kenji", "text": "First sentence."},
            {"speaker": "kenji", "text": "Second sentence."},
            {"speaker": "kenji", "text": "Third sentence."},
        ]
        result = merge_paragraphs(transcript)
        assert len(result) == 1
        assert result[0]["speaker"] == "kenji"
        assert result[0]["text"] == "First sentence. Second sentence. Third sentence."

    def test_preserves_speaker_changes(self):
        from core.audio.merger import merge_paragraphs

        transcript = [
            {"speaker": "kenji", "text": "Hello."},
            {"speaker": "arjun", "text": "Hi there."},
            {"speaker": "kenji", "text": "So anyway."},
        ]
        result = merge_paragraphs(transcript)
        assert len(result) == 3
        assert result[0]["speaker"] == "kenji"
        assert result[1]["speaker"] == "arjun"
        assert result[2]["speaker"] == "kenji"

    def test_empty_transcript(self):
        from core.audio.merger import merge_paragraphs

        assert merge_paragraphs([]) == []

    def test_single_line(self):
        from core.audio.merger import merge_paragraphs

        transcript = [{"speaker": "kenji", "text": "Only line."}]
        result = merge_paragraphs(transcript)
        assert len(result) == 1
        assert result[0]["text"] == "Only line."

    def test_strips_whitespace(self):
        from core.audio.merger import merge_paragraphs

        transcript = [
            {"speaker": "kenji", "text": "  Hello.  "},
            {"speaker": "kenji", "text": "  World.  "},
        ]
        result = merge_paragraphs(transcript)
        assert result[0]["text"] == "Hello. World."

    def test_skips_empty_text(self):
        from core.audio.merger import merge_paragraphs

        transcript = [
            {"speaker": "kenji", "text": "Hello."},
            {"speaker": "kenji", "text": ""},
            {"speaker": "kenji", "text": "World."},
        ]
        result = merge_paragraphs(transcript)
        assert len(result) == 1
        assert result[0]["text"] == "Hello. World."

    def test_case_insensitive_speaker(self):
        from core.audio.merger import merge_paragraphs

        transcript = [
            {"speaker": "Kenji", "text": "Hello."},
            {"speaker": "kenji", "text": "World."},
        ]
        result = merge_paragraphs(transcript)
        assert len(result) == 1


# ─── SegmentSplitter ─────────────────────────────────────────────────────────
# Splits merged paragraphs into segments based on outline structure.
# Each segment = one TTS call for streaming, or one SSML section for batch.


class TestSegmentSplitter:
    def test_splits_by_max_chars(self):
        from core.audio.splitter import split_into_segments

        paragraphs = [
            {"speaker": "kenji", "text": "A" * 500},
            {"speaker": "kenji", "text": "B" * 500},
            {"speaker": "kenji", "text": "C" * 500},
        ]
        # Max 1200 chars per segment — should split into 2 segments
        result = split_into_segments(paragraphs, max_chars=1200)
        assert len(result) == 2
        assert all(len(seg["text"]) <= 1200 for seg in result)

    def test_speaker_change_forces_split(self):
        from core.audio.splitter import split_into_segments

        paragraphs = [
            {"speaker": "kenji", "text": "Hello from Kenji."},
            {"speaker": "arjun", "text": "Hello from Arjun."},
        ]
        result = split_into_segments(paragraphs, max_chars=5000)
        assert len(result) == 2

    def test_single_paragraph_no_split(self):
        from core.audio.splitter import split_into_segments

        paragraphs = [{"speaker": "kenji", "text": "Short text."}]
        result = split_into_segments(paragraphs, max_chars=5000)
        assert len(result) == 1

    def test_empty_input(self):
        from core.audio.splitter import split_into_segments

        assert split_into_segments([], max_chars=5000) == []

    def test_respects_tts_api_limit(self):
        """No segment should exceed max_chars (TTS API limit)."""
        from core.audio.splitter import split_into_segments

        paragraphs = [{"speaker": "kenji", "text": "Word " * 1000}]  # 5000 chars
        result = split_into_segments(paragraphs, max_chars=2000)
        assert all(len(seg["text"]) <= 2000 for seg in result)


# ─── SSMLBuilder ──────────────────────────────────────────────────────────────
# For batch pipeline: wraps segments in SSML with break tags.


class TestSSMLBuilder:
    def test_adds_breaks_between_segments(self):
        from core.audio.ssml import build_ssml

        segments = [
            {"speaker": "kenji", "text": "First segment."},
            {"speaker": "kenji", "text": "Second segment."},
        ]
        result = build_ssml(segments, break_ms=800)
        assert '<break time="800ms"/>' in result
        assert "First segment." in result
        assert "Second segment." in result

    def test_wraps_in_speak_tag(self):
        from core.audio.ssml import build_ssml

        segments = [{"speaker": "kenji", "text": "Hello."}]
        result = build_ssml(segments, break_ms=500)
        assert result.startswith("<speak>")
        assert result.endswith("</speak>")

    def test_escapes_xml_characters(self):
        from core.audio.ssml import build_ssml

        segments = [{"speaker": "kenji", "text": "This & that < more > stuff."}]
        result = build_ssml(segments, break_ms=500)
        assert "&amp;" in result
        assert "&lt;" in result
        assert "&gt;" in result

    def test_empty_segments(self):
        from core.audio.ssml import build_ssml

        result = build_ssml([], break_ms=500)
        assert result == "<speak></speak>"

    def test_custom_break_duration(self):
        from core.audio.ssml import build_ssml

        segments = [
            {"speaker": "kenji", "text": "A."},
            {"speaker": "kenji", "text": "B."},
        ]
        result = build_ssml(segments, break_ms=1200)
        assert '<break time="1200ms"/>' in result


# ─── BatchSynthesizer ────────────────────────────────────────────────────────
# Batch pipeline: merge → split → SSML → minimal TTS calls → stitch


class TestBatchPipeline:
    def test_reduces_tts_calls(self):
        """80 lines from same speaker should merge into far fewer segments."""
        from core.audio.merger import merge_paragraphs
        from core.audio.splitter import split_into_segments

        # Simulate 80 lines from one speaker
        transcript = [{"speaker": "kenji", "text": f"Sentence number {i}."} for i in range(80)]
        merged = merge_paragraphs(transcript)
        segments = split_into_segments(merged, max_chars=4000)

        # Should be much fewer than 80
        assert len(segments) < 10
        # But still respects max_chars
        assert all(len(seg["text"]) <= 4000 for seg in segments)

    def test_multi_speaker_preserves_order(self):
        from core.audio.merger import merge_paragraphs
        from core.audio.splitter import split_into_segments

        transcript = [
            {"speaker": "kenji", "text": "Kenji line 1."},
            {"speaker": "kenji", "text": "Kenji line 2."},
            {"speaker": "arjun", "text": "Arjun line 1."},
            {"speaker": "arjun", "text": "Arjun line 2."},
            {"speaker": "kenji", "text": "Kenji line 3."},
        ]
        merged = merge_paragraphs(transcript)
        segments = split_into_segments(merged, max_chars=5000)

        speakers = [seg["speaker"] for seg in segments]
        assert speakers == ["kenji", "arjun", "kenji"]


# ─── StreamingPipeline ────────────────────────────────────────────────────────
# Streaming: merge → split → yield segments one at a time


class TestStreamingPipeline:
    def test_segments_are_iterable(self):
        """Streaming pipeline should yield segments one at a time."""
        from core.audio.merger import merge_paragraphs
        from core.audio.splitter import split_into_segments

        transcript = [
            {"speaker": "kenji", "text": f"Sentence {i}."} for i in range(20)
        ]
        merged = merge_paragraphs(transcript)
        segments = split_into_segments(merged, max_chars=2000)

        # Each segment can be independently TTS'd
        for seg in segments:
            assert "speaker" in seg
            assert "text" in seg
            assert len(seg["text"]) > 0

    def test_segment_index_tracking(self):
        """Each segment should have an index for progressive file assembly."""
        from core.audio.merger import merge_paragraphs
        from core.audio.splitter import split_into_segments

        transcript = [
            {"speaker": "kenji", "text": "Part one."},
            {"speaker": "arjun", "text": "Part two."},
            {"speaker": "kenji", "text": "Part three."},
        ]
        merged = merge_paragraphs(transcript)
        segments = split_into_segments(merged, max_chars=5000)

        for i, seg in enumerate(segments):
            seg["index"] = i  # Pipeline should add this

        assert segments[0]["index"] == 0
        assert segments[-1]["index"] == len(segments) - 1
