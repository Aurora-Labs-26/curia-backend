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

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
import uvicorn

app = FastAPI()

# In-memory job store (local dev tool, no persistence needed)
_jobs: dict[str, dict] = {}

_TABLE_READY = False

FEEDBACK_FIELDS = [
    "timestamp", "episode_id", "episode_title",
    "stage", "original", "edited", "verdict", "comment",
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
            comment    TEXT
        )
        """,
        {},
    )
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
        row = await db_fetchrow("SELECT COUNT(*) AS cnt FROM eval_feedback", {})
        return int(row["cnt"]) if row else 0
    except Exception:
        return 0


async def _run_pipeline(job_id: str, url: str, show_name: str):
    from core.db.connection import db_fetchrow, db_execute
    from core.ingest import process_source, get_or_create_source
    from studio.generator import process_episode

    steps: list[dict] = []

    def _step_start(text: str):
        steps.append({"text": text, "status": "running"})
        _jobs[job_id] = {"status": "running", "steps": list(steps)}

    def _step_done():
        if steps:
            steps[-1]["status"] = "done"
        _jobs[job_id] = {"status": "running", "steps": list(steps)}

    try:
        _step_start("Looking up account")
        user_row = await db_fetchrow("SELECT id FROM users LIMIT 1", {})
        if not user_row:
            steps[-1]["status"] = "error"
            _jobs[job_id] = {
                "status": "error",
                "message": "No users in DB. Run scripts/create_user.py first.",
                "steps": list(steps),
            }
            return
        user_id = str(user_row["id"])
        _step_done()

        _step_start("Registering source")
        source_id = await get_or_create_source(url=url, user_id=user_id)
        _step_done()

        _step_start("Scraping & ingesting")
        src = await db_fetchrow(
            "SELECT status FROM source WHERE id = $id::uuid", {"id": source_id}
        )
        if not src or src["status"] != "ready":
            await process_source(source_id=source_id)
        _step_done()

        _step_start("Generating episode")
        episode_id = str(uuid4())
        await db_execute(
            """INSERT INTO episode (id, user_id, show_name, status, source_ids)
               VALUES ($id::uuid, $user_id, $show, 'queued', ARRAY[$src_id::uuid])""",
            {"id": episode_id, "user_id": user_id, "show": show_name, "src_id": source_id},
        )
        await process_episode(episode_id=episode_id)
        _step_done()

        _jobs[job_id] = {"status": "done", "episode_id": episode_id, "message": "Done!", "steps": list(steps)}

    except Exception as e:
        if steps:
            steps[-1]["status"] = "error"
        _jobs[job_id] = {"status": "error", "message": str(e), "steps": list(steps)}


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
  #ep-title-text { font-size: 15px; font-weight: 600; color: #111; letter-spacing: -0.01em; }
  #ep-title-meta { font-size: 11px; color: #aaa; margin-top: 4px; }

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
    padding: 20px 22px;
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
  .eval-card-body { padding: 14px 16px; }

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

const STEPS       = ['source', 'outline', 'transcript', 'final'];
const STEP_LABELS = ['Source', 'Outline', 'Transcript', 'Final'];

let feedback = resetFeedback();

function resetFeedback() {
  return { fields: {}, finalNote: '' };
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
  const res = await fetch('/api/episodes');
  episodes  = await res.json();
  renderList();
}

async function loadStats() {
  const res  = await fetch('/api/eval/stats');
  const data = await res.json();
  document.getElementById('stats').textContent = data.total_rated + ' rated';
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
    div.innerHTML = `
      <div class="ep-title">${esc(ep.title || 'Untitled')}</div>
      <div class="ep-meta">
        <span class="ep-show">${esc(ep.show_name || '')}</span>
        <span>${esc(ts)}</span>
        <span>Q: ${score}</span>
      </div>`;
    list.appendChild(div);
  }
}

async function selectEpisode(id) {
  const res = await fetch('/api/episodes/' + id);
  episode   = await res.json();
  step      = 0;
  feedback  = resetFeedback();
  editMode  = false;

  // Highlight sidebar
  document.querySelectorAll('.ep-item').forEach(el => el.classList.remove('active'));
  const idx = episodes.findIndex(e => e.id === id);
  if (idx >= 0) document.querySelectorAll('.ep-item')[idx].classList.add('active');

  // Show eval area
  document.getElementById('empty-state').style.display = 'none';
  const ea = document.getElementById('eval-area');
  ea.style.display = 'flex';

  // Title bar
  document.getElementById('ep-title-text').textContent = episode.title || 'Untitled';
  const score = episode.quality_score != null ? (episode.quality_score * 100).toFixed(0) + '%' : '—';
  document.getElementById('ep-title-meta').textContent =
    (episode.show_name || '') + '  ·  Quality score: ' + score;

  renderStep();
}

// ── Step rendering ────────────────────────────────────────────────────────────

function renderStep() {
  updateStepper();

  const isFinal = (step === 3);

  document.getElementById('btn-prev').disabled = (step === 0);
  if (isFinal) {
    document.getElementById('btn-next').style.display   = 'none';
    document.getElementById('btn-submit').style.display = '';
  } else {
    document.getElementById('btn-next').style.display   = '';
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
  const src = episode.source;

  // Article info header (no verdict)
  const infoCard = document.createElement('div');
  infoCard.className = 'stage-card';
  infoCard.style.marginBottom = '16px';
  if (!src) {
    infoCard.innerHTML = '<div style="color:#ccc;font-size:12px">No source data. Episode predates source tracking.</div>';
    container.appendChild(infoCard);
    return;
  }
  infoCard.innerHTML = `
    <div class="section-label">Article</div>
    <div style="font-size:14px;font-weight:600;color:#111;margin-bottom:6px;line-height:1.4">${esc(src.title || 'Untitled')}</div>
    <a href="${esc(src.url||'')}" target="_blank" style="font-size:11px;color:#6b7280;word-break:break-all;text-decoration:none">${esc(src.url||'')}</a>
    ${src.full_text ? `<div style="margin-top:12px"><div class="section-label" style="margin-bottom:5px">Raw (preview)</div>
      <div style="font-size:11px;line-height:1.7;color:#999;white-space:pre-wrap;max-height:80px;overflow:hidden">${esc((src.full_text||'').slice(0,400))}${(src.full_text||'').length>400?'…':''}</div></div>` : ''}`;
  container.appendChild(infoCard);

  const insights = src.insights || {};
  for (const key of INSIGHT_ORDER) {
    const val = insights[key];
    if (!val) continue;
    const el = document.createElement('div');
    el.style.cssText = 'font-size:12px;line-height:1.8;color:#333;white-space:pre-wrap';
    el.textContent = val;
    container.appendChild(_makeCard('source.' + key, INSIGHT_LABELS[key] || key, el, val));
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

  // Overview card (title + thread)
  const overviewEl = document.createElement('div');
  let overviewText = '';
  if (outline.title) {
    const t = document.createElement('div');
    t.className = 'outline-title'; t.textContent = outline.title;
    overviewEl.appendChild(t); overviewText += outline.title;
  }
  if (outline.thread || outline.central_tension) {
    const th = document.createElement('div');
    th.className = 'outline-thread'; th.textContent = outline.thread || outline.central_tension;
    overviewEl.appendChild(th); overviewText += '\n\n' + (outline.thread || outline.central_tension);
  }
  if (overviewText) container.appendChild(_makeCard('outline.overview', 'Overview', overviewEl, overviewText));

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
}

// ── Actions ───────────────────────────────────────────────────────────────────

function _saveCurrentStep() {
  if (step === 3) {
    const el = document.getElementById('final-note');
    if (el) feedback.finalNote = el.value;
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

  const res  = await fetch('/api/eval/submit', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ episode_id: episode.id, episode_title: episode.title || '', stages }),
  });
  const data = await res.json();

  if (data.ok) {
    toast(`Saved — ${data.rows_saved} row${data.rows_saved !== 1 ? 's' : ''}`, false);
    feedback = resetFeedback();
    loadStats();
  } else {
    toast('Error: ' + (data.error || 'unknown'), true);
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

  const res  = await fetch('/api/ingest', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ url, show_name: show }),
  });
  const data = await res.json();
  if (data.error) {
    setJobStatus('error', '✗ ' + data.error);
    document.getElementById('btn-run').disabled = false;
    return;
  }
  _pollJob(data.job_id);
}

function _pollJob(jobId) {
  clearTimeout(_pollTimer);
  _pollTimer = setTimeout(async () => {
    const res  = await fetch('/api/jobs/' + jobId);
    const data = await res.json();

    if (data.steps && data.steps.length) renderSteps(data.steps, data.status === 'error' ? data.message : null);

    if (data.status === 'running' || data.status === 'pending') {
      if (!data.steps || !data.steps.length)
        setJobStatus('running', '<span class="job-spinner">↻</span> ' + esc(data.message || 'Running…'));
      _pollJob(jobId);
    } else if (data.status === 'done') {
      if (!data.steps || !data.steps.length) setJobStatus('done', '✓ Done!');
      document.getElementById('btn-run').disabled = false;
      document.getElementById('url-input').value = '';
      await loadEpisodes();
      if (data.episode_id) selectEpisode(data.episode_id);
    } else {
      if (!data.steps || !data.steps.length)
        setJobStatus('error', '✗ ' + esc(data.message || 'Error'));
      document.getElementById('btn-run').disabled = false;
    }
  }, 2000);
}

function renderSteps(steps, errMsg) {
  const el = document.getElementById('job-status');
  el.style.display = '';
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
  el.style.display = '';
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

        # Attach source data for step 0
        source_ids = data.get("source_ids") or []
        if source_ids:
            src_id = source_ids[0] if isinstance(source_ids[0], str) else str(source_ids[0])
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
                data["source"] = src_data

        return JSONResponse(content=data)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/api/eval/submit")
async def submit_feedback(request: Request):
    try:
        body          = await request.json()
        episode_id    = body.get("episode_id", "")
        episode_title = body.get("episode_title", "")
        stages        = body.get("stages", [])
        ts            = datetime.now(timezone.utc)

        rows = [
            {
                "timestamp":     ts,
                "episode_id":    episode_id,
                "episode_title": episode_title,
                "stage":         s.get("stage", ""),
                "original":      s.get("original", ""),
                "edited":        s.get("edited", ""),
                "verdict":       s.get("verdict", ""),
                "comment":       s.get("comment", ""),
            }
            for s in stages
        ]
        await _append_rows(rows)
        return JSONResponse(content={"ok": True, "rows_saved": len(rows)})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.get("/api/eval/stats")
async def eval_stats():
    return JSONResponse(content={"total_rated": await _count_rated()})


@app.get("/eval/export.csv")
async def export_csv():
    """Download all eval feedback as a CSV file."""
    try:
        await _ensure_table()
        from core.db.connection import db_query
        rows = await db_query(
            "SELECT timestamp, episode_id, episode_title, stage, original, edited, verdict, comment "
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
    if not url:
        return JSONResponse(content={"error": "url required"}, status_code=400)
    job_id = str(uuid4())
    _jobs[job_id] = {"status": "pending", "message": "Queued"}
    asyncio.create_task(_run_pipeline(job_id, url, show_name))
    return JSONResponse(content={"job_id": job_id})


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return JSONResponse(content={"error": "Not found"}, status_code=404)
    return JSONResponse(content=job)


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8001"))
    host = os.getenv("HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port, log_level="warning")
