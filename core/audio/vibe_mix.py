"""
core/audio/vibe_mix.py
Per-segment vibe BGM + transition SFX mix, layered onto the stitched voice track.

Ported from the prototype at Personal/smallest_tts_test/vibetag.py, adapted to the
production pipeline: bounds are derived from studio/generator.py's existing
tts_timings + segment_transitions (synthesize_and_stitch_v2's lead-in/tail-out
silence and segment-aware pauses) instead of being re-derived from scratch.

Three independent layers get overlaid onto the voice stream, never onto each other:
  1. Voice (base)  — already stitched by studio/generator.py, with a SEGMENT_PAUSE_MS
                      silence at every outline-segment transition (incl. intro lead-in
                      and outro tail-out).
  2. BGM            — one clip per segment, picked by that segment's vibe tag from
                      assets/bgm_bank/<vibe>/, crossfaded into the next segment's vibe
                      across the pause between them, then sidechain-ducked under voice.
  3. SFX            — one fixed clip dropped at every transition timestamp.

Each layer is computed independently against the voice timeline and combined at the
very end via two separate overlay() calls — either can be swapped without the other
needing to change.
"""

from __future__ import annotations

import os
import random
import subprocess
import tempfile
from pathlib import Path

from loguru import logger
from pydub import AudioSegment

from studio.formats import (
    INTRO_SEGMENT,
    OUTRO_SEGMENT,
    INTRO_VIBE,
    OUTRO_VIBE,
    DEFAULT_VIBE,
    SEGMENT_PAUSE_MS,
    SFX_PAD_MS,
)

CURIA_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_BGM_BANK_DIR = CURIA_ROOT / "assets" / "bgm_bank"
DEFAULT_SFX_PATH = CURIA_ROOT / "assets" / "sfx" / "transition.mp3"

# ---------------------------------------------------------------------------
# Asset resolution — the bank (~473MB of mp3s) is deliberately NOT committed
# to git or baked into the image. Resolution order:
#   1. $CURIA_BGM_DIR / $CURIA_SFX_PATH        explicit override
#   2. repo assets/ (dev machines that have the files locally)
#   3. one-time sync from s3://$CURIA_BGM_S3_BUCKET/assets/... to a local cache
# If all three miss, the per-vibe silence fallback in get_segment_bgm_clip()
# keeps episodes shipping voice-only.
# ---------------------------------------------------------------------------

BGM_CACHE_DIR = Path(os.getenv("CURIA_BGM_CACHE_DIR", "/tmp/curia_bgm_cache"))
_S3_SYNC_PREFIXES = ("assets/bgm_bank/", "assets/sfx/")
_s3_sync_attempted = False


def _has_audio(d: Path) -> bool:
    return d.is_dir() and any(
        p.suffix.lower() in (".mp3", ".wav") for p in d.rglob("*") if p.is_file()
    )


def _sync_assets_from_s3() -> None:
    """Download bank + sfx objects to the local cache. Once per process; never raises."""
    global _s3_sync_attempted
    if _s3_sync_attempted:
        return
    _s3_sync_attempted = True
    bucket = os.getenv("CURIA_BGM_S3_BUCKET") or os.getenv("CURIA_S3_BUCKET") or "curia-audio"
    try:
        import boto3

        s3 = boto3.client("s3", region_name=os.getenv("CURIA_S3_REGION", "us-east-1"))
        synced = 0
        for prefix in _S3_SYNC_PREFIXES:
            for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    dest = BGM_CACHE_DIR / key
                    if dest.is_file() and dest.stat().st_size == obj["Size"]:
                        continue
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    s3.download_file(bucket, key, str(dest))
                    synced += 1
        logger.info(f"[vibe_mix] S3 asset sync: {synced} new file(s) from s3://{bucket}")
    except Exception as e:
        logger.warning(f"[vibe_mix] S3 asset sync failed ({e}); BGM/SFX may fall back to silence")


