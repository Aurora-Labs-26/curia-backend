"""
studio/generator.py
Full generation pipeline for standing shows.
selector → briefing → outline → transcript → XTTS → MP3 → save episode → update memory
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import anthropic
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

from shows.profiles import SHOW_PROFILES
from shows.prompts import OUTLINE_PROMPT, TRANSCRIPT_PROMPT
from briefing_builder import build_briefing_packet, briefing_packet_to_str
from intelligence.selector import select_episode_sources
from core.db.connection import db_query

XTTS_PYTHON = "/Users/bhabanimohapatra/Documents/Projects/Hypothesis/progress/xtts_env/bin/python"
EPISODES_DIR = Path(__file__).parent / "episodes"
EPISODES_DIR.mkdir(exist_ok=True)

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


# ---------------------------------------------------------------------------
# LLM calls
# ---------------------------------------------------------------------------

def generate_outline(briefing: str, show_name: str) -> dict:
    profile = SHOW_PROFILES[show_name]
    logger.info(f"Generating outline via {profile.outline_llm}...")
    message = client.messages.create(
        model=profile.outline_llm,
        max_tokens=1024,
        system=OUTLINE_PROMPT,
        messages=[{"role": "user", "content": briefing}]
    )
    raw = message.content[0].text.strip()
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
        allowed_segment_keys = {"segment", "purpose", "primitives_used", "transition"}
        for seg in outline.get("segments", []):
            for k in list(seg.keys()):
                if k not in allowed_segment_keys:
                    del seg[k]
    except (json.JSONDecodeError, Exception) as e:
        logger.warning(f"Outline JSON parse failed ({e}), raw response:\n{raw[:300]}")
        outline = {"title": show_name, "thread": "Follow the material.", "segments": [{"segment": i+1, "purpose": f"Segment {i+1}", "primitives_used": [], "transition": ""} for i in range(8)]}
    logger.info(f"Outline: '{outline.get('title', 'untitled')}' — {len(outline.get('segments', []))} segments")
    return outline


def generate_transcript(briefing: str, outline: dict, show_name: str) -> list[dict]:
    profile = SHOW_PROFILES[show_name]
    logger.info(f"Generating transcript via {profile.transcript_llm}...")
    speaker = profile.speaker_config.speakers[0]
    human = f"SPEAKER:\nName: {speaker.name}\nBackstory: {speaker.backstory}\nSpeech patterns: {speaker.speech_patterns}\n\nBRIEFING:\n{briefing}\n\nEPISODE OUTLINE:\n{json.dumps(outline, indent=2)}"
    message = client.messages.create(
        model=profile.transcript_llm,
        max_tokens=6144,
        system=TRANSCRIPT_PROMPT,
        messages=[{"role": "user", "content": human}]
    )
    raw = message.content[0].text.strip()
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


# ---------------------------------------------------------------------------
# TTS + stitching
# ---------------------------------------------------------------------------

def synthesize_line(text: str, voice_id: str, output_path: str):
    script = f"""
import sys
sys.path.insert(0, '/Users/bhabanimohapatra/Documents/Projects/Hypothesis/progress/xtts_env/lib/python3.11/site-packages')
import torch
_orig = torch.load
def _patched(*args, **kwargs):
    kwargs.setdefault('weights_only', False)
    return _orig(*args, **kwargs)
