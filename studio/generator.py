"""
studio/generator.py
Full generation pipeline for standing shows.
selector → briefing → outline → transcript → ElevenLabs → MP3 → save episode → log topics

LLM calls (outline + transcript) route through DSPy modules in core/prompts/.
TTS routes through core/tts.py (ElevenLabs HTTP API, with stub fallback).
"""

import asyncio
import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

import dspy
from dotenv import load_dotenv
from loguru import logger
try:
    from pydub import AudioSegment
except ImportError:
    AudioSegment = None  # TTS/stitch only — not needed for outline/transcript

CURIA_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(CURIA_ROOT))
sys.path.insert(0, str(Path(__file__).parent))
load_dotenv(dotenv_path=CURIA_ROOT / ".env")

from shows.profiles import EpisodeProfile, SpeakerProfile, SHOW_PROFILES, SPEAKER_PROFILES
from briefing_builder import build_briefing_packet, briefing_packet_to_str
from intelligence.selector import select_episode_sources, get_source_insights
from core.db.connection import db_execute, db_fetchrow, db_query
from core.kb import UserKB, load_kb
from core.llm_config import resolve
from core.prompts.outline import generate_outline as _outline_module
from core.prompts.transcript import generate_transcript as _transcript_module
from core.prompts.transcript_two_host import (
    generate_host_a as _host_a_module,
    generate_host_b as _host_b_module,
    merge_dialogue as _merge_module,
)
from core.llm_config import resolve as _resolve_config
from optimization.rubrics.judge import judge as _rubric_judge

# Audio output dir — container-friendly. CURIA_AUDIO_DIR env var overrides.
EPISODES_DIR = Path(os.getenv("CURIA_AUDIO_DIR", str(CURIA_ROOT / "data" / "audio")))
EPISODES_DIR.mkdir(parents=True, exist_ok=True)

# Quality gate — env tunable. Below this, we re-roll the transcript once.
QUALITY_THRESHOLD = float(os.getenv("CURIA_QUALITY_THRESHOLD", "0.6"))
QUALITY_REROLL_ENABLED = os.getenv("CURIA_QUALITY_REROLL", "true").lower() == "true"


# ---------------------------------------------------------------------------
# LLM calls
# ---------------------------------------------------------------------------

def generate_outline(briefing: str, show_name: str) -> dict:
    import time as _time
    llm_log = logger.bind(log_type="llm")
    logger.info(f"Generating outline (show={show_name})...")
    llm_log.info(f"LLM_CALL_START | task=outline show={show_name} briefing_len={len(briefing)}")
    _start = _time.time()
    # Resolver picks the right model: show-scoped binding overrides task default.
    with dspy.context(lm=resolve.llm("outline", show=show_name)):
        prediction = _outline_module(briefing=briefing)
    _elapsed = _time.time() - _start
    raw = prediction.outline_json.strip()
    llm_log.info(f"LLM_CALL_END | task=outline show={show_name} duration={_elapsed:.2f}s output_len={len(raw)}")
    llm_log.debug(f"LLM_OUTPUT | task=outline | {raw[:3000]}{'...' if len(raw) > 3000 else ''}")
    # Strip markdown code fences if present
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    # Slice to the first { ... last } to handle any leading/trailing prose
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1:
        raw = raw[start:end+1]
    raw = raw.strip()
    try:
        outline = json.loads(raw)
        # Strip unknown keys from segments to avoid downstream issues
        allowed_segment_keys = {"segment", "title", "purpose", "primitives_used", "transition"}
        for seg in outline.get("segments", []):
            for k in list(seg.keys()):
                if k not in allowed_segment_keys:
                    del seg[k]
    except (json.JSONDecodeError, Exception) as e:
        logger.warning(f"Outline JSON parse failed ({e}), raw response:\n{raw[:300]}")
        outline = {"title": show_name, "thread": "Follow the material.", "segments": [{"segment": i+1, "title": f"Segment {i+1}", "purpose": f"Segment {i+1}", "primitives_used": [], "transition": ""} for i in range(8)]}
    logger.info(f"Outline: '{outline.get('title', 'untitled')}' — {len(outline.get('segments', []))} segments")
    return outline


def _format_listener_hints(kb: UserKB | None) -> str:
    """KB-derived hints for the transcript LLM — appended to the speaker_definition."""
    if kb is None:
        return ""
    hints: list[str] = []
    if kb.preferences.preferred_tone:
        hints.append(f"Listener prefers a {kb.preferences.preferred_tone} register.")
    if kb.preferences.tolerates_ambiguity == "high":
        hints.append("Listener prefers unresolved endings; avoid tidy conclusions.")
    elif kb.preferences.tolerates_ambiguity == "low":
        hints.append("Listener prefers explicit takeaways; close segments cleanly.")
    if kb.preferences.preferred_length_minutes:
        hints.append(f"Target ~{kb.preferences.preferred_length_minutes} minutes total.")
    if kb.dislikes.tones:
        hints.append(f"Avoid these tones: {', '.join(kb.dislikes.tones)}.")
    if kb.interests.current_obsession:
        hints.append(
            f"Listener is currently thinking about: {kb.interests.current_obsession}. "
            "Lean toward connections that engage that thread when natural — never force it."
        )
    if not hints:
        return ""
    return "\n\nLISTENER CONTEXT (apply subtly, do not address them directly):\n" + "\n".join(
        f"- {h}" for h in hints
    )


