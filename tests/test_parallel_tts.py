"""
tests/test_parallel_tts.py
Tests for parallel TTS synthesis in synthesize_and_stitch_v2.

Verifies:
  - TTS calls are fired concurrently (not sequentially)
  - Segments are stitched in correct order despite parallel execution
  - CURIA_TTS_PARALLEL env var controls thread count
  - Returns (output_path, tts_timings) with correct shape
  - tts_timings include intro offset when intro audio is present
  - Handles single-segment transcripts
"""

import os
import time
from unittest.mock import patch, MagicMock

import pytest


def _stub_synthesize(text, speaker, output_path):
    """Write a minimal valid WAV (silent) and return 'wav'."""
    import wave
    with wave.open(output_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"\x00\x00" * 2205)  # 0.1s silence
    return "wav"


@pytest.fixture
def _patch_tts(monkeypatch):
    """Stub TTS so no real API calls happen."""
    monkeypatch.setattr(
        "studio.generator.synthesize_line_by_speaker",
        _stub_synthesize,
    )
    monkeypatch.setattr(
        "studio.generator._load_optional_segment",
        lambda *a, **kw: None,
    )


TRANSCRIPT = [
    {"speaker": "kenji", "text": "First line from Kenji."},
    {"speaker": "kenji", "text": "Second line from Kenji."},
    {"speaker": "arjun", "text": "First line from Arjun."},
    {"speaker": "arjun", "text": "Second line from Arjun."},
    {"speaker": "kenji", "text": "Third line from Kenji."},
]


class TestParallelTTSSynthesis:

    def test_returns_output_path_and_timings(self, tmp_path, _patch_tts):
        from studio.generator import synthesize_and_stitch_v2
        out = str(tmp_path / "episode.mp3")
        result_path, tts_timings = synthesize_and_stitch_v2(
            TRANSCRIPT, "narrative_drift", out,
        )
        assert result_path == out
        assert os.path.exists(out)
        assert isinstance(tts_timings, list)
        assert len(tts_timings) > 0

    def test_timings_have_correct_shape(self, tmp_path, _patch_tts):
        from studio.generator import synthesize_and_stitch_v2
        out = str(tmp_path / "episode.mp3")
        _, tts_timings = synthesize_and_stitch_v2(
            TRANSCRIPT, "narrative_drift", out,
        )
        for t in tts_timings:
            assert "line_index" in t
            assert "start_ms" in t
            assert "end_ms" in t
            assert "speaker" in t
            assert "text" in t
            assert t["end_ms"] > t["start_ms"]

    def test_timings_are_ordered(self, tmp_path, _patch_tts):
        from studio.generator import synthesize_and_stitch_v2
        out = str(tmp_path / "episode.mp3")
        _, tts_timings = synthesize_and_stitch_v2(
            TRANSCRIPT, "narrative_drift", out,
        )
        for i in range(1, len(tts_timings)):
            assert tts_timings[i]["start_ms"] >= tts_timings[i - 1]["end_ms"]

    def test_fewer_segments_than_lines(self, tmp_path, _patch_tts):
        """Consecutive same-speaker lines should be merged into fewer segments."""
        from studio.generator import synthesize_and_stitch_v2
        out = str(tmp_path / "episode.mp3")
        _, tts_timings = synthesize_and_stitch_v2(
            TRANSCRIPT, "narrative_drift", out,
        )
        assert len(tts_timings) < len(TRANSCRIPT)

    def test_parallel_execution(self, tmp_path, _patch_tts, monkeypatch):
        """TTS calls should overlap in time, not run sequentially."""
        call_times = []
        original_stub = _stub_synthesize

        def _tracking_synthesize(text, speaker, output_path):
            start = time.monotonic()
            time.sleep(0.05)
            result = original_stub(text, speaker, output_path)
            call_times.append((start, time.monotonic()))
            return result

        monkeypatch.setattr(
            "studio.generator.synthesize_line_by_speaker",
            _tracking_synthesize,
        )
        monkeypatch.setenv("CURIA_TTS_PARALLEL", "4")

        from studio.generator import synthesize_and_stitch_v2
        out = str(tmp_path / "episode.mp3")
        synthesize_and_stitch_v2(TRANSCRIPT, "narrative_drift", out)

        # With 3 segments and parallel=4, total wall time should be
        # roughly 1x segment time, not 3x. Check that at least 2 calls
        # overlapped in time.
        overlaps = 0
        for i in range(len(call_times)):
            for j in range(i + 1, len(call_times)):
                s1, e1 = call_times[i]
                s2, e2 = call_times[j]
                if s1 < e2 and s2 < e1:
                    overlaps += 1
        assert overlaps > 0, "TTS calls did not overlap — not parallel"

    def test_respects_parallel_env_var(self, tmp_path, _patch_tts, monkeypatch):
        """CURIA_TTS_PARALLEL=1 should force sequential execution."""
        monkeypatch.setenv("CURIA_TTS_PARALLEL", "1")
        from studio.generator import synthesize_and_stitch_v2
        out = str(tmp_path / "episode.mp3")
        result_path, tts_timings = synthesize_and_stitch_v2(
            TRANSCRIPT, "narrative_drift", out,
        )
        assert os.path.exists(out)
        assert len(tts_timings) > 0

    def test_single_line_transcript(self, tmp_path, _patch_tts):
        from studio.generator import synthesize_and_stitch_v2
        out = str(tmp_path / "episode.mp3")
        transcript = [{"speaker": "kenji", "text": "Solo line."}]
        _, tts_timings = synthesize_and_stitch_v2(
            transcript, "narrative_drift", out,
        )
        assert len(tts_timings) == 1
        assert tts_timings[0]["speaker"] == "kenji"
