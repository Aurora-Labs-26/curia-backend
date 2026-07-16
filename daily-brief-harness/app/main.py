import os

# Must be set before numpy/torch/scikit-learn are imported anywhere in the
# process (including transitively, e.g. via app.services.pipeline ->
# scoring_service). In a CPU-quota-limited container (e.g. Railway), these
# libraries default to spawning one thread per visible CPU core, but the
# container may only be entitled to a small fraction of a core — the threads
# then fight each other for that sliver of CPU time instead of computing,
# turning a millisecond-scale embedding call into 20+ seconds. Pinning to a
# single thread avoids that oversubscription entirely; these workloads are
# small enough that multithreading has nothing to gain here anyway.
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_NUM_THREADS", "1")

import base64
import logging
import secrets
from datetime import date
from typing import Optional, List
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import Response

from app.config import settings
from app.services.llm_service import llm_service
from app.services import harness_db, cache_service, preopt_runner, user_brief_runner, scoring_service, enrichment_service, eval_gold_service, eval_judges_service, settings_service

# Configure elegant logger format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("app.main")

app = FastAPI(
    title="Curia Daily Brief — Pipeline & Test Harness Backend",
    description="Python & FastAPI backend for the two-phase Pre-Opt + Per-User Brief Cache Daily Brief pipeline.",
    version="1.0.0"
)

# Enable CORS for cross-origin local research/testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def _shared_basic_auth(request: Request, call_next):
    """Optional shared HTTP Basic Auth gate for temporary public test deployments
    (see app/config.py's BASIC_AUTH_USER/PASS). No-op when unset, so local dev
    and docker-compose usage are unaffected."""
    if not settings.BASIC_AUTH_USER or request.method == "OPTIONS":
        return await call_next(request)

    valid = False
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Basic "):
        try:
            user, _, pw = base64.b64decode(auth_header[6:]).decode("utf-8").partition(":")
            valid = secrets.compare_digest(user, settings.BASIC_AUTH_USER) and secrets.compare_digest(
                pw, settings.BASIC_AUTH_PASS
            )
        except Exception:
            valid = False

    if not valid:
        return Response(status_code=401, headers={"WWW-Authenticate": "Basic"})

    return await call_next(request)


@app.on_event("startup")
async def _startup_harness_db_pool() -> None:
    """Eagerly creates the harness cache-schema pool (see app/services/harness_db.py)
    so misconfiguration (bad DATABASE_URL) fails fast at boot rather than on
    the first /api/preopt/run or /api/users/{id}/generate-brief call."""
    await harness_db.get_pool()


@app.on_event("startup")
async def _startup_warm_scoring_model() -> None:
    """Eagerly loads the ranking embedding model at boot (see
    scoring_service.warm_model) instead of on the first Pre-Opt/Generate
    Brief request — that first-load cost shouldn't eat into a user-facing
    request's gateway-timeout budget."""
    await scoring_service.warm_model()


@app.on_event("startup")
async def _startup_warm_html_parser() -> None:
    """Eagerly forces trafilatura/lxml's one-time global libxml2 init at
    boot, single-threaded — see enrichment_service._warm_html_parser for why:
    that init is not itself thread-safe, and racing it across concurrent
    enrichment fetch threads segfaults the whole process."""
    await enrichment_service.warm_html_parser()


@app.on_event("shutdown")
async def _shutdown_harness_db_pool() -> None:
    await harness_db.close_pool()

# ---------------------------------------------------------
# API ROUTING ENDPOINTS
# ---------------------------------------------------------

@app.get("/api/status")
async def get_status(x_anthropic_api_key: Optional[str] = Header(None)):
    """Returns the operational status of services and API keys (checks dynamic key header too)."""
    is_available = llm_service.is_api_available(x_anthropic_api_key)
    return {
        "status": "healthy",
        "anthropic_key_configured": is_available,
        "anthropic_model": "claude-haiku-4-5-20251001",
        "build": "constitution-v1"
    }

# ---------------------------------------------------------
# PRE-OPT + PER-USER BRIEF CACHE ENDPOINTS (harness-triggerable)
# ---------------------------------------------------------
# Two manually-triggered actions backing the two-phase brief caching design
# (see app/services/preopt_runner.py, app/services/user_brief_runner.py,
# app/services/cache_service.py, db/001_schema.sql). Not a cron/scheduler —
# both are plain HTTP-triggered actions. This is the entire Daily Brief
# pipeline.