def resolve_bank_dir() -> Path:
    env = os.getenv("CURIA_BGM_DIR")
    if env:
        return Path(env)
    if _has_audio(DEFAULT_BGM_BANK_DIR):
        return DEFAULT_BGM_BANK_DIR
    _sync_assets_from_s3()
    cached = BGM_CACHE_DIR / "assets" / "bgm_bank"
    return cached if _has_audio(cached) else DEFAULT_BGM_BANK_DIR


def resolve_sfx_path() -> Path:
    env = os.getenv("CURIA_SFX_PATH")
    if env:
        return Path(env)
    if DEFAULT_SFX_PATH.is_file():
        return DEFAULT_SFX_PATH
    _sync_assets_from_s3()
    cached = BGM_CACHE_DIR / "assets" / "sfx" / "transition.mp3"
    return cached if cached.is_file() else DEFAULT_SFX_PATH


BGM_BASELINE_DB = -32.0          # every bank clip is normalized to this level
BGM_CROSSFADE_MS = SEGMENT_PAUSE_MS  # crossfade window = the full transition pause

SIDECHAIN_THRESHOLD_DB = -20.0   # ffmpeg sidechaincompress threshold is linear, converted below
SIDECHAIN_RATIO = 4              # 4:1 — felt, not slammed
SIDECHAIN_ATTACK_MS = 10
SIDECHAIN_RELEASE_MS = 600


def compute_segment_bounds(
    transcript: list[dict],
    tts_timings: list[dict],
    voice_length_ms: int,
) -> tuple[list[int], dict[int, tuple[int, int]]]:
    """
    Build the ordered segment list and each segment's voice span (start_ms, end_ms)
    from tts_timings (already computed by synthesize_and_stitch_v2).

    Ordering is built by formula, never by sorting the raw segment-id set — sorting
    -1, 0, 1, 2... directly would put outro (0) second instead of last.

    INTRO_SEGMENT's span starts at literal 0 (covering the lead-in silence, not just
    its first spoken word) and OUTRO_SEGMENT's span ends at literal voice_length_ms
    (covering the tail-out silence) — both anchor to the absolute track boundary, not
    the nearest spoken word.
    """
    line_segment: dict[int, int] = {
        i: line.get("segment") for i, line in enumerate(transcript)
    }

    spans: dict[int, list[int]] = {}  # segment -> [min_start, max_end]
    for entry in tts_timings:
        seg = line_segment.get(entry["line_index"])
        if seg is None:
            continue
        start, end = entry["start_ms"], entry["end_ms"]
        if seg not in spans:
            spans[seg] = [start, end]
        else:
            spans[seg][0] = min(spans[seg][0], start)
            spans[seg][1] = max(spans[seg][1], end)

    present = set(spans.keys())
    body_segments = sorted(s for s in present if s is not None and s >= 1)
    segments_in_order = (
        ([INTRO_SEGMENT] if INTRO_SEGMENT in present else [])
        + body_segments
        + ([OUTRO_SEGMENT] if OUTRO_SEGMENT in present else [])
    )

    bounds: dict[int, tuple[int, int]] = {}
    for seg in segments_in_order:
        start, end = spans[seg]
        if seg == INTRO_SEGMENT:
            start = 0
        if seg == OUTRO_SEGMENT:
            end = voice_length_ms
        bounds[seg] = (start, end)

    return segments_in_order, bounds


def compute_segment_bgm_bounds(
    segments_in_order: list[int],
    voice_bounds: dict[int, tuple[int, int]],
    bleed_ms: int,
) -> dict[int, tuple[int, int]]:
    """
    Extend each segment's voice span by bleed_ms into the pause it borders on each
    side. Two neighbouring segments' BGM spans then overlap across exactly the pause
    between them — that overlap is where the crossfade happens. The first segment in
    segments_in_order gets no backward bleed and the last gets no forward bleed,
    which — since intro/outro are always first/last — already gives them the
    correct "no bleed past the absolute track boundary" behavior for free.
    """
    bgm_bounds = {}
    for i, seg in enumerate(segments_in_order):
        v_start, v_end = voice_bounds[seg]
        bgm_start = v_start - bleed_ms if i > 0 else v_start
        bgm_end = v_end + bleed_ms if i < len(segments_in_order) - 1 else v_end
        bgm_bounds[seg] = (bgm_start, bgm_end)
    return bgm_bounds


