"""
DB-facing logic for the harness's pre-opt/per-user-brief cache schema
(`harness.*` tables — see db/001_schema.sql). All functions go through
core.db.connection's asyncpg pool.

Separate, unrelated concern from the retired production pipeline's
`legacy/db_service.py` (old `public.daily_briefs` table) — never imported by
it, and vice versa.
"""

from __future__ import annotations

import json
import zlib
from datetime import date, time as time_cls
from typing import Any, Dict, List, Optional

from core.db import connection as harness_db

# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------


def normalize_url(url: str) -> str:
    """Trim, lowercase, strip trailing slash. Does NOT resolve Google News
    redirect tokens to the final publisher URL — near-duplicate collapsing
    across differently-tokenized URLs for the same real-world story is
    handled by scoring_service's embedding-based clustering at ranking time
    instead; this is a cache-efficiency gap, not a correctness bug."""
    normalized = url.strip().lower()
    if normalized.endswith("/"):
        normalized = normalized[:-1]
    return normalized


def estimate_duration_s(word_count: int, wpm: int = 150) -> float:
    """Matches the harness's existing 150 WPM speech-rate convention (see
    static/app.js's duration estimate) — used because real TTS is never
    called this iteration; mp3_url/duration_s are placeholders."""
    return round((word_count / wpm) * 60, 1)


def placeholder_mp3_url(kind: str, key: str) -> str:
    """kind in {"segment", "intro", "outro", "stitched"}."""
    return f"placeholder://{kind}/{key}.mp3"


def _placeholder_simhash(title: str) -> int:
    """Cheap hash of the normalized title — NOT a real simhash implementation,
    just satisfies the `articles.simhash` column. Not used for any dedup/
    cache decision this iteration; dedup is by normalized_url only."""
    normalized = "".join(ch for ch in title.lower() if ch.isalnum())
    return zlib.crc32(normalized.encode("utf-8"))


def _to_date(brief_date: str) -> date:
    return date.fromisoformat(brief_date)


def _to_time(value: Any) -> time_cls:
    if isinstance(value, time_cls):
        return value
    hh, mm = (int(p) for p in str(value).split(":")[:2])
    return time_cls(hh, mm)


def _pg_deleted_count(command_tag: str) -> int:
    """Parses asyncpg's "DELETE N" command tag into just the row count N —
    shared by clear_cache() and clear_users()."""
    return int(command_tag.split()[-1])


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------



def _jsonb(v):
    """Tolerant jsonb read: core/db's codec auto-decodes jsonb to dict/list,
    but be safe against str (e.g. double-encoded legacy rows) and None."""
    if v is None or isinstance(v, (dict, list)):
        return v
    return json.loads(v)

async def list_system_topics() -> List[Dict[str, Any]]:
    pool = await harness_db.get_pool()
    rows = await pool.fetch(
        "SELECT id, name, beat, geo_gl, geo_hl FROM harness.topics WHERE is_system = true ORDER BY name"
    )
    return [dict(r) for r in rows]