@app.post("/api/preopt/run")
async def run_preopt(x_anthropic_api_key: Optional[str] = Header(None)):
    """Runs Pre-Opt across all 7 system topics: fetch -> rank (2b) -> score &
    curate (3) -> full-text enrich (3b) -> per-article segment transcript (6,
    cache-checked) -> cache. See preopt_runner.run_preopt."""
    logger.info("[preopt] run triggered")
    try:
        result = await preopt_runner.run_preopt(api_key=x_anthropic_api_key)
        return result
    except Exception as e:
        logger.error(f"Error running pre-opt: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/preopt/run/{topic_id}")
async def run_preopt_for_topic(topic_id: str, x_anthropic_api_key: Optional[str] = Header(None)):
    """Runs Pre-Opt for a single topic (same pipeline as /api/preopt/run, just
    scoped to one topic). The harness UI uses this instead of the bulk
    endpoint — one topic's full chain (including real article-text
    enrichment) comfortably finishes inside a hosted reverse-proxy's gateway
    timeout, whereas all 7 topics in one request does not. See
    preopt_runner.run_preopt_for_topic_id."""
    logger.info(f"[preopt] run triggered for topic_id={topic_id}")
    try:
        return await preopt_runner.run_preopt_for_topic_id(topic_id, api_key=x_anthropic_api_key)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error running pre-opt for topic_id={topic_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class GenerateBriefRequest(BaseModel):
    date: Optional[str] = None  # YYYY-MM-DD, defaults to today


@app.post("/api/users/{user_id}/generate-brief")
async def generate_brief(
    user_id: str,
    req: Optional[GenerateBriefRequest] = None,
    x_anthropic_api_key: Optional[str] = Header(None),
):
    """Generates one user's daily brief: builds their article pool (reusing
    Pre-Opt's cached candidates for chosen system topics), ranks it, resolves
    the winning 5 articles against the segment cache, generates only what's
    missing, then a fresh intro/outro, and assembles the ordered
    manifest. If a brief already exists for this user/date, the 5-article
    selection is reused as-is but intro/outro are still regenerated
    fresh. See user_brief_runner.generate_brief_for_user."""
    brief_date = (req.date if req else None) or date.today().isoformat()
    logger.info(f"[user_brief] generate-brief triggered for user_id={user_id} date={brief_date}")
    try:
        result = await user_brief_runner.generate_brief_for_user(user_id, brief_date, api_key=x_anthropic_api_key)
        return result
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error generating brief for user_id={user_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/users/{user_id}/reset-brief")
async def reset_user_brief(user_id: str, req: Optional[GenerateBriefRequest] = None):
    """Deletes this one user's daily_briefs row for one date (defaults to
    today, same default as generate-brief), so their next Generate Brief
    click takes the full fetch/rank/curate/segment path again instead of
    the same-day regenerate-bookends shortcut. Scoped to this user/date
    only — every other user's brief, the shared Pre-Opt article_segment_cache,
    and any frozen Gold Set data are untouched. See
    cache_service.reset_user_brief for why this is safe."""
    brief_date = (req.date if req else None) or date.today().isoformat()
    logger.info(f"[user_brief] reset-brief triggered for user_id={user_id} date={brief_date}")
    try:
        return await cache_service.reset_user_brief(user_id, brief_date)
    except Exception as e:
        logger.error(f"Error resetting brief for user_id={user_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/harness/users")
async def list_harness_users():
    """Seeded simulated users + their chosen/custom topics, for the harness's
    user picker (see scripts/seed_users.py)."""
    try:
        users = await cache_service.list_users()
        for u in users:
            u["id"] = str(u["id"])
            topics = await cache_service.get_user_topics(u["id"])
            u["chosen_topics"] = [t["name"] for t in topics["chosen"]]
            u["custom_topics"] = [t["name"] for t in topics["custom"]]
        return {"users": users}
    except Exception as e:
        logger.error(f"Error listing harness users: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class CreateHarnessUserRequest(BaseModel):
    email: str
    location_name: str
    scheduled_time: str = "07:00"
    chosen_topic_names: List[str] = []
    custom_topic_names: List[str] = []


@app.post("/api/harness/users")
async def create_harness_user(req: CreateHarnessUserRequest):
    """Creates (or upserts-by-email) a user + links their chosen/custom
    topics — the harness UI's "Add User" modal. Returns the same enriched
    shape GET /api/harness/users builds per-user, so the frontend can select
    the new user immediately without a re-fetch."""
    try:
        user_id = await cache_service.create_user_with_topics(
            req.email, req.location_name, req.scheduled_time,
            req.chosen_topic_names, req.custom_topic_names,
        )
        user = await cache_service.get_user(user_id)
        topics = await cache_service.get_user_topics(user_id)
        user["id"] = str(user["id"])
        user["chosen_topics"] = [t["name"] for t in topics["chosen"]]
        user["custom_topics"] = [t["name"] for t in topics["custom"]]
        logger.info(f"[harness] user created: {user['email']} ({user['id']})")
        return user
    except Exception as e:
        logger.error(f"Error creating harness user: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/harness/topics")
async def list_harness_topics():
    """The 7 seeded system topics (Beats), for display/debug."""
    try:
        topics = await cache_service.list_system_topics()
        for t in topics:
            t["id"] = str(t["id"])
        return {"topics": topics}
    except Exception as e:
        logger.error(f"Error listing harness topics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/harness/clear-cache")
async def clear_harness_cache():
    """Wipes cached segment transcripts/audio + all brief history (users,
    topics, and articles are left intact) so Pre-Opt/Generate Brief can be
    re-tested from a clean slate without touching the database by hand."""
    try:
        result = await cache_service.clear_cache()
        logger.info(f"[harness] cache cleared: {result}")
        return result
    except Exception as e:
        logger.error(f"Error clearing harness cache: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/harness/article-cache")
async def get_harness_article_cache():
    """Every cached article segment (harness.article_segment_cache joined
    with articles/topics), newest first — backs the "View Article Cache"
    inspector modal in the Multi-User Cache tab."""
    try:
        items = await cache_service.list_article_cache()
        return {"items": items, "count": len(items)}
    except Exception as e:
        logger.error(f"Error listing harness article cache: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/harness/clear-users")
async def clear_harness_users():
    """Deletes ALL users (+ user_topics + brief history). topics/articles/
    article_segment_cache are untouched (topic-scoped, not user-scoped).
    See cache_service.clear_users."""
    try:
        result = await cache_service.clear_users()
        logger.info(f"[harness] users cleared: {result}")
        return result
    except Exception as e:
        logger.error(f"Error clearing harness users: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/harness/transcripts")
async def list_harness_transcripts():
    """Every archived end-to-end transcript (one row per completed Generate
    Brief call — see user_brief_runner.save_transcript_record), newest first.
    Backs the harness UI's Open Coding tab: card list + note editor + full
    transcript view are all populated from this single call."""
    try:
        items = await cache_service.list_transcript_records()
        for item in items:
            item["id"] = str(item["id"])
            item["user_id"] = str(item["user_id"]) if item["user_id"] else None
        return {"items": items, "count": len(items)}
    except Exception as e:
        logger.error(f"Error listing harness transcripts: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class SaveTranscriptNoteRequest(BaseModel):
    note: str
    title: Optional[str] = None


@app.post("/api/harness/transcripts/{transcript_id}/note")
async def save_harness_transcript_note(transcript_id: str, req: SaveTranscriptNoteRequest):
    """Saves (overwrites) the open-coding note for one archived transcript,
    and optionally its title (Open Coding tab's editable title field)."""
    try:
        found = await cache_service.save_transcript_note(transcript_id, req.note, title=req.title)
        if not found:
            raise HTTPException(status_code=404, detail=f"Unknown transcript_id: {transcript_id}")
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error saving transcript note for transcript_id={transcript_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/harness/transcripts/{transcript_id}/note")
async def delete_harness_transcript_note(transcript_id: str):
    """Clears the open-coding note for one archived transcript. The
    transcript record itself (and its full segment text) is never deleted
    this way — only the annotation."""
    try:
        found = await cache_service.delete_transcript_note(transcript_id)
        if not found:
            raise HTTPException(status_code=404, detail=f"Unknown transcript_id: {transcript_id}")
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting transcript note for transcript_id={transcript_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/harness/briefs/{brief_id}")
async def get_harness_brief(brief_id: str):
    """Idempotent re-view of an already-generated brief without re-running it."""
    try:
        detail = await cache_service.get_daily_brief_detail(brief_id)
        if not detail:
            raise HTTPException(status_code=404, detail="Brief not found.")
        return detail
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching brief {brief_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------
# M2 GOLD-LABELED TEST SET (harness.eval_gold_* — see
# db/005_eval_gold_schema.sql and app/services/eval_gold_service.py)
# ---------------------------------------------------------

@app.get("/api/eval/runs/recent")
async def list_recent_eval_runs(kind: Optional[str] = None, limit: int = 30):
    """Recent eval_runs (any kind, or filtered), for picking which one to
    freeze as gold after reviewing its output."""
    try:
        rows = await eval_gold_service.list_recent_runs(kind=kind, limit=limit)
        for r in rows:
            for k in ("id", "topic_id", "user_id", "brief_id"):
                if r.get(k) is not None:
                    r[k] = str(r[k])
        return {"items": rows}
    except Exception as e:
        logger.error(f"Error listing recent eval runs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/eval/gold/runs")
async def list_gold_runs():
    try:
        rows = await eval_gold_service.list_gold_runs()
        for r in rows:
            r["run_id"] = str(r["run_id"])
        return {"items": rows}
    except Exception as e:
        logger.error(f"Error listing gold runs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class FreezeRunRequest(BaseModel):
    profile_label: str


@app.post("/api/eval/gold/runs/{run_id}/freeze")
async def freeze_gold_run(run_id: str, req: FreezeRunRequest):
    try:
        result = await eval_gold_service.freeze_run(run_id, req.profile_label)
        result["run_id"] = str(result["run_id"])
        return result
    except Exception as e:
        logger.error(f"Error freezing eval run {run_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/eval/gold/runs/{run_id}/unfreeze")
async def unfreeze_gold_run(run_id: str):
    try:
        await eval_gold_service.unfreeze_run(run_id)
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error unfreezing eval run {run_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/eval/gold/runs/{run_id}")
async def delete_gold_run(run_id: str):
    """Removes the eval_gold_runs pin only — see eval_gold_service.delete_gold_run
    for why this doesn't touch the underlying eval_runs data."""
    try:
        await eval_gold_service.delete_gold_run(run_id)
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error deleting gold run pin {run_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/eval/gold/runs/{run_id}")
async def get_gold_labeling_view(run_id: str):
    try:
        view = await eval_gold_service.get_labeling_view(run_id)
        if not view:
            raise HTTPException(status_code=404, detail=f"No frozen gold run for run_id: {run_id}")
        return view
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching gold labeling view for run_id={run_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class SaveGoldLabelRequest(BaseModel):
    url: str
    label: str  # "hit" | "miss"
    notes: Optional[str] = None


@app.post("/api/eval/gold/runs/{run_id}/labels")
async def save_gold_label(run_id: str, req: SaveGoldLabelRequest):
    try:
        await eval_gold_service.save_label(run_id, req.url, req.label, notes=req.notes)
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error saving gold label for run_id={run_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class SaveGoldScriptRequest(BaseModel):
    segment_type: str  # "lead" | "standard" | "local" | "intro" | "outro"
    article_url: Optional[str] = None
    script_text: str


@app.post("/api/eval/gold/runs/{run_id}/scripts")
async def save_gold_script(run_id: str, req: SaveGoldScriptRequest):
    try:
        await eval_gold_service.save_script(run_id, req.segment_type, req.article_url, req.script_text)
        return {"status": "success"}
    except Exception as e:
        logger.error(f"Error saving gold script for run_id={run_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class SaveTopPickLabelRequest(BaseModel):
    is_local: bool
    correct: Optional[bool] = None  # None = unmarked/agree (see db/009_order_correctness_labels.sql)


@app.post("/api/eval/gold/runs/{run_id}/top-pick-label")
async def save_top_pick_label(run_id: str, req: SaveTopPickLabelRequest):
    try:
        result = await eval_gold_service.save_top_pick_label(run_id, req.is_local, req.correct)
        result["run_id"] = str(result["run_id"])
        return result
    except Exception as e:
        logger.error(f"Error saving top-pick label for run_id={run_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class SaveOrderRankingLabelRequest(BaseModel):
    is_local: bool
    correct: Optional[bool] = None  # None = unmarked/agree (see db/012_order_ranking_label.sql)


@app.post("/api/eval/gold/runs/{run_id}/order-ranking-label")
async def save_order_ranking_label(run_id: str, req: SaveOrderRankingLabelRequest):
    try:
        result = await eval_gold_service.save_order_ranking_label(run_id, req.is_local, req.correct)
        result["run_id"] = str(result["run_id"])
        return result
    except Exception as e:
        logger.error(f"Error saving order-ranking label for run_id={run_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------
# M3 LLM JUDGES (harness.eval_judge_outputs — see
# db/007_eval_judge_schema.sql and app/services/eval_judges_service.py)
# ---------------------------------------------------------

@app.post("/api/eval/judges/run/{run_id}")
async def run_judges(run_id: str):
    """Runs every M3 judge against one frozen gold run and writes results."""
    try:
        return await eval_judges_service.run_judges_for_gold_run(run_id)
    except Exception as e:
        logger.error(f"Error running judges for run_id={run_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/eval/judges/results/{run_id}")
async def get_judge_results(run_id: str):
    try:
        return {"items": await eval_judges_service.list_judge_outputs(run_id)}
    except Exception as e:
        logger.error(f"Error fetching judge results for run_id={run_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/eval/judges/validation-report")
async def get_judge_validation_report():
    """Cohen's kappa vs M2 gold data — relevance + order-correctness +
    order-ranking only; the other judges have no dedicated gold labels (see
    eval_judges_service.py's module docstring)."""
    try:
        relevance = await eval_judges_service.compute_relevance_kappa()
        order_correctness = await eval_judges_service.compute_order_correctness_agreement()
        order_ranking = await eval_judges_service.compute_order_ranking_agreement()
        return {"relevance": relevance, "order_correctness": order_correctness, "order_ranking": order_ranking}
    except Exception as e:
        logger.error(f"Error computing judge validation report: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------
# AUTOMATED JUDGING — background monitoring, no gold/label required. See
# automated_judging_build_plan.md (Milestone 6). These endpoints back the
# new "Judge Output" tab; the routes above this section are the pre-existing
# manual Gold Set calibration workflow and are untouched.
# ---------------------------------------------------------


@app.get("/api/eval/settings/automated-judging")
async def get_automated_judging_setting():
    try:
        return {"enabled": await settings_service.is_automated_judging_enabled()}
    except Exception as e:
        logger.error(f"Error reading automated-judging setting: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class AutomatedJudgingSettingRequest(BaseModel):
    enabled: bool


@app.post("/api/eval/settings/automated-judging")
async def set_automated_judging_setting(req: AutomatedJudgingSettingRequest):
    """The one on/off switch for the whole automated-judging feature (see
    automated_judging.py's module docstring) — when disabled, the trigger in
    user_brief_runner.py doesn't fire at all, not just "runs and discards
    output"."""
    try:
        result = await settings_service.set_automated_judging_enabled(req.enabled)
        logger.info(f"[automated_judging] toggle set to {req.enabled}")
        return result
    except Exception as e:
        logger.error(f"Error updating automated-judging setting: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/eval/settings/faithfulness-regen")
async def get_faithfulness_regen_setting():
    try:
        return {"enabled": await settings_service.is_faithfulness_regen_enabled()}
    except Exception as e:
        logger.error(f"Error reading faithfulness-regen setting: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class FaithfulnessRegenSettingRequest(BaseModel):
    enabled: bool


@app.post("/api/eval/settings/faithfulness-regen")
async def set_faithfulness_regen_setting(req: FaithfulnessRegenSettingRequest):
    """Governs only the regenerate-on-flag retry loop (see
    faithfulness_regen_service.py) — when disabled, a flagged article segment
    is still judged synchronously and logged, it just isn't rewritten."""
    try:
        result = await settings_service.set_faithfulness_regen_enabled(req.enabled)
        logger.info(f"[faithfulness_regen] toggle set to {req.enabled}")
        return result
    except Exception as e:
        logger.error(f"Error updating faithfulness-regen setting: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/eval/judges/automated/recent")
async def list_automated_judge_runs(limit: int = 50):
    """Runs judged automatically (never frozen as gold) — not gated behind
    eval_gold_runs, unlike /api/eval/runs/recent's kind filter usage
    elsewhere. Powers the Judge Output tab's run list."""
    try:
        return {"items": await eval_judges_service.list_recent_automated_runs(limit)}
    except Exception as e:
        logger.error(f"Error listing automated judge runs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/eval/judges/automated/rollup")
async def get_automated_judging_rollup(days: int = 30):
    """Counts by judge_name/verdict/severity — the actual point of the
    Judge Output tab per judging_pipeline_scope_analysis.md Part 4C (a
    per-run spot-check table alone can't answer "is this trending")."""
    try:
        return await eval_judges_service.compute_automated_judging_rollup(days)
    except Exception as e:
        logger.error(f"Error computing automated judging rollup: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------
# STATIC FILES SERVING (UI)
# ---------------------------------------------------------

# Mount directory static to serve css, js and assets at root /
app.mount("/", StaticFiles(directory="static", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting server on {settings.HOST}:{settings.PORT}")
    uvicorn.run("app.main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