def get_segment_bgm_clip(vibe: str, target_ms: int, bank_dir: Path) -> AudioSegment | None:
    """
    Pick a random file from bank_dir/<vibe>/, then fit it to target_ms — trimmed if
    longer, looped-then-trimmed if shorter. Returns None (caller substitutes silence)
    if the bank folder is missing or empty, so a content gap degrades gracefully
    instead of failing the whole episode.
    """
    folder = bank_dir / vibe
    if not folder.is_dir():
        logger.warning(f"[vibe_mix] no bank folder for vibe '{vibe}' at {folder}; using silence")
        return None

    candidates = [f for f in os.listdir(folder) if f.lower().endswith((".mp3", ".wav"))]
    if not candidates:
        logger.warning(f"[vibe_mix] no BGM files for vibe '{vibe}' in {folder}; using silence")
        return None

    if target_ms <= 0:
        return AudioSegment.silent(duration=0)

    chosen = random.choice(candidates)
    clip = AudioSegment.from_file(str(folder / chosen))

    if len(clip) >= target_ms:
        return clip[:target_ms]

    looped = clip
    while len(looped) < target_ms:
        looped += clip
    return looped[:target_ms]


def build_segment_bgm_track(
    segments_in_order: list[int],
    bgm_bounds: dict[int, tuple[int, int]],
    segment_vibes: dict[int, str],
    voice_length_ms: int,
    bank_dir: Path,
) -> AudioSegment:
    """
    One bank-sourced clip per segment, normalized to BGM_BASELINE_DB, faded in/out
    across the bleed region it shares with each neighbour, then overlaid onto a
    silent canvas at its own bounds. Two neighbouring clips' fades overlap exactly
    across the pause between them, so the vibe crossfades there instead of cutting.
    """
    canvas = AudioSegment.silent(duration=voice_length_ms)
    bleed = BGM_CROSSFADE_MS // 2

    for i, seg in enumerate(segments_in_order):
        start, end = bgm_bounds[seg]
        target_ms = max(0, end - start)

        vibe = segment_vibes.get(seg, DEFAULT_VIBE)
        clip = get_segment_bgm_clip(vibe, target_ms, bank_dir)
        if clip is None:
            continue

        clip = clip.apply_gain(
            BGM_BASELINE_DB - clip.dBFS if clip.dBFS != float("-inf") else BGM_BASELINE_DB
        )

        if i > 0:
            clip = clip.fade_in(bleed)
        if i < len(segments_in_order) - 1:
            clip = clip.fade_out(bleed)

        canvas = canvas.overlay(clip, position=max(0, start))

    return canvas


def build_sfx_track(
    voice_length_ms: int,
    segment_transitions: list[dict],
    sfx_path: Path,
) -> AudioSegment:
    """
    Silent canvas the length of the voice track, with a single fixed SFX clip
    dropped in at every transition timestamp + SFX_PAD_MS. No vibe-awareness —
    every transition (including intro->1 and N->outro) uses the same clip.
    """
    track = AudioSegment.silent(duration=voice_length_ms)

    if not sfx_path.exists():
        logger.warning(f"[vibe_mix] SFX clip not found at {sfx_path}; skipping SFX layer")
        return track

    sfx_clip = AudioSegment.from_file(str(sfx_path))

    for t in segment_transitions:
        start_ms = t["at_ms"] + SFX_PAD_MS
        if start_ms >= voice_length_ms:
            continue
        track = track.overlay(sfx_clip, position=start_ms)

    return track


def _db_to_linear(db: float) -> float:
    """ffmpeg's sidechaincompress threshold is linear amplitude (0-1), not dB."""
    return 10 ** (db / 20)


