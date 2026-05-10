"""
optimization/runs/store.py
DB CRUD for optimization_runs.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from core.db.connection import db_execute, db_fetchrow, db_query


class RunNotFoundError(Exception):
    pass


async def create_run(
    task: str,
    scope_type: str = "global",
    scope_value: str | None = None,
    config: dict | None = None,
    created_by: str | None = None,
) -> dict:
    if scope_type not in ("global", "cohort", "user"):
        raise ValueError(f"scope_type must be global/cohort/user, got {scope_type!r}")
    run_id = str(uuid.uuid4())
    await db_execute(
        """
        INSERT INTO optimization_runs
            (id, task, scope_type, scope_value, config, created_by)
        VALUES
            ($id::uuid, $task, $scope_type, $scope_value, $config::jsonb, $created_by)
        """,
        {
            "id": run_id,
            "task": task,
            "scope_type": scope_type,
            "scope_value": scope_value,
            "config": json.dumps(config or {}),
            "created_by": created_by,
        },
    )
    return await get_run(run_id)


async def get_run(run_id: str) -> dict:
    row = await db_fetchrow(
        "SELECT * FROM optimization_runs WHERE id = $id::uuid",
        {"id": run_id},
    )
    if not row:
        raise RunNotFoundError(run_id)
    return dict(row)


async def list_runs(
    task: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[dict]:
    where_parts = []
    params: dict[str, Any] = {"limit": limit}
    if task:
        where_parts.append("task = $task")
        params["task"] = task
    if status:
        where_parts.append("status = $status")
        params["status"] = status
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    rows = await db_query(
        f"""
        SELECT id, task, scope_type, scope_value, status, metric_name,
               trainset_size, valset_size, metric_score_baseline,
               metric_score_optimized, artifact_path, promoted, error,
               started_at, completed_at, duration_seconds, created_by, created_at
        FROM optimization_runs
        {where}
        ORDER BY created_at DESC
        LIMIT $limit
        """,
        params,
    )
    return [dict(r) for r in rows]


async def set_status(run_id: str, status: str, **fields) -> None:
    """
    Update run status + arbitrary fields atomically.
    Common fields: started_at, completed_at, duration_seconds, error,
                   trainset_size, valset_size, metric_score_baseline,
                   metric_score_optimized, artifact_path.
    """
    if status not in ("queued", "running", "completed", "failed", "cancelled"):
        raise ValueError(f"invalid status {status!r}")
    set_clauses = ["status = $status"]
    params: dict[str, Any] = {"id": run_id, "status": status}
    for k, v in fields.items():
        set_clauses.append(f"{k} = ${k}")
        params[k] = v
    sql = f"UPDATE optimization_runs SET {', '.join(set_clauses)} WHERE id = $id::uuid"
    await db_execute(sql, params)


async def promote_run(run_id: str) -> dict:
    """Mark this run's artifact as the active one for its (task, scope)."""
    run = await get_run(run_id)
    if run.get("status") != "completed":
        raise ValueError(f"can only promote completed runs (status={run.get('status')})")
    if not run.get("artifact_path"):
        raise ValueError("run has no artifact_path")
    # Demote any previously-promoted run for the same (task, scope)
    await db_execute(
        """
        UPDATE optimization_runs
        SET promoted = false
        WHERE task = $task
          AND scope_type = $scope_type
          AND scope_value IS NOT DISTINCT FROM $scope_value
          AND id != $id::uuid
        """,
        {
            "task": run["task"],
            "scope_type": run["scope_type"],
            "scope_value": run.get("scope_value"),
            "id": run_id,
        },
    )
    await db_execute(
        "UPDATE optimization_runs SET promoted = true WHERE id = $id::uuid",
        {"id": run_id},
    )
    return await get_run(run_id)
