"""
core/tension/connect.py
Connect — companion selection over the tension graph.

Cast model ("companion selection research v1.md", frozen v3 design):
  antagonist  same tension, opposite polarity (contrast — near-mandatory)
  wildcard    same tension, different topic bucket (cross-domain surprise)
  depth       same bucket, different angle (topical reinforcement)

Selection is pure SQL + rules; the single LLM call at the end validates the
assembled cast and writes the episode ANGLE (the dramatic through-line handed
to the outline). Connect is allowed to abstain — a weak ensemble degrades to a
standalone episode rather than shipping a bad cast.

LLM calls per run: 1 (angle/validation). Worst case 1 — validation failure
falls back to a template angle rather than a second call.
"""

from __future__ import annotations

import json
import re

import dspy
from loguru import logger

from core.db.connection import db_fetchrow, db_query
from core.prompts.loader import with_prompt

MAX_COMPANIONS = 4


def _primary_tier1(topics) -> str | None:
    """First Tier-1 from a source.topics envelope (dict or JSON string)."""
    try:
        env = json.loads(topics) if isinstance(topics, str) else (topics or {})
        tags = env.get("tags") or []
        return tags[0]["tier1"] if tags else None
    except Exception:
        return None


async def find_cast(seed_id: str, user_id: str) -> dict:
    """
    Deterministic cast assembly. Returns
      {"cast": [{source_id, title, role, tension, seed_polarity, polarity}],
       "abstain": None | reason}
    No LLM calls.
    """
    seed = await db_fetchrow(
        "SELECT id::text AS id, title, topics FROM source WHERE id = $id::uuid AND user_id = $uid",
        {"id": seed_id, "uid": user_id},
    )
    if not seed:
        return {"cast": [], "abstain": "seed not found"}
    seed_t1 = _primary_tier1(seed.get("topics"))

    seed_links = await db_query(
        """
        SELECT st.tension_id::text AS tension_id, st.polarity, t.canonical
        FROM source_tension st JOIN tension t ON t.id = st.tension_id
        WHERE st.source_id = $id::uuid
        """,
        {"id": seed_id},
    )
    seed_pol = {r["tension_id"]: r["polarity"] for r in (seed_links or [])}
    canon = {r["tension_id"]: r["canonical"] for r in (seed_links or [])}

    candidates = []
    if seed_pol:
        candidates = await db_query(
            """
            SELECT s.id::text AS id, s.title, s.topics, s.created_at,
                   st.tension_id::text AS tension_id, st.polarity, st.confidence
            FROM source_tension st
            JOIN source s ON s.id = st.source_id
            WHERE st.tension_id = ANY($tids::uuid[])
              AND s.id != $seed::uuid AND s.user_id = $uid AND s.status = 'ready'
            ORDER BY s.created_at DESC
            """,
            {"tids": list(seed_pol.keys()), "seed": seed_id, "uid": user_id},
        ) or []

    antagonists, wildcards, supports = [], [], []
    for c in candidates:
        tid = c["tension_id"]
        sp, cp = seed_pol.get(tid), c["polarity"]
        c_t1 = _primary_tier1(c.get("topics"))
        entry = {
            "source_id": c["id"], "title": c["title"],
            "tension": canon.get(tid, ""), "seed_polarity": sp, "polarity": cp,
        }
        opposite = {("side_a", "side_b"), ("side_b", "side_a")}
        contrasts = (sp, cp) in opposite or (sp == "neutral" and cp in ("side_a", "side_b"))
        if contrasts and c.get("confidence") != "low":
            antagonists.append({**entry, "role": "antagonist"})
        elif c_t1 and seed_t1 and c_t1 != seed_t1:
            wildcards.append({**entry, "role": "wildcard"})
        else:
            supports.append({**entry, "role": "supports"})

    # depth channel — same bucket, no shared tension required
    depth = []
    if seed_t1:
        depth_rows = await db_query(
            """
            SELECT id::text AS id, title FROM source
            WHERE user_id = $uid AND status = 'ready' AND id != $seed::uuid
              AND topics->'tags'->0->>'tier1' = $t1
            ORDER BY created_at DESC LIMIT 5
            """,
            {"uid": user_id, "seed": seed_id, "t1": seed_t1},
        ) or []
        picked = {e["source_id"] for e in antagonists + wildcards + supports}
        depth = [
            {"source_id": r["id"], "title": r["title"], "role": "depth",
             "tension": "", "seed_polarity": None, "polarity": None}
            for r in depth_rows if r["id"] not in picked
        ]

    # Assembly: antagonist -> wildcard -> depth -> second wildcard/antagonist.
    cast, seen = [], set()
    def take(pool, n=1):
        for e in pool:
            if len(cast) >= MAX_COMPANIONS or n <= 0:
                return
            if e["source_id"] in seen:
                continue
            seen.add(e["source_id"]); cast.append(e); n -= 1
    take(antagonists)
    take(wildcards)
    take(depth)
    take(wildcards[1:] if wildcards else [])
    take(antagonists[1:] if antagonists else [])

    if not any(e["role"] in ("antagonist", "wildcard") for e in cast):
        return {"cast": [], "abstain": "no antagonist or cross-domain companion in the corpus"}
    return {"cast": cast, "abstain": None}