def apply_sidechain_compression(voice_path: str, bgm_path: str, output_path: str) -> str:
    """
    Sidechain-compress the BGM against the voice track via ffmpeg's native
    sidechaincompress filter (pydub doesn't expose it). Ducks the BGM continuously
    wherever voice is present, on top of the per-segment crossfades already baked
    into bgm_path. Outputs only the processed (BGM) stream — caller still needs to
    overlay the result onto the voice afterward.
    """
    threshold_linear = _db_to_linear(SIDECHAIN_THRESHOLD_DB)
    filter_complex = (
        f"[0:a][1:a]sidechaincompress="
        f"threshold={threshold_linear}:"
        f"ratio={SIDECHAIN_RATIO}:"
        f"attack={SIDECHAIN_ATTACK_MS}:"
        f"release={SIDECHAIN_RELEASE_MS}"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", bgm_path,
        "-i", voice_path,
        "-filter_complex", filter_complex,
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg sidechaincompress failed:\n{result.stderr[-1000:]}")
    return output_path


def build_vibe_mix(
    voice_path: str,
    transcript: list[dict],
    tts_timings: list[dict],
    segment_transitions: list[dict],
    segment_vibes: dict[int, str],
    output_path: str,
    bank_dir: Path | str | None = None,
    sfx_path: Path | str | None = None,
    bitrate: str = "128k",
) -> str:
    """
    Load the stitched voice stream, build the per-segment vibe BGM layer and the
    SFX layer independently, sidechain-duck the BGM against the voice, then overlay
    everything together into output_path. segment_vibes must already include the
    sentinel intro/outro entries (callers build this from the outline plus
    INTRO_SEGMENT/OUTRO_SEGMENT -> INTRO_VIBE/OUTRO_VIBE).

    If anything in the BGM/SFX layer fails (missing ffmpeg, missing assets), this
    falls back to the plain voice track rather than failing the whole episode —
    audio backing is an enhancement, not a hard requirement for an episode to ship.
    """
    bank_dir = Path(bank_dir) if bank_dir else resolve_bank_dir()
    sfx_path = Path(sfx_path) if sfx_path else resolve_sfx_path()

    voice = AudioSegment.from_file(voice_path)
    voice_length_ms = len(voice)

    try:
        segments_in_order, voice_bounds = compute_segment_bounds(
            transcript, tts_timings, voice_length_ms
        )
        if not segments_in_order:
            logger.warning("[vibe_mix] no segments resolved from tts_timings; shipping voice-only audio")
            voice.export(output_path, format="mp3", bitrate=bitrate)
            return output_path

        bleed_ms = BGM_CROSSFADE_MS // 2
        bgm_bounds = compute_segment_bgm_bounds(segments_in_order, voice_bounds, bleed_ms)
        bgm_track = build_segment_bgm_track(
            segments_in_order, bgm_bounds, segment_vibes, voice_length_ms, bank_dir
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            bgm_track_path = os.path.join(tmpdir, "bgm_track.wav")
            bgm_track.export(bgm_track_path, format="wav")

            try:
                bgm_ducked_path = os.path.join(tmpdir, "bgm_ducked.wav")
                apply_sidechain_compression(voice_path, bgm_track_path, bgm_ducked_path)
                bgm_final = AudioSegment.from_file(bgm_ducked_path)
            except Exception as e:
                logger.warning(f"[vibe_mix] sidechain compression failed ({e}); using un-ducked BGM")
                bgm_final = bgm_track

        mix = voice.overlay(bgm_final, position=0)

        sfx_track = build_sfx_track(voice_length_ms, segment_transitions, sfx_path)
        mix = mix.overlay(sfx_track, position=0)

        mix.export(output_path, format="mp3", bitrate=bitrate)
        logger.info(f"[vibe_mix] mixed episode exported: {output_path} ({voice_length_ms/1000:.1f}s)")
        return output_path

    except Exception as e:
        logger.error(f"[vibe_mix] BGM/SFX mix failed ({e}); shipping voice-only audio")
        voice.export(output_path, format="mp3", bitrate=bitrate)
        return output_path
