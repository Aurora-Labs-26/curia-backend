"""
tests/test_e2e_generate.py
End-to-end generation tests — builds briefing, generates outline + transcript
for all 4 formats, and produces audio files.

Requires:
  - DATABASE_URL env var pointing to a Postgres instance with source data
  - ANTHROPIC_API_KEY (or whatever LLM provider is configured)
  - HUME_API_KEY for real audio (falls back to silent WAV stubs without it)

Usage:
    DATABASE_URL="postgresql://..." ANTHROPIC_API_KEY="..." python -m pytest tests/test_e2e_generate.py -v -s
"""

import json
import os
import time
from pathlib import Path

import pytest
import pytest_asyncio

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

DB_URL = os.getenv("DATABASE_URL", "")
OUTPUT_DIR = Path("data/audio")

# Skip entire module if no DB URL
pytestmark = pytest.mark.skipif(not DB_URL, reason="DATABASE_URL not set — skipping e2e tests")


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

_cached_source_ids: list[str] | None = None


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    """Reset the DB pool per test so the connection matches the current event loop."""
    import core.db.connection as db
    db.DATABASE_URL = DB_URL
    db._pool = None
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    yield
    try:
        await db.close_pool()
    except Exception:
        pass


@pytest_asyncio.fixture()
async def source_ids():
    """Auto-detect best test user and fetch 3 source IDs with insights. Cached across tests."""
    global _cached_source_ids
    if _cached_source_ids is not None:
        return _cached_source_ids

    from core.db.connection import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        best = await conn.fetchrow("""
            SELECT s.user_id, COUNT(*) as cnt
            FROM source s
            WHERE s.status = 'ready' AND s.hidden = false
            AND (SELECT count(*) FROM source_insight si WHERE si.source_id = s.id) > 0
            GROUP BY s.user_id
            ORDER BY cnt DESC LIMIT 1
        """)
        if not best or best["cnt"] < 2:
            pytest.skip("No user with enough sources+insights in this database")
        user_id = best["user_id"]

        rows = await conn.fetch("""
            SELECT s.id FROM source s
            WHERE s.user_id = $1 AND s.status = 'ready' AND s.hidden = false
            AND (SELECT count(*) FROM source_insight si WHERE si.source_id = s.id) > 0
            ORDER BY s.created_at DESC LIMIT 3
        """, user_id)
    ids = [str(r["id"]) for r in rows]
    if len(ids) < 2:
        pytest.skip(f"Need at least 2 sources with insights, found {len(ids)}")
    _cached_source_ids = ids
    print(f"\n[e2e] Using user {user_id[:12]}... with {len(ids)} sources")
    return ids


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

async def _build_briefing(format_name: str, source_ids: list[str], length_minutes: int = 8):
    """Build a briefing packet from real DB data."""
    from intelligence.selector import get_source_insights
    from core.db.connection import get_pool
    from studio.briefing_builder import build_briefing_packet, briefing_packet_to_str
    from uuid import UUID

    uuids = [UUID(s) for s in source_ids]
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, title, url FROM source WHERE id = ANY($1::uuid[])", uuids
        )
    sources = [dict(r) for r in rows]
    insights = await get_source_insights(source_ids)
    packet = build_briefing_packet(
        format_name=format_name,
        sources=sources,
        insights=insights,
        editorial_direction="Focus on the most surprising or counterintuitive claim.",
        length_override=length_minutes,
    )
    return briefing_packet_to_str(packet)


def _generate_outline(briefing: str, show_name: str) -> dict:
    from studio.generator import generate_outline
    return generate_outline(briefing, show_name)


def _generate_single_host(briefing, outline, show_name, user_kb=None, speaker_override=None):
    from studio.generator import generate_transcript
    return generate_transcript(briefing, outline, show_name, user_kb, speaker_override)


def _generate_two_host(briefing, outline, show_name, user_kb=None):
    from studio.generator import generate_transcript_two_host
    return generate_transcript_two_host(briefing, outline, show_name, user_kb)


async def _synthesize(transcript, show_name, output_path, speaker_override=None):
    """Run TTS + stitching via v2 sync path in a thread."""
    import asyncio
    from studio.generator import synthesize_and_stitch_v2
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, synthesize_and_stitch_v2,
        transcript, show_name, output_path, speaker_override,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tests — one per format
# ─────────────────────────────────────────────────────────────────────────────


class TestNarrativeDrift:
    """Single-host format — kenji solo."""

    @pytest.mark.asyncio
    async def test_generate(self, source_ids):
        show = "narrative_drift"
        t0 = time.time()

        briefing = await _build_briefing(show, source_ids, length_minutes=6)
        outline = _generate_outline(briefing, show)
        assert "title" in outline
        assert len(outline.get("segments", [])) > 0
        print(f"\n[{show}] Outline: {outline['title']} ({len(outline['segments'])} segments)")

        transcript = _generate_single_host(briefing, outline, show)
        assert len(transcript) > 10
        speakers = {line["speaker"].lower() for line in transcript}
        assert len(speakers) == 1, f"Expected 1 speaker, got {speakers}"
        print(f"[{show}] Transcript: {len(transcript)} lines, speaker={speakers.pop()}")

        out_path = str(OUTPUT_DIR / f"test_e2e_{show}.mp3")
        await _synthesize(transcript, show, out_path)
        assert Path(out_path).exists()
        size_kb = Path(out_path).stat().st_size / 1024
        print(f"[{show}] Audio: {out_path} ({size_kb:.0f} KB) in {time.time()-t0:.1f}s")


