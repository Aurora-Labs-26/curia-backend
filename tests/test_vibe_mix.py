"""
tests/test_vibe_mix.py
Per-segment vibe BGM/SFX mixer (core/audio/vibe_mix.py, integrated from
feat/bgm-sfx) — segment-bounds math, clip fallback, and the S3-aware asset
resolution added during integration (bank lives on S3, not in git/image).
Pure/light functions only; no ffmpeg, no network (boto3 mocked).
"""

import io
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.audio import vibe_mix
from core.audio.vibe_mix import (
    compute_segment_bgm_bounds,
    compute_segment_bounds,
    get_segment_bgm_clip,
    resolve_bank_dir,
    resolve_sfx_path,
)
from studio.formats import (
    DEFAULT_VIBE,
    INTRO_SEGMENT,
    OUTRO_SEGMENT,
    SEGMENT_PAUSE_MS,
    VIBE_DEFINITIONS,
)


@pytest.fixture(autouse=True)
def _reset_s3_sync_flag():
    vibe_mix._s3_sync_attempted = False
    yield
    vibe_mix._s3_sync_attempted = False


# ---------------------------------------------------------------------------
# formats constants — the contract between outline LLM, generator, and mixer
# ---------------------------------------------------------------------------


class TestVibeContract:
    def test_eight_vibes_defined(self):
        assert len(VIBE_DEFINITIONS) == 8
        assert set(VIBE_DEFINITIONS) == {
            "grounding", "curious", "building", "tension",
            "momentum", "expansive", "payoff", "reflective",
        }

    def test_default_vibe_is_valid(self):
        assert DEFAULT_VIBE in VIBE_DEFINITIONS

    def test_sentinels(self):
        assert INTRO_SEGMENT == -1
        assert OUTRO_SEGMENT == 0
        assert SEGMENT_PAUSE_MS > 0


# ---------------------------------------------------------------------------
# compute_segment_bounds
# ---------------------------------------------------------------------------


def _timings(*entries):
    return [{"line_index": i, "start_ms": s, "end_ms": e} for i, (s, e) in enumerate(entries)]


class TestComputeSegmentBounds:
    def test_ordering_puts_outro_last_not_second(self):
        # sorting the raw ids (-1, 0, 1, 2) would wrongly put outro(0) second
        transcript = [
            {"segment": INTRO_SEGMENT}, {"segment": 1},
            {"segment": 2}, {"segment": OUTRO_SEGMENT},
        ]
        timings = _timings((3000, 5000), (8000, 20000), (23000, 40000), (43000, 47000))
        order, bounds = compute_segment_bounds(transcript, timings, 50000)
        assert order == [INTRO_SEGMENT, 1, 2, OUTRO_SEGMENT]

    def test_intro_anchors_to_zero_and_outro_to_track_end(self):
        transcript = [{"segment": INTRO_SEGMENT}, {"segment": 1}, {"segment": OUTRO_SEGMENT}]
        timings = _timings((3000, 5000), (8000, 20000), (23000, 27000))
        _, bounds = compute_segment_bounds(transcript, timings, 30000)
        assert bounds[INTRO_SEGMENT][0] == 0          # covers lead-in silence
        assert bounds[OUTRO_SEGMENT][1] == 30000      # covers tail-out silence
        assert bounds[1] == (8000, 20000)             # body uses spoken span

    def test_multi_line_segment_spans_min_to_max(self):
        transcript = [{"segment": 1}, {"segment": 1}, {"segment": 1}]
        timings = _timings((1000, 4000), (4400, 9000), (9400, 15000))
        order, bounds = compute_segment_bounds(transcript, timings, 16000)
        assert order == [1]
        assert bounds[1] == (1000, 15000)

    def test_empty_timings_yield_no_segments(self):
        order, bounds = compute_segment_bounds([{"segment": 1}], [], 10000)
        assert order == [] and bounds == {}


class TestComputeBgmBounds:
    def test_bleed_extends_interior_edges_only(self):
        order = [INTRO_SEGMENT, 1, OUTRO_SEGMENT]
        voice = {INTRO_SEGMENT: (0, 5000), 1: (8000, 20000), OUTRO_SEGMENT: (23000, 30000)}
        bgm = compute_segment_bgm_bounds(order, voice, bleed_ms=1500)
        assert bgm[INTRO_SEGMENT] == (0, 6500)        # no backward bleed at track start
        assert bgm[1] == (6500, 21500)                # bleeds both ways
        assert bgm[OUTRO_SEGMENT] == (21500, 30000)   # no forward bleed at track end

    def test_neighbours_overlap_across_the_pause(self):
        order = [1, 2]
        voice = {1: (0, 10000), 2: (13000, 20000)}
        bgm = compute_segment_bgm_bounds(order, voice, bleed_ms=1500)
        # seg1 ends 11500, seg2 starts 11500 → fades meet exactly mid-pause
        assert bgm[1][1] == bgm[2][0] == 11500


