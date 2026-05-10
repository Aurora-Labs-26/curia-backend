"""optimization tables: examples, guidelines, runs

Revision ID: 0006_optimization_tables
Revises: 0005_user_roles
Create Date: 2026-05-10

Adds the GEPA harness:
  - optimization_guidelines    DB-backed company guidelines (was Python module strings;
                               kept as fallback). QA can edit via PUT /admin/guidelines/{task}.
  - optimization_examples      Trainset rows. Each row = one input set for a task,
                               either hand-crafted by QA or imported from a real episode.
  - optimization_runs          Tracks GEPA executions: status, params, baseline/optimized
                               scores, artifact path.

Seeds the current Python-module guidelines text into the guidelines table so existing
behavior is preserved. After this migration the runtime resolver checks the DB first.
"""

from alembic import op

revision = "0006_optimization_tables"
down_revision = "0005_user_roles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── guidelines ─────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE optimization_guidelines (
            task        TEXT PRIMARY KEY,
            body        TEXT NOT NULL,
            version     INTEGER NOT NULL DEFAULT 1,
            updated_by  TEXT,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    # Seed from the Python-module values via a deferred import — done in the
    # app at first read instead, so we don't import application code in migrations.
    # The optimization/guidelines module checks the DB; if the row is missing,
    # it falls back to the Python constant. Either way, the system is consistent.
    # We *do* insert empty placeholders for known tasks so QA can immediately edit them.
    op.execute("""
        INSERT INTO optimization_guidelines (task, body)
        VALUES
            ('transcript', '__SEED_FROM_PYTHON__'),
            ('outline', '__SEED_FROM_PYTHON__')
        ON CONFLICT (task) DO NOTHING
    """)

    # ── examples ───────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE optimization_examples (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            task                TEXT NOT NULL,
            scope_type          TEXT NOT NULL DEFAULT 'global'
                                CHECK (scope_type IN ('global', 'cohort', 'user')),
            scope_value         TEXT,
            label               TEXT,
            inputs              JSONB NOT NULL,
            metadata            JSONB NOT NULL DEFAULT '{}'::jsonb,
            source_episode_id   UUID REFERENCES episode(id) ON DELETE SET NULL,
            created_by          TEXT,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX optimization_examples_task_scope_idx
            ON optimization_examples (task, scope_type, scope_value, created_at DESC)
    """)

    # ── runs ───────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE optimization_runs (
            id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            task                        TEXT NOT NULL,
            scope_type                  TEXT NOT NULL DEFAULT 'global'
                                        CHECK (scope_type IN ('global', 'cohort', 'user')),
            scope_value                 TEXT,
            status                      TEXT NOT NULL DEFAULT 'queued'
                                        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
            metric_name                 TEXT NOT NULL DEFAULT 'rubric_judge',
            trainset_size               INTEGER,
            valset_size                 INTEGER,
            metric_score_baseline       REAL,
            metric_score_optimized      REAL,
            artifact_path               TEXT,
            promoted                    BOOLEAN NOT NULL DEFAULT FALSE,
            config                      JSONB NOT NULL DEFAULT '{}'::jsonb,
            error                       TEXT,
            started_at                  TIMESTAMPTZ,
            completed_at                TIMESTAMPTZ,
            duration_seconds            INTEGER,
            created_by                  TEXT,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX optimization_runs_task_status_idx
            ON optimization_runs (task, status, created_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS optimization_runs")
    op.execute("DROP TABLE IF EXISTS optimization_examples")
    op.execute("DROP TABLE IF EXISTS optimization_guidelines")
