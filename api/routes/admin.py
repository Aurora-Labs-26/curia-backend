"""
api/routes/admin.py
QA-only endpoints. All gated by `qa_required` — regular users hit a 403.

Capabilities:
  - List all users
  - View any user's profile / KB / rubric (for any task) / sources / episodes / jobs
  - This is *read-only inspection* for QA testing and debugging. Mutations
    (re-run jobs, edit examples, manage trainsets, run GEPA) live in their
    own modules and routes when they ship.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import Response

from api.auth import CurrentUser, qa_required
from api.schemas import (
    AdminUserSummary,
    EpisodeSummary,
    ExampleCreate,
    ExampleImportFromEpisodeRequest,
    ExampleResponse,
    GuidelineResponse,
    GuidelineUpdate,
    OptimizationRunCreate,
    OptimizationRunResponse,
    RubricResponse,
    SourceSummary,
)
from core.db.connection import db_execute, db_fetchrow, db_query
from core.kb import UserKB, load_kb
from core.queue import enqueue
from optimization import examples as examples_store
from optimization import runs as runs_store
from optimization.guidelines import (
    get_guidelines,
    get_guidelines_async,
    invalidate_cache as invalidate_guidelines_cache,
    list_tasks as list_guideline_tasks,
)
from optimization.rubrics.generator import generate_judge_prompt_async

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


@router.get("/users", response_model=list[AdminUserSummary])
async def list_users(
    qa: CurrentUser = Depends(qa_required),
    limit: int = Query(default=100, le=1000),
) -> list[AdminUserSummary]:
    rows = await db_query(
        """
        SELECT
            id, email, name, role, created_at,
            (user_kb IS NOT NULL AND user_kb::text NOT IN ('{}', 'null')) AS has_kb
        FROM users
        ORDER BY created_at DESC
        LIMIT $limit
        """,
        {"limit": limit},
    )
    return [AdminUserSummary(**r) for r in rows]


@router.get("/users/{target_user_id}", response_model=AdminUserSummary)
async def get_user(
    target_user_id: str,
    qa: CurrentUser = Depends(qa_required),
) -> AdminUserSummary:
    row = await db_fetchrow(
        """
        SELECT
            id, email, name, role, created_at,
            (user_kb IS NOT NULL AND user_kb::text NOT IN ('{}', 'null')) AS has_kb
        FROM users WHERE id = $id
        """,
        {"id": target_user_id},
    )
    if not row:
        raise HTTPException(404, "user not found")
    return AdminUserSummary(**row)


# ---------------------------------------------------------------------------
# KB + rubric (per user)
# ---------------------------------------------------------------------------


@router.get("/users/{target_user_id}/kb", response_model=UserKB)
async def get_user_kb(
    target_user_id: str,
    qa: CurrentUser = Depends(qa_required),
) -> UserKB:
    return await load_kb(target_user_id)


@router.get(
    "/users/{target_user_id}/rubric/{task}",
    response_model=RubricResponse,
)
async def get_user_rubric(
    target_user_id: str,
    task: str = Path(..., pattern="^(transcript|outline)$"),
    qa: CurrentUser = Depends(qa_required),
) -> RubricResponse:
    """
    Render the judge prompt for (target_user_id, task) — what the LLM judge sees
    when scoring this user's outputs. Most useful debugging tool.
    """
    try:
        get_guidelines(task)
    except KeyError as e:
        raise HTTPException(404, str(e))

    kb = await load_kb(target_user_id)
    prompt = await generate_judge_prompt_async(
        task=task, user_kb=kb, output="<output goes here>"
    )
    return RubricResponse(task=task, user_id=target_user_id, judge_prompt=prompt)


# ---------------------------------------------------------------------------
# Per-user data inspection (read-only)
# ---------------------------------------------------------------------------


@router.get("/users/{target_user_id}/sources", response_model=list[SourceSummary])
async def get_user_sources(
    target_user_id: str,
    qa: CurrentUser = Depends(qa_required),
    limit: int = Query(default=100, le=500),
) -> list[SourceSummary]:
    rows = await db_query(
        """
        SELECT id, title, url, status, created_at, error
        FROM source
        WHERE user_id = $user_id
        ORDER BY created_at DESC LIMIT $limit
        """,
        {"user_id": target_user_id, "limit": limit},
    )
    return [SourceSummary(**r) for r in rows]


@router.get("/users/{target_user_id}/episodes", response_model=list[EpisodeSummary])
async def get_user_episodes(
    target_user_id: str,
    qa: CurrentUser = Depends(qa_required),
    limit: int = Query(default=100, le=500),
) -> list[EpisodeSummary]:
    rows = await db_query(
        """
        SELECT id, show_name, title, status, created_at, error, quality_score
        FROM episode
        WHERE user_id = $user_id
        ORDER BY created_at DESC LIMIT $limit
        """,
        {"user_id": target_user_id, "limit": limit},
    )
    return [EpisodeSummary(**r) for r in rows]


# ---------------------------------------------------------------------------
# Jobs (operational view across all users)
# ---------------------------------------------------------------------------


@router.get("/jobs")
async def list_jobs(
    qa: CurrentUser = Depends(qa_required),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, le=500),
) -> list[dict]:
    sql = """
        SELECT id, type, status, attempts, max_attempts,
               user_id, created_at, updated_at, locked_at, locked_by, last_error
        FROM jobs
        {where}
        ORDER BY created_at DESC LIMIT $limit
    """
    if status:
        rows = await db_query(
            sql.format(where="WHERE status = $status"),
            {"status": status, "limit": limit},
        )
    else:
        rows = await db_query(sql.format(where=""), {"limit": limit})
    return [dict(r) for r in rows]


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: uuid.UUID,
    qa: CurrentUser = Depends(qa_required),
) -> dict:
    row = await db_fetchrow(
        "SELECT * FROM jobs WHERE id = $id::uuid",
        {"id": str(job_id)},
    )
    if not row:
        raise HTTPException(404, "job not found")
    return dict(row)


# ===========================================================================
# Guidelines (QA-editable quality floor per task)
# ===========================================================================


@router.get("/guidelines")
async def list_guidelines(
    qa: CurrentUser = Depends(qa_required),
) -> list[GuidelineResponse]:
    """List all known tasks + their current guideline body and version."""
    out: list[GuidelineResponse] = []
    for task in list_guideline_tasks():
        row = await db_fetchrow(
            "SELECT body, version FROM optimization_guidelines WHERE task = $task",
            {"task": task},
        )
        body = await get_guidelines_async(task)   # picks up Python fallback if seed marker
        version = int(row["version"]) if row else 0
        out.append(GuidelineResponse(task=task, body=body, version=version))
    return out


@router.get("/guidelines/{task}", response_model=GuidelineResponse)
async def get_guideline(
    task: str,
    qa: CurrentUser = Depends(qa_required),
) -> GuidelineResponse:
    if task not in list_guideline_tasks():
        raise HTTPException(404, f"unknown task '{task}'")
    body = await get_guidelines_async(task)
    row = await db_fetchrow(
        "SELECT version FROM optimization_guidelines WHERE task = $task",
        {"task": task},
    )
    version = int(row["version"]) if row else 0
    return GuidelineResponse(task=task, body=body, version=version)


@router.put("/guidelines/{task}", response_model=GuidelineResponse)
async def put_guideline(
    task: str,
    payload: GuidelineUpdate,
    qa: CurrentUser = Depends(qa_required),
) -> GuidelineResponse:
    """Replace the guideline body for `task`. Bumps version, invalidates cache."""
    if task not in list_guideline_tasks():
        raise HTTPException(404, f"unknown task '{task}'")
    await db_execute(
        """
        INSERT INTO optimization_guidelines (task, body, version, updated_by, updated_at)
        VALUES ($task, $body, 1, $by, now())
        ON CONFLICT (task) DO UPDATE
            SET body = EXCLUDED.body,
                version = optimization_guidelines.version + 1,
                updated_by = EXCLUDED.updated_by,
                updated_at = now()
        """,
        {"task": task, "body": payload.body, "by": qa.id},
    )
    invalidate_guidelines_cache(task)
    row = await db_fetchrow(
        "SELECT version FROM optimization_guidelines WHERE task = $task",
        {"task": task},
    )
    return GuidelineResponse(task=task, body=payload.body, version=int(row["version"]))


# ===========================================================================
# Examples (trainset rows) — CRUD
# ===========================================================================


@router.get("/examples", response_model=list[ExampleResponse])
async def list_examples_admin(
    qa: CurrentUser = Depends(qa_required),
    task: str | None = Query(default=None),
    scope_type: str = Query(default="global"),
    scope_value: str | None = Query(default=None),
    limit: int = Query(default=100, le=500),
) -> list[ExampleResponse]:
    rows = await examples_store.list_examples(
        task=task, scope_type=scope_type, scope_value=scope_value, limit=limit
    )
    return [ExampleResponse(**r) for r in rows]


@router.post("/examples", response_model=ExampleResponse, status_code=201)
async def create_example_admin(
    payload: ExampleCreate,
    qa: CurrentUser = Depends(qa_required),
) -> ExampleResponse:
    try:
        row = await examples_store.create_example(
            task=payload.task,
            inputs=payload.inputs,
            scope_type=payload.scope_type,
            scope_value=payload.scope_value,
            label=payload.label,
            metadata=payload.metadata,
            created_by=qa.id,
        )
    except examples_store.InvalidExampleInputs as e:
        raise HTTPException(422, str(e))
    return ExampleResponse(**row)


@router.get("/examples/{example_id}", response_model=ExampleResponse)
async def get_example_admin(
    example_id: uuid.UUID,
    qa: CurrentUser = Depends(qa_required),
) -> ExampleResponse:
    try:
        row = await examples_store.get_example(str(example_id))
    except examples_store.ExampleNotFoundError:
        raise HTTPException(404, "example not found")
    return ExampleResponse(**row)


@router.delete("/examples/{example_id}", status_code=204, response_class=Response)
async def delete_example_admin(
    example_id: uuid.UUID,
    qa: CurrentUser = Depends(qa_required),
) -> Response:
    await examples_store.delete_example(str(example_id))
    return Response(status_code=204)


@router.post(
    "/examples/from-episode",
    response_model=ExampleResponse,
    status_code=201,
)
async def import_example_from_episode(
    payload: ExampleImportFromEpisodeRequest,
    qa: CurrentUser = Depends(qa_required),
) -> ExampleResponse:
    """Convenience: pull a real episode's inputs into the trainset."""
    try:
        row = await examples_store.import_from_episode(
            episode_id=str(payload.episode_id),
            task=payload.task,
            scope_type=payload.scope_type,
            scope_value=payload.scope_value,
            created_by=qa.id,
        )
    except examples_store.ExampleNotFoundError as e:
        raise HTTPException(404, str(e))
    except examples_store.InvalidExampleInputs as e:
        raise HTTPException(422, str(e))
    return ExampleResponse(**row)


