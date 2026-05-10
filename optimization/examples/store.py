"""
optimization/examples/store.py
DB CRUD for optimization_examples + a convenience importer that turns a real
production episode into a trainset row.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from loguru import logger

from core.db.connection import db_execute, db_fetchrow, db_query


# ---------------------------------------------------------------------------
# Per-task input schema validation
# ---------------------------------------------------------------------------

INPUTS_SCHEMA_BY_TASK: dict[str, set[str]] = {
    # The required fields each task's DSPy module accepts as InputFields.
    "transcript": {"briefing", "outline", "speaker_definition"},
    "outline": {"briefing"},
    # Per-transformation tasks could be added here later, e.g.:
    # "transformation.summary": {"article"},
}


class ExampleNotFoundError(Exception):
    pass


class InvalidExampleInputs(Exception):
    pass


def _validate_inputs(task: str, inputs: dict) -> None:
    if task not in INPUTS_SCHEMA_BY_TASK:
        raise InvalidExampleInputs(
            f"Unknown task '{task}'. Known: {sorted(INPUTS_SCHEMA_BY_TASK)}"
        )
    required = INPUTS_SCHEMA_BY_TASK[task]
    missing = required - set(inputs.keys())
    if missing:
        raise InvalidExampleInputs(
            f"Example for task '{task}' missing required inputs: {sorted(missing)}. "
            f"Required: {sorted(required)}"
        )


def _coerce_input_values(inputs: dict) -> dict:
    """
    JSONB columns store dicts as JSON, but our task inputs are mostly already strings
    (briefing is JSON-text, outline can be dict-or-text). Normalize values to strings
    where the DSPy module expects them — no-op if already strings.
    """
    out = {}
    for k, v in inputs.items():
        if isinstance(v, (dict, list)):
            out[k] = json.dumps(v, ensure_ascii=False)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def list_examples(
    task: str | None = None,
    scope_type: str = "global",
    scope_value: str | None = None,
    limit: int = 100,
) -> list[dict]:
    where_parts = []
    params: dict[str, Any] = {"limit": limit}
    if task:
        where_parts.append("task = $task")
        params["task"] = task
    if scope_type:
        where_parts.append("scope_type = $scope_type")
        params["scope_type"] = scope_type
    if scope_value is not None:
        where_parts.append("scope_value = $scope_value")
        params["scope_value"] = scope_value
    where = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
    sql = f"""
        SELECT id, task, scope_type, scope_value, label,
               inputs, metadata, source_episode_id, created_by, created_at
        FROM optimization_examples
        {where}
        ORDER BY created_at DESC
        LIMIT $limit
    """
    rows = await db_query(sql, params)
    return [dict(r) for r in rows]


async def get_example(example_id: str) -> dict:
    row = await db_fetchrow(
        """
        SELECT id, task, scope_type, scope_value, label, inputs, metadata,
               source_episode_id, created_by, created_at
        FROM optimization_examples WHERE id = $id::uuid
        """,
        {"id": example_id},
    )
    if not row:
        raise ExampleNotFoundError(example_id)
    return dict(row)


async def create_example(
    task: str,
    inputs: dict,
    scope_type: str = "global",
    scope_value: str | None = None,
    label: str | None = None,
    metadata: dict | None = None,
    source_episode_id: str | None = None,
    created_by: str | None = None,
) -> dict:
    if scope_type not in ("global", "cohort", "user"):
        raise InvalidExampleInputs(f"scope_type must be global/cohort/user, got {scope_type!r}")
    _validate_inputs(task, inputs)
    safe_inputs = _coerce_input_values(inputs)
    example_id = str(uuid.uuid4())
    await db_execute(
        """
        INSERT INTO optimization_examples
            (id, task, scope_type, scope_value, label, inputs, metadata,
             source_episode_id, created_by)
        VALUES
            ($id::uuid, $task, $scope_type, $scope_value, $label,
             $inputs::jsonb, $metadata::jsonb, $source_episode_id::uuid, $created_by)
        """,
        {
            "id": example_id,
            "task": task,
            "scope_type": scope_type,
            "scope_value": scope_value,
            "label": label,
            "inputs": json.dumps(safe_inputs),
            "metadata": json.dumps(metadata or {}),
            "source_episode_id": source_episode_id,
            "created_by": created_by,
        },
    )
    return await get_example(example_id)


async def delete_example(example_id: str) -> None:
    result = await db_execute(
        "DELETE FROM optimization_examples WHERE id = $id::uuid",
        {"id": example_id},
    )
    # asyncpg returns "DELETE n" — we don't bother parsing


# ---------------------------------------------------------------------------
# Convenience: import a production episode as a trainset row
# ---------------------------------------------------------------------------


async def import_from_episode(
    episode_id: str,
    task: str,
    scope_type: str = "global",
    scope_value: str | None = None,
    created_by: str | None = None,
) -> dict:
    """
    Pull the inputs that produced a real episode into the trainset for `task`.

    For task='transcript': uses the episode's outline + a recomputed briefing
    (we don't currently persist the full briefing, so we capture what we have:
    the outline, the editorial_direction, and a rendered speaker definition
    derived from the show's profile).

    For task='outline': captures the briefing inputs we have.
    """
    from shows.profiles import SHOW_PROFILES

    row = await db_fetchrow(
        """
        SELECT id, show_name, outline, transcript, editorial_direction
        FROM episode WHERE id = $id::uuid
        """,
        {"id": episode_id},
    )
    if not row:
        raise ExampleNotFoundError(f"episode {episode_id}")

    show_name = row["show_name"]
    profile = SHOW_PROFILES.get(show_name) if show_name else None

    # Briefing isn't stored — caller can fill it in later or we synthesize a
    # placeholder from what we have. For a v1 import this is good enough; QA can edit.
    briefing_stub = json.dumps(
        {
            "format": profile.format_name if profile else None,
            "editorial_direction": row.get("editorial_direction") or "",
            "note": "imported from episode — briefing omitted; recompute if needed",
        }
    )

    outline_text = json.dumps(row.get("outline")) if row.get("outline") else ""

    if task == "transcript":
        speaker = profile.speaker_config.speakers[0] if profile else None
        speaker_def = (
            f"Name: {speaker.name}\n"
            f"Backstory: {speaker.backstory}\n"
            f"Speech patterns: {speaker.speech_patterns}"
        ) if speaker else ""
        inputs = {
            "briefing": briefing_stub,
            "outline": outline_text,
            "speaker_definition": speaker_def,
        }
    elif task == "outline":
        inputs = {"briefing": briefing_stub}
    else:
        raise InvalidExampleInputs(f"import_from_episode does not yet support task '{task}'")

    label = f"imported from episode {episode_id[:8]}…"
    return await create_example(
        task=task,
        inputs=inputs,
        scope_type=scope_type,
        scope_value=scope_value,
        label=label,
        metadata={"imported_from_episode": str(episode_id)},
        source_episode_id=str(episode_id),
        created_by=created_by,
    )