async def connect_eligible(seed_id: str, user_id: str) -> bool:
    """Cheap precheck: does any antagonist- or wildcard-grade link exist?"""
    row = await db_fetchrow(
        """
        SELECT 1 AS ok
        FROM source_tension seed_st
        JOIN source_tension other ON other.tension_id = seed_st.tension_id
                                  AND other.source_id != seed_st.source_id
        JOIN source s ON s.id = other.source_id
        JOIN source seed_s ON seed_s.id = seed_st.source_id
        WHERE seed_st.source_id = $id::uuid
          AND s.user_id = $uid AND s.status = 'ready'
          AND (
                (seed_st.polarity, other.polarity) IN (('side_a','side_b'), ('side_b','side_a'))
             OR (seed_st.polarity = 'neutral' AND other.polarity IN ('side_a','side_b'))
             OR (s.topics->'tags'->0->>'tier1') IS DISTINCT FROM (seed_s.topics->'tags'->0->>'tier1')
          )
        LIMIT 1
        """,
        {"id": seed_id, "uid": user_id},
    )
    return bool(row)


# ---------------------------------------------------------------------------
# The one LLM call — validate the cast + write the episode angle
# ---------------------------------------------------------------------------


class WriteConnectAngle(dspy.Signature):
    """You are the editor of a narrative podcast. A seed article and companion
sources were selected for one multi-source episode; each companion has a role
(antagonist argues against the seed's thesis; wildcard is the same underlying
tension from a different domain; depth reinforces from the same field; supports
agrees).

Do two things:
1. Reject any companion that does not genuinely fit its role (list its number
   in "rejected"). Reject sparingly — only clear misfits.
2. Write the ANGLE: 2-3 sentences describing the dramatic through-line of this
   episode — what the seed claims, who pushes back, where the surprise
   connection lands. Written for the episode outliner, not the listener.
   Name the sources by their titles.

Output ONLY JSON: {"rejected": [<numbers>], "angle": "..."}"""

    seed: str = dspy.InputField(desc="Seed title + summary + tension")
    companions: str = dspy.InputField(desc="Numbered companions: role, title, summary, tension")
    verdict_json: str = dspy.OutputField(desc='{"rejected": [...], "angle": "..."}')


_angle = dspy.Predict(with_prompt(WriteConnectAngle, "connect_angle"))


def _call_angle(seed_desc: str, companions_desc: str) -> str:
    """Isolated LLM invocation — the seam tests patch."""
    from core.llm_config import resolve
    with dspy.context(lm=resolve.llm("connect.angle")):
        return _angle(seed=seed_desc, companions=companions_desc).verdict_json


async def validate_and_angle(seed_id: str, cast: list[dict]) -> tuple[list[dict], str]:
    """One LLM call: prune misfit companions, produce the episode angle.
    Never raises; on failure keeps the full cast with a template angle."""
    rows = await db_query(
        """
        SELECT s.id::text AS id, s.title,
               (SELECT content FROM source_insight i
                WHERE i.source_id = s.id AND i.insight_type = 'summary' LIMIT 1) AS summary
        FROM source s WHERE s.id = ANY($ids::uuid[])
        """,
        {"ids": [seed_id] + [e["source_id"] for e in cast]},
    ) or []
    info = {r["id"]: r for r in rows}
    seed_i = info.get(seed_id, {})
    seed_desc = f"{seed_i.get('title', '?')} — {(seed_i.get('summary') or '')[:400]}"
    comp_lines = []
    for n, e in enumerate(cast, 1):
        ci = info.get(e["source_id"], {})
        comp_lines.append(
            f"{n}. [{e['role']}] {ci.get('title', e['title'])} — "
            f"{(ci.get('summary') or '')[:300]}"
            + (f" | shared tension: {e['tension']}" if e.get("tension") else "")
        )

    fallback_angle = (
        f"Episode centered on '{seed_i.get('title', 'the seed article')}' with "
        + ", ".join(f"{e['title']} ({e['role']})" for e in cast)
    )
    try:
        import asyncio
        raw = await asyncio.get_running_loop().run_in_executor(
            None, _call_angle, seed_desc, "\n".join(comp_lines)
        )
        cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
        verdict = json.loads(cleaned)
        rejected = {int(x) for x in (verdict.get("rejected") or [])}
        kept = [e for n, e in enumerate(cast, 1) if n not in rejected]
        angle = (verdict.get("angle") or "").strip() or fallback_angle
        # never let validation strip the cast below viability
        if any(e["role"] in ("antagonist", "wildcard") for e in kept):
            return kept, angle
        return cast, angle
    except Exception as e:
        logger.warning(f"[connect] angle call failed ({e}); using full cast + template angle")
        return cast, fallback_angle