torch.load = _patched
from TTS.api import TTS
tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2", gpu=False)
tts.tts_to_file(text={json.dumps(text)}, speaker_wav={json.dumps(voice_id)}, language="en", file_path={json.dumps(output_path)})
"""
    result = subprocess.run([XTTS_PYTHON, "-c", script], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"XTTS failed:\n{result.stderr[-300:]}")


def synthesize_and_stitch(transcript: list[dict], show_name: str, output_path: str) -> str:
    profile = SHOW_PROFILES[show_name]
    voice_map = {s.name: s.voice_id for s in profile.speaker_config.speakers}

    logger.info(f"Synthesizing {len(transcript)} lines...")
    clips = []

    with tempfile.TemporaryDirectory() as tmpdir:
        for i, line in enumerate(transcript):
            speaker = line["speaker"]
            voice_id = voice_map.get(speaker)
            if not voice_id:
                raise ValueError(f"No voice_id for speaker '{speaker}'")
            clip_path = os.path.join(tmpdir, f"line_{i:04d}.wav")
            logger.info(f"  [{i+1}/{len(transcript)}] {speaker}: {line['text'][:60]}...")
            synthesize_line(line["text"], voice_id, clip_path)
            clips.append(AudioSegment.from_wav(clip_path))

        logger.info("Stitching audio...")
        combined = AudioSegment.empty()
        gap = AudioSegment.silent(duration=400)
        for clip in clips:
            combined += clip + gap
        combined.export(output_path, format="mp3")

    logger.info(f"Audio exported: {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# Episode save + post-generation
# ---------------------------------------------------------------------------

async def save_episode(
    user_id: str,
    show_name: str,
    title: str,
    transcript: list[dict],
    audio_path: str,
    source_ids: list[str],
    editorial_direction: str
) -> str:
    episode_id = str(uuid.uuid4()).replace("-", "")
    transcript_str = json.dumps(transcript)

    await db_query(
        """CREATE episode SET
            id = $id, user_id = $user_id, show_name = $show_name,
            title = $title, transcript = $transcript, audio_path = $audio_path,
            source_ids = $source_ids, editorial_direction = $editorial_direction,
            created_at = time::now()""",
        {
            "id": episode_id,
            "user_id": user_id,
            "show_name": show_name,
            "title": title,
            "transcript": transcript_str,
            "audio_path": audio_path,
            "source_ids": source_ids,
            "editorial_direction": editorial_direction,
        }
    )
    logger.info(f"Episode saved: {episode_id}")
    return episode_id


async def log_covered_topics(
    user_id: str,
    show_name: str,
    episode_id: str,
    outline: dict,
    source_ids: list[str]
):
    topics = outline.get("thread", outline.get("central_tension", ""))
    await db_query(
        """CREATE covered_topic SET
            user_id = $user_id, show_name = $show_name, episode_id = $episode_id,
            topics = $topics, source_ids = $source_ids, created_at = time::now()""",
        {
            "user_id": user_id,
            "show_name": show_name,
            "episode_id": episode_id,
            "topics": topics,
            "source_ids": source_ids,
        }
    )
    logger.info(f"Covered topics logged for episode {episode_id}")


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

async def generate_standing_show(
    show_name: str,
    user_id: str = "default",
    editorial_direction: str = "",
    source_ids_override: list[str] = None,  # for show_idea episodes
) -> dict:
    """
    Full pipeline: selector → briefing → outline → transcript → audio → save.
    Returns dict with episode_id and audio_path.
    """
    profile = SHOW_PROFILES[show_name]
    logger.info(f"--- Generating: {show_name} for user {user_id} ---")

    # 1. Select sources
    if source_ids_override:
        # show_idea path — sources pre-decided
        from core.db.connection import db_query as q
        sources_raw = await q(
            "SELECT id, title FROM source WHERE id IN $ids",
            {"ids": source_ids_override}
        )
        from intelligence.selector import get_source_insights
        bare_ids = [str(s.get("id", "")).replace("source:", "") for s in sources_raw]
        insights = await get_source_insights(bare_ids)
        sources = sources_raw
    else:
        sources, insights = await select_episode_sources(
            user_id=user_id,
            editorial_direction=editorial_direction,
            n=12
        )

    source_ids = [str(s.get("id", "")) for s in sources]

    # 2. Build briefing packet
    packet = build_briefing_packet(
        format_name=profile.format_name,
        sources=sources,
        insights=insights,
        editorial_direction=editorial_direction,
    )
    briefing = briefing_packet_to_str(packet)

    # 3. Generate outline (Haiku)
    outline = generate_outline(briefing, show_name)
    title = outline.get("title", show_name)

    # 4. Generate transcript (Sonnet)
    transcript = generate_transcript(briefing, outline, show_name)

    # 5. Synthesize + stitch
    episode_id = str(uuid.uuid4()).replace("-", "")
    audio_path = str(EPISODES_DIR / f"{episode_id}.mp3")
    synthesize_and_stitch(transcript, show_name, audio_path)

    # 6. Save episode
    saved_id = await save_episode(
        user_id=user_id,
        show_name=show_name,
        title=title,
        transcript=transcript,
        audio_path=audio_path,
        source_ids=source_ids,
        editorial_direction=editorial_direction
    )

    # 7. Log covered topics
    await log_covered_topics(user_id, show_name, saved_id, outline, source_ids)

    logger.info(f"--- Done: {show_name} / {saved_id} ---")
    return {
        "episode_id": saved_id,
        "title": title,
        "audio_path": audio_path,
        "show_name": show_name,
        "source_count": len(sources),
    }
