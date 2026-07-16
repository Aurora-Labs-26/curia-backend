-- Curia Daily Brief — 006: merge Glimpse into Intro (bookend consolidation)
--
-- Step 7 now returns {"intro", "outro"} instead of {"intro", "glimpse",
-- "outro"} — the story-preview content that used to be its own "glimpse"
-- field is folded directly into "intro" by the prompt itself. This drops
-- the two now-obsolete glimpse-specific columns.
--
-- ORDERING — READ BEFORE RUNNING:
-- On an existing DB with real eval_meta_segments/eval_gold_scripts data,
-- run `python -m scripts.migrate_merge_glimpse_into_intro` BEFORE applying
-- this file, and verify its output. This is a real DROP COLUMN — not a
-- soft delete — and no trigger or freeze mechanism protects the columns
-- below from it (harness.eval_block_frozen_mutation only fires on
-- UPDATE/DELETE DML, never on ALTER TABLE DDL). Once this runs, any
-- glimpse_text/glimpse_mp3_url data not already folded into intro_text is
-- gone for good short of a full Postgres restore.
--
-- Idempotent: safe to re-run via scripts/bootstrap_db.py (IF EXISTS guards).

ALTER TABLE harness.daily_briefs DROP COLUMN IF EXISTS glimpse_mp3_url;
ALTER TABLE harness.eval_meta_segments DROP COLUMN IF EXISTS glimpse_text;