async def get_or_create_custom_topic(name: str) -> str:
    """Upsert-by-(lower(name), is_system=false). A race between two callers
    creating the same custom topic simultaneously is handled by ON CONFLICT
    DO NOTHING + re-select, not a unique-violation crash."""
    pool = await harness_db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO harness.topics (name, is_system)
            VALUES ($1, false)
            ON CONFLICT (lower(name), is_system) DO NOTHING
            RETURNING id
            """,
            name,
        )
        if row:
            return str(row["id"])
        row = await conn.fetchrow(
            "SELECT id FROM harness.topics WHERE lower(name) = lower($1) AND is_system = false",
            name,
        )
        return str(row["id"])


# ---------------------------------------------------------------------------
# Users / user_topics
# ---------------------------------------------------------------------------


async def create_user(user_id: str, display_name: str, location_name: str,
                      scheduled_time: Any = "07:00", timezone: str = "UTC",
                      location_country: Any = None, latitude: Any = None,
                      longitude: Any = None) -> str:
    """Upsert-by-Curia-user-id (port change: harness.users.id IS the Curia
    user id — text — so brief prefs join Curia identity directly; email
    column dropped, display_name added). Resubmitting updates
    display_name/location/schedule/timezone but does NOT touch topic links
    (link_user_topic is additive-only, see below).

    timezone is the client's IANA zone; scheduled_time is interpreted in it
    (see 0037_brief_tz_and_progress). latitude/longitude come from the same
    geocoder match that produced location_name/location_country (brief/
    cities.py) — stored as a Postgres point(lon, lat) so brief/weather.py can
    look up conditions by coordinate instead of re-searching the free-text
    city against a second, independent provider (see 0040 changelog note)."""
    pool = await harness_db.get_pool()
    # asyncpg's native point codec takes an (x, y) tuple directly — no cast needed.
    point = (float(longitude), float(latitude)) if latitude is not None and longitude is not None else None
    row = await pool.fetchrow(
        """
        INSERT INTO harness.users (id, display_name, location_name, scheduled_time, timezone, location_country, location)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (id) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            location_name = EXCLUDED.location_name,
            scheduled_time = EXCLUDED.scheduled_time,
            timezone = EXCLUDED.timezone,
            location_country = EXCLUDED.location_country,
            location = EXCLUDED.location
        RETURNING id
        """,
        user_id, display_name, location_name, _to_time(scheduled_time), timezone or "UTC",
        (location_country or None), point,
    )
    return str(row["id"])


async def link_user_topic(user_id: str, topic_id: str, type_: str) -> None:
    """type_ must be 'chosen' or 'custom'. Additive-only: ON CONFLICT DO
    NOTHING means re-linking an already-linked topic is a silent no-op rather
    than updating `type`/`is_active` — used by scripts/seed_users.py and
    create_user_with_topics below."""
    pool = await harness_db.get_pool()
    await pool.execute(
        """
        INSERT INTO harness.user_topics (user_id, topic_id, type, is_active)
        VALUES ($1, $2, $3::harness.user_topic_type, true)
        ON CONFLICT (user_id, topic_id) DO NOTHING
        """,
        user_id, topic_id, type_,
    )


async def create_user_with_topics(
    user_id: str,
    display_name: str,
    location_name: str,
    scheduled_time: Any,
    chosen_topic_names: List[str],
    custom_topic_names: List[str],
    timezone: str = "UTC",
    location_country: Any = None,
    latitude: Any = None,
    longitude: Any = None,
) -> str:
    """Generalizes scripts/seed_users.py's per-user create+link loop. Unknown
    chosen_topic_names (not matching a system topic) are silently skipped —
    the UI only ever sends names sourced from GET /brief/topics, so a
    mismatch shouldn't happen in practice. Empty topic lists are allowed; no
    minimum-topics validation, matching this module's existing style.

    The topic set is REPLACED, not merged: link_user_topic alone is
    additive-only (ON CONFLICT DO NOTHING), so without the prune below a user
    could never deselect a beat — resaving prefs with fewer topics silently
    kept the old ones. This is the "save preferences" entry point (PUT
    /brief/preferences, from both first-run and the profile screen), so the
    submitted set is the whole truth."""
    user_id = await create_user(user_id, display_name, location_name, scheduled_time, timezone,
                                 location_country, latitude, longitude)

    system_topics = await list_system_topics()
    topic_id_by_name = {t["name"]: str(t["id"]) for t in system_topics}
    keep_ids: List[str] = []

    for name in chosen_topic_names:
        topic_id = topic_id_by_name.get(name)
        if topic_id:
            await link_user_topic(user_id, topic_id, "chosen")
            keep_ids.append(topic_id)

    for name in custom_topic_names:
        cleaned = name.strip()
        if cleaned:
            custom_topic_id = await get_or_create_custom_topic(cleaned)
            await link_user_topic(user_id, custom_topic_id, "custom")
            keep_ids.append(custom_topic_id)

    pool = await harness_db.get_pool()
    await pool.execute(
        "DELETE FROM harness.user_topics WHERE user_id = $1 AND NOT (topic_id = ANY($2::uuid[]))",
        user_id, keep_ids,
    )

    return user_id


async def list_users() -> List[Dict[str, Any]]:
    """For the harness UI's user picker."""
    pool = await harness_db.get_pool()
    rows = await pool.fetch(
        "SELECT id, display_name, location_name, scheduled_time, created_at FROM harness.users ORDER BY created_at"
    )
    return [dict(r) for r in rows]


async def get_user(user_id: str) -> Optional[Dict[str, Any]]:
    pool = await harness_db.get_pool()
    row = await pool.fetchrow(
        "SELECT id, display_name, location_name, scheduled_time, timezone, location_country, location, created_at FROM harness.users WHERE id = $1",
        user_id,
    )
    if not row:
        return None
    user = dict(row)
    # asyncpg decodes point as an (x, y) tuple; we store it as (lon, lat) —
    # see create_user.
    point = user.pop("location", None)
    user["longitude"] = point[0] if point else None
    user["latitude"] = point[1] if point else None
    return user


def display_name_for(user: Dict[str, Any]) -> str:
    """Port change: users carry an explicit display_name (email dropped —
    Curia identity owns it)."""
    return (str(user.get("display_name", "")).strip()
            or str(user.get("id", "")).split("@")[0].split("-")[0].title()
            or "there")




