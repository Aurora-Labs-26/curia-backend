-- Curia Daily Brief — global on/off toggle for automated judging
--
-- Singleton settings row (id forced to `true` by the CHECK constraint, so a
-- second row can never be inserted) rather than an in-memory flag — matches
-- this app's existing convention of keeping all real state in Postgres
-- rather than process memory, and survives a server restart, unlike a bare
-- module-level variable would (see automated_judging_build_plan.md's "Hard
-- constraints" section — the toggle must be a deliberate, persistent
-- decision, not something that silently resets to a default on redeploy).
--
-- Defaults to enabled — automated judging is meant to run once the trigger
-- ships (see Milestone 5); this table exists so it CAN be turned off
-- cleanly, not so it starts off by default.
--
-- Idempotent: safe to re-run via scripts/bootstrap_db.py.

CREATE TABLE IF NOT EXISTS harness.eval_settings (
    id                          boolean PRIMARY KEY DEFAULT true,
    automated_judging_enabled   boolean NOT NULL DEFAULT true,
    updated_at                  timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT chk_eval_settings_singleton CHECK (id = true)
);

INSERT INTO harness.eval_settings (id, automated_judging_enabled)
VALUES (true, true)
ON CONFLICT (id) DO NOTHING;