# ===========================================================================
# Optimization runs — kick off + inspect + promote
# ===========================================================================


@router.post(
    "/optimization/runs",
    response_model=OptimizationRunResponse,
    status_code=202,
)
async def create_optimization_run(
    payload: OptimizationRunCreate,
    qa: CurrentUser = Depends(qa_required),
) -> OptimizationRunResponse:
    """Insert a queued run row + enqueue an 'optimize' job. Worker handles execution."""
    run = await runs_store.create_run(
        task=payload.task,
        scope_type=payload.scope_type,
        scope_value=payload.scope_value,
        config=payload.config,
        created_by=qa.id,
    )
    await enqueue(
        type="optimize",
        payload={"run_id": str(run["id"])},
        user_id=qa.id,
    )
    return OptimizationRunResponse(**run)


@router.get("/optimization/runs", response_model=list[OptimizationRunResponse])
async def list_optimization_runs(
    qa: CurrentUser = Depends(qa_required),
    task: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, le=500),
) -> list[OptimizationRunResponse]:
    rows = await runs_store.list_runs(task=task, status=status, limit=limit)
    return [OptimizationRunResponse(**r) for r in rows]


@router.get(
    "/optimization/runs/{run_id}",
    response_model=OptimizationRunResponse,
)
async def get_optimization_run(
    run_id: uuid.UUID,
    qa: CurrentUser = Depends(qa_required),
) -> OptimizationRunResponse:
    try:
        row = await runs_store.get_run(str(run_id))
    except runs_store.RunNotFoundError:
        raise HTTPException(404, "run not found")
    return OptimizationRunResponse(**row)


@router.post(
    "/optimization/runs/{run_id}/promote",
    response_model=OptimizationRunResponse,
)
async def promote_optimization_run(
    run_id: uuid.UUID,
    qa: CurrentUser = Depends(qa_required),
) -> OptimizationRunResponse:
    """Promote this run's artifact as the active prompt for its (task, scope)."""
    try:
        row = await runs_store.promote_run(str(run_id))
    except runs_store.RunNotFoundError:
        raise HTTPException(404, "run not found")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return OptimizationRunResponse(**row)
