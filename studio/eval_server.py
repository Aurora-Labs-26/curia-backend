"""
studio/eval_server.py
Human eval UI for rating generated episodes and collecting golden data.
Run: python studio/eval_server.py
Open: http://localhost:8001/eval
Feedback saved to: eval_feedback table in PostgreSQL (export at /eval/export.csv)
"""

import asyncio
import csv
import io
import json as _json
import os
import sys
from datetime import datetime, date, timezone
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
import uvicorn

app = FastAPI()

_jobs: dict[str, dict] = {}

_TABLE_READY = False
_JOBS_TABLE_READY = False

FEEDBACK_FIELDS = [
    "timestamp", "episode_id", "episode_title",
    "stage", "original", "edited", "verdict", "comment", "signature",
]


def _json_serial(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return str(obj)  # covers UUID, Decimal, etc.


async def _ensure_table():
    global _TABLE_READY
    if _TABLE_READY:
        return
    from core.db.connection import db_execute
    await db_execute(
        """
        CREATE TABLE IF NOT EXISTS eval_feedback (
            id         SERIAL PRIMARY KEY,
            timestamp  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            episode_id TEXT NOT NULL,
            episode_title TEXT,
            stage      TEXT NOT NULL,
            original   TEXT,
            edited     TEXT,
            verdict    TEXT,
            comment    TEXT,
            signature  TEXT
        )
        """,
        {},
    )
    try:
        await db_execute(
            "ALTER TABLE eval_feedback ADD COLUMN IF NOT EXISTS signature TEXT", {}
        )
    except Exception:
        pass
    _TABLE_READY = True


async def _append_rows(rows: list[dict]):
    await _ensure_table()
    from core.db.connection import db_execute
    for row in rows:
        await db_execute(
            """INSERT INTO eval_feedback
               (timestamp, episode_id, episode_title, stage, original, edited, verdict, comment)
               VALUES ($ts, $episode_id, $episode_title, $stage, $original, $edited, $verdict, $comment)""",
            {
                "ts":            row.get("timestamp", datetime.now(timezone.utc)),
                "episode_id":    row.get("episode_id", ""),
                "episode_title": row.get("episode_title", ""),
                "stage":         row.get("stage", ""),
                "original":      row.get("original", ""),
                "edited":        row.get("edited", ""),
                "verdict":       row.get("verdict", ""),
                "comment":       row.get("comment", ""),
            },
        )


async def _count_rated() -> int:
    try:
        await _ensure_table()
        from core.db.connection import db_fetchrow
        row = await db_fetchrow("SELECT COUNT(DISTINCT episode_id) AS cnt FROM eval_feedback", {})
        return int(row["cnt"]) if row else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# Eval jobs — persist to DB so state survives restarts
# ---------------------------------------------------------------------------

async def _ensure_jobs_table():
    global _JOBS_TABLE_READY
    if _JOBS_TABLE_READY:
        return
    from core.db.connection import db_execute
    await db_execute(
        """
        CREATE TABLE IF NOT EXISTS eval_job (
            id          TEXT PRIMARY KEY,
            status      TEXT NOT NULL DEFAULT 'pending',
            message     TEXT,
            episode_id  TEXT,
            steps       JSONB DEFAULT '[]'::jsonb,
            extra       JSONB DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        {},
    )
    await db_execute("ALTER TABLE eval_job ADD COLUMN IF NOT EXISTS extra JSONB DEFAULT '{}'::jsonb", {})
    await db_execute(
        """
        UPDATE eval_job SET status = 'error', message = 'Server restarted while job was running',
               updated_at = NOW()
        WHERE status IN ('pending', 'running')
        """,
        {},
    )
    _JOBS_TABLE_READY = True


async def _save_job(job_id: str, data: dict):
    await _ensure_jobs_table()
    from core.db.connection import db_execute
    _jobs[job_id] = data
    # extra holds large pipeline state: source_id, show_name, briefing, outline
    extra = {k: data[k] for k in (
        "source_id", "show_name", "briefing", "outline",
        "outline_duration_s", "outline_output_tokens",
        "transcript_duration_s", "transcript_output_tokens",
    ) if k in data}
    await db_execute(
        """
        INSERT INTO eval_job (id, status, message, episode_id, steps, extra, updated_at)
        VALUES ($id, $status, $message, $episode_id, $steps::jsonb, $extra::jsonb, NOW())
        ON CONFLICT (id) DO UPDATE
        SET status = $status, message = $message, episode_id = $episode_id,
            steps = $steps::jsonb, extra = $extra::jsonb, updated_at = NOW()
        """,
        {
            "id": job_id,
            "status": data.get("status", "pending"),
            "message": data.get("message", ""),
            "episode_id": data.get("episode_id", ""),
            "steps": _json.dumps(data.get("steps", [])),
            "extra": _json.dumps(extra),
        },
    )


async def _load_job(job_id: str) -> dict | None:
    if job_id in _jobs:
        return _jobs[job_id]
    await _ensure_jobs_table()
    from core.db.connection import db_fetchrow
    row = await db_fetchrow(
        "SELECT status, message, episode_id, steps, extra FROM eval_job WHERE id = $id",
        {"id": job_id},
    )
    if not row:
        return None
    data = {"status": row["status"], "message": row.get("message") or ""}
    if row.get("episode_id"):
        data["episode_id"] = row["episode_id"]
    for field in ("steps", "extra"):
        val = row.get(field)
        if val:
            data[field] = _json.loads(val) if isinstance(val, str) else val
    # Flatten extra fields into top-level for easy access
    for k, v in (data.get("extra") or {}).items():
        data[k] = v
    _jobs[job_id] = data
    return data


async def _ensure_primitive_embedding(source_id: str):
    """Generate a primitive embedding for clustering, using all insights as fallback."""
    from core.db.connection import db_query, db_execute
    from core.embeddings import get_embedding, get_embedding_column

    col = get_embedding_column()
    rows = await db_query(
        "SELECT insight_type, content FROM source_insight WHERE source_id = $sid::uuid",
        {"sid": source_id},
    )
    # Prefer core_tensions + counterpoints (standard clustering primitives)
    parts = []
    for r in (rows or []):
        c = (r.get("content") or "").strip()
        if c and c.lower() != "null" and r["insight_type"] in ("core_tensions", "counterpoints"):
            parts.append(c)
    # Fallback: use all non-empty insights
    if not parts:
        for r in (rows or []):
            c = (r.get("content") or "").strip()
            if c and c.lower() != "null":
                parts.append(c)
    if not parts:
        return
    vector = await get_embedding("\n".join(parts))
    if not vector:
        return
    await db_execute(
        f"""INSERT INTO source_primitive_embedding (source_id, {col})
            VALUES ($sid::uuid, $vec)
            ON CONFLICT (source_id) DO UPDATE SET {col} = EXCLUDED.{col}""",
        {"sid": source_id, "vec": vector},
    )


async def _build_briefing(source_id: str, show_name: str, user_id: str) -> str:
    """Build briefing string for a single already-ingested source."""
    from core.db.connection import db_fetchrow, db_query
    from studio.briefing_builder import build_briefing_packet, briefing_packet_to_str
    from studio.shows.profiles import SHOW_PROFILES

    profile = SHOW_PROFILES[show_name]
    source_row = await db_fetchrow(
        "SELECT id, title, url, full_text FROM source WHERE id = $id::uuid", {"id": source_id}
    )
    insight_rows = await db_query(
        "SELECT insight_type, content FROM source_insight WHERE source_id = $id::uuid", {"id": source_id}
    )
    sources = [dict(source_row)]
    insights = {source_id: {r["insight_type"]: r["content"] for r in (insight_rows or [])}}
    packet = build_briefing_packet(
        format_name=profile.format_name,
        sources=sources,
        insights=insights,
        editorial_direction=None,
        user_kb=None,
    )
    return briefing_packet_to_str(packet)


async def _run_pipeline(job_id: str, url: str, show_name: str, mode: str = "single",
                        prompt_override: str | None = None, outline_prompt_override: str | None = None,
                        stop_after: str = "full"):
    from core.db.connection import db_fetchrow, db_execute
    from core.ingest import process_source, get_or_create_source
    from studio.generator import process_episode

    steps: list[dict] = []

    async def _step_start(text: str):
        steps.append({"text": text, "status": "running"})
        await _save_job(job_id, {"status": "running", "steps": list(steps)})

    async def _step_done():
        if steps:
            steps[-1]["status"] = "done"
        await _save_job(job_id, {"status": "running", "steps": list(steps)})

    try:
        await _step_start("Looking up account")
        user_row = await db_fetchrow("SELECT id FROM users LIMIT 1", {})
        if not user_row:
            steps[-1]["status"] = "error"
            await _save_job(job_id, {
                "status": "error",
                "message": "No users in DB. Run scripts/create_user.py first.",
                "steps": list(steps),
            })
            return
        user_id = str(user_row["id"])
        await _step_done()

        from core.ingest import normalise_url
        norm_url = normalise_url(url)
        cached = await db_fetchrow(
            "SELECT id::text FROM source WHERE url = $url AND status = 'ready' LIMIT 1",
            {"url": norm_url},
        )
        if cached:
            source_id = cached["id"]
            steps.append({"text": "Source cached — skipping ingest", "status": "done"})
            await _save_job(job_id, {"status": "running", "steps": list(steps)})
        else:
            await _step_start("Registering source")
            source_id = await get_or_create_source(url=url, user_id=user_id)
            await _step_done()

            await _step_start("Scraping & ingesting")
            src = await db_fetchrow(
                "SELECT status FROM source WHERE id = $id::uuid", {"id": source_id}
            )
            if not src or src["status"] != "ready":
                await process_source(source_id=source_id)
            src_after = await db_fetchrow(
                "SELECT status FROM source WHERE id = $id::uuid", {"id": source_id}
            )
            if not src_after or src_after["status"] != "ready":
                raise RuntimeError(f"Source failed to ingest (status: {src_after['status'] if src_after else 'missing'}). The URL may be behind a login, blocked, or empty.")
            await _step_done()

        if mode == "cluster":
            emb_row = await db_fetchrow(
                "SELECT source_id FROM source_primitive_embedding WHERE source_id = $sid::uuid",
                {"sid": source_id},
            )
            if not emb_row:
                await _step_start("Generating embedding for clustering")
                await _ensure_primitive_embedding(source_id)
                await _step_done()

            await _save_job(job_id, {
                "status": "awaiting_selection",
                "source_id": source_id,
                "user_id": user_id,
                "show_name": show_name,
                "message": "Select sources for episode",
                "steps": list(steps),
            })
            return

        if stop_after == "outline":
            await _step_start("Building briefing")
            briefing = await _build_briefing(source_id, show_name, user_id)
            await _step_done()

            await _step_start("Generating outline")
            import time as _time
            from dspy.utils.usage_tracker import track_usage as _track_usage
            from studio.generator import generate_outline

            _t0 = _time.time()
            with _track_usage() as _tracker:
                outline = generate_outline(briefing, show_name, prompt_override=outline_prompt_override)
            _ol_duration = round(_time.time() - _t0, 2)
            _ol_tokens = sum(
                v.get("completion_tokens") or v.get("output_tokens") or 0
                for v in _tracker.get_total_tokens().values()
            )

            if steps:
                steps[-1] = {"text": f"Outline done · {_ol_duration}s · {_ol_tokens:,} tokens", "status": "done"}
            await _save_job(job_id, {
                "status": "outline_done",
                "source_id": source_id,
                "user_id": user_id,
                "show_name": show_name,
                "briefing": briefing,
                "outline": outline,
                "outline_duration_s": _ol_duration,
                "outline_output_tokens": _ol_tokens,
                "steps": list(steps),
            })
            return

        await _step_start("Generating episode")
        episode_id = str(uuid4())
        show_idea_id = str(uuid4())
        await db_execute(
            """INSERT INTO show_idea (id, user_id, angle, idea_type, format, source_ids, generated)
               VALUES ($id::uuid, $user_id, '(eval)', 'standalone', $format, ARRAY[$src_id::uuid], false)""",
            {"id": show_idea_id, "user_id": user_id, "format": show_name, "src_id": source_id},
        )
        await db_execute(
            """INSERT INTO episode (id, user_id, show_name, show_idea_id, status, source_ids)
               VALUES ($id::uuid, $user_id, $show, $idea_id::uuid, 'queued', ARRAY[$src_id::uuid])""",
            {"id": episode_id, "user_id": user_id, "show": show_name,
             "idea_id": show_idea_id, "src_id": source_id},
        )
        await process_episode(episode_id=episode_id, prompt_override=prompt_override, outline_prompt_override=outline_prompt_override)
        await _step_done()

        await _save_job(job_id, {"status": "done", "episode_id": episode_id, "message": "Done!", "steps": list(steps)})

    except Exception as e:
        if steps:
            steps[-1]["status"] = "error"
        await _save_job(job_id, {"status": "error", "message": str(e), "steps": list(steps)})


async def _resume_pipeline(job_id: str, source_ids: list[str]):
    """Resume a cluster-mode job after the user selects sources."""
    from core.db.connection import db_execute
    from studio.generator import process_episode

    job = await _load_job(job_id)
    if not job:
        return

    user_id = job.get("user_id", "")
    show_name = job.get("show_name", "clarity_engine")
    steps = job.get("steps", [])

    try:
        steps.append({"text": f"Generating episode from {len(source_ids)} source(s)", "status": "running"})
        await _save_job(job_id, {"status": "running", "steps": list(steps),
                                  "user_id": user_id, "show_name": show_name})

        episode_id = str(uuid4())
        show_idea_id = str(uuid4())
        src_placeholders = ", ".join(f"${f'sid_{i}'}::uuid" for i in range(len(source_ids)))
        params: dict = {"id": show_idea_id, "user_id": user_id, "format": show_name}
        for i, sid in enumerate(source_ids):
            params[f"sid_{i}"] = sid

        await db_execute(
            f"""INSERT INTO show_idea (id, user_id, angle, idea_type, format, source_ids, generated)
                VALUES ($id::uuid, $user_id, '(eval cluster)', 'standalone', $format, ARRAY[{src_placeholders}], false)""",
            params,
        )

        ep_params: dict = {"id": episode_id, "user_id": user_id, "show": show_name, "idea_id": show_idea_id}
        for i, sid in enumerate(source_ids):
            ep_params[f"sid_{i}"] = sid

        await db_execute(
            f"""INSERT INTO episode (id, user_id, show_name, show_idea_id, status, source_ids)
                VALUES ($id::uuid, $user_id, $show, $idea_id::uuid, 'queued', ARRAY[{src_placeholders}])""",
            ep_params,
        )
        await process_episode(episode_id=episode_id)

        steps[-1]["status"] = "done"
        await _save_job(job_id, {"status": "done", "episode_id": episode_id, "message": "Done!",
                                  "steps": list(steps), "user_id": user_id, "show_name": show_name})

    except Exception as e:
        if steps:
            steps[-1]["status"] = "error"
        await _save_job(job_id, {"status": "error", "message": str(e), "steps": list(steps),
                                  "user_id": user_id, "show_name": show_name})


HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Curia — Eval</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #f7f7f5; color: #111;
    height: 100vh; display: flex; flex-direction: column; overflow: hidden;
  }

  /* ── Header ── */
  header {
    padding: 0 24px; border-bottom: 1px solid #e8e8e8; background: #fff;
    display: flex; align-items: center; gap: 16px;
    flex-shrink: 0; height: 52px;
  }
  header h1 { font-size: 13px; font-weight: 600; color: #111; letter-spacing: -0.01em; }
  #stats { font-size: 11px; color: #bbb; margin-left: auto; }

  /* ── Layout ── */
  #main { flex: 1; display: flex; overflow: hidden; }

  /* ── Sidebar ── */
  #sidebar {
    width: 280px; border-right: 1px solid #e8e8e8; background: #fff;
    display: flex; flex-direction: column; overflow: hidden; flex-shrink: 0;
  }

  /* URL input section */
  #url-section { padding: 14px 16px; border-bottom: 1px solid #f0f0f0; flex-shrink: 0; }
  #url-input {
    width: 100%; background: #f7f7f5; border: 1px solid #e0e0e0; border-radius: 7px;
    color: #111; font-size: 12px; padding: 8px 11px; outline: none;
    font-family: inherit; margin-bottom: 8px;
  }
  #url-input:focus { border-color: #111; background: #fff; }
  #url-input::placeholder { color: #ccc; }
  #url-controls { display: flex; gap: 7px; align-items: center; }
  #show-select {
    flex: 1; background: #f7f7f5; border: 1px solid #e0e0e0; border-radius: 6px;
    color: #555; font-size: 11px; padding: 6px 8px; outline: none;
    cursor: pointer; font-family: inherit;
  }
  #btn-run {
    padding: 6px 13px; border-radius: 6px; border: 1.5px solid #111;
    background: #111; color: #fff; font-size: 11px; cursor: pointer;
    font-weight: 600; white-space: nowrap; flex-shrink: 0;
  }
  #btn-run:hover { background: #333; border-color: #333; }
  #btn-run:disabled { opacity: 0.35; cursor: not-allowed; }

  /* Job status */
  #job-status {
    display: none; padding: 9px 16px; border-bottom: 1px solid #f0f0f0;
    font-size: 11px; color: #888; flex-shrink: 0;
  }
  #job-status.running { color: #2563eb; }
  #job-status.error   { color: #dc2626; }
  #job-status.done    { color: #16a34a; }
  .job-spinner { display: inline-block; animation: spin 1s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }

  /* Step list inside #job-status */
  .step-list { list-style: none; padding: 0; margin: 0; }
  .step-list li { display: flex; align-items: center; gap: 7px; padding: 3px 0; font-size: 11px; line-height: 1.4; }
  .step-list li .sl-icon { width: 14px; text-align: center; flex-shrink: 0; }
  .step-list li.sl-done { color: #555; }
  .step-list li.sl-done .sl-icon { color: #16a34a; }
  .step-list li.sl-running { color: #2563eb; font-weight: 500; }
  .step-list li.sl-running .sl-icon { color: #2563eb; }
  .step-list li.sl-error { color: #dc2626; }
  .step-list li.sl-error .sl-icon { color: #dc2626; }
  .step-list li.sl-pending { color: #ccc; }
  .step-err-msg { margin-top: 7px; font-size: 11px; color: #dc2626; }

  /* Sidebar episode list */
  #sidebar-header { padding: 12px 16px 8px; flex-shrink: 0; }
  #sidebar-title {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.08em; color: #bbb;
  }
  #ep-list { flex: 1; overflow-y: auto; }
  .ep-item {
    padding: 11px 16px; cursor: pointer;
    border-bottom: 1px solid #f5f5f5; transition: background 0.1s;
  }
  .ep-item:hover { background: #fafaf9; }
  .ep-item.active { background: #f5f5f3; border-left: 2px solid #111; padding-left: 14px; }
  .ep-title { font-size: 12px; font-weight: 500; color: #111; line-height: 1.45; }
  .ep-meta { font-size: 10px; color: #bbb; margin-top: 3px; display: flex; gap: 6px; flex-wrap: wrap; }
  .ep-show {
    background: #f5f5f5; border-radius: 3px; padding: 1px 5px; color: #888;
  }
  .ep-rated {
    background: #f0fdf4; color: #16a34a; border-radius: 3px;
    padding: 1px 5px; font-size: 9px; font-weight: 600; margin-left: auto;
  }

  /* Mode toggle */
  #mode-toggle { display: flex; gap: 0; margin-bottom: 8px; }
  .mode-btn {
    flex: 1; padding: 5px 0; border: 1px solid #e0e0e0; background: #f7f7f5;
    color: #888; font-size: 11px; font-weight: 500; cursor: pointer;
    font-family: inherit; text-align: center;
  }
  .mode-btn:first-child { border-radius: 6px 0 0 6px; }
  .mode-btn:last-child { border-radius: 0 6px 6px 0; border-left: none; }
  .mode-btn.active { background: #111; color: #fff; border-color: #111; }

  /* Cluster panel */
  #cluster-panel {
    display: none; padding: 12px 16px; border-bottom: 1px solid #f0f0f0;
    flex-shrink: 0; max-height: 360px; overflow-y: auto;
  }
  .cluster-header { display: flex; align-items: center; gap: 8px; margin-bottom: 10px; }
  .cluster-title {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.08em; color: #bbb;
  }
  .cluster-threshold {
    margin-left: auto; display: flex; align-items: center; gap: 6px;
    font-size: 10px; color: #888;
  }
  .cluster-threshold input[type="range"] {
    width: 80px; height: 3px; accent-color: #111; cursor: pointer;
  }
  .cluster-threshold .tv { font-weight: 600; color: #111; min-width: 28px; text-align: right; }
  .cluster-item {
    display: flex; align-items: flex-start; gap: 8px; padding: 7px 0;
    border-bottom: 1px solid #f8f8f8; font-size: 11px;
  }
  .cluster-item:last-child { border-bottom: none; }
  .cluster-item input[type="checkbox"] { margin-top: 2px; accent-color: #111; flex-shrink: 0; }
  .cluster-item-title { color: #333; line-height: 1.4; flex: 1; }
  .cluster-item-score {
    font-weight: 600; font-size: 10px; flex-shrink: 0; min-width: 36px; text-align: right;
  }
  .cluster-item-score.high { color: #16a34a; }
  .cluster-item-score.mid  { color: #ca8a04; }
  .cluster-item-score.low  { color: #dc2626; }
  .cluster-item.primary { opacity: 0.6; }
  .cluster-item.primary input[type="checkbox"] { pointer-events: none; }
  .cluster-empty { color: #ccc; font-size: 11px; padding: 8px 0; }
  #btn-gen-cluster {
    margin-top: 10px; width: 100%; padding: 7px 0; border-radius: 6px;
    border: 1.5px solid #111; background: #111; color: #fff;
    font-size: 11px; font-weight: 600; cursor: pointer; font-family: inherit;
  }
  #btn-gen-cluster:hover { background: #333; border-color: #333; }
  #btn-gen-cluster:disabled { opacity: 0.35; cursor: not-allowed; }

  /* ── Content ── */
  #content { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
  #empty-state {
    flex: 1; display: flex; align-items: center; justify-content: center;
    color: #ccc; font-size: 13px;
  }
  #eval-area { display: none; flex: 1; flex-direction: column; overflow: hidden; }

  /* Episode title bar */
  #ep-title-bar {
    padding: 16px 28px 14px; border-bottom: 1px solid #ebebeb;
    background: #fff; flex-shrink: 0;
  }
  #ep-title-text { font-size: 12px; font-weight: 500; color: #888; }
  #ep-title-meta { display: none; }
  #title-card-area { padding: 8px 20px 0; background: #fff; flex-shrink: 0; }
  #title-card-area .eval-card { margin-bottom: 0; }

  /* Stepper */
  #stepper {
    padding: 0 28px; border-bottom: 1px solid #ebebeb; background: #fff;
    display: flex; align-items: center; height: 46px; flex-shrink: 0;
  }
  .step-item { display: flex; align-items: center; gap: 8px; }
  .step-dot {
    width: 22px; height: 22px; border-radius: 50%; border: 1.5px solid #ddd;
    display: flex; align-items: center; justify-content: center;
    font-size: 10px; font-weight: 700; color: #bbb; flex-shrink: 0;
  }
  .step-label { font-size: 12px; color: #bbb; white-space: nowrap; }
  .step-sep { width: 32px; height: 1px; background: #ebebeb; margin: 0 6px; }
  .step-item.active .step-dot { border-color: #111; color: #fff; background: #111; }
  .step-item.active .step-label { color: #111; font-weight: 500; }
  .step-item.done .step-dot { border-color: #16a34a; color: #fff; background: #16a34a; }
  .step-item.done .step-label { color: #16a34a; }

  /* Stage scroll area */
  #stage-scroll { flex: 1; overflow-y: auto; padding: 28px; background: #f7f7f5; }

  /* Stage section label */
  .section-label {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.09em; color: #bbb; margin-bottom: 12px;
  }

  /* Outline */
  .outline-title { font-size: 16px; font-weight: 600; color: #111; margin-bottom: 8px; letter-spacing: -0.01em; }
  .outline-thread { font-size: 13px; color: #777; font-style: italic; margin-bottom: 24px; line-height: 1.65; }
  .outline-seg {
    background: #fff; border: 1px solid #ebebeb; border-radius: 10px;
    padding: 16px 18px; margin-bottom: 10px;
  }
  .outline-seg:last-child { margin-bottom: 0; }
  .outline-seg-num {
    font-size: 9px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.09em; color: #bbb; margin-bottom: 5px;
  }
  .outline-seg-focus { font-size: 13px; font-weight: 500; color: #111; line-height: 1.5; }
  .outline-seg-key { font-size: 12px; color: #777; margin-top: 6px; line-height: 1.6; }
  .outline-seg-prims { font-size: 11px; color: #aaa; margin-top: 6px; line-height: 1.7; }

  /* Stage card (transcript, final transcript content) */
  .stage-card {
    background: #fff; border: 1px solid #ebebeb; border-radius: 10px;
    padding: 20px 22px; max-height: 480px; overflow-y: auto;
  }

  /* Final step section label */
  .final-section-label {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.09em; color: #bbb; margin-bottom: 10px;
  }

  /* Per-field eval cards */
  .eval-card {
    background: #fff; border: 1px solid #ebebeb; border-radius: 10px;
    margin-bottom: 10px; overflow: hidden;
  }
  .eval-card-hdr {
    padding: 9px 14px 8px; display: flex; align-items: center; gap: 8px;
    border-bottom: 1px solid #f5f5f5;
  }
  .eval-card-label {
    font-size: 10px; font-weight: 700; text-transform: uppercase;
    letter-spacing: 0.09em; color: #bbb; flex: 1; white-space: nowrap;
  }
  .eval-card-actions { display: flex; gap: 5px; align-items: center; flex-wrap: wrap; }
  .eval-card-body { padding: 14px 16px; max-height: 320px; overflow-y: auto; }

  .btn-f {
    padding: 4px 10px; border-radius: 5px; border: 1px solid #e4e4e4;
    background: #fff; color: #888; font-size: 11px; cursor: pointer; font-weight: 500;
    white-space: nowrap;
  }
  .btn-f:hover { border-color: #bbb; color: #111; }
  .btn-f-good.sel { background: #f0fdf4; border-color: #16a34a; color: #16a34a; font-weight: 600; }
  .btn-f-bad.sel  { background: #fef2f2; border-color: #dc2626; color: #dc2626; font-weight: 600; }

  .field-edit-ta {
    display: none; width: calc(100% - 28px); min-height: 140px;
    margin: 2px 14px 10px; background: #fafafa; border: 1px solid #e0e0e0;
    border-radius: 7px; color: #111; font-size: 12px; font-family: inherit;
    line-height: 1.75; padding: 10px 12px; resize: vertical; outline: none;
  }
  .field-edit-ta:focus { border-color: #111; background: #fff; }

  .field-note {
    display: block; width: calc(100% - 28px); margin: 0 14px 12px;
    background: #f7f7f5; border: 1px solid #ebebeb; border-radius: 6px;
    color: #555; font-size: 11px; font-family: inherit;
    padding: 7px 10px; resize: none; height: 40px; outline: none; line-height: 1.5;
  }
  .field-note:focus { border-color: #ccc; background: #fff; }
  .field-note::placeholder { color: #d0d0d0; }

  /* Nav bar */
  #nav-bar {
    padding: 12px 28px; border-top: 1px solid #f0f0f0;
    display: flex; gap: 8px; flex-shrink: 0; background: #fff;
    align-items: center;
  }
  .btn-nav {
    padding: 8px 18px; border-radius: 7px; border: 1.5px solid #e0e0e0;
    background: #fff; color: #555; font-size: 12px; cursor: pointer; font-weight: 500;
  }
  .btn-nav:hover { border-color: #bbb; color: #111; }
  .btn-nav:disabled { opacity: 0.3; cursor: not-allowed; pointer-events: none; }
  #btn-next { margin-left: auto; }
  #btn-submit {
    margin-left: auto; padding: 8px 22px; border-radius: 7px;
    border: none; background: #111; color: #fff;
    font-size: 12px; cursor: pointer; font-weight: 600; display: none;
  }
  #btn-submit:hover { background: #333; }

  /* Final step */
  .final-title { font-size: 20px; font-weight: 700; color: #111; margin-bottom: 8px; letter-spacing: -0.02em; }
  .final-thread { font-size: 13px; color: #777; font-style: italic; line-height: 1.65; margin-bottom: 28px; }
  .final-note-area {
    margin-top: 28px; background: #fff; border: 1px solid #ebebeb;
    border-radius: 10px; padding: 18px 20px;
  }
  #final-note {
    width: 100%; background: #f7f7f5; border: 1px solid #e8e8e8; border-radius: 7px;
    color: #333; font-size: 12px; padding: 10px 12px; resize: none; outline: none;
    height: 80px; font-family: inherit; line-height: 1.6; margin-top: 10px;
  }
  #final-note::placeholder { color: #d0d0d0; }
  #final-note:focus { border-color: #ccc; background: #fff; }

  /* Toast */
  #toast {
    position: fixed; bottom: 24px; right: 24px;
    background: #111; color: #fff;
    padding: 11px 18px; border-radius: 8px; font-size: 12px; font-weight: 500;
    opacity: 0; transition: opacity 0.2s; pointer-events: none; z-index: 100;
    box-shadow: 0 4px 12px rgba(0,0,0,0.15);
  }
  #toast.show { opacity: 1; }
  #toast.error { background: #dc2626; }

  /* Scrollbar */
  ::-webkit-scrollbar { width: 4px; height: 4px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: #ddd; border-radius: 2px; }
</style>
</head>
<body>

<header>
  <h1>Curia — Eval</h1>
  <span id="stats">0 rated</span>
</header>

<div id="main">
  <aside id="sidebar">
    <div id="url-section">
      <div id="mode-toggle">
        <button class="mode-btn active" data-mode="single" onclick="setMode('single')">Single</button>
        <button class="mode-btn" data-mode="cluster" onclick="setMode('cluster')">Cluster</button>
      </div>
      <input type="url" id="url-input" placeholder="Paste URL to ingest + generate…"
             onkeydown="if(event.key==='Enter') submitUrl()">
      <div id="url-controls">
        <select id="show-select">
          <option value="clarity_engine">Clarity Engine</option>
          <option value="exploration_engine">Exploration Engine</option>
          <option value="narrative_drift">Narrative Drift</option>
          <option value="momentum_loop">Momentum Loop</option>
        </select>
        <button id="btn-run" onclick="submitUrl()">Run →</button>
      </div>
    </div>
    <div id="job-status"></div>
    <div id="cluster-panel"></div>
    <div id="sidebar-header"><div id="sidebar-title">Episodes</div></div>
    <div id="ep-list"><div style="padding:20px;color:#ccc;font-size:12px;text-align:center">Loading…</div></div>
  </aside>

  <div id="content">
    <div id="empty-state">Select an episode to begin</div>

    <div id="eval-area">
      <div id="ep-title-bar">
        <div id="ep-title-text"></div>
        <div id="ep-title-meta"></div>
      </div>
      <div id="title-card-area"></div>

      <div id="stepper">
        <div class="step-item" id="step-0">
          <div class="step-dot">1</div>
          <div class="step-label">Source</div>
        </div>
        <div class="step-sep"></div>
        <div class="step-item" id="step-1">
          <div class="step-dot">2</div>
          <div class="step-label">Outline</div>
        </div>
        <div class="step-sep"></div>
        <div class="step-item" id="step-2">
          <div class="step-dot">3</div>
          <div class="step-label">Transcript</div>
        </div>
        <div class="step-sep"></div>
        <div class="step-item" id="step-3">
          <div class="step-dot">4</div>
          <div class="step-label">Final</div>
        </div>
      </div>

      <div id="stage-scroll"></div>

      <div id="nav-bar">
        <button class="btn-nav" id="btn-prev" onclick="prevStep()" disabled>← Prev</button>
        <button class="btn-nav" id="btn-next" onclick="nextStep()">Next →</button>
        <button id="btn-submit" onclick="submitFeedback()">Submit Feedback</button>
      </div>
    </div>
  </div>
</div>

<div id="toast"></div>

<script>
// ── State ─────────────────────────────────────────────────────────────────────

let episodes = [];
let episode  = null;
let step     = 0;
let ratedIds = new Set();
let mode     = 'single';
let _clusterJobId   = null;
let _clusterSourceId = null;
let _clusterCandidates = [];

const STEPS       = ['source', 'outline', 'transcript', 'final'];
const STEP_LABELS = ['Source', 'Outline', 'Transcript', 'Final'];

let feedback = resetFeedback();

function resetFeedback() {
  return { fields: {}, finalNote: '', signature: '' };
}

// Get or init a per-field feedback slot
function _fb(key) {
  if (!feedback.fields[key]) feedback.fields[key] = { verdict: '', edited: '', comment: '', original: '' };
  return feedback.fields[key];
}

// ── Boot ──────────────────────────────────────────────────────────────────────

async function boot() {
  await Promise.all([loadEpisodes(), loadStats()]);
}

async function loadEpisodes() {
  try {
    const res = await fetch('/api/episodes');
    if (!res.ok) return;
    episodes = await res.json();
  } catch (e) { episodes = []; }
  renderList();
}

async function loadStats() {
  try {
    const res  = await fetch('/api/eval/stats');
    if (!res.ok) return;
    const data = await res.json();
    document.getElementById('stats').textContent = data.total_rated + ' rated';
    ratedIds = new Set(data.rated_ids || []);
    renderList();
  } catch (e) {}
}

// ── Mode toggle ──────────────────────────────────────────────────────────────

function setMode(m) {
  mode = m;
  document.querySelectorAll('.mode-btn').forEach(b => b.classList.toggle('active', b.dataset.mode === m));
  document.getElementById('cluster-panel').style.display = 'none';
  _clusterJobId = null;
  _clusterSourceId = null;
  _clusterCandidates = [];
}

// ── Cluster panel ────────────────────────────────────────────────────────────

async function loadCluster(sourceId, jobId) {
  _clusterSourceId = sourceId;
  _clusterJobId = jobId;
  try {
    const res = await fetch('/api/find-cluster', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ source_id: sourceId, threshold: 0.50 }),
    });
    const data = await res.json();
    if (data.error) {
      toast('Cluster search failed: ' + data.error, true);
      _clusterCandidates = [];
    } else {
      _clusterCandidates = data.candidates || [];
    }
  } catch (e) {
    toast('Failed to load cluster data', true);
    _clusterCandidates = [];
  }
  renderClusterPanel(0.70);
}

function renderClusterPanel(threshold) {
  const panel = document.getElementById('cluster-panel');
  panel.style.display = 'block';

  const checked = _clusterCandidates.filter(c => c.score >= threshold);
  const selectedCount = checked.length + 1;

  let html = `
    <div class="cluster-header">
      <span class="cluster-title">Source Cluster</span>
      <div class="cluster-threshold">
        <span>Threshold</span>
        <input type="range" min="0.50" max="0.95" step="0.05" value="${threshold}"
               oninput="renderClusterPanel(parseFloat(this.value))">
        <span class="tv">${threshold.toFixed(2)}</span>
      </div>
    </div>
    <div class="cluster-item primary">
      <input type="checkbox" checked disabled>
      <span class="cluster-item-title">Primary source (ingested URL)</span>
      <span class="cluster-item-score high">1.00</span>
    </div>`;

  if (!_clusterCandidates.length) {
    html += '<div class="cluster-empty">No similar sources found in the database.</div>';
  } else {
    for (const c of _clusterCandidates) {
      const isChecked = c.score >= threshold;
      const scoreCls = c.score >= 0.80 ? 'high' : c.score >= 0.65 ? 'mid' : 'low';
      html += `
        <div class="cluster-item">
          <input type="checkbox" ${isChecked ? 'checked' : ''} data-sid="${esc(c.source_id)}"
                 onchange="updateGenButton()">
          <span class="cluster-item-title">${esc(c.title)}</span>
          <span class="cluster-item-score ${scoreCls}">${c.score.toFixed(2)}</span>
        </div>`;
    }
  }

  html += `<button id="btn-gen-cluster" onclick="generateFromCluster()">Generate with ${selectedCount} source${selectedCount !== 1 ? 's' : ''}</button>`;
  panel.innerHTML = html;
}

function updateGenButton() {
  const boxes = document.querySelectorAll('#cluster-panel .cluster-item:not(.primary) input[type="checkbox"]');
  let count = 1;
  boxes.forEach(b => { if (b.checked) count++; });
  const btn = document.getElementById('btn-gen-cluster');
  if (btn) btn.textContent = `Generate with ${count} source${count !== 1 ? 's' : ''}`;
}

async function generateFromCluster() {
  const boxes = document.querySelectorAll('#cluster-panel .cluster-item:not(.primary) input[type="checkbox"]:checked');
  const sourceIds = [_clusterSourceId];
  boxes.forEach(b => sourceIds.push(b.dataset.sid));

  const btn = document.getElementById('btn-gen-cluster');
  if (btn) { btn.disabled = true; btn.textContent = 'Generating…'; }

  try {
    const res = await fetch('/api/generate-from-cluster', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ job_id: _clusterJobId, source_ids: sourceIds }),
    });
    const data = await res.json();
    if (data.error) { toast(data.error, true); if (btn) btn.disabled = false; return; }
    document.getElementById('cluster-panel').style.display = 'none';
    _pollJob(_clusterJobId);
  } catch (e) {
    toast('Network error', true);
    if (btn) btn.disabled = false;
  }
}

// ── Episode list ──────────────────────────────────────────────────────────────

function renderList() {
  const list = document.getElementById('ep-list');
  if (!episodes.length) {
    list.innerHTML = '<div style="padding:20px;color:#2a2a2a;font-size:12px;text-align:center">No ready episodes</div>';
    return;
  }
  list.innerHTML = '';
  for (const ep of episodes) {
    const div = document.createElement('div');
    div.className = 'ep-item' + (episode?.id === ep.id ? ' active' : '');
    div.onclick   = () => selectEpisode(ep.id);
    const ts    = ep.created_at
      ? new Date(ep.created_at).toLocaleString('en-US', {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'})
      : '';
    const score = ep.quality_score != null ? (ep.quality_score * 100).toFixed(0) + '%' : '—';
    const rated = ratedIds.has(ep.id) ? '<span class="ep-rated">Rated</span>' : '';
    div.innerHTML = `
      <div class="ep-title">${esc(ep.title || 'Untitled')}</div>
      <div class="ep-meta">
        <span class="ep-show">${esc(ep.show_name || '')}</span>
        <span>${esc(ts)}</span>
        <span>Q: ${score}</span>
        ${rated}
      </div>`;
    list.appendChild(div);
  }
}

async function selectEpisode(id) {
  _saveCurrentStep();

  // Highlight sidebar immediately
  document.querySelectorAll('.ep-item').forEach(el => el.classList.remove('active'));
  const idx = episodes.findIndex(e => e.id === id);
  if (idx >= 0) document.querySelectorAll('.ep-item')[idx].classList.add('active');

  // Show loading state
  document.getElementById('empty-state').style.display = 'none';
  const ea = document.getElementById('eval-area');
  ea.style.display = 'flex';
  document.getElementById('stage-scroll').innerHTML =
    '<div style="display:flex;align-items:center;justify-content:center;height:200px;color:#ccc;font-size:12px">Loading…</div>';

  try {
    const res = await fetch('/api/episodes/' + id);
    if (!res.ok) { toast('Failed to load episode', true); return; }
    episode = await res.json();
    if (episode.error) { toast(episode.error, true); return; }
  } catch (e) {
    toast('Network error loading episode', true);
    return;
  }

  step     = 0;
  feedback = resetFeedback();

  // Title bar — show name + score as static meta
  const titleText = episode.title || 'Untitled';
  document.getElementById('ep-title-text').textContent =
    (episode.show_name || '') + '  ·  Quality score: ' +
    (episode.quality_score != null ? (episode.quality_score * 100).toFixed(0) + '%' : '—');
  document.getElementById('ep-title-meta').textContent = '';

  // Ratable title card
  const titleContainer = document.getElementById('title-card-area');
  titleContainer.innerHTML = '';
  const titleEl = document.createElement('div');
  titleEl.style.cssText = 'font-size:16px;font-weight:600;color:#e0e0e0';
  titleEl.textContent = titleText;
  titleContainer.appendChild(_makeCard('episode.title', 'Episode Title', titleEl, titleText));

  renderStep();
}

// ── Step rendering ────────────────────────────────────────────────────────────

function renderStep() {
  updateStepper();

  const isFinal = (step === 3);

  document.getElementById('btn-prev').disabled = (step === 0);
  if (isFinal) {
    document.getElementById('btn-next').style.display   = 'none';
    document.getElementById('btn-submit').style.display = 'inline-block';
  } else {
    document.getElementById('btn-next').style.display   = 'inline-block';
    document.getElementById('btn-submit').style.display = 'none';
  }

  const area = document.getElementById('stage-scroll');
  area.innerHTML = '';
  area.scrollTop = 0;

  if (step === 0) buildSource(area);
  else if (step === 1) buildOutline(area);
  else if (step === 2) buildTranscript(area);
  else buildFinal(area);
}

function updateStepper() {
  for (let i = 0; i <= 3; i++) {
    const el   = document.getElementById('step-' + i);
    const dot  = el.querySelector('.step-dot');
    el.classList.remove('active', 'done');
    if (i < step)      { el.classList.add('done');   dot.textContent = '✓'; }
    else if (i === step){ el.classList.add('active'); dot.textContent = i + 1; }
    else               {                              dot.textContent = i + 1; }
  }
}

// ── Per-field card builder ────────────────────────────────────────────────────

function _makeCard(key, label, contentEl, originalText) {
  const fb = _fb(key);
  fb.original = originalText || '';

  const card = document.createElement('div');
  card.className = 'eval-card';

  // Header
  const hdr = document.createElement('div');
  hdr.className = 'eval-card-hdr';
  const lbl = document.createElement('span');
  lbl.className = 'eval-card-label';
  lbl.textContent = label;
  hdr.appendChild(lbl);

  const acts = document.createElement('div');
  acts.className = 'eval-card-actions';

  const btnG = document.createElement('button');
  btnG.className = 'btn-f btn-f-good' + (fb.verdict === 'good' ? ' sel' : '');
  btnG.textContent = '✓ Good';

  const btnB = document.createElement('button');
  btnB.className = 'btn-f btn-f-bad' + (fb.verdict === 'bad' ? ' sel' : '');
  btnB.textContent = '✗ Bad';

  const btnE = document.createElement('button');
  btnE.className = 'btn-f';
  btnE.textContent = 'Edit';

  const btnS = document.createElement('button');
  btnS.className = 'btn-f';
  btnS.textContent = 'Save';
  btnS.style.display = 'none';

  const btnC = document.createElement('button');
  btnC.className = 'btn-f';
  btnC.textContent = 'Cancel';
  btnC.style.display = 'none';

  btnG.onclick = () => {
    fb.verdict = (fb.verdict === 'good') ? '' : 'good';
    btnG.classList.toggle('sel', fb.verdict === 'good');
    btnB.classList.toggle('sel', fb.verdict === 'bad');
  };
  btnB.onclick = () => {
    fb.verdict = (fb.verdict === 'bad') ? '' : 'bad';
    btnG.classList.toggle('sel', fb.verdict === 'good');
    btnB.classList.toggle('sel', fb.verdict === 'bad');
  };

  [btnG, btnB, btnE, btnS, btnC].forEach(b => acts.appendChild(b));
  hdr.appendChild(acts);
  card.appendChild(hdr);

  // Body (display)
  const body = document.createElement('div');
  body.className = 'eval-card-body';
  body.appendChild(contentEl);
  card.appendChild(body);

  // Edit textarea
  const ta = document.createElement('textarea');
  ta.className = 'field-edit-ta';
  ta.value = fb.edited || originalText || '';
  card.appendChild(ta);

  btnE.onclick = () => {
    body.style.display = 'none'; ta.style.display = 'block'; ta.focus();
    btnE.style.display = 'none'; btnS.style.display = ''; btnC.style.display = '';
  };
  btnS.onclick = () => {
    fb.edited = ta.value;
    if (!fb.verdict) { fb.verdict = 'bad'; btnB.classList.add('sel'); btnG.classList.remove('sel'); }
    body.style.display = ''; ta.style.display = 'none';
    btnE.style.display = ''; btnS.style.display = 'none'; btnC.style.display = 'none';
  };
  btnC.onclick = () => {
    ta.value = fb.edited || originalText || '';
    body.style.display = ''; ta.style.display = 'none';
    btnE.style.display = ''; btnS.style.display = 'none'; btnC.style.display = 'none';
  };

  // Note
  const note = document.createElement('textarea');
  note.className = 'field-note';
  note.placeholder = 'Note…';
  note.value = fb.comment || '';
  note.oninput = () => { fb.comment = note.value; };
  card.appendChild(note);

  return card;
}

// ── Stage builders ────────────────────────────────────────────────────────────

const INSIGHT_ORDER = ['summary', 'key_insights', 'human_stakes', 'core_tensions', 'counterpoints', 'examples', 'metadata'];
const INSIGHT_LABELS = {
  summary: 'Summary', key_insights: 'Key Insights', human_stakes: 'Human Stakes',
  core_tensions: 'Core Tensions', counterpoints: 'Counterpoints',
  examples: 'Examples', metadata: 'Metadata',
};

function buildSource(container) {
  const sources = (mode === 'cluster' && episode.sources && episode.sources.length > 1)
    ? episode.sources
    : (episode.source ? [episode.source] : []);

  if (!sources.length) {
    const empty = document.createElement('div');
    empty.className = 'stage-card';
    empty.style.marginBottom = '16px';
    empty.innerHTML = '<div style="color:#ccc;font-size:12px">No source data. Episode predates source tracking.</div>';
    container.appendChild(empty);
    return;
  }

  for (let si = 0; si < sources.length; si++) {
    const src = sources[si];
    const label = sources.length > 1 ? ` ${si + 1} of ${sources.length}` : '';

    const infoCard = document.createElement('div');
    infoCard.className = 'stage-card';
    infoCard.style.marginBottom = '16px';
    infoCard.innerHTML = `
      <div class="section-label">Article${esc(label)}</div>
      <div style="font-size:14px;font-weight:600;color:#111;margin-bottom:6px;line-height:1.4">${esc(src.title || 'Untitled')}</div>
      <a href="${esc(src.url||'')}" target="_blank" style="font-size:11px;color:#6b7280;word-break:break-all;text-decoration:none">${esc(src.url||'')}</a>
      ${src.full_text ? `<div style="margin-top:12px"><div class="section-label" style="margin-bottom:5px">Raw (full text)</div>
        <div style="font-size:11px;line-height:1.7;color:#999;white-space:pre-wrap;max-height:320px;overflow-y:auto">${esc(src.full_text)}</div></div>` : ''}`;
    container.appendChild(infoCard);

    const insights = src.insights || {};
    const keyPrefix = sources.length > 1 ? `source.${si}.` : 'source.';
    for (const key of INSIGHT_ORDER) {
      const val = insights[key];
      if (!val) continue;
      const el = document.createElement('div');
      el.style.cssText = 'font-size:12px;line-height:1.8;color:#333;white-space:pre-wrap';
      el.textContent = val;
      container.appendChild(_makeCard(keyPrefix + key, INSIGHT_LABELS[key] || key, el, val));
    }
  }
}

function buildOutline(container) {
  const outline = episode.outline;

  if (!outline || typeof outline !== 'object') {
    const el = document.createElement('pre');
    el.style.cssText = 'font-size:12px;line-height:1.75;color:#555;white-space:pre-wrap';
    el.textContent = String(outline || '');
    container.appendChild(_makeCard('outline.text', 'Outline', el, String(outline||'')));
    return;
  }

  // Thread / central tension card
  if (outline.thread || outline.central_tension) {
    const threadVal = outline.thread || outline.central_tension;
    const threadEl = document.createElement('div');
    threadEl.className = 'outline-thread'; threadEl.textContent = threadVal;
    container.appendChild(_makeCard('outline.thread', 'Thread', threadEl, threadVal));
  }

  // Per-segment cards
  (outline.segments || []).forEach((seg, i) => {
    const prims = Array.isArray(seg.primitives_used) ? seg.primitives_used : [];
    const segEl = document.createElement('div');
    segEl.innerHTML = `
      <div class="outline-seg-num">Segment ${esc(String(seg.segment || i+1))}</div>
      <div class="outline-seg-focus">${esc(seg.title || seg.focus || '')}</div>
      ${(seg.purpose||seg.key_point)?`<div class="outline-seg-key">${esc(seg.purpose||seg.key_point)}</div>`:''}
      ${prims.length?`<div class="outline-seg-prims">${prims.map(p=>'· '+esc(p)).join('<br>')}</div>`:''}`;
    const segText = [seg.title||seg.focus, seg.purpose||seg.key_point, ...prims].filter(Boolean).join('\n');
    container.appendChild(_makeCard('outline.seg.' + i, 'Segment ' + (seg.segment||i+1), segEl, segText));
  });
}

function buildTranscript(container) {
  const lines = Array.isArray(episode.transcript) ? episode.transcript : [];
  const fullText = lines.map(l => l.text || '').join('\n\n');

  const el = document.createElement('div');
  if (!lines.length) {
    el.innerHTML = '<div style="color:#ccc">No transcript available</div>';
  } else {
    const para = document.createElement('div');
    para.style.cssText = 'font-size:13px;line-height:1.9;color:#333;white-space:pre-wrap';
    para.textContent = fullText;
    el.appendChild(para);
  }
  container.appendChild(_makeCard('transcript', 'Transcript', el, fullText));
}

function buildFinal(container) {
  const lines   = Array.isArray(episode.transcript) ? episode.transcript : [];
  const outline = episode.outline;

  if (outline && typeof outline === 'object' && outline.title) {
    const hdr = document.createElement('div');
    hdr.style.marginBottom = '20px';
    hdr.innerHTML = `
      <div class="final-section-label">Episode</div>
      <div class="final-title">${esc(outline.title)}</div>
      ${(outline.thread||outline.central_tension)?`<div class="final-thread">${esc(outline.thread||outline.central_tension)}</div>`:''}`;
    container.appendChild(hdr);
  }

  const card = document.createElement('div');
  card.className = 'stage-card';
  if (!lines.length) {
    card.innerHTML = '<div style="color:#ccc">No transcript available</div>';
  } else {
    const para = document.createElement('div');
    para.style.cssText = 'font-size:13px;line-height:1.9;color:#333;white-space:pre-wrap';
    para.textContent = lines.map(l => l.text || '').join('\n\n');
    card.appendChild(para);
  }
  container.appendChild(card);

  const noteBox = document.createElement('div');
  noteBox.className = 'final-note-area';
  noteBox.innerHTML = `
    <div class="final-section-label">Global Note</div>
    <textarea id="final-note" placeholder="Overall thoughts on this episode…">${esc(feedback.finalNote||'')}</textarea>`;
  container.appendChild(noteBox);

  const sigBox = document.createElement('div');
  sigBox.className = 'final-note-area';
  sigBox.innerHTML = `
    <div class="final-section-label">Signature</div>
    <input type="text" id="signature-input" placeholder="Your name"
           value="${esc(feedback.signature||'')}"
           style="width:100%;padding:10px 12px;border:1.5px solid #e5e5e5;border-radius:8px;font-size:13px;background:#fafafa;outline:none;box-sizing:border-box">`;
  container.appendChild(sigBox);
}

// ── Actions ───────────────────────────────────────────────────────────────────

function _saveCurrentStep() {
  if (step === 3) {
    const el = document.getElementById('final-note');
    if (el) feedback.finalNote = el.value;
    const sig = document.getElementById('signature-input');
    if (sig) feedback.signature = sig.value;
  }
}

function prevStep() {
  _saveCurrentStep();
  if (step > 0) { step--; renderStep(); }
}

function nextStep() {
  _saveCurrentStep();
  if (step < STEPS.length - 1) { step++; renderStep(); }
}

// ── Submit ────────────────────────────────────────────────────────────────────

async function submitFeedback() {
  _saveCurrentStep();

  const stages = Object.entries(feedback.fields).map(([key, fb]) => ({
    stage:    key,
    original: fb.original || '',
    edited:   fb.edited   || '',
    verdict:  fb.verdict  || '',
    comment:  fb.comment  || '',
  }));

  if (feedback.finalNote && feedback.finalNote.trim()) {
    stages.push({ stage: 'final_note', original: '', edited: '', verdict: '', comment: feedback.finalNote });
  }

  if (!stages.length) { toast('No feedback to submit yet.', true); return; }

  try {
    const res  = await fetch('/api/eval/submit', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ episode_id: episode.id, episode_title: episode.title || '', signature: feedback.signature || '', stages }),
    });
    const data = await res.json();

    if (data.ok) {
      toast(`Saved — ${data.rows_saved} row${data.rows_saved !== 1 ? 's' : ''}`, false);
      ratedIds.add(episode.id);
      renderList();
      feedback = resetFeedback();
      loadStats();
    } else {
      toast('Error: ' + (data.error || 'unknown'), true);
    }
  } catch (e) {
    toast('Network error submitting feedback', true);
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function toast(msg, isError = false) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.toggle('error', isError);
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 3500);
}

function esc(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// ── URL ingest ────────────────────────────────────────────────────────────────

let _pollTimer = null;

async function submitUrl() {
  const url  = document.getElementById('url-input').value.trim();
  if (!url) return;
  const show = document.getElementById('show-select').value;

  document.getElementById('btn-run').disabled = true;
  setJobStatus('running', '<span class="job-spinner">↻</span> Starting pipeline…');

  try {
    const res  = await fetch('/api/ingest', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ url, show_name: show, mode }),
    });
    const data = await res.json();
    if (data.error) {
      setJobStatus('error', '✗ ' + esc(data.error));
      document.getElementById('btn-run').disabled = false;
      return;
    }
    _pollJob(data.job_id);
  } catch (e) {
    setJobStatus('error', '✗ ' + esc(e.message || 'Network error'));
    document.getElementById('btn-run').disabled = false;
  }
}

function _pollJob(jobId) {
  clearTimeout(_pollTimer);
  _pollTimer = setTimeout(async () => {
    let data;
    try {
      const res = await fetch('/api/jobs/' + jobId);
      data = await res.json();
    } catch (e) {
      setJobStatus('error', '✗ Lost connection to server');
      document.getElementById('btn-run').disabled = false;
      return;
    }

    if (data.status === 'running' || data.status === 'pending') {
      if (data.steps && data.steps.length) renderSteps(data.steps, null);
      else setJobStatus('running', '<span class="job-spinner">↻</span> ' + esc(data.message || 'Running…'));
      _pollJob(jobId);
    } else if (data.status === 'awaiting_selection') {
      if (data.steps && data.steps.length) renderSteps(data.steps, null);
      setJobStatus('done', '✓ Ingested — select sources below');
      document.getElementById('btn-run').disabled = false;
      loadCluster(data.source_id, jobId);
    } else if (data.status === 'done') {
      if (data.steps && data.steps.length) renderSteps(data.steps, null);
      else setJobStatus('done', '✓ Done!');
      document.getElementById('btn-run').disabled = false;
      document.getElementById('url-input').value = '';
      document.getElementById('cluster-panel').style.display = 'none';
      setTimeout(() => { document.getElementById('job-status').style.display = 'none'; }, 5000);
      await loadEpisodes();
      if (data.episode_id) selectEpisode(data.episode_id);
    } else {
      if (data.steps && data.steps.length) renderSteps(data.steps, data.message || 'Error');
      else setJobStatus('error', '✗ ' + esc(data.message || 'Error'));
      document.getElementById('btn-run').disabled = false;
    }
  }, 2000);
}

function renderSteps(steps, errMsg) {
  const el = document.getElementById('job-status');
  el.style.display = 'block';
  el.className = 'running';
  let html = '<ul class="step-list">';
  for (const s of steps) {
    let cls, icon;
    if      (s.status === 'done')    { cls = 'sl-done';    icon = '✓'; }
    else if (s.status === 'running') { cls = 'sl-running'; icon = '<span class="job-spinner">↻</span>'; }
    else if (s.status === 'error')   { cls = 'sl-error';   icon = '✗'; }
    else                             { cls = 'sl-pending';  icon = '·'; }
    html += `<li class="${cls}"><span class="sl-icon">${icon}</span>${esc(s.text)}</li>`;
  }
  html += '</ul>';
  if (errMsg) html += `<div class="step-err-msg">${esc(errMsg)}</div>`;
  el.innerHTML = html;
}

function setJobStatus(cls, html) {
  const el = document.getElementById('job-status');
  el.style.display = 'block';
  el.className     = cls;
  el.innerHTML     = html;
}

boot();
</script>
</body>
</html>"""


# ── API ───────────────────────────────────────────────────────────────────────

@app.get("/eval", response_class=HTMLResponse)
async def eval_page():
    return HTML


@app.get("/api/episodes")
async def list_episodes():
    from core.db.connection import db_query
    try:
        rows = await db_query(
            """SELECT id, title, show_name, quality_score, created_at
               FROM episode
               WHERE status = 'ready'
               ORDER BY created_at DESC
               LIMIT 100""",
            {},
        )
        return JSONResponse(
            content=_json.loads(_json.dumps(rows or [], default=_json_serial))
        )
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/api/episodes/{episode_id}")
async def get_episode(episode_id: str):
    from core.db.connection import db_fetchrow, db_query
    try:
        row = await db_fetchrow(
            """SELECT id, title, show_name, outline, transcript,
                      quality_score, quality_feedback, created_at, source_ids
               FROM episode WHERE id = $id::uuid""",
            {"id": episode_id},
        )
        if not row:
            return JSONResponse(content={"error": "Not found"}, status_code=404)
        data = _json.loads(_json.dumps(dict(row), default=_json_serial))
        for key in ("outline", "transcript"):
            if isinstance(data.get(key), str):
                try:
                    data[key] = _json.loads(data[key])
                except Exception:
                    pass

        # Attach sources for step 0
        source_ids = data.get("source_ids") or []
        sources_data = []
        for src_id_raw in source_ids:
            src_id = str(src_id_raw) if not isinstance(src_id_raw, str) else src_id_raw
            src_row = await db_fetchrow(
                "SELECT id, title, url, full_text FROM source WHERE id = $id::uuid",
                {"id": src_id},
            )
            if src_row:
                src_data = _json.loads(_json.dumps(dict(src_row), default=_json_serial))
                insight_rows = await db_query(
                    "SELECT insight_type, content FROM source_insight WHERE source_id = $sid::uuid ORDER BY insight_type",
                    {"sid": src_id},
                )
                src_data["insights"] = {r["insight_type"]: r["content"] for r in (insight_rows or [])}
                sources_data.append(src_data)
        if sources_data:
            data["source"] = sources_data[0]
            data["sources"] = sources_data

        return JSONResponse(content=data)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/api/eval/submit")
async def submit_feedback(request: Request):
    try:
        body          = await request.json()
        episode_id    = body.get("episode_id", "")
        episode_title = body.get("episode_title", "")
        signature     = body.get("signature", "")
        stages        = body.get("stages", [])
        ts            = datetime.now(timezone.utc)

        await _ensure_table()
        from core.db.connection import get_db

        def _sanitize(val: str) -> str:
            if not val:
                return ""
            return val.replace("\x00", "")

        async with get_db() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM eval_feedback WHERE episode_id = $1",
                    episode_id,
                )
                for s in stages:
                    await conn.execute(
                        """INSERT INTO eval_feedback
                           (timestamp, episode_id, episode_title, stage, original, edited, verdict, comment, signature)
                           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)""",
                        ts,
                        episode_id,
                        episode_title,
                        s.get("stage", ""),
                        _sanitize(s.get("original", "")),
                        _sanitize(s.get("edited", "")),
                        s.get("verdict", ""),
                        s.get("comment", ""),
                        signature,
                    )

            count = await conn.fetchval(
                "SELECT COUNT(*) FROM eval_feedback WHERE episode_id = $1",
                episode_id,
            )

        return JSONResponse(content={"ok": True, "rows_saved": count})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/api/eval/stats")
async def eval_stats():
    total = await _count_rated()
    rated_ids: list[str] = []
    try:
        await _ensure_table()
        from core.db.connection import db_query
        rows = await db_query("SELECT DISTINCT episode_id FROM eval_feedback", {})
        rated_ids = [r["episode_id"] for r in (rows or [])]
    except Exception:
        pass
    return JSONResponse(content={"total_rated": total, "rated_ids": rated_ids})


@app.get("/eval/export.csv")
async def export_csv():
    """Download all eval feedback as a CSV file."""
    try:
        await _ensure_table()
        from core.db.connection import db_query
        rows = await db_query(
            "SELECT timestamp, episode_id, episode_title, stage, original, edited, verdict, comment, signature "
            "FROM eval_feedback ORDER BY timestamp",
            {},
        )
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=FEEDBACK_FIELDS)
        writer.writeheader()
        for row in (rows or []):
            writer.writerow({k: str(row.get(k) or "") for k in FEEDBACK_FIELDS})
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=eval_feedback.csv"},
        )
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/api/ingest")
async def start_ingest(request: Request):
    body      = await request.json()
    url       = (body.get("url") or "").strip()
    show_name = body.get("show_name") or "clarity_engine"
    mode      = body.get("mode") or "single"
    if not url:
        return JSONResponse(content={"error": "url required"}, status_code=400)
    job_id = str(uuid4())
    await _save_job(job_id, {"status": "pending", "message": "Queued"})
    asyncio.create_task(_run_pipeline(job_id, url, show_name, mode=mode))
    return JSONResponse(content={"job_id": job_id})


