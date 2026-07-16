-- Seeds the 7 system topics ("Beats"), matching app/services/news_service.py's
-- BEATS dict exactly (name + beat key are identical for system topics).
--
-- geo_gl/geo_hl here are informational snapshots only (per BEAT_GEO_MODE):
-- "global" beats snapshot the US edition; "mixed"/"national" beats snapshot
-- the default national edition (India, matching DEFAULT_NATIONAL_GEO). The
-- real fetch (news_service.fetch_articles_for_beat) always re-derives live
-- geo params from BEATS/BEAT_GEO_MODE by `beat` name — a "mixed" beat needs
-- BOTH editions, which a single column pair can't represent — these columns
-- are never read back for that decision.
--
-- Idempotent: safe to re-run.

INSERT INTO harness.topics (name, beat, geo_gl, geo_hl, is_system) VALUES
    ('Tech',             'Tech',             'US', 'en-US', true),
    ('Business',         'Business',         'IN', 'en-IN', true),
    ('World',            'World',            'US', 'en-US', true),
    ('Science & Health', 'Science & Health', 'US', 'en-US', true),
    ('Culture',          'Culture',          'IN', 'en-IN', true),
    ('Lifestyle',        'Lifestyle',        'IN', 'en-IN', true),
    ('Sports',           'Sports',           'US', 'en-US', true)
ON CONFLICT (lower(name), is_system) DO NOTHING;
