"""
optimization/runner/gepa_runner.py
GEPA execution against a stored (task, scope) trainset, using the rubric judge as metric.

Heavy lifting:
  1. Load examples from optimization_examples
  2. Build a dspy.Module for the task (transcript / outline)
  3. Define a metric function that calls the rubric judge against (output, KB)
  4. Score baseline (un-optimized module) on the trainset → baseline_score
  5. Run GEPA (or skip if SKIP_REAL_GEPA env is set; useful for dry-runs)
  6. Score optimized module → optimized_score
  7. Save artifact JSON to disk + update the run row

Artifact layout:
    {CURIA_OPTIMIZATION_ARTIFACTS_DIR}/{task}/{scope_type}/{scope_value or 'global'}/{run_id}.json
default dir: ./prompts/optimized

Environment knobs:
    CURIA_OPTIMIZATION_ARTIFACTS_DIR    where to save compiled prompt JSONs
    CURIA_GEPA_DRY_RUN=true             load + score baseline only; skip the GEPA call
                                        (useful when you don't want to spend $$ on every run)
"""

from __future__ import annotations

import json
import os
import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import dspy
from loguru import logger

from core.kb import UserKB, load_kb
from core.llm_config import resolve
from core.prompts.loader import save_prompt
from core.prompts.outline import GenerateOutline
from core.prompts.transcript import GenerateTranscript
from optimization.examples import list_examples
from optimization.rubrics import judge as rubric_judge
from optimization.runs import RunNotFoundError, get_run, set_status

ARTIFACTS_DIR = Path(os.getenv("CURIA_OPTIMIZATION_ARTIFACTS_DIR", "prompts/optimized"))
DRY_RUN = os.getenv("CURIA_GEPA_DRY_RUN", "").lower() == "true"


# Mapping: task name → (DSPy Signature class, output field name on the prediction)
_TASK_BUILDERS: dict[str, tuple[type[dspy.Signature], str]] = {
    "transcript": (GenerateTranscript, "transcript_json"),
    "outline": (GenerateOutline, "outline_json"),
}


# ---------------------------------------------------------------------------
# Metric — rubric judge wrapped as a GEPA-compatible scorer
# ---------------------------------------------------------------------------


def _make_metric(task: str, scope_user_id: str | None):
    """
    Build a GEPA metric closure for `task`. If scope_user_id is given, the rubric is
    rendered against that user's KB (per-user scope). Otherwise it's guideline-only
    (global scope) — falls back to a guideline-only judge.

    GEPA expects: (example, prediction[, trace]) -> score | dspy.Prediction(score=, feedback=)
    Returning a Prediction with .score AND .feedback is what unlocks GEPA's reflective mode.
    """

    cached_kb: UserKB | None = None

    async def _resolve_kb() -> UserKB | None:
        nonlocal cached_kb
        if scope_user_id is None:
            return None
        if cached_kb is not None:
            return cached_kb
        try:
            cached_kb = await load_kb(scope_user_id)
        except Exception as e:
            logger.warning(f"[runner] could not load KB for user {scope_user_id}: {e}")
            cached_kb = None
        return cached_kb

    output_field = _TASK_BUILDERS[task][1]

    async def metric(example, prediction, trace=None):
        # Get the prediction's serialized output text
        output_text = getattr(prediction, output_field, None)
        if output_text is None:
            return dspy.Prediction(score=0.0, feedback="prediction missing expected output field")

        kb = await _resolve_kb()
        judgment = await rubric_judge(task=task, output=str(output_text), user_kb=kb)
        return dspy.Prediction(
            score=judgment.overall_score,
            feedback=judgment.feedback or "",
        )

    return metric


# ---------------------------------------------------------------------------
# Trainset assembly
# ---------------------------------------------------------------------------


async def _build_trainset(
    task: str,
    scope_type: str,
    scope_value: str | None,
) -> list[dspy.Example]:
    rows = await list_examples(
        task=task, scope_type=scope_type, scope_value=scope_value, limit=500
    )
    if not rows:
        return []
    sig_cls, _ = _TASK_BUILDERS[task]
    input_fields = [
        name
        for name, field in sig_cls.fields.items()
        if getattr(field, "json_schema_extra", {}).get("__dspy_field_type") == "input"
    ] if hasattr(sig_cls, "fields") else []

    # Fallback: derive input fields from our static map if introspection fails.
    if not input_fields:
        from optimization.examples import INPUTS_SCHEMA_BY_TASK
        input_fields = sorted(INPUTS_SCHEMA_BY_TASK[task])

    examples: list[dspy.Example] = []
    for row in rows:
        raw_inputs = row.get("inputs") or {}
        if isinstance(raw_inputs, str):
            raw_inputs = json.loads(raw_inputs)
        kwargs = {field: raw_inputs.get(field, "") for field in input_fields}
        # Carry side metadata for the metric to read if it cares (e.g., completion_pct)
        ex = dspy.Example(**kwargs).with_inputs(*input_fields)
        examples.append(ex)
    return examples