@app.post("/api/find-cluster")
async def find_cluster(request: Request):
    body = await request.json()
    source_id = (body.get("source_id") or "").strip()
    threshold = float(body.get("threshold", 0.50))
    if not source_id:
        return JSONResponse(content={"error": "source_id required"}, status_code=400)
    try:
        from intelligence.clustering import find_similar_sources
        candidates = await find_similar_sources(source_id, threshold=threshold)
        return JSONResponse(content={"candidates": candidates})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/api/generate-from-cluster")
async def generate_from_cluster(request: Request):
    body = await request.json()
    job_id = (body.get("job_id") or "").strip()
    source_ids = body.get("source_ids") or []
    if not job_id or not source_ids:
        return JSONResponse(content={"error": "job_id and source_ids required"}, status_code=400)
    job = await _load_job(job_id)
    if not job:
        return JSONResponse(content={"error": "Job not found"}, status_code=404)
    if job.get("status") != "awaiting_selection":
        return JSONResponse(content={"error": "Job not awaiting selection"}, status_code=400)
    asyncio.create_task(_resume_pipeline(job_id, source_ids))
    return JSONResponse(content={"ok": True})


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = await _load_job(job_id)
    if not job:
        return JSONResponse(content={"error": "Not found"}, status_code=404)
    return JSONResponse(content=job)


