"""
optimization/runs
=================
Tracks GEPA run lifecycle: queued → running → completed | failed.

Every run targets a (task, scope) combo and produces a compiled prompt artifact
saved to disk. The DB row has:
    - status, started_at, completed_at, duration
    - trainset_size, valset_size
    - metric_score_baseline, metric_score_optimized (lift)
    - artifact_path (where the JSON ended up)
    - promoted (false until QA explicitly promotes via promote endpoint)

Public API:
    create_run(task, scope_type, scope_value, config, created_by) -> run dict
    get_run(run_id) -> dict
    list_runs(task=None, status=None, limit=50) -> list[dict]
    set_status(run_id, status, **fields)
    promote_run(run_id) -> dict
"""

from .store import (
    RunNotFoundError,
    create_run,
    get_run,
    list_runs,
    promote_run,
    set_status,
)

__all__ = [
    "RunNotFoundError",
    "create_run",
    "get_run",
    "list_runs",
    "promote_run",
    "set_status",
]
