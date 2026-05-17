"""
tests/test_audio_stitcher.py
TDD tests for the new segment-based audio stitcher that replaces per-line stitching.

Tests the integration: transcript → merge → split → synthesize per-segment → stitch MP3.
Uses mocked TTS to avoid real API calls.
"""

import json
import wave
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ─── SegmentStitcher ──────────────────────────────────────────────────────────
# Replaces the old per-line loop with merge → split → TTS per-segment → stitch.


class TestSegmentStitcher:

    def _make_transcript(self, n_lines: int, speaker: str = "kenji") -> list[dict]:
        return [{"speaker": speaker, "text": f"Sentence number {i}."} for i in range(n_lines)]

    def _make_multi_speaker(self) -> list[dict]:
        return [
            {"speaker": "kenji", "text": "Hello from Kenji."},
            {"speaker": "kenji", "text": "I have more to say."},
            {"speaker": "arjun", "text": "Hello from Arjun."},
            {"speaker": "arjun", "text": "I also have more."},
            {"speaker": "kenji", "text": "Back to Kenji."},
        ]

    def test_fewer_tts_calls_than_lines(self):
        """50 same-speaker lines should produce far fewer TTS calls than 50."""
        from core.audio.stitcher import prepare_segments

        transcript = self._make_transcript(50)
        segments = prepare_segments(transcript)
        assert len(segments) < 10
        assert len(segments) >= 1

    def test_multi_speaker_segments(self):
        """Speaker changes should create segment boundaries."""
        from core.audio.stitcher import prepare_segments

        transcript = self._make_multi_speaker()
        segments = prepare_segments(transcript)
        speakers = [s["speaker"] for s in segments]
        assert speakers == ["kenji", "arjun", "kenji"]

    def test_prepare_segments_preserves_all_text(self):
        """No text should be lost during merge + split."""
        from core.audio.stitcher import prepare_segments

        transcript = self._make_transcript(20)
        all_original_text = " ".join(line["text"] for line in transcript)
        segments = prepare_segments(transcript)
        all_segment_text = " ".join(seg["text"] for seg in segments)

        # Every original sentence should appear in the output
        for line in transcript:
            assert line["text"] in all_segment_text

    def test_synthesize_segments_calls_tts_per_segment(self, tmp_path):
        """TTS should be called once per segment, not once per line."""
        from core.audio.stitcher import prepare_segments, synthesize_segments

        transcript = self._make_transcript(30)
        segments = prepare_segments(transcript)
        n_segments = len(segments)

        call_count = 0

        def mock_synthesize(text, speaker, output_path):
            nonlocal call_count
            call_count += 1
            # Write a valid silent WAV
            _write_test_wav(output_path)

        wav_paths = synthesize_segments(
            segments, tmp_path, synthesize_fn=mock_synthesize
        )
        assert call_count == n_segments
        assert len(wav_paths) == n_segments
        assert all(Path(p).exists() for p in wav_paths)

    def test_stitch_produces_wav(self, tmp_path):
        """Stitching segments should produce a valid WAV file."""
        from core.audio.stitcher import stitch_wavs

        # Create 3 test WAV files
        wav_paths = []
        for i in range(3):
            p = str(tmp_path / f"seg_{i}.wav")
            _write_test_wav(p, duration_seconds=0.5)
            wav_paths.append(p)

        output = str(tmp_path / "stitched.wav")
        stitch_wavs(wav_paths, output, gap_ms=200)
        assert Path(output).exists()

        with wave.open(output, "rb") as w:
            assert w.getnchannels() == 1
            assert w.getsampwidth() == 2
            # 3 clips of 0.5s + 2 gaps of 0.2s = ~1.9s at 22050Hz
            assert w.getnframes() > 22050  # at least 1 second

    def test_stitch_with_gap(self, tmp_path):
        """Gap duration should affect total output length."""
        from core.audio.stitcher import stitch_wavs

        wav_paths = []
        for i in range(2):
            p = str(tmp_path / f"seg_{i}.wav")
            _write_test_wav(p, duration_seconds=0.5)
            wav_paths.append(p)

        out_small_gap = str(tmp_path / "small_gap.wav")
        out_big_gap = str(tmp_path / "big_gap.wav")

        stitch_wavs(wav_paths, out_small_gap, gap_ms=100)
        stitch_wavs(wav_paths, out_big_gap, gap_ms=1000)

        with wave.open(out_small_gap, "rb") as w1, wave.open(out_big_gap, "rb") as w2:
            assert w2.getnframes() > w1.getnframes()

    def test_full_pipeline_mock(self, tmp_path):
        """Full pipeline: transcript → segments → synthesize → stitch → output."""
        from core.audio.stitcher import prepare_segments, synthesize_segments, stitch_wavs

        transcript = self._make_multi_speaker()
        segments = prepare_segments(transcript)

        def mock_synthesize(text, speaker, output_path):
            _write_test_wav(output_path, duration_seconds=0.3)

        wav_paths = synthesize_segments(segments, tmp_path, synthesize_fn=mock_synthesize)
        output = str(tmp_path / "episode.wav")
        stitch_wavs(wav_paths, output, gap_ms=300)

        assert Path(output).exists()
        with wave.open(output, "rb") as w:
            assert w.getnframes() > 0

    def test_empty_transcript(self, tmp_path):
        """Empty transcript should produce empty segments."""
        from core.audio.stitcher import prepare_segments

        assert prepare_segments([]) == []


# ─── Batch-specific: SSML path ───────────────────────────────────────────────


class TestBatchSSMLStitcher:
    def test_build_batch_ssml_from_transcript(self):
        """Full batch path: transcript → merge → split → SSML."""
        from core.audio.stitcher import prepare_segments
        from core.audio.ssml import build_ssml

        # Multi-speaker so merge produces multiple segments
        transcript = [
            {"speaker": "kenji", "text": "Kenji opens."},
            {"speaker": "arjun", "text": "Arjun responds."},
            {"speaker": "kenji", "text": "Kenji closes."},
        ]
        segments = prepare_segments(transcript)
        ssml = build_ssml(segments, break_ms=600)

        assert "<speak>" in ssml
        assert '<break time="600ms"/>' in ssml
        assert "Kenji opens." in ssml
        assert "Arjun responds." in ssml
        assert "Kenji closes." in ssml


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _write_test_wav(path: str, duration_seconds: float = 1.0):
    """Write a valid silent WAV file for testing."""
    sample_rate = 22050
    num_samples = int(sample_rate * duration_seconds)
    silence = b"\x00\x00" * num_samples
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(silence)