async def _run_transcript_step(job_id: str, transcript_prompt_override: str | None = None,
                               briefing_override: str | None = None):
    """Generate transcript from stored job state (briefing + outline). Skips audio for speed."""
    from core.db.connection import db_execute
    from studio.generator import generate_transcript
    import json as _js

    job = await _load_job(job_id)
    if not job:
        raise RuntimeError("Job not found")
    briefing   = briefing_override or job.get("briefing") or ""
    outline    = job.get("outline") or {}
    show_name  = job.get("show_name") or "clarity_engine"
    source_id  = job.get("source_id") or ""
    user_id    = job.get("user_id") or "default"
    steps      = list(job.get("steps") or [])

    steps.append({"text": "Generating transcript", "status": "running"})
    await _save_job(job_id, {**job, "status": "running", "steps": steps})

    try:
        import time as _time
        from dspy.utils.usage_tracker import track_usage as _track_usage

        _t0 = _time.time()
        with _track_usage() as _tracker:
            transcript = generate_transcript(
                briefing, outline, show_name,
                prompt_override=transcript_prompt_override,
            )
        _duration_s = round(_time.time() - _t0, 2)

        # Extract output tokens from tracker
        _total = _tracker.get_total_tokens()
        _output_tokens = sum(
            v.get("completion_tokens") or v.get("output_tokens") or 0
            for v in _total.values()
        )

        # Persist to episode row (create or update)
        episode_id = job.get("episode_id") or str(uuid4())
        if not job.get("episode_id"):
            show_idea_id = str(uuid4())
            await db_execute(
                """INSERT INTO show_idea (id, user_id, angle, idea_type, format, source_ids, generated)
                   VALUES ($id::uuid, $user_id, '(eval)', 'standalone', $format, ARRAY[$src_id::uuid], false)""",
                {"id": show_idea_id, "user_id": user_id, "format": show_name, "src_id": source_id},
            )
            await db_execute(
                """INSERT INTO episode (id, user_id, show_name, show_idea_id, status, source_ids,
                          outline, transcript, title)
                   VALUES ($id::uuid, $user_id, $show, $idea_id::uuid, 'ready',
                          ARRAY[$src_id::uuid], $outline::jsonb, $transcript::jsonb, $title)""",
                {
                    "id": episode_id, "user_id": user_id, "show": show_name,
                    "idea_id": show_idea_id, "src_id": source_id,
                    "outline": _js.dumps(outline),
                    "transcript": _js.dumps(transcript),
                    "title": (outline.get("title") or show_name),
                },
            )
        else:
            await db_execute(
                """UPDATE episode SET transcript = $transcript::jsonb, status = 'ready'
                   WHERE id = $id::uuid""",
                {"id": episode_id, "transcript": _js.dumps(transcript)},
            )

        steps[-1] = {"text": f"Transcript done · {_duration_s}s · {_output_tokens:,} output tokens", "status": "done"}
        await _save_job(job_id, {**job, "status": "done", "episode_id": episode_id,
                                  "steps": steps, "transcript": transcript,
                                  "transcript_duration_s": _duration_s,
                                  "transcript_output_tokens": _output_tokens})
        return episode_id

    except Exception as e:
        steps[-1]["status"] = "error"
        await _save_job(job_id, {**job, "status": "error", "message": str(e), "steps": steps})
        raise


async def _rerun_outline_step(job_id: str, outline_prompt_override: str | None = None):
    """Re-run outline from stored briefing. Updates stored outline in job state."""
    from studio.generator import generate_outline

    job = await _load_job(job_id)
    if not job:
        raise RuntimeError("Job not found")

    briefing  = job.get("briefing") or ""
    show_name = job.get("show_name") or "clarity_engine"
    steps     = list(job.get("steps") or [])

    steps.append({"text": "Re-running outline", "status": "running"})
    await _save_job(job_id, {**job, "status": "running", "steps": steps})

    try:
        outline = generate_outline(briefing, show_name, prompt_override=outline_prompt_override)
        steps[-1]["status"] = "done"
        # Clear episode_id — transcript will need re-run too
        await _save_job(job_id, {**job, "status": "outline_done", "outline": outline,
                                  "steps": steps, "episode_id": None})
        return outline
    except Exception as e:
        steps[-1]["status"] = "error"
        await _save_job(job_id, {**job, "status": "error", "message": str(e), "steps": steps})
        raise


# ── Compare page ──────────────────────────────────────────────────────────────

_COMPARE_TABLE_READY = False
_COMPARE_RUN_TABLE_READY = False
_NARRATIVE_TABLE_READY = False

async def _ensure_narrative_table():
    global _NARRATIVE_TABLE_READY
    if _NARRATIVE_TABLE_READY:
        return
    from core.db.connection import db_execute
    await db_execute(
        """
        CREATE TABLE IF NOT EXISTS narrative_analysis (
            id         SERIAL PRIMARY KEY,
            ts         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            source_key TEXT NOT NULL,
            mode       TEXT NOT NULL,
            label      TEXT,
            result     JSONB NOT NULL,
            UNIQUE (source_key, mode)
        )
        """,
        {},
    )
    _NARRATIVE_TABLE_READY = True