def generate_transcript(
    briefing: str,
    outline: dict,
    show_name: str,
    user_kb: UserKB | None = None,
    speaker_override: str | None = None,
) -> list[dict]:
    profile = SHOW_PROFILES[show_name]
    import time as _time
    llm_log = logger.bind(log_type="llm")
    logger.info(f"Generating transcript (show={show_name})...")

    if speaker_override:
        if speaker_override not in SPEAKER_PROFILES:
            raise ValueError(
                f"Unknown speaker override '{speaker_override}'. "
                f"Known: {list(SPEAKER_PROFILES)}"
            )
        speaker = SPEAKER_PROFILES[speaker_override].speakers[0]
        logger.info(f"  speaker override → {speaker.name}")
    else:
        speaker = profile.speaker_config.speakers[0]

    speaker_definition = (
        f"Name: {speaker.name}\n"
        f"Backstory: {speaker.backstory}\n"
        f"Speech patterns: {speaker.speech_patterns}"
        + _format_listener_hints(user_kb)
    )
    # Resolver picks the right model: show-scoped binding overrides task default.
    llm_log.info(f"LLM_CALL_START | task=transcript show={show_name} briefing_len={len(briefing)}")
    _start = _time.time()
    with dspy.context(lm=resolve.llm("transcript", show=show_name)):
        prediction = _transcript_module(
            briefing=f"<briefing>\n{briefing}\n</briefing>",
            speaker=f"<speaker>\n{speaker_definition}\n</speaker>",
            outline=f"<outline>\n{json.dumps(outline, indent=2)}\n</outline>",
        )
    _elapsed = _time.time() - _start
    raw = prediction.transcript_json.strip()
    llm_log.info(f"LLM_CALL_END | task=transcript show={show_name} duration={_elapsed:.2f}s output_len={len(raw)}")
    llm_log.debug(f"LLM_OUTPUT | task=transcript | {raw[:3000]}{'...' if len(raw) > 3000 else ''}")
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    try:
        transcript = json.loads(raw)
    except json.JSONDecodeError:
        logger.error(f"Transcript JSON parse failed:\n{raw[:300]}")
        raise
    logger.info(f"Transcript: {len(transcript)} lines")
    return transcript


