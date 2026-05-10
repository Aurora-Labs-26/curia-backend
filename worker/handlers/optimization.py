"""
worker/handlers/optimization.py
Worker handler for the 'optimize' job type.

Payload: { "run_id": "<uuid>" }
The optimization_runs row already exists with status='queued' (created by
POST /admin/optimization/runs). Worker calls execute_run() which fills in
all the lifecycle + result fields.
"""

from loguru import logger

from optimization.runner import execute_run


async def handle_optimize(payload: dict) -> None:
    run_id = payload.get("run_id")
    if not run_id:
        raise ValueError("optimize job: missing run_id in payload")
    logger.info(f"[handle_optimize] starting run_id={run_id}")
    await execute_run(run_id=run_id)