async def _get_cached_narrative(source_key: str, mode: str) -> dict | None:
    try:
        await _ensure_narrative_table()
        from core.db.connection import db_fetchrow
        row = await db_fetchrow(
            "SELECT result FROM narrative_analysis WHERE source_key = $key AND mode = $mode",
            {"key": source_key, "mode": mode},
        )
        if row:
            r = row["result"]
            return _json.loads(r) if isinstance(r, str) else r
    except Exception:
        pass
    return None


async def _store_narrative(source_key: str, mode: str, label: str, result: dict):
    try:
        await _ensure_narrative_table()
        from core.db.connection import db_execute
        await db_execute(
            """INSERT INTO narrative_analysis (source_key, mode, label, result)
               VALUES ($key, $mode, $label, $result::jsonb)
               ON CONFLICT (source_key, mode) DO UPDATE
               SET result = EXCLUDED.result, ts = NOW(), label = EXCLUDED.label""",
            {"key": source_key, "mode": mode, "label": label,
             "result": _json.dumps(result)},
        )
    except Exception:
        pass

async def _ensure_compare_run_table():
    global _COMPARE_RUN_TABLE_READY
    if _COMPARE_RUN_TABLE_READY:
        return
    from core.db.connection import db_execute
    await db_execute(
        """
        CREATE TABLE IF NOT EXISTS compare_run (
            id                          SERIAL PRIMARY KEY,
            ts                          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            url                         TEXT NOT NULL,
            show_name_a                 TEXT,
            show_name_b                 TEXT,
            outline_prompt_b            TEXT,
            transcript_prompt_b         TEXT,
            effective_transcript_prompt TEXT,
            effective_outline_prompt    TEXT,
            change_note                 TEXT,
            job_id_a                    TEXT,
            job_id_b                    TEXT,
            episode_id_a                TEXT,
            episode_id_b                TEXT
        )
        """,
        {},
    )
    for col in ("effective_transcript_prompt TEXT", "effective_outline_prompt TEXT", "change_note TEXT"):
        try:
            await db_execute(f"ALTER TABLE compare_run ADD COLUMN IF NOT EXISTS {col}", {})
        except Exception:
            pass
    _COMPARE_RUN_TABLE_READY = True


def _resolve_effective_prompt(task: str, override: str | None) -> str:
    """Return the actual prompt that will be used: override if set, else live file/docstring."""
    if override:
        return override
    from core.prompts.loader import load_prompt
    if task == "transcript":
        from core.prompts.transcript import GenerateTranscript as _S
    else:
        from core.prompts.outline import GenerateOutline as _S
    return load_prompt(task) or (_S.__doc__ or "").strip()


async def _save_compare_run(url: str, show_a: str, show_b: str,
                             outline_b: str | None, transcript_b: str | None,
                             job_a: str, job_b: str,
                             change_note: str | None = None) -> int:
    await _ensure_compare_run_table()
    from core.db.connection import db_fetchrow
    eff_transcript = _resolve_effective_prompt("transcript", transcript_b)
    eff_outline    = _resolve_effective_prompt("outline", outline_b)
    row = await db_fetchrow(
        """INSERT INTO compare_run (url, show_name_a, show_name_b, outline_prompt_b,
               transcript_prompt_b, effective_transcript_prompt, effective_outline_prompt,
               change_note, job_id_a, job_id_b)
           VALUES ($url, $show_a, $show_b, $outline_b, $transcript_b, $eff_t, $eff_o,
                   $note, $job_a, $job_b)
           RETURNING id""",
        {"url": url, "show_a": show_a, "show_b": show_b,
         "outline_b": outline_b, "transcript_b": transcript_b,
         "eff_t": eff_transcript, "eff_o": eff_outline,
         "note": change_note or "", "job_a": job_a, "job_b": job_b},
    )
    return row["id"]


async def _update_compare_run_episodes(job_a: str, job_b: str,
                                        ep_a: str | None, ep_b: str | None):
    try:
        await _ensure_compare_run_table()
        from core.db.connection import db_execute
        await db_execute(
            """UPDATE compare_run SET episode_id_a = $ep_a, episode_id_b = $ep_b
               WHERE job_id_a = $job_a AND job_id_b = $job_b""",
            {"ep_a": ep_a or "", "ep_b": ep_b or "", "job_a": job_a, "job_b": job_b},
        )
    except Exception:
        pass


async def _ensure_compare_table():
    global _COMPARE_TABLE_READY
    if _COMPARE_TABLE_READY:
        return
    from core.db.connection import db_execute
    await db_execute(
        """
        CREATE TABLE IF NOT EXISTS compare_feedback (
            id                 SERIAL PRIMARY KEY,
            ts                 TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            episode_id         TEXT,
            external_label     TEXT,
            curia_transcript   TEXT,
            external_transcript TEXT,
            judge_scores       JSONB,
            user_verdict       TEXT,
            user_note          TEXT
        )
        """,
        {},
    )
    _COMPARE_TABLE_READY = True


async def _run_judge(source_text: str, source_insights: dict, transcript_a: str, label_a: str, transcript_b: str, label_b: str) -> dict:
    """LLM-as-judge: score two transcripts on 5 axes, return structured result."""
    import anthropic

    AXES = [
        ("source_fidelity",   "Source Fidelity",   "Accurately represents the source. No hallucinations. Covers the key facts."),
        ("naturalness",       "Naturalness",        "Sounds like a real podcast monologue — conversational, not academic or essay-like."),
        ("hook_quality",      "Hook Quality",       "The opening grabs attention. Doesn't start with preamble or self-introduction."),
        ("coverage",          "Coverage",           "Hits the key insights and tensions from the source material."),
        ("narrative_arc",     "Narrative Arc",      "Builds toward something. Has momentum and direction, not just information delivery."),
    ]

    insights_text = "\n".join(f"- {k}: {v}" for k, v in (source_insights or {}).items() if v and str(v).lower() != "null")

    system = """You are an expert podcast quality evaluator. You score two podcast transcripts against each other on specific axes.
For each axis, give a score 1–5 for each transcript and a one-sentence rationale explaining the difference.
Then give an overall winner (or "tie") and a 2–3 sentence summary of the key differences.
Respond in JSON only."""

    human = f"""SOURCE MATERIAL (excerpt, first 3000 chars):
{source_text[:3000]}

KEY INSIGHTS FROM SOURCE:
{insights_text or "(none)"}

TRANSCRIPT A — {label_a}:
{transcript_a[:4000]}

TRANSCRIPT B — {label_b}:
{transcript_b[:4000]}

Score each axis 1–5 for both transcripts. Return JSON:
{{
  "axes": [
    {{"key": "source_fidelity", "label": "Source Fidelity", "score_a": <1-5>, "score_b": <1-5>, "rationale": "..."}},
    {{"key": "naturalness", "label": "Naturalness", "score_a": <1-5>, "score_b": <1-5>, "rationale": "..."}},
    {{"key": "hook_quality", "label": "Hook Quality", "score_a": <1-5>, "score_b": <1-5>, "rationale": "..."}},
    {{"key": "coverage", "label": "Coverage", "score_a": <1-5>, "score_b": <1-5>, "rationale": "..."}},
    {{"key": "narrative_arc", "label": "Narrative Arc", "score_a": <1-5>, "score_b": <1-5>, "rationale": "..."}}
  ],
  "winner": "A" | "B" | "tie",
  "summary": "2-3 sentences on key differences"
}}"""

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"error": "ANTHROPIC_API_KEY not set"}

    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": human}],
    )
    raw = msg.content[0].text.strip()
    # strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        return _json.loads(raw)
    except Exception:
        return {"error": "Judge returned invalid JSON", "raw": raw[:500]}


@app.get("/api/prompts/{task}")
async def get_prompt(task: str):
    from core.prompts.loader import load_prompt, PROMPTS_DIR
    allowed = {"transcript", "outline"}
    if task not in allowed:
        return JSONResponse(content={"error": "unknown task"}, status_code=400)
    if task == "transcript":
        from core.prompts.transcript import GenerateTranscript as _Sig
    else:
        from core.prompts.outline import GenerateOutline as _Sig
    text = load_prompt(task) or (_Sig.__doc__ or "").strip()
    return JSONResponse(content={"prompt": text, "source": "file" if (PROMPTS_DIR / f"{task}.txt").exists() else "docstring"})


@app.post("/api/compare/run-outline")
async def compare_run_outline(request: Request):
    """Phase 1: ingest + outline only for both A and B."""
    body = await request.json()
    url          = (body.get("url") or "").strip()
    show_name_a  = (body.get("show_name_a") or "clarity_engine").strip()
    show_name_b  = (body.get("show_name_b") or show_name_a).strip()
    outline_b    = body.get("outline_prompt_b") or None
    transcript_b = body.get("transcript_prompt_b") or None
    change_note  = (body.get("change_note") or "").strip() or None
    if not url:
        return JSONResponse(content={"error": "url required"}, status_code=400)
    job_a = str(uuid4())
    job_b = str(uuid4())
    await _save_compare_run(url, show_name_a, show_name_b, outline_b, transcript_b,
                             job_a, job_b, change_note=change_note)
    asyncio.create_task(_run_pipeline(job_a, url, show_name_a, stop_after="outline"))
    asyncio.create_task(_run_pipeline(job_b, url, show_name_b, stop_after="outline",
                                      outline_prompt_override=outline_b))
    return JSONResponse(content={"job_a": job_a, "job_b": job_b})


@app.get("/api/compare/runs-for-url")
async def runs_for_url(url: str):
    try:
        from core.ingest import normalise_url
        norm = normalise_url(url)
        await _ensure_compare_run_table()
        from core.db.connection import db_query
        rows = await db_query(
            """SELECT id, ts, show_name_a, show_name_b, change_note,
                      outline_prompt_b IS NOT NULL AS outline_overridden,
                      transcript_prompt_b IS NOT NULL AS transcript_overridden,
                      effective_transcript_prompt, effective_outline_prompt,
                      job_id_a, job_id_b, episode_id_a, episode_id_b
               FROM compare_run WHERE url = $url ORDER BY ts ASC""",
            {"url": norm},
        )
        return JSONResponse(content=_json.loads(_json.dumps(rows or [], default=_json_serial)))
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/api/compare/existing-episode")
async def existing_episode(url: str):
    """Find most recent ready episode for this URL from any user (production data)."""
    try:
        from core.ingest import normalise_url
        from core.db.connection import db_fetchrow
        norm = normalise_url(url)
        row = await db_fetchrow(
            """SELECT e.id, e.title, e.show_name, e.created_at,
                      e.outline::text AS outline, e.transcript::text AS transcript
               FROM episode e
               JOIN source s ON s.id = ANY(e.source_ids)
               WHERE s.url = $url AND s.status = 'ready' AND e.status = 'ready'
               ORDER BY e.created_at DESC LIMIT 1""",
            {"url": norm},
        )
        if not row:
            return JSONResponse(content={"found": False})
        data = _json.loads(_json.dumps(dict(row), default=_json_serial))
        # Parse JSON fields
        for k in ("outline", "transcript"):
            if isinstance(data.get(k), str):
                try: data[k] = _json.loads(data[k])
                except Exception: pass
        data["found"] = True
        return JSONResponse(content=data)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/api/compare/run-transcript")
async def compare_run_transcript(request: Request):
    """Phase 2: generate transcripts from stored outline state."""
    body = await request.json()
    job_id_a     = (body.get("job_a") or "").strip()
    job_id_b     = (body.get("job_b") or "").strip()
    transcript_b = body.get("transcript_prompt_b") or None
    briefing_b   = body.get("briefing_b") or None
    if not job_id_a or not job_id_b:
        return JSONResponse(content={"error": "job_a and job_b required"}, status_code=400)

    async def _run_both():
        import asyncio as _aio
        ep_a, ep_b = None, None
        try:
            ep_a = await _run_transcript_step(job_id_a)
        except Exception:
            pass
        try:
            ep_b = await _run_transcript_step(job_id_b, transcript_prompt_override=transcript_b,
                                               briefing_override=briefing_b)
        except Exception:
            pass
        await _update_compare_run_episodes(job_id_a, job_id_b, ep_a, ep_b)

    asyncio.create_task(_run_both())
    return JSONResponse(content={"ok": True})


@app.post("/api/compare/rerun-step")
async def compare_rerun_step(request: Request):
    """Re-run a single step (outline or transcript) for one job with a new prompt."""
    body      = await request.json()
    job_id    = (body.get("job_id") or "").strip()
    step      = (body.get("step") or "").strip()   # "outline" | "transcript"
    prompt    = body.get("prompt") or None
    if not job_id or step not in ("outline", "transcript"):
        return JSONResponse(content={"error": "job_id and step (outline|transcript) required"}, status_code=400)
    if step == "outline":
        asyncio.create_task(_rerun_outline_step(job_id, outline_prompt_override=prompt))
    else:
        asyncio.create_task(_run_transcript_step(job_id, transcript_prompt_override=prompt))
    return JSONResponse(content={"ok": True})


@app.post("/api/compare/run")
async def compare_run(request: Request):
    """Legacy full-pipeline run (kept for backwards compat)."""
    body = await request.json()
    url              = (body.get("url") or "").strip()
    show_name_a      = (body.get("show_name_a") or "clarity_engine").strip()
    show_name_b      = (body.get("show_name_b") or show_name_a).strip()
    outline_b        = body.get("outline_prompt_b") or None
    transcript_b     = body.get("transcript_prompt_b") or None
    if not url:
        return JSONResponse(content={"error": "url required"}, status_code=400)
    job_a = str(uuid4())
    job_b = str(uuid4())
    asyncio.create_task(_run_pipeline(job_a, url, show_name_a))
    asyncio.create_task(_run_pipeline(job_b, url, show_name_b,
                                      prompt_override=transcript_b,
                                      outline_prompt_override=outline_b))
    return JSONResponse(content={"job_a": job_a, "job_b": job_b})


@app.post("/api/compare/transcribe")
async def compare_transcribe(file: UploadFile = File(...)):
    groq_key     = os.getenv("GROQ_API_KEY", "")
    smallest_key = os.getenv("SMALLEST_API_KEY", "")
    if not groq_key and not smallest_key:
        return JSONResponse(content={"error": "No transcription key set (GROQ_API_KEY or SMALLEST_API_KEY)"}, status_code=500)

    data     = await file.read()
    filename = file.filename or "audio.mp3"
    groq_err = None

    # Try Groq first
    if groq_key:
        try:
            from groq import Groq
            client = Groq(api_key=groq_key)
            result = client.audio.transcriptions.create(
                model="whisper-large-v3",
                file=(filename, data),
            )
            return JSONResponse(content={"transcript": result.text, "provider": "groq"})
        except Exception as e:
            groq_err = str(e)
            # Only fall through on size/request errors; re-raise auth errors
            if "api_key" in groq_err.lower() or "authentication" in groq_err.lower():
                return JSONResponse(content={"error": groq_err}, status_code=500)

    # Fallback: Smallest AI Pulse STT
    # Endpoint: POST https://api.smallest.ai/waves/v1/stt/
    # Body: raw audio bytes, Content-Type: application/octet-stream
    if smallest_key:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=180) as client:
                resp = await client.post(
                    "https://api.smallest.ai/waves/v1/stt/",
                    params={"model": "pulse-pro", "language": "en"},
                    headers={
                        "Authorization": f"Bearer {smallest_key}",
                        "Content-Type": "application/octet-stream",
                    },
                    content=data,
                )
                resp.raise_for_status()
                text = resp.json().get("transcription") or ""
                return JSONResponse(content={"transcript": text, "provider": "smallest"})
        except Exception as e:
            fallback_err = str(e)
            err_msg = f"Groq: {groq_err} | Smallest: {fallback_err}" if groq_err else fallback_err
            return JSONResponse(content={"error": err_msg}, status_code=500)

    return JSONResponse(content={"error": groq_err or "No fallback available"}, status_code=500)


@app.post("/api/compare/youtube-transcript")
async def youtube_transcript(request: Request):
    """Fetch transcript for a YouTube URL via usetranscribe.io (no API key required)."""
    import re
    import httpx

    body = await request.json()
    url = (body.get("url") or "").strip()
    if not url:
        return JSONResponse(content={"error": "url required"}, status_code=400)

    # Extract video ID
    m = re.search(r'(?:v=|youtu\.be/|shorts/)([a-zA-Z0-9_-]{11})', url)
    if not m:
        return JSONResponse(content={"error": "Could not extract YouTube video ID from URL"}, status_code=400)
    video_id = m.group(1)

    BASE = "https://www.usetranscribe.io"
    HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; CuriaEval/1.0)"}

    async def _fetch_transcript(vid: str) -> dict:
        async with httpx.AsyncClient(timeout=30, headers=HEADERS, follow_redirects=True) as client:
            resp = await client.get(f"{BASE}/yt/{vid}?format=json")
            resp.raise_for_status()
            data = resp.json()
        # Extract and clean plain text from segments
        import re as _re

        def _clean_segments(segs: list) -> str:
            try:
                segs = sorted(segs, key=lambda s: s.get("start", 0) if isinstance(s, dict) else 0)
            except Exception:
                pass
            parts = []
            for s in segs:
                if not isinstance(s, dict):
                    continue
                t = (s.get("text") or s.get("content") or "")
                t = t.replace("\xa0", " ").replace("\n", " ")
                t = " ".join(t.split()).strip()
                if _re.fullmatch(r'[\[\(][^\]\)]{0,30}[\]\)]', t):
                    continue
                if t:
                    parts.append(t)
            return " ".join(parts).strip()

        # API may return segments at top-level OR nested under a "transcript" object
        segments = data.get("segments")
        if isinstance(segments, list) and segments:
            plain = _clean_segments(segments)
        else:
            t = data.get("transcript")
            if isinstance(t, str) and t.strip():
                # Already a plain text string
                plain = t.replace("\xa0", " ").strip()
            elif isinstance(t, dict):
                # Nested object — look for segments inside
                plain = _clean_segments(t.get("segments") or [])
                if not plain and t.get("text"):
                    plain = str(t["text"]).replace("\xa0", " ").strip()
            elif isinstance(t, list):
                plain = _clean_segments(t)
            else:
                plain = ""
        return {
            "transcript": plain,
            "title": data.get("title") or "",
            "duration": data.get("duration") or 0,
            "video_id": vid,
        }

    try:
        # Step 1: cache check
        async with httpx.AsyncClient(timeout=10, headers=HEADERS) as client:
            check = await client.get(f"{BASE}/api/check?platform=youtube&id={video_id}")
            cached = check.json().get("cached", False)

        if cached:
            return JSONResponse(content={**(await _fetch_transcript(video_id)), "source": "cache"})

        # Step 2: trigger transcription via SSE, wait for completion
        done = False
        error_msg = None
        async with httpx.AsyncClient(timeout=300, headers=HEADERS) as client:
            async with client.stream("GET", f"{BASE}/transcribe?url={url}&summarize=0") as resp:
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        evt = _json.loads(raw)
                    except Exception:
                        continue
                    stage = evt.get("stage") or evt.get("type") or ""
                    if stage in ("done", "complete", "ready") or evt.get("permalink"):
                        done = True
                        break
                    if stage == "error" or evt.get("error"):
                        error_msg = evt.get("message") or evt.get("error") or "Transcription failed"
                        break

        if error_msg:
            return JSONResponse(content={"error": error_msg}, status_code=500)

        if not done:
            return JSONResponse(content={"error": "Transcription did not complete"}, status_code=500)

        return JSONResponse(content={**(await _fetch_transcript(video_id)), "source": "fresh"})

    except httpx.TimeoutException:
        return JSONResponse(content={"error": "Transcription timed out (max 5 min). Try again or use a shorter video."}, status_code=504)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/api/compare/judge")
async def compare_judge(request: Request):
    body = await request.json()
    episode_id  = body.get("episode_id", "")
    transcript_a = body.get("transcript_a", "")
    label_a      = body.get("label_a", "Curia")
    transcript_b = body.get("transcript_b", "")
    label_b      = body.get("label_b", "External")
    source_text  = body.get("source_text", "")
    source_insights = body.get("source_insights", {})
    if not transcript_a or not transcript_b:
        return JSONResponse(content={"error": "Both transcripts required"}, status_code=400)
    result = await _run_judge(source_text, source_insights, transcript_a, label_a, transcript_b, label_b)
    return JSONResponse(content=result)