def _parse_json_response(raw: str, label: str) -> list | dict:
    """Strip markdown fences, DSPy markers, and parse JSON from an LLM response."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    # Strip DSPy completion markers
    if "[[ ##" in raw:
        raw = raw[:raw.index("[[ ##")]
    raw = raw.strip()
    # Find the JSON array/object boundaries
    if raw.startswith("["):
        end = raw.rfind("]")
        if end != -1:
            raw = raw[:end + 1]
    elif raw.startswith("{"):
        end = raw.rfind("}")
        if end != -1:
            raw = raw[:end + 1]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.error(f"{label} JSON parse failed:\n{raw[:300]}")
        raise


def generate_transcript_two_host(
    briefing: str,
    outline: dict,
    show_name: str,
    user_kb: UserKB | None = None,
    profile_override: "EpisodeProfile | None" = None,
) -> list[dict]:
    """
    Three-call two-host transcript pipeline:
      1. Host A builds the case (teacher / thesis-holder)
      2. Host B reacts to Host A's actual words (student / antithesis)
      3. Merger interleaves into natural dialogue with intro/outro

    The role difference is encoded in the speaker backstory/patterns in profiles.py.
    profile_override: pass a dynamically-built EpisodeProfile to use custom speaker pairs.
    Returns the merged transcript as [{speaker, text}, ...].
    """
    import time as _time

    profile = profile_override if profile_override is not None else SHOW_PROFILES[show_name]
    llm_log = logger.bind(log_type="llm")
    logger.info(f"Generating two-host transcript (show={show_name})...")

    speakers = profile.speaker_config.speakers
    if len(speakers) < 2:
        raise ValueError(
            f"Two-host pipeline requires at least 2 speakers in profile '{show_name}', "
            f"got {len(speakers)}"
        )
    speaker_a = speakers[0]
    speaker_b = speakers[1]

    speaker_a_def = (
        f"Name: {speaker_a.name}\n"
        f"Backstory: {speaker_a.backstory}\n"
        f"Speech patterns: {speaker_a.speech_patterns}"
        + _format_listener_hints(user_kb)
    )
    speaker_b_def = (
        f"Name: {speaker_b.name}\n"
        f"Backstory: {speaker_b.backstory}\n"
        f"Speech patterns: {speaker_b.speech_patterns}"
    )
    outline_json = json.dumps(outline, indent=2)
    briefing_tagged = f"<briefing>\n{briefing}\n</briefing>"
    outline_tagged = f"<outline>\n{outline_json}\n</outline>"

    # ── Call 1: Host A ──────────────────────────────────────────────────
    llm_log.info(f"LLM_CALL_START | task=transcript_host_a show={show_name}")
    _t1 = _time.time()
    with dspy.context(lm=resolve.llm("transcript", show=show_name)):
        pred_a = _host_a_module(
            briefing=briefing_tagged,
            speaker=f"<speaker>\n{speaker_a_def}\n</speaker>",
            outline=outline_tagged,
        )
    _e1 = _time.time() - _t1
    host_a_transcript = _parse_json_response(pred_a.host_a_json, "Host A")
    # Force correct speaker name — LLM may output "Host", "HOST_A", etc.
    for line in host_a_transcript:
        line["speaker"] = speaker_a.name.lower()
    llm_log.info(f"LLM_CALL_END | task=transcript_host_a duration={_e1:.2f}s lines={len(host_a_transcript)}")

    # ── Call 2: Host B (reacts to A) ────────────────────────────────────
    host_a_json_str = json.dumps(host_a_transcript, indent=2, ensure_ascii=False)
    llm_log.info(f"LLM_CALL_START | task=transcript_host_b show={show_name}")
    _t2 = _time.time()
    with dspy.context(lm=resolve.llm("transcript", show=show_name)):
        pred_b = _host_b_module(
            briefing=briefing_tagged,
            speaker=f"<speaker>\n{speaker_b_def}\n</speaker>",
            outline=outline_tagged,
            host_a_transcript=f"<host_a_transcript>\n{host_a_json_str}\n</host_a_transcript>",
        )
    _e2 = _time.time() - _t2
    host_b_transcript = _parse_json_response(pred_b.host_b_json, "Host B")
    for line in host_b_transcript:
        line["speaker"] = speaker_b.name.lower()
    llm_log.info(f"LLM_CALL_END | task=transcript_host_b duration={_e2:.2f}s lines={len(host_b_transcript)}")

    # ── Call 3: Merge ───────────────────────────────────────────────────
    host_b_json_str = json.dumps(host_b_transcript, indent=2, ensure_ascii=False)
    llm_log.info(f"LLM_CALL_START | task=transcript_merge show={show_name}")
    _t3 = _time.time()
    with dspy.context(lm=resolve.llm("transcript", show=show_name)):
        pred_merge = _merge_module(
            briefing=briefing_tagged,
            outline=outline_tagged,
            host_a_transcript=f"<host_a_transcript>\n{host_a_json_str}\n</host_a_transcript>",
            host_b_transcript=f"<host_b_transcript>\n{host_b_json_str}\n</host_b_transcript>",
            speaker_a_name=speaker_a.name.lower(),
            speaker_b_name=speaker_b.name.lower(),
        )
    _e3 = _time.time() - _t3
    merged = _parse_json_response(pred_merge.merged_json, "Merger")
    # Normalize speaker names — LLM may capitalize or use variants
    name_a = speaker_a.name.lower()
    name_b = speaker_b.name.lower()
    for line in merged:
        raw = (line.get("speaker") or "").strip().lower()
        if name_a in raw:
            line["speaker"] = name_a
        elif name_b in raw:
            line["speaker"] = name_b
        else:
            # Fallback: alternate assignment or default to host A
            line["speaker"] = name_a
    llm_log.info(
        f"LLM_CALL_END | task=transcript_merge duration={_e3:.2f}s "
        f"lines={len(merged)} (A={len(host_a_transcript)}, B={len(host_b_transcript)})"
    )

    total = _e1 + _e2 + _e3
    logger.info(
        f"Two-host transcript complete: {len(merged)} lines in {total:.2f}s "
        f"(A={_e1:.1f}s, B={_e2:.1f}s, merge={_e3:.1f}s)"
    )
    return merged


# ---------------------------------------------------------------------------
# TTS + stitching
# ---------------------------------------------------------------------------
#
# Audio output is configurable via env + per-show profile:
#   CURIA_AUDIO_BITRATE      MP3 bitrate (default '128k')
#   CURIA_STITCH_GAP_MS      silence between transcript lines (default 400)
#
# Per show profile (studio/shows/profiles.py):
#   intro_audio_path         pre-rendered WAV/MP3 crossfaded into the TTS body
#   outro_audio_path         crossfaded out from the TTS body
#   music_audio_path         looped + overlaid under the body at music_gain_db
#
# All three are optional; behavior with none set matches the pre-existing
# "speech only, 400 ms gaps" stitcher.
#
# Intro/outro crossfade constants — tune by ear, no code changes needed:
INTRO_FULL_MS     = 8000   # intro plays at full volume for this long
INTRO_FADE_MS     = 5000   # intro fades out over this long, overlapping TTS start
INTRO_GAIN_DB     = 4.0    # dB above episode level (intro punches over voice at open)
OUTRO_FADE_IN_MS  = 5000   # outro fades in under last N ms of TTS
OUTRO_FULL_MS     = 3000   # outro plays at full volume after TTS ends
OUTRO_FADE_OUT_MS = 2000   # outro fades to silence
OUTRO_GAIN_DB     = 0.0    # dB relative to episode level (matched, sits underneath)


def _load_optional_segment(path: str | None, label: str) -> "AudioSegment | None":
    """Load a sound file from disk if the path is set + the file exists. Logs and skips otherwise."""
    if not path:
        return None
    p = Path(path) if not Path(path).is_absolute() else Path(path)
    if not p.is_absolute():
        p = (CURIA_ROOT / path).resolve()
    if not p.exists():
        logger.warning(f"  {label}_audio_path set to {path} but file not found; skipping.")
        return None
    try:
        return AudioSegment.from_file(str(p))
    except Exception as e:
        logger.warning(f"  Could not decode {label} audio at {p}: {e}; skipping.")
        return None


def _overlay_music(body: "AudioSegment", music: "AudioSegment", gain_db: float) -> "AudioSegment":
    """Loop `music` to match `body` length, attenuate by gain_db, overlay under body."""
    if len(music) == 0:
        return body
    loops_needed = (len(body) // len(music)) + 1
    track = music * loops_needed
    track = track[: len(body)] + gain_db   # `+ gain_db` adjusts amplitude (negative = quieter)
    return body.overlay(track)


def _derive_display_fields(
    outline: dict, duration_seconds: int, intro_ms: int = 0
) -> tuple[str, list[dict]]:
    """Extract description and chapters from the outline dict.

    intro_ms: the number of milliseconds of intro music that precede the first
    spoken word in the final stitched MP3. Chapter 1 always starts at 0:00
    (it absorbs the intro visually); all subsequent chapters are shifted right
    by intro_ms so their scrubber positions match the stitched file.
    """
    description = outline.get("thread") or outline.get("central_tension") or ""
    segments = outline.get("segments") or []
    duration_minutes = duration_seconds / 60 if duration_seconds else 0
    segment_count = max(1, len(segments))
    intro_minutes = intro_ms / 60000  # fractional — do NOT round
    chapters = []
    for i, seg in enumerate(segments):
        # Evenly distribute segments over body duration, then shift by intro offset.
        # Chapter 1 (i=0) starts at 0:00 regardless — it absorbs the intro music.
        body_start = duration_minutes * i / segment_count
        start_minute = body_start if i == 0 else body_start + intro_minutes
        chapters.append({
            "id": f"segment-{seg.get('segment', i + 1)}",
            "title": seg.get("title", ""),
            "start_minute": start_minute,
        })
    return description, chapters


def _map_word_timings_to_lines(
    word_timings: list[dict],
    segment_text: str,
    line_indices: list[int],
    line_char_ranges: list[tuple[int, int]],
    segment_start_ms: int,
    transcript: list[dict],
) -> list[dict]:
    """
    Map word-level timestamps from a merged segment back to original transcript line indices.

    Word timings are in seconds relative to segment start.
    Returns line-level tts_timings: [{line_index, start_ms, end_ms, speaker, text}]
    """
    if not line_indices:
        return []

    if not word_timings:
        # Fallback: proportional split by character count
        total_chars = sum(end - start for start, end in line_char_ranges) or 1
        # We don't have duration here — caller will use clip length
        return []  # handled by caller's proportional fallback

    # Build list of (char_position, word_timing) by scanning segment_text for each word
    # We walk the text sequentially matching words to avoid index errors
    text_lower = segment_text.lower()
    cursor = 0
    word_char_positions: list[tuple[int, int]] = []  # (start_char, end_char) per word timing

    for wt in word_timings:
        word = (wt.get("word") or "").strip().lower()
        # Strip punctuation for matching
        word_stripped = word.strip(".,!?;:\"'()-")
        if not word_stripped:
            word_char_positions.append((cursor, cursor))
            continue
        # Find word in text starting from cursor
        idx = text_lower.find(word_stripped, cursor)
        if idx == -1:
            # Fuzzy: try from beginning of segment (handles minor mismatches)
            idx = text_lower.find(word_stripped, 0)
        if idx == -1:
            word_char_positions.append((cursor, cursor))
        else:
            end_idx = idx + len(word_stripped)
            word_char_positions.append((idx, end_idx))
            cursor = end_idx

    # Assign each word to a line based on char ranges
    line_word_timings: dict[int, list[dict]] = {idx: [] for idx in line_indices}
    for wt, (wchar_start, wchar_end) in zip(word_timings, word_char_positions):
        # Find which line range this word falls in
        best_idx = line_indices[0]
        for line_idx, (range_start, range_end) in zip(line_indices, line_char_ranges):
            if wchar_start >= range_start:
                best_idx = line_idx
        line_word_timings[best_idx].append(wt)

    # Build line-level timings
    result: list[dict] = []
    for line_idx, (range_start, range_end) in zip(line_indices, line_char_ranges):
        words = line_word_timings.get(line_idx, [])
        line_info = transcript[line_idx] if line_idx < len(transcript) else {}
        if words:
            start_ms = segment_start_ms + int(words[0].get("start", 0) * 1000)
            end_ms = segment_start_ms + int(words[-1].get("end", 0) * 1000)
        else:
            # Proportional fallback for lines with no matched words
            total_chars = sum(e - s for s, e in line_char_ranges) or 1
            line_chars = range_end - range_start
            # Use 0 duration as placeholder — will be filled by adjacent lines
            start_ms = segment_start_ms
            end_ms = segment_start_ms
        result.append({
            "line_index": line_idx,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "speaker": line_info.get("speaker", ""),
            "text": line_info.get("text", ""),
        })
    return result


def _proportional_line_timings(
    line_indices: list[int],
    line_char_ranges: list[tuple[int, int]],
    segment_start_ms: int,
    segment_duration_ms: int,
    transcript: list[dict],
) -> list[dict]:
    """Fallback when no word timings available — split by character proportion."""
    total_chars = sum(end - start for start, end in line_char_ranges) or 1
    result: list[dict] = []
    cursor = segment_start_ms
    for line_idx, (start_char, end_char) in zip(line_indices, line_char_ranges):
        line_chars = end_char - start_char
        line_duration = int(segment_duration_ms * line_chars / total_chars)
        line_info = transcript[line_idx] if line_idx < len(transcript) else {}
        result.append({
            "line_index": line_idx,
            "start_ms": cursor,
            "end_ms": cursor + line_duration,
            "speaker": line_info.get("speaker", ""),
            "text": line_info.get("text", ""),
        })
        cursor += line_duration
    return result


def synthesize_and_stitch_v2(
    transcript: list[dict],
    show_name: str,
    output_path: str,
    speaker_override: str | None = None,
    speaker_pair: list[str] | None = None,
) -> tuple[str, list[dict]]:
    """
    Segment-based synthesis — merges same-speaker lines into paragraphs,
    makes far fewer TTS calls, and produces more natural prosody.

    Returns (output_path, tts_timings) matching synthesize_and_stitch's signature.
    tts_timings: [{line_index, start_ms, end_ms, speaker, text}]

    Key improvements over v1:
    - Reuses TTS adapter per speaker → generation_id chaining (voice consistency)
    - Word-level timestamps from Hume/Smallest.ai → accurate line timings
    - Proportional fallback for other providers
    """
    from core.audio.stitcher import prepare_segments

    profile = SHOW_PROFILES[show_name]
    if speaker_override and speaker_override in SPEAKER_PROFILES:
        allowed_speakers = {speaker_override.lower()}
    elif speaker_pair and len(speaker_pair) == 2:
        allowed_speakers = {s.lower() for s in speaker_pair}
    else:
        allowed_speakers = {s.name.lower() for s in profile.speaker_config.speakers}

    bitrate = os.getenv("CURIA_AUDIO_BITRATE", "128k")
    gap_ms = int(os.getenv("CURIA_STITCH_GAP_MS", "400"))

    segments = prepare_segments(transcript)
    logger.info(
        f"Synthesizing {len(transcript)} lines as {len(segments)} segments "
        f"(bitrate={bitrate}, gap={gap_ms}ms)..."
    )

    clips: list[AudioSegment] = []
    segment_word_timings: list[list[dict]] = []

    # Reuse one adapter per speaker — preserves generation_id state for voice consistency
    adapters: dict[str, object] = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        for i, seg in enumerate(segments):
            raw_speaker = seg["speaker"]
            if raw_speaker not in allowed_speakers:
                raw_speaker = next(iter(allowed_speakers))

            # Get or create adapter for this speaker
            if raw_speaker not in adapters:
                adapters[raw_speaker] = _resolve_config.tts(speaker=raw_speaker)
            adapter = adapters[raw_speaker]

            clip_path = os.path.join(tmpdir, f"segment_{i:04d}.wav")
            logger.info(f"  [{i+1}/{len(segments)}] {raw_speaker}: {seg['text'][:60]}...")

            word_timings = adapter.synthesize_with_timings(seg["text"], clip_path)
            segment_word_timings.append(word_timings)

            fmt = adapter.output_format
            if fmt == "mp3":
                clips.append(AudioSegment.from_mp3(clip_path))
            else:
                clips.append(AudioSegment.from_wav(clip_path))

        logger.info("Stitching segments...")
        gap = AudioSegment.silent(duration=gap_ms)
        body = AudioSegment.empty()
        for clip in clips:
            body += clip + gap

        intro = _load_optional_segment(profile.intro_audio_path, "intro")
        outro = _load_optional_segment(profile.outro_audio_path, "outro")
        music = _load_optional_segment(profile.music_audio_path, "music")

        intro_offset_ms = 0

        if intro is not None:
            logger.info(f"  crossfading intro ({len(intro)/1000:.1f}s source)")
            episode_dbfs = body.dBFS
            intro_gain = (episode_dbfs - intro.dBFS + INTRO_GAIN_DB) if intro.dBFS != float("-inf") else 0
            intro = intro.apply_gain(intro_gain)
            intro_full_clip = intro[:INTRO_FULL_MS]
            intro_fade_clip = intro[INTRO_FULL_MS: INTRO_FULL_MS + INTRO_FADE_MS].fade_out(INTRO_FADE_MS)
            body = intro_full_clip + body
            body = body.overlay(intro_fade_clip, position=INTRO_FULL_MS)
            intro_offset_ms = INTRO_FULL_MS

        if outro is not None:
            logger.info(f"  crossfading outro ({len(outro)/1000:.1f}s source)")
            episode_dbfs = body.dBFS
            outro_gain = (episode_dbfs - outro.dBFS + OUTRO_GAIN_DB) if outro.dBFS != float("-inf") else 0
            outro = outro.apply_gain(outro_gain)
            outro_fade_in   = outro[:OUTRO_FADE_IN_MS].fade_in(OUTRO_FADE_IN_MS)
            outro_full_clip = outro[OUTRO_FADE_IN_MS: OUTRO_FADE_IN_MS + OUTRO_FULL_MS]
            outro_fade_out  = outro[OUTRO_FADE_IN_MS + OUTRO_FULL_MS: OUTRO_FADE_IN_MS + OUTRO_FULL_MS + OUTRO_FADE_OUT_MS].fade_out(OUTRO_FADE_OUT_MS)
            outro_ready = outro_fade_in + outro_full_clip + outro_fade_out
            tts_end_pos   = len(body)
            outro_start_pos = tts_end_pos - OUTRO_FADE_IN_MS
            tail_ms = OUTRO_FULL_MS + OUTRO_FADE_OUT_MS
            body = body + AudioSegment.silent(duration=tail_ms)
            body = body.overlay(outro_ready, position=max(0, outro_start_pos))

        if music is not None:
            logger.info(
                f"  overlaying music ({len(music)/1000:.1f}s loop) "
                f"at {profile.music_gain_db:+.1f} dB"
            )
            body = _overlay_music(body, music, profile.music_gain_db)

        body.export(output_path, format="mp3", bitrate=bitrate)

    # Build tts_timings — map word timestamps back to original transcript line indices
    tts_timings: list[dict] = []
    cursor_ms = intro_offset_ms

    for seg, clip, word_timings in zip(segments, clips, segment_word_timings):
        clip_ms = len(clip)
        line_indices = seg.get("line_indices", [])
        line_char_ranges = seg.get("line_char_ranges", [])

        if word_timings and line_indices:
            line_entries = _map_word_timings_to_lines(
                word_timings=word_timings,
                segment_text=seg["text"],
                line_indices=line_indices,
                line_char_ranges=line_char_ranges,
                segment_start_ms=cursor_ms,
                transcript=transcript,
            )
            tts_timings.extend(line_entries)
        elif line_indices:
            # No word timings — proportional fallback
            tts_timings.extend(_proportional_line_timings(
                line_indices=line_indices,
                line_char_ranges=line_char_ranges,
                segment_start_ms=cursor_ms,
                segment_duration_ms=clip_ms,
                transcript=transcript,
            ))

        cursor_ms += clip_ms + gap_ms

    # Sort by line_index to match expected order
    tts_timings.sort(key=lambda x: x["line_index"])

    # Deduplicate: a line that straddles a chunk boundary gets an entry from each chunk.
    # Keep the entry with the largest duration (the chunk containing most of the line).
    seen: dict[int, dict] = {}
    for entry in tts_timings:
        idx = entry["line_index"]
        if idx not in seen or (entry["end_ms"] - entry["start_ms"]) > (seen[idx]["end_ms"] - seen[idx]["start_ms"]):
            seen[idx] = entry
    tts_timings = [seen[k] for k in sorted(seen)]

    logger.info(f"Audio exported: {output_path} ({len(body)/1000:.1f}s), {len(tts_timings)} timing entries")
    return output_path, tts_timings


# ---------------------------------------------------------------------------
# Episode save + post-generation
# ---------------------------------------------------------------------------

def _coerce_source_uuids(source_ids: list) -> list:
    """Strip 'source:' prefixes (legacy), deduplicate (preserve order), return UUID list."""
    from uuid import UUID
    seen: set[str] = set()
    out = []
    for sid in source_ids or []:
        s = str(sid).replace("source:", "")
        try:
            u = UUID(s)
            if s not in seen:
                seen.add(s)
                out.append(u)
        except (ValueError, TypeError):
            logger.warning(f"Skipping non-UUID source_id: {sid!r}")
    return out


async def save_episode(
    user_id: str,
    show_name: str,
    title: str,
    transcript: list[dict],
    audio_path: str,
    source_ids: list[str],
    editorial_direction: str,
) -> str:
    episode_id = str(uuid.uuid4())
    transcript_json = json.dumps(transcript)
    source_uuids = _coerce_source_uuids(source_ids)

    await db_execute(
        """
        INSERT INTO episode
            (id, user_id, show_name, title, transcript, audio_path, source_ids, editorial_direction)
        VALUES
            ($id, $user_id, $show_name, $title, $transcript::jsonb, $audio_path, $source_ids, $editorial_direction)
        """,
        {
            "id": episode_id,
            "user_id": user_id,
            "show_name": show_name,
            "title": title,
            "transcript": transcript_json,
            "audio_path": audio_path,
            "source_ids": source_uuids,
            "editorial_direction": editorial_direction,
        },
    )
    logger.info(f"Episode saved: {episode_id}")
    return episode_id


async def log_covered_topics(
    user_id: str,
    show_name: str,
    episode_id: str,
    outline: dict,
    source_ids: list[str],
):
    topics = outline.get("thread", outline.get("central_tension", ""))
    source_uuids = _coerce_source_uuids(source_ids)
    await db_execute(
        """
        INSERT INTO covered_topic (user_id, show_name, episode_id, topics, source_ids)
        VALUES ($user_id, $show_name, $episode_id, $topics, $source_ids)
        """,
        {
            "user_id": user_id,
            "show_name": show_name,
            "episode_id": episode_id,
            "topics": topics,
            "source_ids": source_uuids,
        },
    )
    logger.info(f"Covered topics logged for episode {episode_id}")


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

async def _set_episode_status(
    episode_id: str, status: str, error: str | None = None
) -> None:
    await db_execute(
        """
        UPDATE episode
        SET status = $status, error = $error
        WHERE id = $id::uuid
        """,
        {"id": episode_id, "status": status, "error": error},
    )


async def _resolve_sources_for_episode(
    user_id: str,
    editorial_direction: str,
    show_idea_id: str | None,
    user_kb: UserKB | None = None,
) -> tuple[list[dict], dict[str, dict]]:
    """
    Either pull sources from a show_idea (override path) or run the selector against
    the user's archive. Returns (sources, insights).

    KB is forwarded to the selector so it can derive an editorial_direction from
    the user's current_obsession / interests when none was provided.
    """
    if show_idea_id:
        idea_row = await db_fetchrow(
            "SELECT source_ids FROM show_idea WHERE id = $id::uuid",
            {"id": str(show_idea_id)},
        )
        if not idea_row:
            raise ValueError(f"show_idea {show_idea_id} not found")
        bare_override_ids = [str(s).replace("source:", "") for s in (idea_row.get("source_ids") or [])]
        if not bare_override_ids:
            raise ValueError(f"show_idea {show_idea_id} has no source_ids")
        sources = await db_query(
            "SELECT id, title FROM source WHERE id = ANY($ids::uuid[])",
            {"ids": bare_override_ids},
        )
        bare_ids = [str(s.get("id", "")).replace("source:", "") for s in sources]
        insights = await get_source_insights(bare_ids)
        return sources, insights

    return await select_episode_sources(
        user_id=user_id,
        editorial_direction=editorial_direction,
        n=12,
        user_kb=user_kb,
    )


async def process_episode(episode_id: str) -> None:
    """
    Process an episode row that already exists in the DB (status='queued').
    Reads show_name + show_idea_id + editorial_direction from the row, runs the full
    pipeline, updates the row with title/transcript/outline/audio_path/status='ready'.

    On any exception: status='failed' + error column set, then re-raises.

    Used by the worker handler. Idempotent for re-runs that fall through to ready
    (audio gets re-rendered; previous file is overwritten).
    """
    row = await db_fetchrow(
        """
        SELECT user_id, show_name, show_idea_id, editorial_direction,
               length_minutes, speaker_override, speaker_pair
        FROM episode WHERE id = $id::uuid
        """,
        {"id": episode_id},
    )
    if not row:
        raise ValueError(f"episode {episode_id} not found")

    user_id = row["user_id"]
    show_name = row["show_name"]
    show_idea_id = row.get("show_idea_id")
    editorial_direction = row.get("editorial_direction") or ""
    length_override: int | None = row.get("length_minutes")
    speaker_override: str | None = row.get("speaker_override")
    speaker_pair: list[str] | None = row.get("speaker_pair")

    if show_name not in SHOW_PROFILES:
        raise ValueError(
            f"Unknown show_name '{show_name}'. Known: {list(SHOW_PROFILES)}"
        )

    import time as _ep_time
    _ep_start = _ep_time.time()
    worker_log = logger.bind(log_type="worker")

    profile = SHOW_PROFILES[show_name]

    # If a speaker_pair override was provided (and no single-host override),
    # build a dynamic two-host profile using the priority-sorted pair.
    # Priority is already sorted at the API layer (kenji=1, arjun=2, emeka=3).
    if speaker_pair and len(speaker_pair) == 2 and not speaker_override:
        a, b = speaker_pair
        if a not in SPEAKER_PROFILES or b not in SPEAKER_PROFILES:
            logger.warning(f"  unknown speaker in pair {speaker_pair}, falling back to profile default")
        else:
            speaker_a = SPEAKER_PROFILES[a].speakers[0]
            speaker_b = SPEAKER_PROFILES[b].speakers[0]
            dynamic_config = SpeakerProfile(
                name=f"{a}_{b}",
                speakers=[speaker_a, speaker_b],
            )
            profile = EpisodeProfile(
                name=profile.name,
                format_name=profile.format_name,
                language=profile.language,
                speaker_config=dynamic_config,
                intro_audio_path=profile.intro_audio_path,
                outro_audio_path=profile.outro_audio_path,
                music_audio_path=profile.music_audio_path,
                music_gain_db=profile.music_gain_db,
            )
            logger.info(f"  speaker_pair override → {a} (role A) + {b} (role B)")
    logger.info(f"--- Processing episode {episode_id} (show={show_name}, user={user_id}) ---")
    worker_log.info(
        f"EPISODE_START | id={episode_id} show={show_name} user={user_id}"
    )

    # Load the user's KB once; propagate to selector, briefing, transcript.
    try:
        user_kb = await load_kb(user_id)
    except Exception as e:
        logger.warning(f"  could not load KB for {user_id}: {e}; proceeding without")
        user_kb = None

    try:
        # 1. Select sources (KB seeds editorial direction when none was provided)
        await _set_episode_status(episode_id, "selecting")
        sources, insights = await _resolve_sources_for_episode(
            user_id=user_id,
            editorial_direction=editorial_direction,
            show_idea_id=str(show_idea_id) if show_idea_id else None,
            user_kb=user_kb,
        )
        source_ids = [str(s.get("id", "")) for s in sources]

        # 2. Build briefing packet (KB injects listener_context + length override)
        packet = build_briefing_packet(
            format_name=profile.format_name,
            sources=sources,
            insights=insights,
            editorial_direction=editorial_direction,
            user_kb=user_kb,
            length_override=length_override,
        )
        briefing = briefing_packet_to_str(packet)

        # 3. Generate outline (sync DSPy call — offload to thread pool)
        await _set_episode_status(episode_id, "outlining")
        loop = asyncio.get_running_loop()
        outline = await loop.run_in_executor(
            None, generate_outline, briefing, show_name
        )
        title = outline.get("title", show_name)

        # 4. Generate transcript — pick single-host or two-host based on speaker count
        await _set_episode_status(episode_id, "transcribing")
        is_two_host = len(profile.speaker_config.speakers) >= 2 and speaker_override is None
        if is_two_host:
            logger.info(f"  two-host pipeline (speakers: {[s.name for s in profile.speaker_config.speakers]})")
            transcript = await loop.run_in_executor(
                None, generate_transcript_two_host,
                briefing, outline, show_name, user_kb, profile,
            )
        else:
            transcript = await loop.run_in_executor(
                None, generate_transcript,
                briefing, outline, show_name, user_kb, speaker_override,
            )

        judgment = await _rubric_judge(
            task="transcript",
            output=json.dumps(transcript, ensure_ascii=False),
            user_id=user_id,
        )
        regenerated = False
        if (
            QUALITY_REROLL_ENABLED
            and judgment.overall_score < QUALITY_THRESHOLD
        ):
            logger.info(
                f"  judge score {judgment.overall_score:.2f} < threshold "
                f"{QUALITY_THRESHOLD} — re-rolling transcript once "
                f"(violations: {judgment.floor_violations})"
            )

            # Save v1
            transcript_v1 = transcript
            judgment_v1 = judgment

            # Generate v2
            if is_two_host:
                transcript_v2 = await loop.run_in_executor(
                    None, generate_transcript_two_host,
                    briefing, outline, show_name, user_kb, profile,
                )
            else:
                transcript_v2 = await loop.run_in_executor(
                    None, generate_transcript,
                    briefing, outline, show_name, user_kb, speaker_override,
                )
            judgment_v2 = await _rubric_judge(
                task="transcript",
                output=json.dumps(transcript_v2, ensure_ascii=False),
                user_id=user_id,
            )

            # Keep whichever scores better
            if judgment_v2.overall_score >= judgment_v1.overall_score:
                transcript = transcript_v2
                judgment = judgment_v2
                logger.info(f"  re-roll improved: {judgment_v1.overall_score:.2f} → {judgment_v2.overall_score:.2f}")
            else:
                transcript = transcript_v1
                judgment = judgment_v1
                logger.info(f"  re-roll was worse: {judgment_v1.overall_score:.2f} → {judgment_v2.overall_score:.2f}, keeping original")

            regenerated = True
        logger.info(
            f"  final judge score: {judgment.overall_score:.2f} "
            f"(pref={judgment.preference_score:.2f}, "
            f"floor_violations={len(judgment.floor_violations)})"
        )

        # 5. Synthesize + stitch (sync TTS + pydub — offload to thread pool)
        await _set_episode_status(episode_id, "synthesizing")
        audio_path = str(EPISODES_DIR / f"{episode_id}.mp3")
        _, tts_timings = await loop.run_in_executor(
            None, synthesize_and_stitch_v2,
            transcript, show_name, audio_path, speaker_override, speaker_pair,
        )

        # 6. Persist results onto the existing row
        # Derive actual duration from the stitched MP3
        actual_duration_seconds: int | None = None
        actual_length_minutes: int | None = None
        try:
            from pydub import AudioSegment as _AS
            _audio = await asyncio.to_thread(_AS.from_mp3, audio_path)
            actual_duration_seconds = int(_audio.duration_seconds)
            actual_length_minutes = max(1, round(_audio.duration_seconds / 60))
        except Exception:
            pass

        # Upload to R2/S3 if configured; fall back to local disk path
        audio_url: str | None = None
        from core.storage.blob import get_storage_backend, upload_file
        if get_storage_backend() == "s3":
            try:
                r2_key = f"audio/{episode_id}.mp3"
                await upload_file(audio_path, r2_key, content_type="audio/mpeg")
                audio_url = r2_key
                logger.info(f"[process_episode] audio uploaded to R2: {r2_key}")
            except Exception as upload_err:
                logger.error(f"[process_episode] R2 upload failed, keeping local path: {upload_err}")

        # Pass intro_ms so chapter startMinutes are offset correctly in the stitched file
        intro_ms = tts_timings[0]["start_ms"] if tts_timings else 0
        description, chapters = _derive_display_fields(outline, actual_duration_seconds or 0, intro_ms=intro_ms)

        source_uuids = _coerce_source_uuids(source_ids)
        await db_execute(
            """
            UPDATE episode
            SET title = $title,
                transcript = $transcript::jsonb,
                outline = $outline::jsonb,
                audio_path = $audio_path,
                audio_url = $audio_url,
                source_ids = $source_ids,
                quality_score = $score,
                quality_feedback = $feedback,
                quality_violations = $violations,
                regenerated = $regenerated,
                tts_timings = $tts_timings::jsonb,
                length_minutes = $length_minutes,
                duration_seconds = $duration_seconds,
                description = $description,
                chapters = $chapters::jsonb,
                status = 'ready',
                error = NULL
            WHERE id = $id::uuid
            """,
            {
                "id": episode_id,
                "title": title,
                "transcript": json.dumps(transcript),
                "outline": json.dumps(outline),
                "audio_path": audio_path,
                "audio_url": audio_url,
                "source_ids": source_uuids,
                "score": judgment.overall_score,
                "feedback": judgment.feedback,
                "violations": judgment.floor_violations,
                "regenerated": regenerated,
                "tts_timings": json.dumps(tts_timings),
                "length_minutes": actual_length_minutes,
                "duration_seconds": actual_duration_seconds,
                "description": description,
                "chapters": json.dumps(chapters),
            },
        )

        # 7. Mark idea as generated (if applicable) + log covered topic
        if show_idea_id:
            await db_execute(
                "UPDATE show_idea SET generated = true WHERE id = $id::uuid",
                {"id": str(show_idea_id)},
            )
        try:
            await log_covered_topics(user_id, show_name, episode_id, outline, source_ids)
        except Exception as _ct_err:
            logger.warning(f"covered_topic log skipped for {episode_id}: {_ct_err}")

        _ep_elapsed = _ep_time.time() - _ep_start
        logger.info(f"--- Done: episode {episode_id} title='{title}' ---")
        worker_log.info(
            f"EPISODE_SUCCESS | id={episode_id} show={show_name} "
            f"title={title} duration={_ep_elapsed:.2f}s "
            f"quality_score={judgment.overall_score:.2f}"
        )

    except Exception as e:
        _ep_elapsed = _ep_time.time() - _ep_start
        worker_log.error(
            f"EPISODE_FAIL | id={episode_id} show={show_name} "
            f"duration={_ep_elapsed:.2f}s error={e}"
        )
        await _set_episode_status(episode_id, "failed", error=str(e)[:1000])
        raise


async def generate_standing_show(
    show_name: str,
    user_id: str = "default",
    editorial_direction: str = "",
    source_ids_override: list[str] | None = None,
) -> dict:
    """
    CLI-friendly wrapper: creates an episode row + calls process_episode.

    `source_ids_override` triggers the show_idea path (creates an inline temporary
    show_idea or just uses raw IDs — for v1 we use them directly via process_episode's
    show_idea_id path; CLI can pre-create the show_idea if needed).
    """
    if show_name not in SHOW_PROFILES:
        raise ValueError(f"Unknown show_name '{show_name}'")

    if source_ids_override:
        # Convenience: stash an ad-hoc show_idea so process_episode can pick it up
        show_idea_id = str(uuid.uuid4())
        await db_execute(
            """
            INSERT INTO show_idea (id, user_id, angle, idea_type, format, source_ids, generated)
            VALUES ($id::uuid, $user_id, $angle, 'standalone', $format, $source_ids, false)
            """,
            {
                "id": show_idea_id,
                "user_id": user_id,
                "angle": editorial_direction or "(cli override)",
                "format": SHOW_PROFILES[show_name].format_name,
                "source_ids": _coerce_source_uuids(source_ids_override),
            },
        )
    else:
        show_idea_id = None

    episode_id = str(uuid.uuid4())
    await db_execute(
        """
        INSERT INTO episode (id, user_id, show_name, show_idea_id, editorial_direction, status)
        VALUES ($id::uuid, $user_id, $show_name, $show_idea_id::uuid, $direction, 'queued')
        """,
        {
            "id": episode_id,
            "user_id": user_id,
            "show_name": show_name,
            "show_idea_id": show_idea_id,
            "direction": editorial_direction,
        },
    )
    await process_episode(episode_id)

    row = await db_fetchrow(
        "SELECT title, audio_path FROM episode WHERE id = $id::uuid",
        {"id": episode_id},
    )
    return {
        "episode_id": episode_id,
        "title": row.get("title") if row else show_name,
        "audio_path": row.get("audio_path") if row else None,
        "show_name": show_name,
    }
