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
        assert intro["start_s"] == 1.5 and intro["duration_s"] == 1.0   # scored intro lead-in
        assert outro["start_s"] == 3.1 and outro["duration_s"] == 0.5
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


# ---------------------------------------------------------------------------
# Bookend BGM — music under intro/outro ONLY; length + spans invariant;
# voice-only on any failure.
# ---------------------------------------------------------------------------

from pydub import AudioSegment
from pydub.generators import Sine


def _loud(ms):
    return Sine(440).to_audio_segment(duration=ms).apply_gain(-3)


def _rms(seg, start, end):
    return seg[start:end].rms


class TestBookendBgm:
    SPANS = [(300, 1300), (1900, 2400), (3000, 4000)]
    KINDS = ["intro", "standard", "outro"]

    def _base(self):
        return AudioSegment.silent(duration=4600)   # matches spans + trailing pause

    def test_music_lands_on_bookends_not_the_news(self):
        with patch("core.audio.vibe_mix.get_segment_bgm_clip",
                   side_effect=lambda vibe, ms, bank: _loud(ms)), \
             patch("core.audio.vibe_mix.resolve_bank_dir", return_value="/x"):
            out = audio._apply_bookend_bgm(self._base(), self.SPANS, self.KINDS)
        assert len(out) == 4600                       # length invariant
        assert _rms(out, 500, 1200) > 0               # intro scored
        assert _rms(out, 3200, 4000) > 0              # outro scored
        assert _rms(out, 2000, 2300) == 0             # the news segment stays clean
        # the outro music must FADE OUT inside the file — a clip fitted longer
        # than the remaining runway puts its fade beyond the end and the music
        # cuts abruptly (caught by a survived mutant)
        assert _rms(out, 4550, 4600) < _rms(out, 3200, 3400) / 3

    def test_no_bookend_kinds_no_music(self):
        with patch("core.audio.vibe_mix.get_segment_bgm_clip",
                   side_effect=lambda vibe, ms, bank: _loud(ms)), \
             patch("core.audio.vibe_mix.resolve_bank_dir", return_value="/x"):
            out = audio._apply_bookend_bgm(
                self._base(), self.SPANS, ["lead", "standard", "local"])
        assert out.rms == 0

    def test_empty_bank_ships_voice_only(self):
        with patch("core.audio.vibe_mix.get_segment_bgm_clip",
                   side_effect=lambda vibe, ms, bank: None), \
             patch("core.audio.vibe_mix.resolve_bank_dir", return_value="/x"):
            out = audio._apply_bookend_bgm(self._base(), self.SPANS, self.KINDS)
        assert out.rms == 0

    def test_bank_crash_ships_voice_only(self):
        with patch("core.audio.vibe_mix.resolve_bank_dir",
                   side_effect=RuntimeError("s3 down")):
            out = audio._apply_bookend_bgm(self._base(), self.SPANS, self.KINDS)
        assert out.rms == 0
        assert len(out) == 4600

    def test_stitch_threads_kinds_and_keeps_spans(self, tmp_path):
        a, b = str(tmp_path / "a.wav"), str(tmp_path / "b.wav")
        _wav(a, 1.0); _wav(b, 0.5)
        with patch.object(audio, "_apply_bookend_bgm",
                          side_effect=lambda c, sp, k: c) as apply:
            secs, spans = audio._stitch([a, b], str(tmp_path / "o.mp3"),
                                        kinds=["intro", "outro"])
        assert spans == [(1500, 2500), (3100, 3600)]   # scored lead-in, pre-overlay
        apply.assert_called_once()
        assert apply.call_args.args[2] == ["intro", "outro"]

    def test_stitch_without_kinds_skips_bgm(self, tmp_path):
        a = str(tmp_path / "a.wav"); _wav(a, 0.5)
        with patch.object(audio, "_apply_bookend_bgm") as apply:
            audio._stitch([a], str(tmp_path / "o.mp3"))
        apply.assert_not_called()


class TestBookendLeveling:
    """Normalization to target dBFS — a fixed relative gain on unmastered bank
    tracks put music ~12dB under voice (humanly inaudible; found live)."""

    def _mix(self, clip_gain):
        def picker(vibe, ms, bank):
            return Sine(440).to_audio_segment(duration=ms).apply_gain(clip_gain)
        base = AudioSegment.silent(duration=4600)
        with patch("core.audio.vibe_mix.get_segment_bgm_clip", side_effect=picker), \
             patch("core.audio.vibe_mix.resolve_bank_dir", return_value="/x"):
            return audio._apply_bookend_bgm(
                base, [(300, 1300), (1900, 2400), (3000, 4000)],
                ["intro", "standard", "outro"])

    def test_quiet_track_is_lifted_to_target(self):
        out = self._mix(-40)          # whisper-mastered bank track
        mid_intro = out[600:1000]     # past the fade-in
        assert abs(mid_intro.dBFS - audio.BOOKEND_BGM_TARGET_DBFS) < 3.0

    def test_loud_track_is_pulled_down_to_target(self):
        out = self._mix(-3)
        mid_intro = out[600:1000]
        assert abs(mid_intro.dBFS - audio.BOOKEND_BGM_TARGET_DBFS) < 3.0


class TestBookendRoom:
    def test_scored_intro_gets_long_lead_in(self, tmp_path):
        a = str(tmp_path / "a.wav"); _wav(a, 1.0)
        with patch.object(audio, "_apply_bookend_bgm", side_effect=lambda c, sp, k: c):
            _, spans = audio._stitch([a], str(tmp_path / "o.mp3"), kinds=["intro"])
        assert spans == [(1500, 2500)]

    def test_unscored_stitch_keeps_short_lead(self, tmp_path):
        a = str(tmp_path / "a.wav"); _wav(a, 1.0)
        _, spans = audio._stitch([a], str(tmp_path / "o.mp3"))
        assert spans == [(300, 1300)]

    def test_outro_gets_ring_out_tail(self, tmp_path):
        a = str(tmp_path / "a.wav"); _wav(a, 1.0)
        with patch.object(audio, "_apply_bookend_bgm", side_effect=lambda c, sp, k: c) as ap:
            secs, spans = audio._stitch([a], str(tmp_path / "o.mp3"), kinds=["outro"])
        # 300 lead + 1000 clip + 600 pause + 1500 tail = 3.4s
        assert secs == pytest.approx(3.4, abs=0.1)
        assert len(ap.call_args.args[0]) == 3400