async def get_user_topics(user_id: str) -> Dict[str, List[Dict[str, Any]]]:
    """{"chosen": [...], "custom": [...]} — active links only."""
    pool = await harness_db.get_pool()
    rows = await pool.fetch(
        """
        SELECT t.id, t.name, t.beat, t.is_system, ut.type
        FROM harness.user_topics ut
        JOIN harness.topics t ON t.id = ut.topic_id
        WHERE ut.user_id = $1 AND ut.is_active = true
        """,
        user_id,
    )
    chosen = [dict(r) for r in rows if r["type"] == "chosen"]
    custom = [dict(r) for r in rows if r["type"] == "custom"]
    return {"chosen": chosen, "custom": custom}


# ---------------------------------------------------------------------------
# Articles
# ---------------------------------------------------------------------------


async def upsert_article(
    url: str,
    title: str,
    topic_id: Optional[str] = None,
    sig_score: Optional[float] = None,
    topic_sim_score: Optional[float] = None,
    composite_score: Optional[float] = None,
) -> str:
    """ON CONFLICT (normalized_url) DO UPDATE — full overwrite; these score
    columns are just a "last seen" snapshot, not cache-hit-relevant. This is
    what makes the SAME real-world article, independently discovered by
    Pre-Opt's topic fetch and by a per-user custom/local fetch, resolve to
    the SAME row — the entire mechanism cross-context cache hits depend on."""
    normalized = normalize_url(url)
    simhash = _placeholder_simhash(title)
    pool = await harness_db.get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO harness.articles (url, normalized_url, title, topic_id, simhash, sig_score, topic_sim_score, composite_score, fetched_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now())
        ON CONFLICT (normalized_url) DO UPDATE SET
            title = EXCLUDED.title,
            topic_id = EXCLUDED.topic_id,
            simhash = EXCLUDED.simhash,
            sig_score = EXCLUDED.sig_score,
            topic_sim_score = EXCLUDED.topic_sim_score,
            composite_score = EXCLUDED.composite_score,
            fetched_at = EXCLUDED.fetched_at
        RETURNING id
        """,
        url, normalized, title, topic_id, simhash, sig_score, topic_sim_score, composite_score,
    )
    return str(row["id"])


async def get_preopt_candidates(topic_ids: List[str]) -> List[Dict[str, Any]]:
    """Pre-opt's already-cached top-4-per-topic candidates for the given
    (chosen) topic ids — lets user_brief_runner skip re-fetching/re-heuristic-
    ranking system topics Pre-Opt already processed. Only non-local segments
    are ever cached against a topic_id (local articles have topic_id=NULL)."""
    if not topic_ids:
        return []
    pool = await harness_db.get_pool()
    rows = await pool.fetch(
        """
        SELECT a.id AS article_id, a.url, a.title, a.topic_id
        FROM harness.articles a
        JOIN harness.article_segment_cache c ON c.article_id = a.id
        WHERE a.topic_id::text = ANY($1::text[])
          AND c.segment_type IN ('lead', 'standard')
          AND c.is_local = false
        """,
        [str(t) for t in topic_ids],
    )
    return [dict(r) for r in rows]


async def prune_topic_segment_cache(topic_id: str, keep_article_ids: List[str]) -> int:
    """Deletes cached lead/standard segments for `topic_id`'s articles that
    are NOT in `keep_article_ids` (this Pre-Opt run's fresh top-4 selection)
    — enforces "only the current top-4 per system topic stay cached", so a
    re-fetch REPLACES the old 4 instead of accumulating alongside them (see
    get_preopt_candidates above, which has no such limit and would otherwise
    keep returning every article ever cached for the topic).

    Only prunes article_segment_cache rows — harness.articles rows are left
    alone, same reasoning as clear_cache(): daily_brief_articles.article_id
    is FK RESTRICT against articles, so deleting them could fail
    unpredictably depending on brief history. article_segment_cache has no
    such restriction (daily_brief_articles.cache_id is ON DELETE SET NULL),
    so this is always safe — a past brief referencing a pruned segment just
    loses that one entry's cache_id/transcript display, nothing else."""
    pool = await harness_db.get_pool()
    result = await pool.execute(
        """
        DELETE FROM harness.article_segment_cache c
        USING harness.articles a
        WHERE c.article_id = a.id
          AND a.topic_id::text = $1
          AND c.is_local = false
          AND c.segment_type IN ('lead', 'standard')
          AND NOT (c.article_id::text = ANY($2::text[]))
        """,
        topic_id, keep_article_ids,
    )
    return _pg_deleted_count(result)


# ---------------------------------------------------------------------------
# Article segment cache
# ---------------------------------------------------------------------------


async def get_cached_segment(article_id: str, segment_type: str, is_local: bool) -> Optional[Dict[str, Any]]:
    pool = await harness_db.get_pool()
    row = await pool.fetchrow(
        """
        SELECT id, article_id, segment_type, is_local, transcript_json, mp3_url, duration_s, generated_at,
               faithfulness_severity, faithfulness_detail
        FROM harness.article_segment_cache
        WHERE article_id = $1 AND segment_type = $2::harness.segment_type AND is_local = $3
        """,
        article_id, segment_type, is_local,
    )
    if not row:
        return None
    result = dict(row)
    result["transcript_json"] = _jsonb(result["transcript_json"])
    result["faithfulness_detail"] = _jsonb(result["faithfulness_detail"])
    return result


async def put_cached_segment(
    article_id: str,
    segment_type: str,
    is_local: bool,
    transcript_json: Dict[str, Any],
    mp3_url: str,
    duration_s: float,
) -> str:
    pool = await harness_db.get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO harness.article_segment_cache (article_id, segment_type, is_local, transcript_json, mp3_url, duration_s, generated_at)
        VALUES ($1, $2::harness.segment_type, $3, $4::jsonb, $5, $6, now())
        ON CONFLICT (article_id, segment_type, is_local) DO UPDATE SET
            transcript_json = EXCLUDED.transcript_json,
            mp3_url = EXCLUDED.mp3_url,
            duration_s = EXCLUDED.duration_s,
            generated_at = EXCLUDED.generated_at,
            faithfulness_severity = NULL,
            faithfulness_detail = NULL
        RETURNING id
        """,
        article_id, segment_type, is_local, json.dumps(transcript_json), mp3_url, duration_s,
    )
    return str(row["id"])


async def set_cached_segment_faithfulness(
    article_id: str, segment_type: str, is_local: bool, severity: str, detail: Dict[str, Any],
) -> None:
    """Memoizes an automated faithfulness_article verdict onto the shared
    segment-cache row it was computed for (db/014_faithfulness_memoization.sql)
    — article_segment_cache is reused across every user, so without this the
    same cached segment text gets a fresh Sonnet faithfulness call every time
    a different user happens to receive it. Only the automated path
    (eval_judges_service.run_automated_judges) reads/writes these columns —
    the manual Gold Set "Run Judges" button always computes fresh, on
    purpose (see eval_judges_service.py's module docstring)."""
    pool = await harness_db.get_pool()
    await pool.execute(
        """
        UPDATE harness.article_segment_cache
        SET faithfulness_severity = $4, faithfulness_detail = $5::jsonb
        WHERE article_id = $1 AND segment_type = $2::harness.segment_type AND is_local = $3
        """,
        article_id, segment_type, is_local, severity, json.dumps(detail),
    )


async def list_article_cache() -> List[Dict[str, Any]]:
    """Every cached article segment, newest first — backs the harness UI's
    "View Article Cache" inspector modal (Multi-User Cache tab). Read-only;
    joins articles/topics purely for display, doesn't affect cache-hit logic."""
    pool = await harness_db.get_pool()
    rows = await pool.fetch(
        """
        SELECT
            c.id AS cache_id,
            a.id AS article_id,
            a.title,
            a.url,
            t.name AS topic_name,
            c.segment_type,
            c.is_local,
            c.duration_s,
            c.generated_at
        FROM harness.article_segment_cache c
        JOIN harness.articles a ON a.id = c.article_id
        LEFT JOIN harness.topics t ON t.id = a.topic_id
        ORDER BY c.generated_at DESC
        """
    )
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Daily briefs
# ---------------------------------------------------------------------------


async def list_due_users_for_generation() -> List[Dict[str, Any]]:
    """{"user_id", "local_date"} for users whose local delivery time has
    passed today (their local today) and who don't have a brief for that
    local date yet. local_date must be threaded through to the
    generate_brief job as-is (not recomputed from date.today() in the
    handler) — the handler runs in the container's own clock/date and could
    disagree with what this query just computed the instant it ran.

    Intentionally not a tight time-window match ("is it currently exactly
    09:00-09:15 for this user") — it's a >= comparison, so a worker outage or
    slow poll just means the next run still catches everyone who's due,
    without over-firing: once generate_brief_for_user runs, the row this
    query checks for exists, and the user drops out of the result on the very
    next poll. get_or_create_daily_brief's (user_id, date) uniqueness makes
    enqueuing the same user twice in one cycle (two overlapping poller runs)
    harmless too — the second call just reuses the first's row.

    Only excludes 'generating' (already in flight — don't double-enqueue) and
    'ready' (already done) rows. A 'failed' row does NOT exclude a user: the
    exclusion used to be "any row exists at all", which meant a failed
    generation was silently never retried until the next day — worse than
    the double-run race this query exists to prevent. generate_brief_for_user
    already handles a re-run against an existing non-ready row correctly (its
    reuse short-circuit only fires on status == "ready").

    `now() AT TIME ZONE u.timezone` converts the instant to that zone's local
    wall-clock time — requires timezone to be a valid IANA name (validated by
    Postgres itself at query time; an invalid stored value would error here
    rather than silently misfire, which is the right failure mode for a
    scheduling bug)."""
    pool = await harness_db.get_pool()
    rows = await pool.fetch(
        """
        SELECT u.id, (now() AT TIME ZONE u.timezone)::date AS local_date
        FROM harness.users u
        WHERE (now() AT TIME ZONE u.timezone)::time >= u.scheduled_time
          AND NOT EXISTS (
              SELECT 1 FROM harness.daily_briefs db
              WHERE db.user_id = u.id
                AND db.date = (now() AT TIME ZONE u.timezone)::date
                AND (
                    db.status IN ('generating', 'ready')
                    -- A failed brief retries only for 2h after its FIRST
                    -- attempt (the row's created_at — get_or_create reuses
                    -- one row per (user, date)); a permanently failing
                    -- generation must not burn a full pipeline run every
                    -- 15-minute poll all day. (v3.1 review v1.md §4.2)
                    OR (db.status = 'failed'
                        AND db.created_at < now() - interval '2 hours')
                )
          )
        """
    )
    return [{"user_id": r["id"], "local_date": r["local_date"].isoformat()} for r in rows]


async def is_user_due_now(user_id: str) -> bool:
    """Same condition as list_due_users_for_generation's WHERE clause, scoped
    to one user — lets GET /brief/today tell "waiting for a future delivery
    time" apart from "delivery time already passed, dispatch just hasn't
    polled yet" so it can trigger generation eagerly instead of making the
    user wait up to 15 minutes for the next poll."""
    pool = await harness_db.get_pool()
    row = await pool.fetchrow(
        """
        SELECT (now() AT TIME ZONE u.timezone)::time >= u.scheduled_time AS due
        FROM harness.users u WHERE u.id = $1
        """,
        user_id,
    )
    return bool(row and row["due"])


async def get_or_create_daily_brief(user_id: str, brief_date: str) -> Dict[str, Any]:
    """Idempotent insert, mirroring legacy/db_service.py's brief_set_generating
    pattern (separate table/module, not imported).
    Returns {"id", "status", "created"} — `created=False` means a row already
    existed (possibly already `ready`); callers use `status` to decide
    whether to short-circuit."""
    pool = await harness_db.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO harness.daily_briefs (user_id, date, status)
            VALUES ($1, $2, 'generating')
            ON CONFLICT (user_id, date) DO NOTHING
            RETURNING id, status
            """,
            user_id, _to_date(brief_date),
        )
        if row:
            return {"id": str(row["id"]), "status": row["status"], "created": True}
        row = await conn.fetchrow(
            "SELECT id, status FROM harness.daily_briefs WHERE user_id = $1 AND date = $2",
            user_id, _to_date(brief_date),
        )
        return {"id": str(row["id"]), "status": row["status"], "created": False}


async def set_daily_brief_urls(
    brief_id: str,
    intro_mp3_url: str,
    outro_mp3_url: str,
    stitched_mp3_url: str,
    status: str = "ready",
) -> None:
    pool = await harness_db.get_pool()
    await pool.execute(
        """
        UPDATE harness.daily_briefs
        SET intro_mp3_url = $2, outro_mp3_url = $3,
            stitched_mp3_url = $4, status = $5::harness.brief_status
        WHERE id = $1
        """,
        brief_id, intro_mp3_url, outro_mp3_url, stitched_mp3_url, status,
    )


async def mark_daily_brief_failed(brief_id: str, status: str = "failed") -> None:
    pool = await harness_db.get_pool()
    await pool.execute(
        "UPDATE harness.daily_briefs SET status = $2::harness.brief_status WHERE id = $1",
        brief_id, status,
    )


async def add_daily_brief_article(
    brief_id: str,
    article_id: str,
    cache_id: Optional[str],
    segment_type: str,
    rank: int,
    cache_hit: bool,
    reason: Optional[str] = None,
) -> str:
    pool = await harness_db.get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO harness.daily_brief_articles (brief_id, article_id, cache_id, segment_type, rank, cache_hit, reason)
        VALUES ($1, $2, $3, $4::harness.segment_type, $5, $6, $7)
        ON CONFLICT (brief_id, segment_type, rank) DO UPDATE SET
            article_id = EXCLUDED.article_id,
            cache_id = EXCLUDED.cache_id,
            cache_hit = EXCLUDED.cache_hit,
            reason = EXCLUDED.reason
        RETURNING id
        """,
        brief_id, article_id, cache_id, segment_type, rank, cache_hit, reason,
    )
    return str(row["id"])


async def set_article_resolved_url(article_id: str, resolved_url: str) -> None:
    """Records the decoded publisher URL next to the Google News token
    (0036); url stays the dedup/identity key."""
    pool = await harness_db.get_pool()
    await pool.execute(
        "UPDATE harness.articles SET resolved_url = $2 WHERE id = $1",
        article_id, resolved_url,
    )


async def set_daily_brief_audio(brief_id: str, stitched_mp3_url: str) -> None:
    """Port addition: store the real stitched-audio S3 key (brief/audio.py)."""
    pool = await harness_db.get_pool()
    await pool.execute(
        "UPDATE harness.daily_briefs SET stitched_mp3_url = $2 WHERE id = $1",
        brief_id, stitched_mp3_url,
    )


async def get_latest_manifest(user_id: str, brief_date: str):
    """Port addition: the ordered segment manifest (incl. intro/outro texts)
    persisted by save_transcript_record for this user's brief_date."""
    import json as _json
    pool = await harness_db.get_pool()
    row = await pool.fetchrow(
        """
        SELECT segments FROM harness.transcript_records
        WHERE user_id = $1 AND title LIKE '%' || $2 || '%'
        ORDER BY created_at DESC LIMIT 1
        """,
        user_id, brief_date,
    )
    if not row:
        return None
    return _jsonb(row["segments"])


async def set_latest_manifest_segments(user_id: str, brief_date: str, segments) -> None:
    """Overwrites the newest transcript record's segments for this user's
    brief_date — used by brief/audio.py to replace the 150-wpm duration
    estimates with the real stitched start_s/duration_s per segment."""
    import json as _json
    pool = await harness_db.get_pool()
    await pool.execute(
        """
        UPDATE harness.transcript_records SET segments = $3
        WHERE id = (
            SELECT id FROM harness.transcript_records
            WHERE user_id = $1 AND title LIKE '%' || $2 || '%'
            ORDER BY created_at DESC LIMIT 1
        )
        """,
        user_id, brief_date, _json.dumps(segments),
    )


async def get_daily_brief_for_date(user_id: str, brief_date: str):
    """Read-only lookup (port addition for GET /brief/today)."""
    pool = await harness_db.get_pool()
    row = await pool.fetchrow(
        "SELECT id, user_id, date, status, stitched_mp3_url, created_at, "
        "       play_progress, listened, last_played_at "
        "FROM harness.daily_briefs WHERE user_id = $1 AND date = $2",
        user_id, _to_date(brief_date),
    )
    return {**dict(row), "id": str(row["id"])} if row else None


async def set_daily_brief_progress(
    brief_id: str, user_id: str, play_progress: Optional[float], listened: bool,
    client_ts: Optional[Any] = None,
) -> str:
    """Playback resume for a brief. user_id is in the WHERE clause so a
    caller can only move their own brief.

    client_ts (offline-first replay guard): when the app replays a QUEUED
    progress update after reconnecting, a stale event must not rewind
    progress a newer event already wrote. With client_ts, last_played_at
    stores the CLIENT's event time and the UPDATE only applies when it is
    newer than what's stored. Without it (older clients), behavior is the
    original blind overwrite with server now().

    Returns "applied", "stale" (guarded out — not an error), or
    "not_found"."""
    pool = await harness_db.get_pool()
    if client_ts is not None:
        result = await pool.execute(
            """
            UPDATE harness.daily_briefs
            SET play_progress = $3, listened = $4, last_played_at = $5
            WHERE id = $1 AND user_id = $2
              AND (last_played_at IS NULL OR last_played_at < $5)
            """,
            brief_id, user_id, play_progress, listened, client_ts,
        )
        if not str(result).endswith(" 0"):
            return "applied"
        exists = await pool.fetchval(
            "SELECT 1 FROM harness.daily_briefs WHERE id = $1 AND user_id = $2",
            brief_id, user_id,
        )
        return "stale" if exists else "not_found"
    result = await pool.execute(
        """
        UPDATE harness.daily_briefs
        SET play_progress = $3, listened = $4, last_played_at = now()
        WHERE id = $1 AND user_id = $2
        """,
        brief_id, user_id, play_progress, listened,
    )
    return "applied" if not str(result).endswith(" 0") else "not_found"


async def get_daily_brief_detail(brief_id: str) -> Optional[Dict[str, Any]]:
    pool = await harness_db.get_pool()
    brief = await pool.fetchrow(
        """
        SELECT id, user_id, date, status, intro_mp3_url,
               outro_mp3_url, stitched_mp3_url, created_at,
               play_progress, listened, last_played_at
        FROM harness.daily_briefs WHERE id = $1
        """,
        brief_id,
    )
    if not brief:
        return None
    articles = await pool.fetch(
        """
        SELECT dba.id, dba.article_id, dba.cache_id, dba.segment_type, dba.rank, dba.cache_hit, dba.reason,
               a.title, a.url, a.resolved_url,
               c.transcript_json, c.mp3_url, c.duration_s
        FROM harness.daily_brief_articles dba
        JOIN harness.articles a ON a.id = dba.article_id
        LEFT JOIN harness.article_segment_cache c ON c.id = dba.cache_id
        WHERE dba.brief_id = $1
        ORDER BY
            CASE dba.segment_type WHEN 'lead' THEN 0 WHEN 'standard' THEN 1 WHEN 'local' THEN 2 ELSE 3 END,
            dba.rank
        """,
        brief_id,
    )
    parsed_articles = []
    for a in articles:
        entry = dict(a)
        # asyncpg returns jsonb as raw JSON text — parse it here so the API
        # response (and the harness UI) can show the actual segment text,
        # not just its title/mp3_url. NULL only if cache_id was never
        # resolved (shouldn't happen in practice — see the ERD note on
        # daily_brief_articles.cache_id).
        entry["transcript_json"] = _jsonb(entry["transcript_json"])
        parsed_articles.append(entry)
    return {"brief": dict(brief), "articles": parsed_articles}


async def save_transcript_record(
    user_id: Optional[str],
    display_name: str,
    brief_date: str,
    topics_used: List[str],
    segments: List[Dict[str, Any]],
) -> str:
    """Archives one completed end-to-end "Generate Brief" run for the harness
    UI's Open Coding tab. Called once per successful generate_brief_for_user
    call (see user_brief_runner) — purely additive logging, never read by any
    cache-hit/generation decision. `segments` is the exact manifest already
    built for the API response (intro/lead/standard*/local/outro),
    stored as-is so the archived transcript is byte-for-byte what the user
    actually received."""
    title = f"{display_name} — {brief_date}"
    pool = await harness_db.get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO harness.transcript_records (user_id, title, display_name, topics_used, segments)
        VALUES ($1, $2, $3, $4::jsonb, $5::jsonb)
        RETURNING id
        """,
        user_id, title, display_name, json.dumps(topics_used), json.dumps(segments),
    )
    return str(row["id"])


async def list_transcript_records() -> List[Dict[str, Any]]:
    """Every archived transcript, newest first — backs the Open Coding tab's
    card list + detail view in one call (no separate per-card detail fetch;
    the harness is a low-volume testing tool, so returning full segments/note
    for every row up front is simpler than paginating)."""
    pool = await harness_db.get_pool()
    rows = await pool.fetch(
        """
        SELECT id, user_id, title, display_name, topics_used, segments, note, created_at
        FROM harness.transcript_records
        ORDER BY created_at DESC
        """
    )
    results = []
    for r in rows:
        entry = dict(r)
        entry["topics_used"] = _jsonb(entry["topics_used"])
        entry["segments"] = _jsonb(entry["segments"])
        entry["is_open_coded"] = bool(entry["note"] and entry["note"].strip())
        results.append(entry)
    return results


async def save_transcript_note(transcript_id: str, note: str, title: Optional[str] = None) -> bool:
    """Returns False if transcript_id doesn't exist (caller maps to 404).
    `title` is optional — the Open Coding tab's title field is user-editable
    alongside its note; omitted (None) leaves the existing title untouched."""
    pool = await harness_db.get_pool()
    if title is not None:
        row = await pool.fetchrow(
            "UPDATE harness.transcript_records SET note = $2, title = $3 WHERE id = $1 RETURNING id",
            transcript_id, note, title,
        )
    else:
        row = await pool.fetchrow(
            "UPDATE harness.transcript_records SET note = $2 WHERE id = $1 RETURNING id",
            transcript_id, note,
        )
    return row is not None


async def delete_transcript_note(transcript_id: str) -> bool:
    """Clears the note (sets NULL) — the transcript record itself is never
    deleted this way, only its annotation. Returns False if transcript_id
    doesn't exist (caller maps to 404)."""
    pool = await harness_db.get_pool()
    row = await pool.fetchrow(
        "UPDATE harness.transcript_records SET note = NULL WHERE id = $1 RETURNING id",
        transcript_id,
    )
    return row is not None


async def clear_cache() -> Dict[str, int]:
    """Wipes cached segment transcripts/audio and all brief history — used by
    the harness UI's "Clear Cache" button so Pre-Opt/Generate Brief can be
    re-tested from a clean slate. Deliberately leaves `users`/`topics`/
    `user_topics`/`articles` intact: those are seed/reference data, not
    "cache" — and `daily_brief_articles.article_id` is FK RESTRICT on
    `articles`, so wiping articles too would require deleting brief history
    first anyway, for no real benefit (articles are just dedup keys).
    Deletion order respects FK constraints: daily_brief_articles (child) ->
    daily_briefs -> article_segment_cache."""
    pool = await harness_db.get_pool()

    def _deleted_count(command_tag: str) -> int:
        return int(command_tag.split()[-1])

    async with pool.acquire() as conn:
        async with conn.transaction():
            r1 = await conn.execute("DELETE FROM harness.daily_brief_articles")
            r2 = await conn.execute("DELETE FROM harness.daily_briefs")
            r3 = await conn.execute("DELETE FROM harness.article_segment_cache")

    return {
        "daily_brief_articles_deleted": _pg_deleted_count(r1),
        "daily_briefs_deleted": _pg_deleted_count(r2),
        "article_segment_cache_deleted": _pg_deleted_count(r3),
    }


async def clear_users() -> Dict[str, int]:
    """Deletes ALL users (+ their user_topics + brief history) — used by the
    harness UI's "Clear All Users" button. Deliberately leaves `topics`/
    `articles`/`article_segment_cache` intact: Pre-Opt's cache is topic-scoped,
    not user-scoped, and must survive a user reset the same way it already
    survives clear_cache(). Every relevant FK back to `users` is ON DELETE
    CASCADE (see db/001_schema.sql), so a bare `DELETE FROM harness.users`
    would work on its own — deletion is still done in explicit FK-safe order
    here purely to get accurate per-table counts back."""
    pool = await harness_db.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            r1 = await conn.execute("DELETE FROM harness.daily_brief_articles")
            r2 = await conn.execute("DELETE FROM harness.daily_briefs")
            r3 = await conn.execute("DELETE FROM harness.user_topics")
            r4 = await conn.execute("DELETE FROM harness.users")

    return {
        "daily_brief_articles_deleted": _pg_deleted_count(r1),
        "daily_briefs_deleted": _pg_deleted_count(r2),
        "user_topics_deleted": _pg_deleted_count(r3),
        "users_deleted": _pg_deleted_count(r4),
    }


async def reset_user_brief(user_id: str, brief_date: str) -> Dict[str, int]:
    """Deletes ONE user's `daily_briefs` row for ONE date (+ its
    `daily_brief_articles`) — a scoped version of clear_cache() above, for
    when you only want to force a fresh full pipeline run for one user
    without wiping everyone else's cache.

    Why this is needed: generate_brief_for_user's `ON CONFLICT (user_id,
    date) DO NOTHING` check means a second "Generate Brief" click for the
    same user on the same day always takes the regenerate-bookends
    shortcut (reuses the persisted 5-article selection and cached segment
    text untouched, only rewrites intro/outro) — so re-fetching Pre-Opt
    articles or editing a segment/curation prompt has no visible effect
    until that day's daily_briefs row is gone. Deleting it here makes the
    next click take the full fetch/rank/curate/segment path again.

    Deliberately does NOT touch `harness.article_segment_cache` (Pre-Opt's
    cache is shared/reused across every other user and topic) or any other
    user's `daily_briefs`. Also safe for Gold Set data: `eval_runs.brief_id`
    is ON DELETE SET NULL (db/003_eval_schema.sql), and gold profiles are
    pinned by `eval_runs.id`, never `brief_id`, so a frozen run that
    happens to reference this exact brief is unaffected — only the live
    `daily_briefs`/`daily_brief_articles` rows go away, not the eval_*
    snapshot tables the Gold Set tab actually reads from."""
    pool = await harness_db.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            r1 = await conn.execute(
                """
                DELETE FROM harness.daily_brief_articles
                WHERE brief_id IN (
                    SELECT id FROM harness.daily_briefs WHERE user_id = $1 AND date = $2
                )
                """,
                user_id, _to_date(brief_date),
            )
            r2 = await conn.execute(
                "DELETE FROM harness.daily_briefs WHERE user_id = $1 AND date = $2",
                user_id, _to_date(brief_date),
            )

    return {
        "daily_brief_articles_deleted": _pg_deleted_count(r1),
        "daily_briefs_deleted": _pg_deleted_count(r2),
    }
