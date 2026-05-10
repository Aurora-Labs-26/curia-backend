"""
optimization/runner
===================
Executes a GEPA run against a (task, scope) using the rubric judge as metric.

Public API:
    execute_run(run_id) -> dict          Async — does the actual work; updates run row
                                         in-place. Returns the final run dict.

Called by worker/handlers/optimization.py when an 'optimize' job is dispatched.
"""

from .gepa_runner import execute_run

__all__ = ["execute_run"]