# ---------------------------------------------------------------------------
# Score helpers
# ---------------------------------------------------------------------------


async def _score_module(
    module: dspy.Module,
    trainset: list[dspy.Example],
    metric,
) -> float:
    if not trainset:
        return 0.0
    total = 0.0
    n = 0
    for ex in trainset:
        try:
            pred = module(**ex.inputs())
            result = await metric(ex, pred)
            total += float(getattr(result, "score", result))
            n += 1
        except Exception as e:
            logger.warning(f"[runner] scoring example failed: {e}")
    return total / n if n else 0.0


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------


async def execute_run(run_id: str) -> dict:
    """
    Execute the optimization run identified by run_id.

    The run row is updated in-place: status moves queued → running → completed|failed.
    Returns the final run dict.
    """
    run = await get_run(run_id)
    task = run["task"]
    scope_type = run["scope_type"]
    scope_value = run.get("scope_value")

    if task not in _TASK_BUILDERS:
        raise ValueError(f"unsupported task: {task}")

    started = datetime.now(timezone.utc)
    started_perf = time.perf_counter()
    await set_status(run_id, "running", started_at=started)

    try:
        # 1. Load trainset
        examples = await _build_trainset(task, scope_type, scope_value)
        if len(examples) < 2:
            raise ValueError(
                f"need at least 2 examples for optimization, found {len(examples)}. "
                f"Add examples via POST /admin/examples first."
            )

        # Hold-out split
        split = max(1, len(examples) // 5)   # 20% val
        trainset = examples[:-split]
        valset = examples[-split:]

        # 2. Build module + metric + reflection LM
        sig_cls, _ = _TASK_BUILDERS[task]
        student = dspy.Predict(sig_cls)
        scope_user_id = scope_value if scope_type == "user" else None
        metric = _make_metric(task=task, scope_user_id=scope_user_id)

        # 3. Set the LM the optimized module will use during scoring.
        with dspy.context(lm=resolve.llm(task)):
            baseline_score = await _score_module(student, valset, metric)

        # 4. Run GEPA (or skip in dry-run mode)
        artifact_path = (
            ARTIFACTS_DIR
            / task
            / scope_type
            / (scope_value or "global")
            / f"{run_id}.json"
        )
        artifact_path.parent.mkdir(parents=True, exist_ok=True)

        if DRY_RUN:
            logger.info(f"[runner] CURIA_GEPA_DRY_RUN=true — skipping real GEPA call")
            optimized_score = baseline_score
        else:
            try:
                from dspy.teleprompt import GEPA
            except ImportError as e:
                raise RuntimeError(
                    f"dspy.teleprompt.GEPA not available in this DSPy version: {e}"
                )

            reflection_lm = resolve.llm("gepa_reflection")
            optimizer = GEPA(
                metric=metric,
                reflection_lm=reflection_lm,
                auto="light",   # 'light' for dev sanity; bump to 'medium'/'heavy' for real runs
            )
            with dspy.context(lm=resolve.llm(task)):
                optimized = optimizer.compile(
                    student=student,
                    trainset=trainset,
                    valset=valset,
                )
                optimized_score = await _score_module(optimized, valset, metric)
                optimized.save(str(artifact_path))

            # Write the optimized prompt back to the .txt file so it takes
            # effect on the next cold start without loading the GEPA artifact.
            optimized_doc = getattr(optimized, "__doc__", None)
            if optimized_doc:
                await asyncio.to_thread(save_prompt, task, optimized_doc)

        completed = datetime.now(timezone.utc)
        duration = int(time.perf_counter() - started_perf)

        await set_status(
            run_id,
            "completed",
            completed_at=completed,
            duration_seconds=duration,
            trainset_size=len(trainset),
            valset_size=len(valset),
            metric_score_baseline=baseline_score,
            metric_score_optimized=optimized_score,
            artifact_path=str(artifact_path),
        )

    except Exception as e:
        logger.exception(f"[runner] optimization run {run_id} failed: {e}")
        completed = datetime.now(timezone.utc)
        duration = int(time.perf_counter() - started_perf)
        await set_status(
            run_id,
            "failed",
            completed_at=completed,
            duration_seconds=duration,
            error=str(e)[:2000],
        )
        raise

    return await get_run(run_id)