class TestClarityEngine:
    """Two-host format — kenji (teacher) + arjun (student)."""

    @pytest.mark.asyncio
    async def test_generate(self, source_ids):
        show = "clarity_engine"
        t0 = time.time()

        briefing = await _build_briefing(show, source_ids, length_minutes=6)
        outline = _generate_outline(briefing, show)
        assert "title" in outline
        print(f"\n[{show}] Outline: {outline['title']} ({len(outline['segments'])} segments)")

        transcript = _generate_two_host(briefing, outline, show)
        assert len(transcript) > 10
        speakers = {line["speaker"].lower() for line in transcript}
        assert "kenji" in speakers, f"Missing kenji in speakers: {speakers}"
        assert "arjun" in speakers, f"Missing arjun in speakers: {speakers}"
        kenji_lines = sum(1 for l in transcript if l["speaker"] == "kenji")
        arjun_lines = sum(1 for l in transcript if l["speaker"] == "arjun")
        print(f"[{show}] Transcript: {len(transcript)} lines (kenji={kenji_lines}, arjun={arjun_lines})")

        out_path = str(OUTPUT_DIR / f"test_e2e_{show}.mp3")
        await _synthesize(transcript, show, out_path)
        assert Path(out_path).exists()
        size_kb = Path(out_path).stat().st_size / 1024
        print(f"[{show}] Audio: {out_path} ({size_kb:.0f} KB) in {time.time()-t0:.1f}s")


class TestMomentumLoop:
    """Single-host format — kenji solo."""

    @pytest.mark.asyncio
    async def test_generate(self, source_ids):
        show = "momentum_loop"
        t0 = time.time()

        briefing = await _build_briefing(show, source_ids, length_minutes=6)
        outline = _generate_outline(briefing, show)
        assert "title" in outline
        print(f"\n[{show}] Outline: {outline['title']} ({len(outline['segments'])} segments)")

        transcript = _generate_single_host(briefing, outline, show)
        assert len(transcript) > 10
        speakers = {line["speaker"].lower() for line in transcript}
        assert len(speakers) == 1, f"Expected 1 speaker, got {speakers}"
        print(f"[{show}] Transcript: {len(transcript)} lines, speaker={speakers.pop()}")

        out_path = str(OUTPUT_DIR / f"test_e2e_{show}.mp3")
        await _synthesize(transcript, show, out_path)
        assert Path(out_path).exists()
        size_kb = Path(out_path).stat().st_size / 1024
        print(f"[{show}] Audio: {out_path} ({size_kb:.0f} KB) in {time.time()-t0:.1f}s")


class TestExplorationEngine:
    """Two-host format — kenji (thesis) + arjun (antithesis)."""

    @pytest.mark.asyncio
    async def test_generate(self, source_ids):
        show = "exploration_engine"
        t0 = time.time()

        briefing = await _build_briefing(show, source_ids, length_minutes=6)
        outline = _generate_outline(briefing, show)
        assert "title" in outline
        print(f"\n[{show}] Outline: {outline['title']} ({len(outline['segments'])} segments)")

        transcript = _generate_two_host(briefing, outline, show)
        assert len(transcript) > 10
        speakers = {line["speaker"].lower() for line in transcript}
        assert "kenji" in speakers, f"Missing kenji in speakers: {speakers}"
        assert "arjun" in speakers, f"Missing arjun in speakers: {speakers}"
        kenji_lines = sum(1 for l in transcript if l["speaker"] == "kenji")
        arjun_lines = sum(1 for l in transcript if l["speaker"] == "arjun")
        print(f"[{show}] Transcript: {len(transcript)} lines (kenji={kenji_lines}, arjun={arjun_lines})")

        out_path = str(OUTPUT_DIR / f"test_e2e_{show}.mp3")
        await _synthesize(transcript, show, out_path)
        assert Path(out_path).exists()
        size_kb = Path(out_path).stat().st_size / 1024
        print(f"[{show}] Audio: {out_path} ({size_kb:.0f} KB) in {time.time()-t0:.1f}s")


class TestSpeakerOverride:
    """clarity_engine with speaker_override=emeka → single-host."""

    @pytest.mark.asyncio
    async def test_override_forces_single_host(self, source_ids):
        show = "clarity_engine"
        t0 = time.time()

        briefing = await _build_briefing(show, source_ids, length_minutes=5)
        outline = _generate_outline(briefing, show)

        transcript = _generate_single_host(briefing, outline, show, speaker_override="emeka")
        assert len(transcript) > 5
        speakers = {line["speaker"].lower() for line in transcript}
        assert len(speakers) == 1, f"Expected 1 speaker with override, got {speakers}"
        print(f"\n[{show}+override] Transcript: {len(transcript)} lines, speaker={speakers.pop()}")

        out_path = str(OUTPUT_DIR / f"test_e2e_{show}_override.mp3")
        await _synthesize(transcript, show, out_path, speaker_override="emeka")
        assert Path(out_path).exists()
        size_kb = Path(out_path).stat().st_size / 1024
        print(f"[{show}+override] Audio: {out_path} ({size_kb:.0f} KB) in {time.time()-t0:.1f}s")