# ---------------------------------------------------------------------------
# get_segment_bgm_clip — graceful degradation
# ---------------------------------------------------------------------------


class TestClipFallback:
    def test_missing_vibe_folder_returns_none(self, tmp_path):
        assert get_segment_bgm_clip("tension", 5000, tmp_path) is None

    def test_empty_vibe_folder_returns_none(self, tmp_path):
        (tmp_path / "curious").mkdir()
        assert get_segment_bgm_clip("curious", 5000, tmp_path) is None


# ---------------------------------------------------------------------------
# S3-aware asset resolution (integration addition)
# ---------------------------------------------------------------------------


def _touch_mp3(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"ID3fake")


class TestAssetResolution:
    def test_env_override_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CURIA_BGM_DIR", str(tmp_path / "custom"))
        assert resolve_bank_dir() == tmp_path / "custom"

    def test_local_repo_assets_used_when_present(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CURIA_BGM_DIR", raising=False)
        bank = tmp_path / "bank"
        _touch_mp3(bank / "curious" / "a.mp3")
        sync = MagicMock()
        with patch.object(vibe_mix, "DEFAULT_BGM_BANK_DIR", bank), \
             patch.object(vibe_mix, "_sync_assets_from_s3", sync):
            assert resolve_bank_dir() == bank
        sync.assert_not_called()                       # no S3 touch when local exists

    def test_falls_back_to_s3_cache(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CURIA_BGM_DIR", raising=False)
        empty_default = tmp_path / "missing_bank"
        cache = tmp_path / "cache"
        _touch_mp3(cache / "assets" / "bgm_bank" / "payoff" / "s3.mp3")
        with patch.object(vibe_mix, "DEFAULT_BGM_BANK_DIR", empty_default), \
             patch.object(vibe_mix, "BGM_CACHE_DIR", cache), \
             patch.object(vibe_mix, "_sync_assets_from_s3", MagicMock()) as sync:
            assert resolve_bank_dir() == cache / "assets" / "bgm_bank"
        sync.assert_called_once()

    def test_sfx_resolution_prefers_local_then_cache(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CURIA_SFX_PATH", raising=False)
        cache = tmp_path / "cache"
        cached_sfx = cache / "assets" / "sfx" / "transition.mp3"
        _touch_mp3(cached_sfx)
        with patch.object(vibe_mix, "DEFAULT_SFX_PATH", tmp_path / "nope.mp3"), \
             patch.object(vibe_mix, "BGM_CACHE_DIR", cache), \
             patch.object(vibe_mix, "_sync_assets_from_s3", MagicMock()):
            assert resolve_sfx_path() == cached_sfx

    def test_sync_downloads_missing_and_skips_size_matched(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CURIA_BGM_S3_BUCKET", raising=False)
        monkeypatch.setenv("CURIA_S3_BUCKET", "test-bucket")
        cache = tmp_path / "cache"
        # pre-existing file with matching size → must be skipped
        existing = cache / "assets" / "bgm_bank" / "curious" / "hg2.mp3"
        _touch_mp3(existing)
        listing = {"Contents": [
            {"Key": "assets/bgm_bank/curious/hg2.mp3", "Size": existing.stat().st_size},
            {"Key": "assets/bgm_bank/tension/mv1.mp3", "Size": 999},
        ]}
        s3 = MagicMock()
        s3.get_paginator.return_value.paginate.return_value = [listing]
        with patch.object(vibe_mix, "BGM_CACHE_DIR", cache), \
             patch("boto3.client", return_value=s3):
            vibe_mix._sync_assets_from_s3()
        downloaded = [c.args[1] for c in s3.download_file.call_args_list]
        assert "assets/bgm_bank/tension/mv1.mp3" in downloaded
        assert "assets/bgm_bank/curious/hg2.mp3" not in downloaded
        # bucket came from CURIA_S3_BUCKET fallback
        assert s3.download_file.call_args_list[0].args[0] == "test-bucket"

    def test_sync_never_raises(self):
        with patch("boto3.client", side_effect=RuntimeError("no creds")):
            vibe_mix._sync_assets_from_s3()             # must not raise

    def test_sync_runs_once_per_process(self):
        with patch("boto3.client", side_effect=RuntimeError("no creds")) as bc:
            vibe_mix._sync_assets_from_s3()
            vibe_mix._sync_assets_from_s3()
        assert bc.call_count == 1