@app.post("/api/compare/analyze-structure")
async def analyze_structure(request: Request):
    """Narrative structure analysis. mode='structured' uses a taxonomy; mode='open' is fully unsupervised."""
    body = await request.json()
    transcript_text = (body.get("transcript") or "").strip()
    label           = (body.get("label") or "Transcript").strip()
    mode            = (body.get("mode") or "structured").strip()
    source_key      = (body.get("source_key") or "").strip()
    if not transcript_text:
        return JSONResponse(content={"error": "transcript required"}, status_code=400)

    # Cache check
    if source_key:
        cached = await _get_cached_narrative(source_key, mode)
        if cached:
            cached["cached"] = True
            return JSONResponse(content=cached)

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return JSONResponse(content={"error": "ANTHROPIC_API_KEY not set"}, status_code=500)

    import anthropic

    system = """You are an expert narrative analyst specializing in audio content and podcasts.
Your job is to identify the natural narrative structure of a transcript — not the topic, but what each section is *doing* to the listener narratively.
Find where the story's intention shifts. Each boundary is where the listener is being asked to feel or think something differently.
Return only valid JSON, no prose."""

    if mode == "evaluate":
        human = f"""You are analyzing the narrative structure of a podcast transcript. Your job is to reverse-engineer the structural logic — not evaluate content quality, just structure.

Read the full transcript carefully and answer the following 8 questions. Return a single JSON object with the exact keys shown.

TRANSCRIPT ({label}):
{transcript_text[:7000]}

Return this JSON (fill every field — no nulls, use "unclear" if genuinely uncertain):
{{
  "segment_mapping": [
    {{
      "segment": 1,
      "role": "what this segment is doing narratively",
      "position": "0–20% of transcript",
      "transition": "how it moves into the next segment"
    }}
  ],
  "opening": {{
    "first_sentence": "exact first sentence or very close paraphrase",
    "type": "one of: claim | question | scene | fact | contrast | other",
    "time_to_clarity": "how long before you know what the episode is about"
  }},
  "closing": {{
    "description": "how it ends — exact words or close paraphrase",
    "type": "one of: summarize | reflect | open_question | take_position | trail_off",
    "energy": "one of: up | down | flat"
  }},
  "world_knowledge": "Does the host stay strictly inside the source material or bring in outside connections? If outside knowledge appears, at what point and is it labeled or blended?",
  "fact_opinion": "Does the episode distinguish between factual claims and editorial opinion? How — explicitly flagged, tonal shift, or not at all?",
  "pacing": "Roughly how many distinct beats or ideas per minute? Does it re-hook mid-episode or assume you're staying? Where does attention risk dipping?",
  "format_contract": "One sentence: what was this episode's implicit promise to the listener? What job was it hired to do?",
  "limitations": "What listener intent would this structure fail to serve? What would a user want that this format could not deliver?"
}}"""

    elif mode == "open":
        human = f"""Read this transcript and find where the narrative intention shifts.

Do NOT use standard labels (Hook, Stakes, etc.) or podcast/essay terminology.
Name each section in your own words — describe what it's genuinely doing.
If you observe something that doesn't have a common name, invent a phrase that captures it precisely.
Let the structure tell you what it is; don't fit it to a template.

For each section:
- Where it sits in the transcript (approximate % range)
- Your name for what it's doing — in plain language, not jargon
- One sentence: what is this section doing to the listener?
- One short quoted sentence from the section as an anchor

Also describe the overall shape of the piece in 1–2 sentences — what kind of journey does the listener go on?

TRANSCRIPT ({label}):
{transcript_text[:6000]}

Return JSON:
{{
  "arc_pattern": "your own name for the overall shape",
  "arc_description": "1-2 sentences describing the listener's journey",
  "segments": [
    {{
      "position": "0-20%",
      "role": "your own label in plain language",
      "function": "one sentence — what it does to the listener",
      "anchor_quote": "short quote"
    }}
  ]
}}"""
    else:
        human = f"""Analyze the narrative structure of this transcript. Find 4–8 natural narrative segments.

Use the taxonomy below to label each segment. Pick the label that best describes what the segment is *doing*. If none fit cleanly, combine two (e.g. "Hook + Stakes") or invent a label — but default to this list first.

TAXONOMY:
- Hook: the opening move that earns the next 30 seconds — provocative claim, unexpected fact, concrete scene, or unresolved question. Job: make the listener unable to stop.
- Stakes: why this matters, who is affected. Without stakes, information is trivia.
- Mechanism: the how/why beneath the what — not "X happened" but "X happened because of this underlying force."
- Example/Anchor: a concrete instance that makes an abstract idea tangible. The mechanism lives in your head; the example makes it stick.
- Tension: two forces in conflict with no obvious resolution. The engine that keeps a piece moving.
- Counterpoint: the strongest version of the opposing view — steelmanned, not dismissed.
- Turn: the moment the piece goes somewhere unexpected. Changes direction. Good writing has at least one.
- Reframe: showing the same thing from a different angle such that it looks fundamentally different. Not a conclusion — a lens shift.
- Payoff: the moment the tension introduced earlier gets resolved or deepened. Every hook promises a payoff.
- Landing: how the piece ends — summary (weakest), reflection, open question, position taken, or a resonant detail.

Note: for audio episodes, Hook / Tension / Turn / Landing are the four that define whether a piece has a shape or just has content. Flag if any are missing.

For each segment:
- Where it sits (approximate % range)
- Role label from the taxonomy above
- One sentence: what it does to the listener
- One short quoted sentence as an anchor

Also name the overall arc pattern and write 1–2 sentences on the shape of the whole piece.

TRANSCRIPT ({label}):
{transcript_text[:6000]}

Return JSON:
{{
  "arc_pattern": "...",
  "arc_description": "1-2 sentences on the overall narrative shape",
  "missing_elements": ["list any of Hook/Tension/Turn/Landing that are absent or weak"],
  "segments": [
    {{
      "position": "0-20%",
      "role": "Hook",
      "function": "one sentence — what it does to the listener",
      "anchor_quote": "short quote from segment"
    }}
  ]
}}"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        # Evaluate mode needs more depth — use Sonnet; structured/open are fine on Haiku
        model = "claude-sonnet-4-6" if mode == "evaluate" else "claude-haiku-4-5-20251001"
        max_tok = 2400 if mode == "evaluate" else 1400
        msg = client.messages.create(
            model=model,
            max_tokens=max_tok,
            system=system,
            messages=[{"role": "user", "content": human}],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = _json.loads(raw.strip())
        result["mode"] = mode
        result["cached"] = False
        if source_key:
            await _store_narrative(source_key, mode, label, result)
        return JSONResponse(content=result)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/api/compare/feedback")
async def compare_feedback(request: Request):
    body = await request.json()
    await _ensure_compare_table()
    from core.db.connection import db_execute
    scores = body.get("judge_scores")
    # Add new columns if table was created before this change
    for col in ("curia_transcript TEXT", "external_transcript TEXT"):
        try:
            from core.db.connection import db_execute as _dbe
            await _dbe(f"ALTER TABLE compare_feedback ADD COLUMN IF NOT EXISTS {col}", {})
        except Exception:
            pass
    await db_execute(
        """INSERT INTO compare_feedback
               (episode_id, external_label, curia_transcript, external_transcript,
                judge_scores, user_verdict, user_note)
           VALUES ($episode_id, $label, $curia_t, $ext_t, $scores::jsonb, $verdict, $note)""",
        {
            "episode_id": body.get("episode_id", ""),
            "label":      body.get("external_label", ""),
            "curia_t":    body.get("curia_transcript", ""),
            "ext_t":      body.get("external_transcript", ""),
            "scores":     _json.dumps(scores) if scores else "{}",
            "verdict":    body.get("user_verdict", ""),
            "note":       body.get("user_note", ""),
        },
    )
    return JSONResponse(content={"ok": True})


@app.get("/api/compare/runs")
async def list_compare_runs():
    try:
        await _ensure_compare_run_table()
        from core.db.connection import db_query
        rows = await db_query(
            """SELECT id, ts, url, show_name_a, show_name_b,
                      outline_prompt_b IS NOT NULL AS has_outline_override,
                      transcript_prompt_b IS NOT NULL AS has_transcript_override,
                      job_id_a, job_id_b, episode_id_a, episode_id_b
               FROM compare_run ORDER BY ts DESC LIMIT 50""",
            {},
        )
        return JSONResponse(content=_json.loads(_json.dumps(rows or [], default=_json_serial)))
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/compare", response_class=HTMLResponse)
async def compare_page():
    return HTMLResponse(content=COMPARE_HTML)


COMPARE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Curia — Compare</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Lora:wght@400;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#FAFAF8;
  --surface:#FFFFFF;
  --border:#E8E4DF;
  --border-strong:#D4CFC9;
  --text:#1a1a1a;
  --muted:#6B6B6B;
  --subtle:#9A9590;
  --accent:#FF6719;
  --accent-light:#FFF3EE;
  --accent-mid:#FFD4BC;
  --good:#16a34a;
  --good-bg:#F0FDF4;
  --bad:#dc2626;
  --bad-bg:#FEF2F2;
  --blue:#2563eb;
  --blue-bg:#EFF6FF;
  --radius:10px;
  --shadow:0 1px 3px rgba(0,0,0,.07),0 1px 2px rgba(0,0,0,.04);
  --shadow-md:0 4px 12px rgba(0,0,0,.08),0 2px 4px rgba(0,0,0,.04);
}
body{font-family:'Inter',-apple-system,BlinkMacSystemFont,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}

/* ── Header ── */
header{
  background:var(--surface);border-bottom:1px solid var(--border);
  padding:0 32px;height:56px;display:flex;align-items:center;justify-content:space-between;
  position:sticky;top:0;z-index:50;
}
.logo{display:flex;align-items:baseline;gap:10px}
.logo-name{font-family:'Lora',Georgia,serif;font-size:18px;font-weight:700;color:var(--text)}
.logo-badge{
  font-size:11px;font-weight:600;letter-spacing:.04em;
  padding:2px 8px;border-radius:20px;
  background:var(--accent-light);color:var(--accent)
}
nav{display:flex;gap:20px;align-items:center}
nav a{font-size:13px;color:var(--muted);text-decoration:none;transition:color .15s}
nav a:hover{color:var(--accent)}

/* ── Tabs ── */
.tabs-bar{
  background:var(--surface);border-bottom:1px solid var(--border);
  padding:0 32px;display:flex;gap:4px
}
.tab-btn{
  padding:14px 20px;font-size:13px;font-weight:500;color:var(--muted);
  background:none;border:none;cursor:pointer;position:relative;transition:color .15s;
  border-bottom:2px solid transparent;margin-bottom:-1px
}
.tab-btn:hover{color:var(--text)}
.tab-btn.active{color:var(--accent);border-bottom-color:var(--accent)}

/* ── Layout ── */
.tab-panel{display:none;padding:32px;max-width:1200px;margin:0 auto}
.tab-panel.active{display:block}
#tab-format{padding:0}
#tab-format.active{display:flex;height:calc(100vh - 108px);overflow:hidden}

/* ── Section headers ── */
.section-eyebrow{font-size:11px;font-weight:600;letter-spacing:.08em;text-transform:uppercase;color:var(--subtle);margin-bottom:8px}
.section-title{font-family:'Lora',Georgia,serif;font-size:22px;font-weight:600;margin-bottom:6px}
.section-sub{font-size:14px;color:var(--muted);margin-bottom:28px;line-height:1.6}

/* ── Cards ── */
.card{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);box-shadow:var(--shadow)}

/* ── Config row ── */
.config-card{padding:24px;margin-bottom:28px}
.config-row{display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap}
.config-row input[type=url]{
  flex:1;min-width:280px;padding:10px 14px;
  border:1px solid var(--border);border-radius:8px;
  font-size:14px;background:var(--surface);color:var(--text);
  transition:border-color .15s,box-shadow .15s
}
.config-row input[type=url]:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-light)}
.config-field{display:flex;flex-direction:column;gap:5px}
.config-field label{font-size:11px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;color:var(--subtle)}
select{
  padding:10px 14px;border:1px solid var(--border);border-radius:8px;
  font-size:13px;background:var(--surface);color:var(--text);cursor:pointer;
  appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%239A9590'/%3E%3C/svg%3E");
  background-repeat:no-repeat;background-position:right 12px center;padding-right:32px;
  transition:border-color .15s
}
select:focus{outline:none;border-color:var(--accent)}
.btn-primary{
  padding:10px 22px;background:var(--accent);color:#fff;border:none;border-radius:8px;
  font-size:14px;font-weight:600;cursor:pointer;white-space:nowrap;transition:background .15s,transform .1s
}
.btn-primary:hover{background:#e85c10}
.btn-primary:active{transform:scale(.98)}
.btn-primary:disabled{background:#D4CFC9;cursor:not-allowed}
.btn-secondary{
  padding:9px 18px;background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:8px;
  font-size:13px;font-weight:500;cursor:pointer;transition:all .15s
}
.btn-secondary:hover{border-color:var(--accent);color:var(--accent)}

/* ── Two-column compare ── */
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.col-label{
  font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  color:var(--subtle);padding:16px 20px 10px;border-bottom:1px solid var(--border)
}
.col-label.col-a{color:var(--blue)}
.col-label.col-b{color:#7c3aed}
.col-body{padding:20px;font-size:14px;line-height:1.8;color:var(--text)}
.col-body pre{font-size:13px;white-space:pre-wrap;word-break:break-word;color:var(--text)}

/* ── Progress steps ── */
.steps-list{list-style:none;padding:16px 20px;display:flex;flex-direction:column;gap:8px}
.step{display:flex;align-items:center;gap:10px;font-size:13px;color:var(--muted)}
.step.done{color:var(--good)}.step.running{color:var(--accent)}.step.error{color:var(--bad)}
.step-icon{width:18px;height:18px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:10px;font-weight:700;flex-shrink:0}
.step.done .step-icon{background:var(--good-bg);color:var(--good)}
.step.running .step-icon{background:var(--accent-light);color:var(--accent)}
.step.error .step-icon{background:var(--bad-bg);color:var(--bad)}
.step.pending .step-icon{background:#f3f0ed;color:var(--subtle)}
@keyframes spin{to{transform:rotate(360deg)}}
.spin-icon{display:inline-block;animation:spin .7s linear infinite}

/* ── Episode result within column ── */
.result-section{border-top:1px solid var(--border);padding:20px}
.result-section-label{font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--subtle);margin-bottom:8px}
.result-title{font-family:'Lora',Georgia,serif;font-size:17px;font-weight:600;margin-bottom:6px}
.result-thread{font-size:13px;color:var(--muted);font-style:italic;line-height:1.6;margin-bottom:14px}
.transcript-block{font-size:13px;line-height:1.9;color:var(--text);max-height:420px;overflow-y:auto}

/* ── Prompt lab sidebar ── */
.analysis-mode-btn{padding:5px 12px;font-size:11px;font-weight:500;border:1px solid var(--border);border-radius:6px;background:var(--surface);color:var(--muted);cursor:pointer;transition:all .12s}
.analysis-mode-btn:hover{border-color:var(--border-strong);color:var(--text)}
.analysis-mode-btn.selected-structured{background:#EFF6FF;border-color:var(--blue);color:var(--blue)}
.analysis-mode-btn.selected-open{background:#f3f0ed;border-color:var(--border-strong);color:var(--text)}
.analysis-mode-btn.selected-evaluate{background:var(--accent-light);border-color:var(--accent);color:var(--accent)}
.plab-run{padding:9px 10px;border-radius:7px;cursor:pointer;margin-bottom:2px;transition:background .12s;border:1px solid transparent}
.plab-run:hover{background:var(--bg)}
.plab-run.slot-a{background:var(--blue-bg);border-color:#bfdbfe}
.plab-run.slot-b{background:var(--purple-bg);border-color:#ddd6fe}
.plab-run.slot-ab{background:linear-gradient(135deg,var(--blue-bg) 50%,var(--purple-bg) 50%);border-color:var(--border-strong)}
.plab-run-top{display:flex;align-items:center;gap:6px;margin-bottom:2px}
.plab-ver{font-size:10px;font-weight:700;padding:1px 7px;border-radius:10px;background:var(--bg);border:1px solid var(--border);color:var(--muted);flex-shrink:0}
.plab-run.slot-a .plab-ver,.plab-run.slot-ab .plab-ver{background:var(--blue-bg);border-color:#93c5fd;color:var(--blue)}
.plab-run.slot-b .plab-ver{background:var(--purple-bg);border-color:#c4b5fd;color:var(--purple)}
.plab-change{font-size:12px;font-weight:500;color:var(--text);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.plab-meta{font-size:10px;color:var(--subtle);margin-bottom:4px}
.plab-slots{display:flex;gap:4px;opacity:0;transition:opacity .15s}
.plab-run:hover .plab-slots,.plab-run.slot-a .plab-slots,.plab-run.slot-b .plab-slots,.plab-run.slot-ab .plab-slots{opacity:1}
.plab-slot-btn{padding:2px 8px;font-size:10px;font-weight:600;border-radius:4px;cursor:pointer;border:none;transition:all .12s}
.plab-slot-a{background:var(--blue-bg);color:var(--blue)}
.plab-slot-a:hover,.plab-slot-a.on{background:var(--blue);color:#fff}
.plab-slot-b{background:var(--purple-bg);color:var(--purple)}
.plab-slot-b:hover,.plab-slot-b.on{background:var(--purple);color:#fff}
.plab-prod-badge{font-size:9px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;padding:1px 6px;border-radius:10px;background:#fef9c3;color:#854d0e;flex-shrink:0}
.transcript-line{margin-bottom:14px}
.transcript-speaker{font-size:10px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;color:var(--subtle);margin-bottom:3px}
.transcript-text{color:var(--text)}

/* ── External compare ── */
.ext-grid{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:24px}

/* ── Upload zone ── */
.upload-zone{
  border:2px dashed var(--border-strong);border-radius:var(--radius);padding:28px 20px;
  text-align:center;cursor:pointer;transition:all .2s;background:var(--bg);position:relative
}
.upload-zone:hover,.upload-zone.drag-over{border-color:var(--accent);background:var(--accent-light)}
.upload-zone input[type=file]{position:absolute;inset:0;opacity:0;cursor:pointer;width:100%;height:100%}
.upload-zone-icon{font-size:24px;margin-bottom:8px;color:var(--muted)}
.upload-zone-text{font-size:14px;font-weight:500;color:var(--text);margin-bottom:4px}
.upload-zone-sub{font-size:12px;color:var(--muted)}
.upload-filename{margin-top:8px;font-size:12px;color:var(--accent);font-weight:500}

/* ── Episode picker ── */
.episode-picker-card{padding:20px;margin-bottom:20px}
.episode-picker-label{font-size:11px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;color:var(--subtle);margin-bottom:8px}
#episode-select{width:100%}

/* ── Status bar ── */
.status-bar{
  display:none;padding:12px 16px;border-radius:8px;font-size:13px;
  align-items:center;gap:10px;margin-bottom:16px
}
.status-bar.running{display:flex;background:var(--accent-light);color:var(--accent);border:1px solid var(--accent-mid)}
.status-bar.error{display:flex;background:var(--bad-bg);color:var(--bad);border:1px solid #fecaca}
.status-bar.done{display:flex;background:var(--good-bg);color:var(--good);border:1px solid #bbf7d0}

/* ── Judge panel ── */
.judge-card{margin-bottom:24px}
.judge-header{
  padding:18px 24px;border-bottom:1px solid var(--border);
  display:flex;align-items:center;justify-content:space-between
}
.judge-title{font-family:'Lora',Georgia,serif;font-size:17px;font-weight:600}
.judge-subtitle{font-size:13px;color:var(--muted);margin-top:2px}
.judge-body{padding:20px 24px}
.axis-row{
  display:grid;grid-template-columns:140px 1fr 1fr 1fr;gap:12px;align-items:start;
  padding:14px 0;border-bottom:1px solid var(--border)
}
.axis-row:last-child{border-bottom:none}
.axis-name{font-size:13px;font-weight:600;color:var(--text);padding-top:2px}
.score-cell{text-align:center}
.score-label{font-size:10px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;color:var(--subtle);margin-bottom:6px}
.score-pip-row{display:flex;gap:3px;justify-content:center;margin-bottom:4px}
.score-pip{width:14px;height:14px;border-radius:3px;background:var(--border)}
.score-pip.filled-a{background:var(--blue)}
.score-pip.filled-b{background:#7c3aed}
.score-num{font-size:18px;font-weight:700}
.score-num.col-a{color:var(--blue)}
.score-num.col-b{color:#7c3aed}
.axis-rationale{font-size:12px;color:var(--muted);line-height:1.6;padding-top:2px}
.judge-winner{
  margin-top:16px;padding:16px 20px;border-radius:8px;
  background:var(--accent-light);border:1px solid var(--accent-mid)
}
.judge-winner-label{font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--accent);margin-bottom:6px}
.judge-winner-text{font-size:14px;line-height:1.7;color:var(--text)}
.axes-header{
  display:grid;grid-template-columns:140px 1fr 1fr 1fr;gap:12px;
  padding:0 0 10px;border-bottom:2px solid var(--border-strong);margin-bottom:4px
}
.axes-header span{font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--subtle);text-align:center}
.axes-header span:first-child{text-align:left}

/* ── User feedback ── */
.feedback-card{margin-bottom:24px}
.feedback-body{padding:20px 24px}
.verdict-row{display:flex;gap:10px;margin-bottom:16px;flex-wrap:wrap}
.verdict-btn{
  padding:8px 18px;border-radius:20px;font-size:13px;font-weight:500;
  border:1px solid var(--border);background:var(--surface);color:var(--muted);cursor:pointer;transition:all .15s
}
.verdict-btn:hover{border-color:var(--border-strong);color:var(--text)}
.verdict-btn.sel-a{background:var(--blue-bg);border-color:var(--blue);color:var(--blue)}
.verdict-btn.sel-b{background:#f5f3ff;border-color:#7c3aed;color:#7c3aed}
.verdict-btn.sel-tie{background:var(--accent-light);border-color:var(--accent);color:var(--accent)}
.feedback-note{
  width:100%;padding:10px 14px;border:1px solid var(--border);border-radius:8px;
  font-size:13px;font-family:inherit;resize:vertical;min-height:72px;
  color:var(--text);transition:border-color .15s;margin-bottom:12px
}
.feedback-note:focus{outline:none;border-color:var(--accent)}
.saved-confirm{display:none;color:var(--good);font-size:13px;font-weight:500;margin-top:8px}
.saved-confirm.show{display:block}

/* ── Empty states ── */
.empty-hint{
  padding:40px 20px;text-align:center;color:var(--subtle);font-size:14px;line-height:1.7
}
.empty-icon{font-size:32px;margin-bottom:12px;opacity:.5}

/* ── Transcript compare side-by-side ── */
.compare-cols{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:24px}
</style>
</head>
<body>

<header>
  <div class="logo">
    <span class="logo-name">Curia</span>
    <span class="logo-badge">compare</span>
  </div>
  <nav>
    <a href="/eval">← Eval</a>
    <a href="/eval/export.csv">Export CSV</a>
  </nav>
</header>

<div class="tabs-bar">
  <button class="tab-btn active" id="tab-btn-format" onclick="switchTab('format')">Prompt A vs B</button>
  <button class="tab-btn" id="tab-btn-external" onclick="switchTab('external')">vs External</button>
  <button class="tab-btn" id="tab-btn-history" onclick="switchTab('history');loadHistory()">History</button>
</div>

<!-- ── Tab: Prompt A vs B ── -->
<div class="tab-panel active" id="tab-format">

  <!-- ── Run history sidebar ── -->
  <div id="plab-sidebar" style="width:268px;flex-shrink:0;border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden;background:var(--surface)">
    <div style="padding:14px 16px;border-bottom:1px solid var(--border);flex-shrink:0">
      <div style="font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--subtle);margin-bottom:6px">Article</div>
      <div id="plab-url-display" style="font-size:11px;color:var(--muted);font-style:italic">Enter a URL to start</div>
    </div>
    <div style="padding:10px 12px 6px;display:flex;align-items:center;justify-content:space-between;flex-shrink:0">
      <span style="font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--subtle)">Runs</span>
      <span id="plab-run-count" style="font-size:11px;color:var(--subtle)"></span>
    </div>
    <div id="plab-runs" style="flex:1;overflow-y:auto;padding:0 8px 8px"></div>
    <div style="padding:10px;border-top:1px solid var(--border);flex-shrink:0">
      <button onclick="startNewRun()" style="width:100%;padding:8px;background:var(--accent);color:#fff;border:none;border-radius:7px;font-size:12px;font-weight:600;cursor:pointer;transition:background .15s" onmouseover="this.style.background='#e85c10'" onmouseout="this.style.background='var(--accent)'">+ New run</button>
    </div>
  </div>

  <!-- ── Main area ── -->
  <div style="flex:1;overflow-y:auto;padding:24px 28px">

    <!-- URL + run -->
    <div class="card config-card" style="margin-bottom:20px">
      <div class="config-row">
        <input type="url" id="fmt-url" placeholder="https://…" style="flex:1" oninput="onUrlInput()">
        <button class="btn-primary" id="fmt-btn" onclick="runPromptCompare()">Generate outlines →</button>
      </div>
    </div>

    <div class="two-col" style="margin-bottom:20px;align-items:start">

      <!-- A: Control -->
      <div class="card" style="opacity:.75">
        <div class="col-label col-a" style="display:flex;align-items:center;justify-content:space-between">
          <span>A — Control</span>
          <span style="font-size:10px;font-weight:500;background:#EFF6FF;color:var(--blue);padding:2px 8px;border-radius:10px;text-transform:none;letter-spacing:0">current pipeline</span>
        </div>
        <div style="padding:16px 20px">
          <div class="config-field" style="margin-bottom:8px">
            <label>Show format</label>
            <select id="fmt-show-a" style="width:100%">
              <option value="clarity_engine">clarity_engine</option>
              <option value="narrative_drift">narrative_drift</option>
              <option value="momentum_loop">momentum_loop</option>
              <option value="exploration_engine">exploration_engine</option>
            </select>
          </div>
          <div id="existing-ep-notice" style="display:none;font-size:11px;padding:6px 8px;background:var(--good-bg);border:1px solid #bbf7d0;border-radius:6px;color:var(--good)">
            ✓ Production episode found — will use cached output
          </div>
          <div id="no-existing-notice" style="font-size:12px;color:var(--muted);font-style:italic">Outline + transcript prompts: live defaults</div>
        </div>
      </div>

      <!-- B: Variant -->
      <div class="card">
        <div class="col-label col-b" style="display:flex;align-items:center;justify-content:space-between">
          <span>B — Variant</span>
          <span id="b-change-count" style="font-size:10px;font-weight:500;background:var(--accent-light);color:var(--accent);padding:2px 8px;border-radius:10px;text-transform:none;letter-spacing:0;display:none">0 overrides</span>
        </div>
        <div style="padding:16px 20px">
          <!-- Change note -->
          <div style="margin-bottom:14px">
            <div style="font-size:11px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;color:var(--subtle);margin-bottom:5px">What changed?</div>
            <input type="text" id="change-note-input" placeholder="e.g. removed rhetorical questions rule"
              style="width:100%;padding:7px 10px;border:1px solid var(--border);border-radius:6px;font-size:12px;color:var(--text);background:var(--bg);outline:none;transition:border-color .15s"
              onfocus="this.style.borderColor='var(--accent)'" onblur="this.style.borderColor='var(--border)'">
          </div>
          <!-- Show format -->
          <div class="config-field" style="margin-bottom:14px">
            <label>Show format</label>
            <select id="fmt-show-b" style="width:100%" onchange="updateChangeCount()">
              <option value="clarity_engine">clarity_engine</option>
              <option value="narrative_drift">narrative_drift</option>
              <option value="momentum_loop">momentum_loop</option>
              <option value="exploration_engine">exploration_engine</option>
            </select>
          </div>
          <!-- Outline prompt -->
          <div style="margin-bottom:12px">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:5px">
              <span style="font-size:12px;font-weight:600;color:var(--text)">Outline prompt</span>
              <div style="display:flex;align-items:center;gap:8px">
                <span id="outline-pill" style="font-size:10px;padding:2px 8px;border-radius:10px;background:#f3f0ed;color:var(--subtle)">default</span>
                <button class="btn-secondary" style="padding:4px 10px;font-size:11px" onclick="togglePromptOverride('outline')">Edit</button>
              </div>
            </div>
            <textarea id="outline-override" data-task="outline"
              style="display:none;width:100%;height:180px;padding:10px 12px;font-family:'SF Mono',Menlo,monospace;font-size:11px;line-height:1.65;border:1px solid var(--border);border-radius:6px;resize:vertical;color:var(--text);background:var(--bg);outline:none"
              oninput="onPromptEdit('outline')"></textarea>
            <div id="outline-actions" style="display:none;margin-top:8px;gap:8px;justify-content:flex-end">
              <button class="btn-secondary" style="padding:5px 12px;font-size:12px" onclick="cancelPromptEdit('outline')">Cancel</button>
              <button class="btn-primary" style="padding:5px 14px;font-size:12px" onclick="submitPromptEdit('outline')">Submit ✓</button>
            </div>
          </div>
          <!-- Transcript prompt -->
          <div>
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:5px">
              <span style="font-size:12px;font-weight:600;color:var(--text)">Transcript prompt</span>
              <div style="display:flex;align-items:center;gap:8px">
                <span id="transcript-pill" style="font-size:10px;padding:2px 8px;border-radius:10px;background:#f3f0ed;color:var(--subtle)">default</span>
                <button class="btn-secondary" style="padding:4px 10px;font-size:11px" onclick="togglePromptOverride('transcript')">Edit</button>
              </div>
            </div>
            <textarea id="transcript-override" data-task="transcript"
              style="display:none;width:100%;height:260px;padding:10px 12px;font-family:'SF Mono',Menlo,monospace;font-size:11px;line-height:1.65;border:1px solid var(--border);border-radius:6px;resize:vertical;color:var(--text);background:var(--bg);outline:none"
              oninput="onPromptEdit('transcript')"></textarea>
            <div id="transcript-actions" style="display:none;margin-top:8px;gap:8px;justify-content:flex-end">
              <button class="btn-secondary" style="padding:5px 12px;font-size:12px" onclick="cancelPromptEdit('transcript')">Cancel</button>
              <button class="btn-primary" style="padding:5px 14px;font-size:12px" onclick="submitPromptEdit('transcript')">Submit ✓</button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Phase 2 bar -->
    <div id="phase2-bar" style="display:none;margin-bottom:20px;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);overflow:hidden">
      <div style="padding:14px 18px;display:flex;align-items:center;justify-content:space-between;gap:16px">
        <div>
          <div style="font-size:13px;font-weight:600;color:var(--text)">Outlines ready</div>
          <div style="font-size:12px;color:var(--muted);margin-top:2px">Review outlines above, then generate transcripts</div>
        </div>
        <div style="display:flex;align-items:center;gap:8px">
          <button class="btn-secondary" id="briefing-toggle-btn" onclick="toggleBriefingEdit()">Edit B briefing ↓</button>
          <button class="btn-primary" id="transcript-btn" onclick="runTranscripts()">Generate transcripts →</button>
        </div>
      </div>
      <div id="briefing-edit-panel" style="display:none;padding:0 18px 14px;border-top:1px solid var(--border)">
        <div style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:0.07em;color:var(--muted);margin:12px 0 6px">B — Briefing JSON (edit format_config to change voice style + rules)</div>
        <textarea id="briefing-b-override"
          style="width:100%;height:320px;background:#0a0a0a;border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:11px;font-family:ui-monospace,monospace;padding:10px 12px;resize:vertical;outline:none;line-height:1.6"
          placeholder="Loading briefing…"
          oninput="this.dataset.modified='1'"></textarea>
      </div>
    </div>

    <!-- Results columns -->
    <div class="two-col" id="fmt-cols" style="display:none">
      <div class="card" id="fmt-col-a">
        <div class="col-label col-a" id="fmt-label-a">A — Control</div>
        <ul class="steps-list" id="fmt-steps-a"></ul>
        <div id="fmt-result-a"></div>
      </div>
      <div class="card" id="fmt-col-b">
        <div class="col-label col-b" id="fmt-label-b">B — Variant</div>
        <ul class="steps-list" id="fmt-steps-b"></ul>
        <div id="fmt-result-b"></div>
        <div id="fmt-rerun-b" style="display:none;padding:12px 16px;border-top:1px solid var(--border);gap:8px;flex-wrap:wrap"></div>
      </div>
    </div>

  </div><!-- /main area -->
</div>

<!-- ── Tab: vs External ── -->
<div class="tab-panel" id="tab-external">
  <div class="section-eyebrow">Analysis &amp; comparison</div>
  <h2 class="section-title">Transcript Analysis</h2>
  <p class="section-sub">Load a Curia episode, upload an external transcript (MP3 or text file), or both. Analyze narrative structure independently or compare side by side.</p>

  <!-- Pickers row -->
  <div class="ext-grid">
    <div class="card episode-picker-card">
      <div class="episode-picker-label">Curia episode (optional)</div>
      <select id="episode-select" onchange="onEpisodeSelect()">
        <option value="">— or skip if analyzing external only —</option>
      </select>
    </div>
    <div class="card" style="padding:16px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
        <div class="episode-picker-label" style="margin-bottom:0">External transcript (optional)</div>
        <div style="display:flex;gap:4px">
          <button id="src-tab-file" class="analysis-mode-btn selected-structured" onclick="switchExtSource('file')" style="font-size:10px;padding:2px 9px">File</button>
          <button id="src-tab-youtube" class="analysis-mode-btn" onclick="switchExtSource('youtube')" style="font-size:10px;padding:2px 9px">YouTube</button>
        </div>
      </div>

      <!-- File upload panel -->
      <div id="ext-source-file">
        <div class="upload-zone" id="upload-zone">
          <input type="file" id="mp3-file" accept="audio/*,video/mp4,text/plain,.txt,.md" onchange="onFileSelect(this)">
          <div class="upload-zone-icon">🎙</div>
          <div class="upload-zone-text">MP3 or text file (.txt)</div>
          <div class="upload-zone-sub">NotebookLM, Wondercraft, plain transcript…</div>
          <div class="upload-filename" id="upload-filename"></div>
        </div>
        <div style="margin-top:10px;display:flex;gap:8px">
          <input type="text" id="ext-label" placeholder='Label (e.g. "NotebookLM")' style="flex:1;padding:8px 12px;border:1px solid var(--border);border-radius:6px;font-size:13px">
          <button class="btn-primary" id="transcribe-btn" onclick="transcribeAudio()" disabled>Load →</button>
        </div>
      </div>

      <!-- YouTube URL panel -->
      <div id="ext-source-youtube" style="display:none">
        <input type="url" id="yt-url" placeholder="https://youtube.com/watch?v=..." oninput="onYtUrlInput()"
          style="width:100%;padding:9px 12px;border:1px solid var(--border);border-radius:6px;font-size:13px;margin-bottom:8px;box-sizing:border-box">
        <div style="display:flex;gap:8px">
          <input type="text" id="yt-label" placeholder='Label (e.g. "NotebookLM podcast")' style="flex:1;padding:8px 12px;border:1px solid var(--border);border-radius:6px;font-size:13px">
          <button class="btn-primary" id="yt-btn" onclick="fetchYoutubeTranscript()" disabled>Get transcript →</button>
        </div>
        <div style="margin-top:8px;font-size:11px;color:var(--subtle)">Powered by usetranscribe.io · max 90 min · no API key needed</div>
      </div>
    </div>
  </div>

  <div class="status-bar" id="ext-status"></div>

  <!-- Transcripts — each shown independently, side by side when both loaded -->
  <div id="ext-transcripts" style="display:none;margin-bottom:24px">
    <div id="ext-transcript-cols" style="display:grid;gap:16px">
      <div class="card" id="ext-curia-card" style="display:none">
        <div style="display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--border)">
          <div class="col-label col-a" id="curia-col-label" style="border-bottom:none;padding-bottom:14px">Curia</div>
          <div style="display:flex;align-items:center;gap:8px;padding:10px 16px 10px">
            <button onclick="toggleTranscript('curia')" id="toggle-curia-transcript"
              style="padding:3px 10px;font-size:11px;border:1px solid var(--border);border-radius:6px;background:var(--surface);color:var(--muted);cursor:pointer">Hide transcript</button>
          </div>
        </div>
        <div class="col-body" id="curia-transcript-body"></div>
        <div style="border-top:1px solid var(--border);padding:10px 16px 12px">
          <div style="display:flex;gap:6px;margin-bottom:8px">
            <button class="analysis-mode-btn" id="mode-curia-structured" data-side="curia" data-mode="structured" onclick="selectAnalysisMode('curia','structured')">Structured</button>
            <button class="analysis-mode-btn" id="mode-curia-open" data-side="curia" data-mode="open" onclick="selectAnalysisMode('curia','open')">Open</button>
            <button class="analysis-mode-btn" id="mode-curia-evaluate" data-side="curia" data-mode="evaluate" onclick="selectAnalysisMode('curia','evaluate')">Evaluate</button>
          </div>
          <div style="display:flex;align-items:center;gap:8px;margin-top:0">
            <button class="btn-primary" id="run-curia-btn" style="padding:5px 14px;font-size:12px;display:none" onclick="analyzeStructure('curia', _selectedMode['curia'])">Run analysis →</button>
            <button id="view-prompt-curia" style="display:none;padding:4px 10px;font-size:11px;border:1px solid var(--border);border-radius:6px;background:var(--surface);color:var(--muted);cursor:pointer" onclick="togglePromptView('curia')">View prompt</button>
          </div>
          <div id="prompt-view-curia" style="display:none;margin-top:10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;overflow:hidden">
            <pre style="padding:12px;font-size:10.5px;font-family:'SF Mono',Menlo,monospace;line-height:1.65;white-space:pre-wrap;word-break:break-word;color:var(--muted);max-height:280px;overflow-y:auto;margin:0"></pre>
          </div>
        </div>
      </div>
      <div class="card" id="ext-external-card" style="display:none">
        <div style="display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid var(--border)">
          <div class="col-label col-b" id="ext-col-label" style="border-bottom:none;padding-bottom:14px">External</div>
          <div style="display:flex;align-items:center;gap:8px;padding:10px 16px 10px">
            <button onclick="toggleTranscript('external')" id="toggle-external-transcript"
              style="padding:3px 10px;font-size:11px;border:1px solid var(--border);border-radius:6px;background:var(--surface);color:var(--muted);cursor:pointer">Hide transcript</button>
          </div>
        </div>
        <div class="col-body" id="ext-transcript-body"></div>
        <div style="border-top:1px solid var(--border);padding:10px 16px 12px">
          <div style="display:flex;gap:6px;margin-bottom:8px">
            <button class="analysis-mode-btn" id="mode-external-structured" data-side="external" data-mode="structured" onclick="selectAnalysisMode('external','structured')">Structured</button>
            <button class="analysis-mode-btn" id="mode-external-open" data-side="external" data-mode="open" onclick="selectAnalysisMode('external','open')">Open</button>
            <button class="analysis-mode-btn" id="mode-external-evaluate" data-side="external" data-mode="evaluate" onclick="selectAnalysisMode('external','evaluate')">Evaluate</button>
          </div>
          <div style="display:flex;align-items:center;gap:8px;margin-top:0">
            <button class="btn-primary" id="run-external-btn" style="padding:5px 14px;font-size:12px;display:none" onclick="analyzeStructure('external', _selectedMode['external'])">Run analysis →</button>
            <button id="view-prompt-external" style="display:none;padding:4px 10px;font-size:11px;border:1px solid var(--border);border-radius:6px;background:var(--surface);color:var(--muted);cursor:pointer" onclick="togglePromptView('external')">View prompt</button>
          </div>
          <div id="prompt-view-external" style="display:none;margin-top:10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;overflow:hidden">
            <pre style="padding:12px;font-size:10.5px;font-family:'SF Mono',Menlo,monospace;line-height:1.65;white-space:pre-wrap;word-break:break-word;color:var(--muted);max-height:280px;overflow-y:auto;margin:0"></pre>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- Narrative structure panels — one per loaded transcript -->
  <div id="narrative-panels" style="display:none;margin-bottom:24px">
    <div style="font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--subtle);margin-bottom:12px">Narrative Structure</div>
    <div id="narrative-cols" style="display:grid;gap:16px"></div>
  </div>

  <!-- Judge panel (only when both loaded) -->
  <div class="card judge-card" id="judge-card" style="display:none">
    <div class="judge-header">
      <div>
        <div class="judge-title">LLM Judge</div>
        <div class="judge-subtitle">Scores both transcripts on 5 axes — requires both loaded</div>
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <button class="btn-secondary" style="padding:6px 12px;font-size:12px" onclick="toggleJudgePrompt()">View prompt</button>
        <button class="btn-primary" id="judge-btn" onclick="runJudge()">Run judge →</button>
      </div>
    </div>
    <!-- Judge prompt — collapsed by default -->
    <div id="judge-prompt-panel" style="display:none;border-bottom:1px solid var(--border);background:var(--bg)">
      <div style="padding:12px 20px;font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--subtle)">System prompt</div>
      <pre style="padding:0 20px 12px;font-size:11px;font-family:'SF Mono',Menlo,monospace;line-height:1.65;color:var(--muted);white-space:pre-wrap;word-break:break-word">You are an expert podcast quality evaluator. You score two podcast transcripts against each other on specific axes.
For each axis, give a score 1–5 for each transcript and a one-sentence rationale explaining the difference.
Then give an overall winner (or "tie") and a 2–3 sentence summary of the key differences.
Respond in JSON only.</pre>
      <div style="padding:0 20px 4px;font-size:10px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:var(--subtle)">Scoring axes</div>
      <div style="padding:4px 20px 14px;font-size:12px;color:var(--muted);line-height:1.8">
        <div><span style="font-weight:600;color:var(--text)">Source Fidelity</span> — Accurately represents the source. No hallucinations. Covers the key facts.</div>
        <div><span style="font-weight:600;color:var(--text)">Naturalness</span> — Sounds like a real podcast monologue — conversational, not academic or essay-like.</div>
        <div><span style="font-weight:600;color:var(--text)">Hook Quality</span> — The opening grabs attention. Doesn't start with preamble or self-introduction.</div>
        <div><span style="font-weight:600;color:var(--text)">Coverage</span> — Hits the key insights and tensions from the source material.</div>
        <div><span style="font-weight:600;color:var(--text)">Narrative Arc</span> — Builds toward something. Has momentum and direction, not just information delivery.</div>
      </div>
    </div>
    <div class="judge-body" id="judge-body">
      <div class="empty-hint" style="padding:20px 0">Load both transcripts to run the judge.</div>
    </div>
  </div>

  <!-- User feedback -->
  <div class="card feedback-card" id="feedback-card" style="display:none">
    <div class="judge-header">
      <div>
        <div class="judge-title">Your verdict</div>
        <div class="judge-subtitle">Override or confirm the judge's assessment</div>
      </div>
    </div>
    <div class="feedback-body">
      <div class="verdict-row">
        <button class="verdict-btn" id="vb-a" onclick="setVerdict('curia')">Curia better</button>
        <button class="verdict-btn" id="vb-b" onclick="setVerdict('external')">External better</button>
        <button class="verdict-btn" id="vb-tie" onclick="setVerdict('tie')">About equal</button>
      </div>
      <textarea class="feedback-note" id="feedback-note" placeholder="Notes — what was different? what worked?"></textarea>
      <button class="btn-primary" onclick="submitFeedback()">Save verdict →</button>
      <div class="saved-confirm" id="saved-confirm">✓ Saved</div>
    </div>
  </div>
</div>

<!-- ── Tab: History ── -->
<div class="tab-panel" id="tab-history">
  <div class="section-eyebrow">Past runs</div>
  <h2 class="section-title">Run history</h2>
  <p class="section-sub">Every compare run is saved here — URL, show config, what was overridden, episode IDs.</p>
  <div id="history-body">
    <div class="empty-hint"><div class="empty-icon">📋</div>Loading…</div>
  </div>
</div>

<script>
const esc = s => String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');

// ── Tab switching ─────────────────────────────────────────────────────────────
function switchTab(name) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  document.getElementById('tab-btn-' + name).classList.add('active');
}

// ── Prompt lab sidebar ────────────────────────────────────────────────────────
let _plabRuns = [];     // all runs for current URL
let _plabSlotA = null;  // run index in slot A
let _plabSlotB = null;  // run index in slot B
let _existingEp = null; // production episode for current URL
let _urlCheckTimer = null;

function onUrlInput() {
  clearTimeout(_urlCheckTimer);
  _urlCheckTimer = setTimeout(checkUrlForExistingData, 600);
}

async function checkUrlForExistingData() {
  const url = document.getElementById('fmt-url').value.trim();
  if (!url || !url.startsWith('http')) return;

  // Update sidebar URL display
  try { document.getElementById('plab-url-display').textContent = new URL(url).hostname + '…'; }
  catch { document.getElementById('plab-url-display').textContent = url.slice(0, 40) + '…'; }

  // Check for existing production episode
  const epRes = await fetch('/api/compare/existing-episode?url=' + encodeURIComponent(url));
  const epData = await epRes.json();
  _existingEp = epData.found ? epData : null;
  document.getElementById('existing-ep-notice').style.display = _existingEp ? '' : 'none';
  document.getElementById('no-existing-notice').style.display = _existingEp ? 'none' : '';

  // Load run history for this URL
  await refreshSidebar(url);
}

async function refreshSidebar(url) {
  if (!url) url = document.getElementById('fmt-url').value.trim();
  if (!url) return;
  try {
    const res = await fetch('/api/compare/runs-for-url?url=' + encodeURIComponent(url));
    const data = await res.json();
    _plabRuns = data || [];
    renderSidebar();
  } catch {}
}

function renderSidebar() {
  const el = document.getElementById('plab-runs');
  document.getElementById('plab-run-count').textContent = _plabRuns.length ? `${_plabRuns.length} run${_plabRuns.length > 1 ? 's' : ''}` : '';

  let html = '';

  // Production episode entry
  if (_existingEp) {
    html += `<div class="plab-run" id="plab-prod" style="border:1px dashed var(--border-strong)">
      <div class="plab-run-top">
        <span class="plab-ver" style="background:#fef9c3;border-color:#fde047;color:#854d0e">prod</span>
        <span class="plab-change">Production episode</span>
        <span class="plab-prod-badge">cached</span>
      </div>
      <div class="plab-meta">${esc(new Date(_existingEp.created_at).toLocaleDateString('en-US', {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}))}</div>
      <div class="plab-slots">
        <button class="plab-slot-btn plab-slot-a" onclick="loadExistingToSlot('a');event.stopPropagation()">← Left</button>
        <button class="plab-slot-btn plab-slot-b" onclick="loadExistingToSlot('b');event.stopPropagation()">Right →</button>
      </div>
    </div>`;
  }

  if (!_plabRuns.length && !_existingEp) {
    html += `<div style="padding:20px 8px;text-align:center;font-size:12px;color:var(--subtle)">No runs yet for this URL</div>`;
  }

  _plabRuns.forEach((r, i) => {
    const isA = _plabSlotA === i, isB = _plabSlotB === i;
    const cls = isA && isB ? 'slot-ab' : isA ? 'slot-a' : isB ? 'slot-b' : '';
    const ver = 'v' + (i + 1);
    const note = r.change_note || (r.transcript_overridden ? 'transcript override' : r.outline_overridden ? 'outline override' : 'default prompts');
    const ts = new Date(r.ts).toLocaleDateString('en-US', {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
    const hasEp = r.episode_id_b || r.episode_id_a;
    html += `<div class="plab-run ${cls}" id="plab-run-${i}">
      <div class="plab-run-top">
        <span class="plab-ver">${ver}</span>
        <span class="plab-change">${esc(note)}</span>
        ${!hasEp ? '<span style="font-size:9px;color:var(--subtle);font-style:italic">outline only</span>' : ''}
      </div>
      <div class="plab-meta">${esc(ts)} · ${esc(r.show_name_b || r.show_name_a || '')}</div>
      <div class="plab-slots">
        <button class="plab-slot-btn plab-slot-a ${isA ? 'on' : ''}" onclick="assignSlot(${i},'a');event.stopPropagation()">← Left</button>
        <button class="plab-slot-btn plab-slot-b ${isB ? 'on' : ''}" onclick="assignSlot(${i},'b');event.stopPropagation()">Right →</button>
      </div>
    </div>`;
  });
  el.innerHTML = html;
}

function startNewRun() {
  // Clear B overrides + change note, keep URL + show format, ready for next iteration
  document.getElementById('change-note-input').value = '';
  cancelPromptEdit('outline');
  cancelPromptEdit('transcript');
  document.getElementById('fmt-url').focus();
  document.getElementById('fmt-rerun-b').style.display = 'none';
  document.getElementById('fmt-rerun-b').innerHTML = '';
}

function assignSlot(idx, slot) {
  if (slot === 'a') _plabSlotA = idx;
  else _plabSlotB = idx;
  renderSidebar();
  const run = _plabRuns[idx];
  const epId = slot === 'a' ? run.episode_id_a : run.episode_id_b;
  if (epId) loadEpisodeIntoColumn(epId, slot === 'a' ? 'a' : 'b');
}

async function loadExistingToSlot(slot, phase) {
  // phase: 'outline' = show outline only (matching B during outline step)
  //        'transcript' or undefined = show full episode
  if (!_existingEp) return;
  document.getElementById('fmt-cols').style.display = 'grid';
  const container = document.getElementById(slot === 'a' ? 'fmt-result-a' : 'fmt-result-b');
  container.innerHTML = '';
  const div = document.createElement('div');
  const outline = _existingEp.outline || {};
  const lines = Array.isArray(_existingEp.transcript) ? _existingEp.transcript : [];

  if (outline.title || outline.thread || (outline.segments||[]).length) {
    div.innerHTML += `<div class="result-section">
      <div class="result-section-label">Outline${phase === 'outline' ? ' (production / cached)' : ''}</div>
      ${outline.title ? `<div class="result-title">${esc(outline.title)}</div>` : ''}
      ${outline.thread ? `<div class="result-thread">${esc(outline.thread)}</div>` : ''}
      ${(outline.segments||[]).map((s,i)=>`
        <div style="margin-bottom:6px;font-size:13px">
          <span style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:var(--subtle)">Seg ${s.segment||i+1}</span>
          <span style="margin-left:8px">${esc(s.title||s.focus||s.purpose||'')}</span>
        </div>`).join('')}
    </div>`;
  }

  if (phase !== 'outline' && lines.length) {
    const plainText = lines.map(l => l.text||'').join('\n\n');
    const copyId = 'copy-prod-' + slot;
    div.innerHTML += `<div class="result-section">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
        <div class="result-section-label" style="margin-bottom:0">Transcript (production / cached)</div>
        <button id="${copyId}" onclick="copyTranscript('${copyId}', \`${plainText.replace(/`/g,'\\`').replace(/\$/g,'\\$')}\`)"
          style="padding:3px 10px;font-size:11px;font-weight:500;border:1px solid var(--border);border-radius:6px;background:var(--surface);color:var(--muted);cursor:pointer">Copy</button>
      </div>
      <div class="transcript-block">${lines.map(l=>`<div class="transcript-line"><div class="transcript-text">${esc(l.text||'')}</div></div>`).join('')}</div>
    </div>`;
  }
  container.appendChild(div);
}

// ── Control vs Variant ────────────────────────────────────────────────────────
let fmtPollA = null, fmtPollB = null;
const _promptCache = {};

async function togglePromptOverride(task) {
  const ta = document.getElementById(task + '-override');
  const actions = document.getElementById(task + '-actions');
  const isHidden = ta.style.display === 'none';
  if (isHidden) {
    if (!ta.value) {
      if (!_promptCache[task]) {
        const r = await fetch('/api/prompts/' + task);
        _promptCache[task] = (await r.json()).prompt;
      }
      ta.value = _promptCache[task];
    }
    ta.style.display = 'block';
    actions.style.display = 'flex';
    document.querySelector(`[onclick="togglePromptOverride('${task}')"]`).textContent = 'Remove';
  } else {
    cancelPromptEdit(task);
  }
}

function onPromptEdit(task) {
  // live feedback — pill updates but edit isn't "committed" until Submit
  const pill = document.getElementById(task + '-pill');
  pill.textContent = 'editing…';
  pill.style.background = '#FFF3EE';
  pill.style.color = 'var(--accent)';
}

function submitPromptEdit(task) {
  const ta = document.getElementById(task + '-override');
  const pill = document.getElementById(task + '-pill');
  ta.dataset.modified = '1';
  pill.textContent = 'modified ✓';
  pill.style.background = 'var(--accent-light)';
  pill.style.color = 'var(--accent)';
  ta.style.display = 'none';
  document.getElementById(task + '-actions').style.display = 'none';
  document.querySelector(`[onclick="togglePromptOverride('${task}')"]`).textContent = 'Edit';
  updateChangeCount();
  // If a run is already done, offer re-run
  if (_fmtJobB) showRerunButton(task);
}

function cancelPromptEdit(task) {
  const ta = document.getElementById(task + '-override');
  ta.style.display = 'none';
  ta.dataset.modified = '';
  document.getElementById(task + '-actions').style.display = 'none';
  document.getElementById(task + '-pill').textContent = 'default';
  document.getElementById(task + '-pill').style.background = '#f3f0ed';
  document.getElementById(task + '-pill').style.color = 'var(--subtle)';
  document.querySelector(`[onclick="togglePromptOverride('${task}')"]`).textContent = 'Edit';
  updateChangeCount();
}

function updateChangeCount() {
  let n = 0;
  const showA = document.getElementById('fmt-show-a').value;
  const showB = document.getElementById('fmt-show-b').value;
  if (showA !== showB) n++;
  if (document.getElementById('outline-override').dataset.modified) n++;
  if (document.getElementById('transcript-override').dataset.modified) n++;
  const el = document.getElementById('b-change-count');
  el.style.display = n > 0 ? '' : 'none';
  el.textContent = n + (n === 1 ? ' override' : ' overrides');
}

let _fmtJobA = null, _fmtJobB = null;
let _outlineDoneA = false, _outlineDoneB = false;

function _getOutlinePromptB() {
  const ta = document.getElementById('outline-override');
  return (ta.dataset.modified && ta.value.trim()) ? ta.value.trim() : null;
}
function _getTranscriptPromptB() {
  const ta = document.getElementById('transcript-override');
  return (ta.dataset.modified && ta.value.trim()) ? ta.value.trim() : null;
}
function _getBriefingB() {
  const ta = document.getElementById('briefing-b-override');
  return (ta && ta.dataset.modified && ta.value.trim()) ? ta.value.trim() : null;
}

async function toggleBriefingEdit() {
  const panel = document.getElementById('briefing-edit-panel');
  const btn   = document.getElementById('briefing-toggle-btn');
  const isOpen = panel.style.display !== 'none';
  if (isOpen) {
    panel.style.display = 'none';
    btn.textContent = 'Edit B briefing ↓';
  } else {
    panel.style.display = 'block';
    btn.textContent = 'Hide briefing ↑';
    const ta = document.getElementById('briefing-b-override');
    if (!ta.dataset.loaded && _fmtJobB) {
      ta.value = 'Loading…';
      try {
        const res  = await fetch('/api/jobs/' + _fmtJobB);
        const data = await res.json();
        ta.value = data.briefing ? JSON.stringify(JSON.parse(data.briefing), null, 2) : (data.briefing || '');
      } catch(e) {
        ta.value = '';
      }
      ta.dataset.loaded = '1';
    }
  }
}

async function runPromptCompare() {
  const url = document.getElementById('fmt-url').value.trim();
  if (!url) { alert('Enter a URL'); return; }

  const showA      = document.getElementById('fmt-show-a').value;
  const showB      = document.getElementById('fmt-show-b').value;
  const changeNote = document.getElementById('change-note-input').value.trim() || null;

  document.getElementById('fmt-btn').disabled = true;
  document.getElementById('phase2-bar').style.display = 'none';
  document.getElementById('fmt-cols').style.display = 'grid';
  document.getElementById('fmt-steps-a').innerHTML = '';
  document.getElementById('fmt-steps-b').innerHTML = '';
  document.getElementById('fmt-result-a').innerHTML = '';
  document.getElementById('fmt-result-b').innerHTML = '';
  document.getElementById('fmt-rerun-b').style.display = 'none';
  document.getElementById('fmt-rerun-b').innerHTML = '';
  _outlineDoneA = false; _outlineDoneB = false;
  if (fmtPollA) clearTimeout(fmtPollA);
  if (fmtPollB) clearTimeout(fmtPollB);

  // If production episode exists, show its outline in column A (outline phase only)
  if (_existingEp) {
    _outlineDoneA = true;
    loadExistingToSlot('a', 'outline');
    document.getElementById('fmt-steps-a').innerHTML =
      '<li class="step done"><span class="step-icon">✓</span><span>Outline from cached production episode</span></li>';
  }

  const res = await fetch('/api/compare/run-outline', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      url,
      show_name_a: showA,
      show_name_b: showB,
      outline_prompt_b: _getOutlinePromptB(),
      transcript_prompt_b: _getTranscriptPromptB(),
      change_note: changeNote,
    })
  });
  const d = await res.json();
  _fmtJobA = d.job_a; _fmtJobB = d.job_b;

  // Only poll A if we're actually running it (no cached episode)
  if (!_existingEp) pollFmtJob(_fmtJobA, 'a', 'outline');
  pollFmtJob(_fmtJobB, 'b', 'outline');

  // compare_run row is already saved — refresh sidebar immediately
  await refreshSidebar(url);
}

async function runTranscripts() {
  document.getElementById('transcript-btn').disabled = true;
  document.getElementById('fmt-steps-a').innerHTML = '';
  document.getElementById('fmt-steps-b').innerHTML = '';
  document.getElementById('fmt-result-a').innerHTML = '';
  document.getElementById('fmt-result-b').innerHTML = '';

  await fetch('/api/compare/run-transcript', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      job_a: _fmtJobA, job_b: _fmtJobB,
      transcript_prompt_b: _getTranscriptPromptB(),
      briefing_b: _getBriefingB(),
    })
  });

  if (_existingEp) {
    // Load cached full episode into A immediately — no pipeline needed
    loadExistingToSlot('a', 'transcript');
    document.getElementById('fmt-steps-a').innerHTML =
      '<li class="step done"><span class="step-icon">✓</span><span>Transcript from cached production episode</span></li>';
    document.getElementById('fmt-btn').disabled = false;
  } else {
    pollFmtJob(_fmtJobA, 'a', 'transcript');
  }
  pollFmtJob(_fmtJobB, 'b', 'transcript');
}

async function rerunStep(step) {
  const prompt = step === 'outline' ? _getOutlinePromptB() : _getTranscriptPromptB();
  document.getElementById('fmt-rerun-b').style.display = 'none';
  document.getElementById('fmt-steps-b').innerHTML = '';
  document.getElementById('fmt-result-b').innerHTML = '';
  await fetch('/api/compare/rerun-step', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({job_id: _fmtJobB, step, prompt})
  });
  if (step === 'outline') {
    document.getElementById('phase2-bar').style.display = 'none';
    _outlineDoneB = false;
    pollFmtJob(_fmtJobB, 'b', 'outline');
  } else {
    pollFmtJob(_fmtJobB, 'b', 'transcript');
  }
}

function pollFmtJob(jobId, side, phase) {
  setTimeout(async () => {
    const res = await fetch('/api/jobs/' + jobId);
    const data = await res.json();
    renderSteps(data.steps || [], 'fmt-steps-' + side);

    if (data.status === 'outline_done') {
      // Show outline content
      if (data.outline) renderOutlineInColumn(data.outline, side);
      if (side === 'a') _outlineDoneA = true;
      if (side === 'b') _outlineDoneB = true;
      if (_outlineDoneA && _outlineDoneB) {
        document.getElementById('fmt-btn').disabled = false;
        document.getElementById('phase2-bar').style.display = 'flex';
        // Show re-run outline button for B
        showRerunButton('outline');
      }
    } else if (data.status === 'done') {
      document.getElementById('fmt-btn').disabled = false;
      if (data.episode_id) loadEpisodeIntoColumn(data.episode_id, side, data);
      if (side === 'b') {
        showRerunButton('transcript');
        refreshSidebar();
      }
      if (side === 'b' && document.getElementById('transcript-btn'))
        document.getElementById('transcript-btn').disabled = false;
    } else if (data.status === 'error') {
      document.getElementById('fmt-btn').disabled = false;
      if (document.getElementById('transcript-btn'))
        document.getElementById('transcript-btn').disabled = false;
      const err = document.createElement('div');
      err.style.cssText = 'padding:14px 20px;color:var(--bad);font-size:13px';
      err.textContent = data.message || 'Error';
      document.getElementById('fmt-result-' + side).appendChild(err);
    } else {
      pollFmtJob(jobId, side, phase);
    }
  }, 2000);
}

function showRerunButton(step) {
  const bar = document.getElementById('fmt-rerun-b');
  // Remove existing button for this step if any
  const existing = bar.querySelector(`[data-step="${step}"]`);
  if (existing) existing.remove();
  const btn = document.createElement('button');
  btn.className = 'btn-secondary';
  btn.dataset.step = step;
  btn.style.cssText = 'padding:6px 14px;font-size:12px';
  btn.textContent = `↺ Re-run ${step} (B)`;
  btn.onclick = () => rerunStep(step);
  bar.appendChild(btn);
  bar.style.display = 'flex';
}

function renderOutlineInColumn(outline, side) {
  const container = document.getElementById('fmt-result-' + side);
  container.innerHTML = '';
  const div = document.createElement('div');
  div.innerHTML = `<div class="result-section">
    <div class="result-section-label">Outline</div>
    ${outline.title ? `<div class="result-title">${esc(outline.title)}</div>` : ''}
    ${outline.thread ? `<div class="result-thread">${esc(outline.thread)}</div>` : ''}
    ${(outline.segments||[]).map((s,i) => `
      <div style="margin-bottom:8px;font-size:13px">
        <span style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:var(--subtle)">Seg ${s.segment||i+1}</span>
        <span style="margin-left:8px;color:var(--text)">${esc(s.title||s.focus||s.purpose||'')}</span>
      </div>`).join('')}
  </div>`;
  container.appendChild(div);
}

async function loadEpisodeIntoColumn(episodeId, side, jobData) {
  const res = await fetch('/api/episodes/' + episodeId);
  const ep = await res.json();
  const container = document.getElementById('fmt-result-' + side);
  container.innerHTML = '';

  const outline = ep.outline || {};
  const lines = Array.isArray(ep.transcript) ? ep.transcript : [];

  const div = document.createElement('div');

  // Stats bar (timing + tokens)
  if (jobData) {
    const dur   = jobData.transcript_duration_s;
    const toks  = jobData.transcript_output_tokens;
    const cost  = toks ? (toks * 15 / 1_000_000).toFixed(4) : null;
    const parts = [];
    if (dur)  parts.push(`${dur}s`);
    if (toks) parts.push(`${toks.toLocaleString()} output tokens`);
    if (cost) parts.push(`~$${cost}`);
    if (parts.length) {
      div.innerHTML += `<div style="padding:8px 20px;font-size:11px;color:var(--muted);background:var(--bg);border-bottom:1px solid var(--border);display:flex;gap:12px">
        ${parts.map(p => `<span>${esc(p)}</span>`).join('<span style="color:var(--border-strong)">·</span>')}
      </div>`;
    }
  }

  if (outline.title) {
    div.innerHTML += `<div class="result-section">
      <div class="result-section-label">Episode</div>
      <div class="result-title">${esc(outline.title)}</div>
      ${outline.thread ? `<div class="result-thread">${esc(outline.thread)}</div>` : ''}
    </div>`;
  }

  if (lines.length) {
    const plainText = lines.map(l => l.text||'').join('\n\n');
    const transcriptHtml = lines.map(l =>
      `<div class="transcript-line">
        <div class="transcript-text">${esc(l.text||'')}</div>
      </div>`
    ).join('');
    const copyId = 'copy-' + side + '-' + episodeId.slice(0,8);
    div.innerHTML += `<div class="result-section">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:6px">
        <div class="result-section-label" style="margin-bottom:0">Transcript</div>
        <button id="${copyId}" onclick="copyTranscript('${copyId}', \`${plainText.replace(/`/g,'\\`').replace(/\$/g,'\\$')}\`)"
          style="padding:3px 10px;font-size:11px;font-weight:500;border:1px solid var(--border);border-radius:6px;background:var(--surface);color:var(--muted);cursor:pointer;transition:all .15s"
          onmouseover="this.style.borderColor='var(--accent)';this.style.color='var(--accent)'"
          onmouseout="this.style.borderColor='var(--border)';this.style.color='var(--muted)'">Copy</button>
      </div>
      <div class="transcript-block">${transcriptHtml}</div>
    </div>`;
  }
  container.appendChild(div);
}

function copyTranscript(btnId, text) {
  navigator.clipboard.writeText(text).then(() => {
    const btn = document.getElementById(btnId);
    if (!btn) return;
    btn.textContent = '✓ Copied';
    btn.style.color = 'var(--good)';
    btn.style.borderColor = 'var(--good)';
    setTimeout(() => {
      btn.textContent = 'Copy';
      btn.style.color = 'var(--muted)';
      btn.style.borderColor = 'var(--border)';
    }, 2000);
  });
}

function renderSteps(steps, elId) {
  const el = document.getElementById(elId);
  el.innerHTML = steps.map(s => {
    const iconMap = {done:'✓', running:'↻', error:'✕', pending:'·'};
    const icon = s.status === 'running'
      ? `<span class="spin-icon">↻</span>`
      : (iconMap[s.status] || '·');
    return `<li class="step ${s.status||'pending'}">
      <span class="step-icon">${icon}</span>
      <span>${esc(s.text)}</span>
    </li>`;
  }).join('');
}

// ── vs External ───────────────────────────────────────────────────────────────
let curiaEpisode = null;
let externalTranscript = '';
let externalLabel = '';
let userVerdict = '';
let judgeResult = null;

async function loadEpisodes() {
  const res = await fetch('/api/episodes');
  const episodes = await res.json();
  const sel = document.getElementById('episode-select');
  sel.innerHTML = '<option value="">— select an episode —</option>' +
    (episodes || []).map(ep =>
      `<option value="${esc(ep.id)}">${esc(ep.title || ep.show_name || ep.id)} · ${esc(new Date(ep.created_at).toLocaleDateString())}</option>`
    ).join('');
}

async function onEpisodeSelect() {
  const id = document.getElementById('episode-select').value;
  if (!id) return;
  setExtStatus('running', 'Loading episode…');
  const res = await fetch('/api/episodes/' + id);
  curiaEpisode = await res.json();
  setExtStatus('', '');

  const lines = Array.isArray(curiaEpisode.transcript) ? curiaEpisode.transcript : [];
  const bodyEl = document.getElementById('curia-transcript-body');
  bodyEl.innerHTML = lines.length
    ? lines.map(l => `<div class="transcript-line"><div class="transcript-text">${esc(l.text||'')}</div></div>`).join('')
    : `<div class="empty-hint">No transcript available.</div>`;

  const label = document.getElementById('episode-select').selectedOptions[0]?.text || 'Curia';
  document.getElementById('curia-col-label').textContent = 'Curia — ' + label.split(' · ')[0];
  _showExtCard('curia', true);
  checkShowJudge();
}

function onFileSelect(input) {
  const file = input.files[0];
  if (!file) return;
  document.getElementById('upload-filename').textContent = file.name;
  document.getElementById('transcribe-btn').disabled = false;
  document.getElementById('transcribe-btn').textContent = _isTextFile(file) ? 'Load text →' : 'Transcribe →';
  const zone = document.getElementById('upload-zone');
  zone.style.borderColor = 'var(--accent)';
  zone.style.background = 'var(--accent-light)';
  // Build file key for cache lookup: name + size + lastModified
  _extFileKey = `${file.name}:${file.size}:${file.lastModified}`;
}

function _isTextFile(file) {
  return file.type === 'text/plain' || /\.(txt|md)$/i.test(file.name);
}

async function transcribeAudio() {
  const fileInput = document.getElementById('mp3-file');
  if (!fileInput.files[0]) return;
  const file = fileInput.files[0];
  const label = document.getElementById('ext-label').value.trim() || file.name.replace(/\.[^.]+$/, '');
  externalLabel = label;
  document.getElementById('transcribe-btn').disabled = true;

  try {
    if (_isTextFile(file)) {
      // Text file — read client-side, no transcription needed
      setExtStatus('running', 'Reading text file…');
      externalTranscript = await file.text();
      setExtStatus('done', 'Text loaded');
    } else {
      setExtStatus('running', 'Transcribing… this may take a minute');
      const fd = new FormData();
      fd.append('file', file);
      const res = await fetch('/api/compare/transcribe', {method:'POST', body:fd});
      const data = await res.json();
      if (data.error) throw new Error(data.error);
      externalTranscript = data.transcript;
      setExtStatus('done', `Transcription complete (${data.provider || 'groq'})`);
    }

    document.getElementById('ext-transcript-body').innerHTML =
      `<div style="font-size:13px;line-height:1.9;white-space:pre-wrap;color:var(--text)">${esc(externalTranscript)}</div>`;
    document.getElementById('ext-col-label').textContent = 'External — ' + label;
    _showExtCard('external', true);
    checkShowJudge();
  } catch(e) {
    setExtStatus('error', e.message);
    document.getElementById('transcribe-btn').disabled = false;
  }
}

function _showExtCard(side, show) {
  const cardId = side === 'curia' ? 'ext-curia-card' : 'ext-external-card';
  document.getElementById(cardId).style.display = show ? '' : 'none';
  document.getElementById('ext-transcripts').style.display = '';
  // Switch to two-column grid when both are loaded
  const both = curiaEpisode && externalTranscript;
  document.getElementById('ext-transcript-cols').style.gridTemplateColumns = both ? '1fr 1fr' : '1fr';
}

function checkShowJudge() {
  const hasEp = !!curiaEpisode;
  const hasExt = !!externalTranscript;
  // Judge requires both; feedback requires both; narrative analyze is per-transcript (handled by buttons)
  document.getElementById('judge-card').style.display = (hasEp && hasExt) ? '' : 'none';
  document.getElementById('feedback-card').style.display = (hasEp && hasExt) ? '' : 'none';
}

// ── Narrative structure analysis ──────────────────────────────────────────────
// _narrativeResults: { 'curia:structured': {label, result}, 'curia:open': ..., 'external:structured': ..., ... }
const _narrativeResults = {};
let _extFileKey = '';  // set when a file is selected
const _selectedMode = {curia: null, external: null};

const NARRATIVE_PROMPTS = {
  structured: `Analyze the narrative structure using this taxonomy:

Hook: the opening move that earns the next 30 seconds — provocative claim, unexpected fact, concrete scene, or unresolved question.
Stakes: why this matters, who is affected.
Mechanism: the how/why beneath the what.
Example/Anchor: a concrete instance that makes an abstract idea tangible.
Tension: two forces in conflict with no obvious resolution.
Counterpoint: the strongest version of the opposing view — steelmanned.
Turn: the moment the piece goes somewhere unexpected. Changes direction.
Reframe: showing the same thing from a different angle.
Payoff: the tension introduced earlier gets resolved or deepened.
Landing: how the piece ends — summary, reflection, open question, position, or resonant detail.

Hook / Tension / Turn / Landing are the four that define whether a piece has a shape or just has content. Missing elements are flagged in the output.`,

  open: `Read this transcript and find where the narrative intention shifts.

Do NOT use standard labels (Hook, Stakes, etc.) or podcast/essay terminology.
Name each section in your own words — describe what it's genuinely doing.
If you observe something that doesn't have a common name, invent a phrase that captures it precisely.
Let the structure tell you what it is; don't fit it to a template.

For each section: position in transcript, your own label, what it does to the listener, anchor quote.
Overall: what kind of journey does the listener go on?`,

  evaluate: `8-question structural audit:

1. Segment mapping — what is each segment doing narratively, approximate position, how does it transition?
2. Opening — exact first sentence, type (claim/question/scene/fact/contrast), how long before you know what the episode is about?
3. Closing — how does it end, type (summarize/reflect/question/position/trail), energy (up/down/flat)?
4. World knowledge — does the host stay inside the source or bring in outside connections? When does outside knowledge enter?
5. Fact vs opinion — does the episode distinguish between factual claims and editorial opinion? How?
6. Pacing — roughly how many distinct ideas per minute? Does it re-hook mid-episode?
7. Format contract — one sentence: what was this episode's implicit promise to the listener?
8. Limitations — what listener intent would this structure fail to serve?

Model: Claude Sonnet (deeper analysis than Structured/Open modes)`
};

function selectAnalysisMode(side, mode) {
  _selectedMode[side] = mode;
  // Update button styles
  ['structured','open','evaluate'].forEach(m => {
    const btn = document.getElementById(`mode-${side}-${m}`);
    if (btn) btn.className = 'analysis-mode-btn' + (m === mode ? ` selected-${mode}` : '');
  });
  // Show run + view-prompt buttons
  const runBtn = document.getElementById(`run-${side}-btn`);
  if (runBtn) { runBtn.style.display = ''; runBtn.textContent = mode === 'evaluate' ? 'Run evaluation →' : 'Run analysis →'; }
  const vpBtn = document.getElementById(`view-prompt-${side}`);
  if (vpBtn) vpBtn.style.display = '';
  // Update prompt preview content (in case panel is already open)
  const panel = document.getElementById(`prompt-view-${side}`);
  if (panel && panel.style.display !== 'none') {
    panel.querySelector('pre').textContent = NARRATIVE_PROMPTS[mode] || '';
  }
}

function togglePromptView(side) {
  const panel = document.getElementById(`prompt-view-${side}`);
  const btn   = document.getElementById(`view-prompt-${side}`);
  if (!panel || !btn) return;
  const visible = panel.style.display !== 'none';
  if (!visible) {
    const mode = _selectedMode[side] || 'structured';
    panel.querySelector('pre').textContent = NARRATIVE_PROMPTS[mode] || '';
  }
  panel.style.display = visible ? 'none' : '';
  btn.textContent = visible ? 'View prompt' : 'Hide prompt';
  btn.style.color = visible ? 'var(--muted)' : 'var(--accent)';
  btn.style.borderColor = visible ? 'var(--border)' : 'var(--accent)';
}

function _buildSourceKey(side) {
  if (side === 'curia' && curiaEpisode?.id) return `episode:${curiaEpisode.id}`;
  if (side === 'external' && _extFileKey) return `file:${_extFileKey}`;
  return '';
}

async function analyzeStructure(side, mode) {
  const runBtn = document.getElementById(`run-${side}-btn`);
  if (runBtn) { runBtn.disabled = true; runBtn.textContent = '↻ Running…'; }

  let text = '';
  let label = '';
  if (side === 'curia' && curiaEpisode) {
    const lines = Array.isArray(curiaEpisode.transcript) ? curiaEpisode.transcript : [];
    text = lines.map(l => l.text || '').join('\n\n');
    label = document.getElementById('curia-col-label').textContent;
  } else if (side === 'external') {
    text = externalTranscript;
    label = externalLabel || 'External';
  }

  if (!text.trim()) {
    if (runBtn) { runBtn.disabled = false; runBtn.textContent = mode === 'evaluate' ? 'Run evaluation →' : 'Run analysis →'; }
    return;
  }

  const source_key = _buildSourceKey(side);

  try {
    const res = await fetch('/api/compare/analyze-structure', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({transcript: text, label, mode, source_key})
    });
    const result = await res.json();
    _narrativeResults[`${side}:${mode}`] = {side, label, mode, result};
    renderNarrativePanels();
    if (runBtn) runBtn.title = result.cached ? 'Loaded from cache' : 'Fresh analysis';
  } catch(e) { /* silent */ }
  if (runBtn) { runBtn.disabled = false; runBtn.textContent = mode === 'evaluate' ? 'Run evaluation →' : 'Run analysis →'; }
}

function _buildNarrativeCard(entry) {
  const {side, label, mode, result} = entry;
  if (result.error) return `<div class="card" style="padding:16px;color:var(--bad);font-size:13px">Error: ${esc(result.error)}</div>`;

  const colorVar = side === 'curia' ? 'var(--blue)' : 'var(--purple)';
  const modeColors = {structured: colorVar, open: 'var(--subtle)', evaluate: 'var(--accent)'};
  const modeBgs   = {structured: `${colorVar}15`, open: '#f3f0ed', evaluate: 'var(--accent-light)'};
  const modeLabel = `<span style="font-size:9px;font-weight:600;letter-spacing:.06em;text-transform:uppercase;padding:2px 7px;border-radius:10px;background:${modeBgs[mode]||modeBgs.structured};color:${modeColors[mode]||colorVar}">${mode}</span>`;

  // Evaluate mode gets a different layout
  if (mode === 'evaluate') {
    const sections = [
      {key: 'opening', label: 'Opening'},
      {key: 'closing', label: 'Closing'},
      {key: 'world_knowledge', label: 'World knowledge vs source'},
      {key: 'fact_opinion', label: 'Fact vs opinion'},
      {key: 'pacing', label: 'Pacing'},
      {key: 'format_contract', label: 'Format contract'},
      {key: 'limitations', label: 'Limitations'},
    ];
    const cacheLabel2 = result.cached ? `<span style="font-size:9px;padding:2px 7px;border-radius:10px;background:var(--good-bg);color:var(--good)">cached</span>` : '';

    // Segment mapping table
    const segs = (result.segment_mapping || []).map((s,i) => `
      <div style="padding:9px 0;border-bottom:1px solid var(--border)">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:2px">
          <span style="font-size:10px;font-weight:700;padding:1px 7px;border-radius:10px;background:var(--accent-light);color:var(--accent)">Seg ${s.segment||i+1}</span>
          <span style="font-size:10px;color:var(--subtle)">${esc(s.position||'')}</span>
        </div>
        <div style="font-size:12px;font-weight:500;color:var(--text);margin-bottom:2px">${esc(s.role||'')}</div>
        <div style="font-size:11px;color:var(--muted)">↳ ${esc(s.transition||'')}</div>
      </div>`).join('');

    const prose = sections.map(({key, label: lbl}) => {
      let val = result[key];
      if (typeof val === 'object' && val !== null) {
        val = Object.entries(val).map(([k,v]) => `<span style="font-weight:600">${esc(k)}:</span> ${esc(String(v))}`).join(' &nbsp;·&nbsp; ');
      } else {
        val = esc(String(val || ''));
      }
      return `<div style="padding:11px 0;border-bottom:1px solid var(--border)">
        <div style="font-size:10px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--subtle);margin-bottom:4px">${esc(lbl)}</div>
        <div style="font-size:13px;color:var(--text);line-height:1.6">${val}</div>
      </div>`;
    }).join('');

    return `<div class="card" style="margin-bottom:12px">
      <div style="padding:12px 18px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px">
        <span style="font-size:13px;font-weight:700;color:${colorVar}">${esc(label)}</span>
        ${modeLabel} ${cacheLabel2}
      </div>
      ${segs ? `<div style="padding:0 18px;border-bottom:1px solid var(--border)">
        <div style="font-size:10px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--subtle);padding:10px 0 4px">Segments</div>
        ${segs}
      </div>` : ''}
      <div style="padding:0 18px">${prose}</div>
    </div>`;
  }
  const cacheLabel = result.cached
    ? `<span style="font-size:9px;padding:2px 7px;border-radius:10px;background:var(--good-bg);color:var(--good)">cached</span>`
    : '';

  const segs = (result.segments || []).map(s => `
    <div style="padding:11px 0;border-bottom:1px solid var(--border)">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:3px">
        <span style="font-size:10px;font-weight:700;padding:1px 8px;border-radius:10px;background:${colorVar}18;color:${colorVar}">${esc(s.role)}</span>
        <span style="font-size:10px;color:var(--subtle)">${esc(s.position||'')}</span>
      </div>
      <div style="font-size:13px;color:var(--text);margin-bottom:3px">${esc(s.function||'')}</div>
      ${s.anchor_quote ? `<div style="font-size:11px;color:var(--muted);font-style:italic;padding-left:10px;border-left:2px solid var(--border)">"${esc(s.anchor_quote)}"</div>` : ''}
    </div>`).join('');

  return `<div class="card" style="margin-bottom:12px">
    <div style="padding:12px 18px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px">
      <span style="font-size:13px;font-weight:700;color:${colorVar}">${esc(label)}</span>
      ${modeLabel}
      ${cacheLabel}
      <span style="font-size:12px;font-weight:600;color:var(--text)">${esc(result.arc_pattern||'')}</span>
    </div>
    <div style="padding:10px 18px;background:var(--bg);border-bottom:1px solid var(--border);font-size:12px;color:var(--muted);line-height:1.6">
      ${esc(result.arc_description||'')}
      ${(result.missing_elements?.length) ? `<div style="margin-top:6px;font-size:11px;color:var(--bad)">Missing or weak: ${result.missing_elements.map(e => esc(e)).join(', ')}</div>` : ''}
    </div>
    <div style="padding:0 18px">${segs}</div>
  </div>`;
}

function renderNarrativePanels() {
  const allKeys = Object.keys(_narrativeResults);
  if (!allKeys.length) return;
  document.getElementById('narrative-panels').style.display = '';

  // Group by side: curia keys vs external keys
  const curiaKeys = allKeys.filter(k => k.startsWith('curia:'));
  const extKeys   = allKeys.filter(k => k.startsWith('external:'));
  const hasBoth   = curiaKeys.length && extKeys.length;

  const container = document.getElementById('narrative-cols');
  container.style.gridTemplateColumns = hasBoth ? '1fr 1fr' : '1fr';

  const renderSide = keys => keys.map(k => _buildNarrativeCard(_narrativeResults[k])).join('');

  if (hasBoth) {
    container.innerHTML = `<div>${renderSide(curiaKeys)}</div><div>${renderSide(extKeys)}</div>`;
  } else {
    container.innerHTML = renderSide([...curiaKeys, ...extKeys]);
  }
}

function toggleJudgePrompt() {
  const panel = document.getElementById('judge-prompt-panel');
  const btn = event.currentTarget;
  const visible = panel.style.display !== 'none';
  panel.style.display = visible ? 'none' : '';
  btn.textContent = visible ? 'View prompt' : 'Hide prompt';
}

async function runJudge() {
  if (!curiaEpisode || !externalTranscript) {
    alert('Load both a Curia episode and an external transcript first.');
    return;
  }
  document.getElementById('judge-btn').disabled = true;
  document.getElementById('judge-body').innerHTML = `<div class="empty-hint"><span class="spin-icon" style="font-size:20px">↻</span><br><br>Running judge…</div>`;

  const lines = Array.isArray(curiaEpisode.transcript) ? curiaEpisode.transcript : [];
  const curiaText = lines.map(l => l.text || '').join('\n\n');
  const sourceText = curiaEpisode.source?.full_text || '';
  const insights = curiaEpisode.source?.insights || {};
  const epLabel = document.getElementById('episode-select').selectedOptions[0]?.text?.split(' · ')[0] || 'Curia';

  try {
    const res = await fetch('/api/compare/judge', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({
        episode_id: curiaEpisode.id,
        transcript_a: curiaText,
        label_a: 'Curia (' + epLabel + ')',
        transcript_b: externalTranscript,
        label_b: externalLabel || 'External',
        source_text: sourceText,
        source_insights: insights,
      })
    });
    judgeResult = await res.json();
    renderJudge(judgeResult, epLabel, externalLabel || 'External');
  } catch(e) {
    document.getElementById('judge-body').innerHTML = `<div style="color:var(--bad);padding:12px 0;font-size:13px">${esc(e.message)}</div>`;
  }
  document.getElementById('judge-btn').disabled = false;
}

function renderJudge(result, labelA, labelB) {
  if (result.error) {
    document.getElementById('judge-body').innerHTML = `<div style="color:var(--bad);padding:12px 0;font-size:13px">Judge error: ${esc(result.error)}</div>`;
    return;
  }

  const axes = result.axes || [];
  let html = `<div class="axes-header">
    <span>Axis</span>
    <span style="color:var(--blue)">${esc(labelA)}</span>
    <span style="color:#7c3aed">${esc(labelB)}</span>
    <span>Rationale</span>
  </div>`;

  axes.forEach(ax => {
    const pipsA = Array.from({length:5}, (_,i) =>
      `<div class="score-pip ${i < ax.score_a ? 'filled-a' : ''}"></div>`).join('');
    const pipsB = Array.from({length:5}, (_,i) =>
      `<div class="score-pip ${i < ax.score_b ? 'filled-b' : ''}"></div>`).join('');
    html += `<div class="axis-row">
      <div class="axis-name">${esc(ax.label)}</div>
      <div class="score-cell">
        <div class="score-pip-row">${pipsA}</div>
        <div class="score-num col-a">${ax.score_a}/5</div>
      </div>
      <div class="score-cell">
        <div class="score-pip-row">${pipsB}</div>
        <div class="score-num col-b">${ax.score_b}/5</div>
      </div>
      <div class="axis-rationale">${esc(ax.rationale)}</div>
    </div>`;
  });

  if (result.winner && result.summary) {
    const winnerLabel = result.winner === 'A' ? labelA : result.winner === 'B' ? labelB : 'Tie';
    html += `<div class="judge-winner">
      <div class="judge-winner-label">Winner: ${esc(winnerLabel)}</div>
      <div class="judge-winner-text">${esc(result.summary)}</div>
    </div>`;
  }

  document.getElementById('judge-body').innerHTML = html;
}

function setVerdict(v) {
  userVerdict = v;
  document.getElementById('vb-a').className = 'verdict-btn' + (v==='curia' ? ' sel-a' : '');
  document.getElementById('vb-b').className = 'verdict-btn' + (v==='external' ? ' sel-b' : '');
  document.getElementById('vb-tie').className = 'verdict-btn' + (v==='tie' ? ' sel-tie' : '');
}

async function submitFeedback() {
  if (!userVerdict) { alert('Select a verdict first.'); return; }
  const note = document.getElementById('feedback-note').value;
  const lines = Array.isArray(curiaEpisode?.transcript) ? curiaEpisode.transcript : [];
  const curiaText = lines.map(l => l.text || '').join('\n\n');
  await fetch('/api/compare/feedback', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      episode_id:          curiaEpisode?.id || '',
      external_label:      externalLabel,
      curia_transcript:    curiaText,
      external_transcript: externalTranscript,
      judge_scores:        judgeResult,
      user_verdict:        userVerdict,
      user_note:           note,
    })
  });
  const conf = document.getElementById('saved-confirm');
  conf.classList.add('show');
  setTimeout(() => conf.classList.remove('show'), 2500);
}

function setExtStatus(type, msg) {
  const el = document.getElementById('ext-status');
  el.className = 'status-bar' + (type ? ' ' + type : '');
  el.innerHTML = type === 'running'
    ? `<span class="spin-icon" style="font-size:16px">↻</span> ${esc(msg)}`
    : esc(msg);
}

// Upload drag-over
const zone = document.getElementById('upload-zone');
zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
zone.addEventListener('drop', e => {
  e.preventDefault();
  zone.classList.remove('drag-over');
  const f = e.dataTransfer.files[0];
  if (f) {
    document.getElementById('mp3-file').files = e.dataTransfer.files;
    onFileSelect({files: e.dataTransfer.files});
  }
});

// ── History ───────────────────────────────────────────────────────────────────
let _historyLoaded = false;
async function loadHistory() {
  if (_historyLoaded) return;
  _historyLoaded = true;
  const res = await fetch('/api/compare/runs');
  const runs = await res.json();
  const el = document.getElementById('history-body');
  if (!runs.length) {
    el.innerHTML = '<div class="empty-hint"><div class="empty-icon">📋</div>No runs yet.</div>';
    return;
  }
  const rows = runs.map(r => {
    const ts = new Date(r.ts).toLocaleString('en-US', {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
    const overrides = [];
    if (r.has_outline_override) overrides.push('outline');
    if (r.has_transcript_override) overrides.push('transcript');
    const badge = overrides.length
      ? `<span style="font-size:10px;padding:1px 7px;border-radius:10px;background:var(--accent-light);color:var(--accent)">${overrides.join(', ')} overridden</span>`
      : `<span style="font-size:10px;padding:1px 7px;border-radius:10px;background:#f3f0ed;color:var(--subtle)">defaults</span>`;
    const epLinks = [r.episode_id_a, r.episode_id_b].filter(Boolean).map((id,i) =>
      `<a href="/eval" style="font-size:11px;color:var(--accent);text-decoration:none">${['A','B'][i]} episode ↗</a>`
    ).join('  ');
    return `<div style="padding:14px 0;border-bottom:1px solid var(--border);display:flex;align-items:start;gap:16px">
      <div style="font-size:11px;color:var(--subtle);white-space:nowrap;padding-top:2px">${esc(ts)}</div>
      <div style="flex:1;min-width:0">
        <div style="font-size:13px;font-weight:500;color:var(--text);margin-bottom:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(r.url)}</div>
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <span style="font-size:11px;color:var(--muted)">${esc(r.show_name_a||'')} vs ${esc(r.show_name_b||'')}</span>
          ${badge}
          ${epLinks}
        </div>
      </div>
    </div>`;
  }).join('');
  el.innerHTML = `<div style="border-top:1px solid var(--border)">${rows}</div>`;
}

function switchExtSource(tab) {
  document.getElementById('ext-source-file').style.display    = tab === 'file'    ? '' : 'none';
  document.getElementById('ext-source-youtube').style.display = tab === 'youtube' ? '' : 'none';
  document.getElementById('src-tab-file').className    = 'analysis-mode-btn' + (tab === 'file'    ? ' selected-structured' : '');
  document.getElementById('src-tab-youtube').className = 'analysis-mode-btn' + (tab === 'youtube' ? ' selected-evaluate'   : '');
}

function onYtUrlInput() {
  const url = document.getElementById('yt-url').value.trim();
  const valid = /(?:v=|youtu\.be\/|shorts\/)([a-zA-Z0-9_-]{11})/.test(url);
  document.getElementById('yt-btn').disabled = !valid;
}

async function fetchYoutubeTranscript() {
  const url   = document.getElementById('yt-url').value.trim();
  const label = document.getElementById('yt-label').value.trim() || 'YouTube';
  externalLabel = label;

  document.getElementById('yt-btn').disabled = true;
  setExtStatus('running', 'Fetching transcript… may take 1–3 min for uncached videos');

  try {
    const res = await fetch('/api/compare/youtube-transcript', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({url})
    });
    const data = await res.json();
    if (data.error) throw new Error(data.error);

    externalTranscript = data.transcript;
    const src = data.source === 'cache' ? ' (cached)' : '';
    setExtStatus('done', `Transcript loaded${src} · ${data.title || ''}`);
    document.getElementById('ext-transcript-body').innerHTML =
      `<div style="font-size:13px;line-height:1.9;white-space:pre-wrap;color:var(--text)">${esc(externalTranscript)}</div>`;
    document.getElementById('ext-col-label').textContent = 'External — ' + label;
    // Set file key for cache: use video_id
    _extFileKey = `yt:${data.video_id || url}`;
    _showExtCard('external', true);
    checkShowJudge();
  } catch(e) {
    setExtStatus('error', e.message);
  }
  document.getElementById('yt-btn').disabled = false;
}

function toggleTranscript(side) {
  const bodyId = side === 'curia' ? 'curia-transcript-body' : 'ext-transcript-body';
  const btnId  = `toggle-${side}-transcript`;
  const body   = document.getElementById(bodyId);
  const btn    = document.getElementById(btnId);
  if (!body || !btn) return;
  const hidden = body.style.display === 'none';
  body.style.display = hidden ? '' : 'none';
  btn.textContent = hidden ? 'Hide transcript' : 'Show transcript';
  btn.style.color = hidden ? 'var(--muted)' : 'var(--accent)';
  btn.style.borderColor = hidden ? 'var(--border)' : 'var(--accent)';
}

// Boot
loadEpisodes();
</script>
</body>
</html>"""


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8001"))
    host = os.getenv("HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port, log_level="warning")
