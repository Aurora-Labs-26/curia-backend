-- Curia Daily Brief — memoized faithfulness_article verdicts on the shared
-- segment cache
--
-- harness.article_segment_cache is shared across every user (Pre-Opt's whole
-- point) — without memoizing the faithfulness verdict for a given
-- (article_id, segment_type, is_local), automated judging (see
-- automated_judging_build_plan.md Milestone 4) would re-run the same Sonnet
-- faithfulness call on identical text every time a different user happens to
-- receive that same cached article. Storing the verdict on the cache row
-- itself means it's computed once per unique piece of text, not once per
-- user who sees it.
--
-- cache_service.put_cached_segment's ON CONFLICT ... DO UPDATE must null
-- these out whenever transcript_json actually changes (a real regeneration
-- after a prompt edit + cache clear), so a stale verdict never survives a
-- text change — enforced in application code (cache_service.py), not here.
--
-- Idempotent: safe to re-run via scripts/bootstrap_db.py.

ALTER TABLE harness.article_segment_cache ADD COLUMN IF NOT EXISTS faithfulness_severity text NULL;
ALTER TABLE harness.article_segment_cache ADD COLUMN IF NOT EXISTS faithfulness_detail jsonb NULL;
