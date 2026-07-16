-- Curia Daily Brief — synchronous faithfulness regeneration loop
--
-- Two additive changes supporting the article-segment regen loop (see
-- faithfulness-judge-plan.md section 5):
--
-- 1. attempt_number on eval_judge_outputs — a flagged article segment now
--    gets regenerated and re-judged up to twice more (3 rows total worst
--    case: attempt 1/2/3), instead of always being exactly one row per
--    (run_id, judge_name, segment_type). Defaults to 1 so every existing
--    row (and every judge that never regenerates — order/relevance/
--    bookend/etc.) is unaffected. Nullable would be equally correct but a
--    NOT NULL default keeps every consumer's ORDER BY/GROUP BY simpler.
--
-- 2. faithfulness_regen_enabled toggle, same singleton-row pattern as
--    automated_judging_enabled (db/015_judging_settings.sql) — a separate
--    switch from automated judging itself, since disabling this one only
--    turns off the regenerate-on-flag behavior; faithfulness_article
--    judging itself still happens synchronously either way (see
--    faithfulness_regen_service.py). Defaults to enabled.
--
-- Idempotent: safe to re-run via scripts/bootstrap_db.py.

ALTER TABLE harness.eval_judge_outputs ADD COLUMN IF NOT EXISTS attempt_number smallint NOT NULL DEFAULT 1;

ALTER TABLE harness.eval_settings ADD COLUMN IF NOT EXISTS faithfulness_regen_enabled boolean NOT NULL DEFAULT true;
