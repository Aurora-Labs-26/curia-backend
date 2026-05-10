"""
optimization/examples
=====================
QA-curated trainset rows for GEPA. Each row = one input set for one task.

Storage: `optimization_examples` table (see migration 0006).
Inputs schema (JSONB) per task — validated at insert time:

  transcript:  {"briefing": str, "outline": str|dict, "speaker_definition": str}
  outline:     {"briefing": str}

Public API:
    list_examples(task, scope_type='global', scope_value=None, limit)
    get_example(example_id)
    create_example(task, inputs, scope_type='global', scope_value=None,
                   label=None, metadata=None, source_episode_id=None, created_by=None)
    delete_example(example_id)
    import_from_episode(episode_id, task, scope_type='global', scope_value=None, created_by=None)
"""

from .store import (
    INPUTS_SCHEMA_BY_TASK,
    ExampleNotFoundError,
    InvalidExampleInputs,
    create_example,
    delete_example,
    get_example,
    import_from_episode,
    list_examples,
)

__all__ = [
    "INPUTS_SCHEMA_BY_TASK",
    "ExampleNotFoundError",
    "InvalidExampleInputs",
    "create_example",
    "delete_example",
    "get_example",
    "import_from_episode",
    "list_examples",
]
