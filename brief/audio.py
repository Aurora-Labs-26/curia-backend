"""
brief/audio.py
Real audio for the daily brief — the piece the harness never had (its
mp3_urls were `placeholder://` stubs). Pure reuse of host primitives:
resolve.tts(speaker) adapters, pydub stitching, core/storage/blob upload.

render_brief_audio(brief_id): read the ready brief's ordered segments
(intro → lead → standards → local → outro), synthesize each with the brief
speaker, stitch with short pauses, upload one MP3 to S3, store the key on
harness.daily_briefs.stitched_mp3_url. Failures never un-ready the brief —
the text manifest remains the product; audio is the enhancement (same
posture as the episode BGM layer).
"""

from __future__ import annotations

import asyncio
import os
import tempfile

from loguru import logger

from brief import store

BRIEF_SPEAKER = os.getenv("CURIA_BRIEF_SPEAKER", "kenji")
SEGMENT_PAUSE_MS = 600
# Bookend BGM is NORMALIZED to a target level, not relatively attenuated —
# a fixed -14dB on tracks we never mastered put music at -29.8 dBFS under
# -18 dBFS voice: technically present, humanly inaudible (found by Arihant).
# -25 dBFS sits clearly audible under voice and pleasant when solo. The news
# segments stay clean either way. Bookends also get music-only room: a
# lead-in before the greeting and a ring-out after the sign-off.
BOOKEND_BGM_TARGET_DBFS = float(os.getenv("CURIA_BRIEF_BGM_TARGET_DBFS", "-25"))
INTRO_LEAD_MS = int(os.getenv("CURIA_BRIEF_INTRO_LEAD_MS", "1500"))
OUTRO_TAIL_MS = int(os.getenv("CURIA_BRIEF_OUTRO_TAIL_MS", "1500"))


def _leveled(clip):
    """Normalize a bank clip to the bookend target — robust to however any
    individual bank track happens to be mastered."""
    return clip.apply_gain(BOOKEND_BGM_TARGET_DBFS - clip.dBFS)


def _apply_bookend_bgm(combined, spans: list[tuple[int, int]], kinds: list[str]):
    """Overlay bank music under the intro and outro segments ONLY. The clip is
    fitted to the target region by vibe_mix's own picker (trim/loop), overlaid
    (pydub overlay never extends the base), attenuated and faded — so the
    output length and the recorded spans are invariant. Any failure returns
    the voice-only mix unchanged: music is an enhancement, exactly the
    episode-BGM posture."""
    try:
        from core.audio.vibe_mix import get_segment_bgm_clip, resolve_bank_dir

        bank = resolve_bank_dir()
        out = combined
        if kinds and kinds[0] == "intro" and spans:
            # cover lead-in + intro + its pause, fading out into the first story
            end_ms = min(spans[0][1] + SEGMENT_PAUSE_MS, len(out))
            clip = get_segment_bgm_clip("intro", end_ms, bank)
            if clip:
                out = out.overlay(
                    _leveled(clip).fade_in(400).fade_out(1200), position=0)
        if kinds and kinds[-1] == "outro" and spans:
            # start a beat early (in the preceding pause), ride out to the end
            start_ms = max(spans[-1][0] - 300, 0)
            clip = get_segment_bgm_clip("outro", len(out) - start_ms, bank)
            if clip:
                out = out.overlay(
                    _leveled(clip).fade_in(800).fade_out(1200), position=start_ms)
        return out
    except Exception as e:
        logger.warning(f"[brief.audio] bookend BGM skipped ({e}); shipping voice-only")
        return combined


def _stitch(paths: list[str], out_path: str,
            kinds: list[str] | None = None) -> tuple[float, list[tuple[int, int]]]:
    """Concat WAV/MP3 segment files with pauses → one MP3. Returns
    (total_seconds, [(start_ms, end_ms) per clip]) — the spans are exact by
    construction (we are the ones doing the concatenation), and they're what
    makes the brief chapter-seekable: the manifest's word-count duration
    estimates drift 15-30s from real TTS pace by the later segments."""
    from pydub import AudioSegment

    # Music-only room: a real lead-in before the greeting when the intro is
    # scored, and a ring-out after the sign-off. Spans are computed from
    # len(combined) so they stay exact under any lead length.
    lead_ms = INTRO_LEAD_MS if (kinds and kinds[0] == "intro") else 300
    combined = AudioSegment.silent(duration=lead_ms)
    pause = AudioSegment.silent(duration=SEGMENT_PAUSE_MS)
    spans: list[tuple[int, int]] = []
    for p in paths:
        clip = AudioSegment.from_file(p)
        start = len(combined)
        combined += clip + pause
        spans.append((start, start + len(clip)))
    if kinds and kinds[-1] == "outro":
        combined += AudioSegment.silent(duration=OUTRO_TAIL_MS)
    if kinds:
        combined = _apply_bookend_bgm(combined, spans, kinds)
    combined.export(out_path, format="mp3", bitrate=os.getenv("CURIA_AUDIO_BITRATE", "128k"))
    return combined.duration_seconds, spans


async def render_brief_audio(brief_id: str) -> str | None:
    """Synthesize + stitch + upload. Returns the S3 key, or None on failure."""
    detail = await store.get_daily_brief_detail(brief_id)
    row = (detail or {}).get("brief") or {}   # detail = {"brief": {...}, "articles": [...]}
    if row.get("status") != "ready":
        logger.info(f"[brief.audio] brief {brief_id} not ready — skipping audio")
        return None
    manifest = await store.get_latest_manifest(row["user_id"], str(row["date"])) or []
    spoken = [m for m in manifest if m.get("text", "").strip()]
    texts = [m["text"].strip() for m in spoken]
    if not texts:
        logger.warning(f"[brief.audio] brief {brief_id} has no segment text")
        return None

    from core.llm_config import resolve
    adapter = resolve.tts(speaker=BRIEF_SPEAKER)

    try:
        with tempfile.TemporaryDirectory() as tmp:
            seg_paths = []
            for i, text in enumerate(texts):
                p = os.path.join(tmp, f"seg_{i}.{adapter.output_format}")
                await adapter.synthesize_async(text, p)
                seg_paths.append(p)
            out = os.path.join(tmp, "brief.mp3")
            secs, spans = await asyncio.to_thread(
                _stitch, seg_paths, out, [m.get("kind", "") for m in spoken])

            key = f"audio/brief/{brief_id}.mp3"
            from core.storage.blob import upload_file
            await upload_file(out, key, content_type="audio/mpeg")

        # Write the REAL timings back onto the manifest (spoken segments only,
        # same filter as synthesis — zip is positionally safe by construction).
        # GET /brief/today serves these, so client chapter offsets match the
        # MP3 exactly instead of summing 150-wpm estimates. Best-effort: a
        # failed write must not un-ship the audio.
        try:
            for m, (s_ms, e_ms) in zip(spoken, spans):
                m["start_s"] = round(s_ms / 1000, 2)
                m["duration_s"] = round((e_ms - s_ms) / 1000, 2)
            await store.set_latest_manifest_segments(
                row["user_id"], str(row["date"]), manifest)
        except Exception as e:
            logger.warning(f"[brief.audio] timing write-back failed for {brief_id}: {e}")

        await store.set_daily_brief_audio(brief_id, key)
        logger.info(f"[brief.audio] brief {brief_id}: {len(texts)} segments, {secs:.0f}s → {key}")
        return key
    except Exception as e:
        logger.warning(f"[brief.audio] render failed for {brief_id}: {e}")
        return None
