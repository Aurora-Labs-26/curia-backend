"""
tests/test_brief_audio_timings.py — chapter-accurate segment timings captured
at stitch time (brief/audio.py). The stitcher is exercised on REAL generated
WAVs of known lengths, so the asserted offsets are exact math over the actual
concatenation (300ms lead + clip + 600ms pause each), not mocks agreeing
with mocks. Persistence back onto the manifest is asserted via the store fn.
"""

import os
import wave
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brief import audio, store


def _wav(path: str, seconds: float, rate: int = 22050) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))


class TestStitchSpans:
    def test_spans_are_exact_over_real_audio(self, tmp_path):
        a, b = str(tmp_path / "a.wav"), str(tmp_path / "b.wav")
        _wav(a, 1.0); _wav(b, 0.5)
        out = str(tmp_path / "out.mp3")
        secs, spans = audio._stitch([a, b], out)
        # 300 lead | a: 300..1300 | pause 600 | b: 1900..2400 | pause 600
        assert spans == [(300, 1300), (1900, 2400)]
        assert secs == pytest.approx(3.0, abs=0.1)   # mp3 framing jitter
        assert os.path.getsize(out) > 0

    def test_single_clip(self, tmp_path):
        a = str(tmp_path / "a.wav"); _wav(a, 2.0)
        secs, spans = audio._stitch([a], str(tmp_path / "o.mp3"))
        assert spans == [(300, 2300)]
        assert secs == pytest.approx(2.9, abs=0.1)


class TestRenderWritesTimings:
    def _manifest(self):
        return [{"kind": "intro", "text": "hello", "duration_s": 99.0},
                {"kind": "lead", "text": "", "duration_s": 99.0},      # silent: skipped
                {"kind": "outro", "text": "bye", "duration_s": 99.0}]

    async def test_real_timings_replace_estimates(self, tmp_path):
        manifest = self._manifest()
        lengths = iter([1.0, 0.5])

        async def fake_tts(text, path):
            _wav(path, next(lengths))

        adapter = MagicMock(output_format="wav")
        adapter.synthesize_async = AsyncMock(side_effect=fake_tts)
        set_segments = AsyncMock()
        with patch.object(audio.store, "get_daily_brief_detail", AsyncMock(
                return_value={"brief": {"status": "ready", "user_id": "u1",
                                        "date": "2026-08-03"}, "articles": []})), \
             patch.object(audio.store, "get_latest_manifest",
                          AsyncMock(return_value=manifest)), \
             patch.object(audio.store, "set_latest_manifest_segments", set_segments), \
             patch.object(audio.store, "set_daily_brief_audio", AsyncMock()), \
             patch("core.llm_config.resolve.tts", return_value=adapter), \
             patch("core.storage.blob.upload_file", AsyncMock()):
            key = await audio.render_brief_audio("b-1")

        assert key is not None
        written = set_segments.await_args.args[2]
        intro, silent, outro = written
        assert intro["start_s"] == 0.3 and intro["duration_s"] == 1.0
        assert outro["start_s"] == 1.9 and outro["duration_s"] == 0.5
        # untimed segments keep no bogus timing — and never their 99.0 estimate
        assert "start_s" not in silent
        assert set_segments.await_args.args[0] == "u1"
        assert set_segments.await_args.args[1] == "2026-08-03"

    async def test_timing_write_failure_never_kills_audio(self, tmp_path):
        async def fake_tts(text, path):
            _wav(path, 0.3)
        adapter = MagicMock(output_format="wav")
        adapter.synthesize_async = AsyncMock(side_effect=fake_tts)
        with patch.object(audio.store, "get_daily_brief_detail", AsyncMock(
                return_value={"brief": {"status": "ready", "user_id": "u1",
                                        "date": "2026-08-03"}, "articles": []})), \
             patch.object(audio.store, "get_latest_manifest",
                          AsyncMock(return_value=[{"kind": "intro", "text": "hi"}])), \
             patch.object(audio.store, "set_latest_manifest_segments",
                          AsyncMock(side_effect=RuntimeError("db blip"))), \
             patch.object(audio.store, "set_daily_brief_audio", AsyncMock()) as set_url, \
             patch("core.llm_config.resolve.tts", return_value=adapter), \
             patch("core.storage.blob.upload_file", AsyncMock()):
            key = await audio.render_brief_audio("b-1")
        assert key is not None                # audio still ships
        set_url.assert_awaited_once()


class TestStoreSetLatestManifestSegments:
    async def test_updates_only_the_latest_record(self):
        pool = MagicMock(); pool.execute = AsyncMock()
        with patch.object(store.harness_db, "get_pool", AsyncMock(return_value=pool)):
            await store.set_latest_manifest_segments("u1", "2026-08-03", [{"a": 1}])
        sql = pool.execute.await_args.args[0]
        assert "UPDATE harness.transcript_records" in sql
        assert "ORDER BY created_at DESC" in sql and "LIMIT 1" in sql
