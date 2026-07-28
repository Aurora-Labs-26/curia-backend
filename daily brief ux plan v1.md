# Daily Brief — UX & Integration Plan v1

**Date:** 2026-07-28
**Scope:** shipping the daily-brief backend (landed on `v3`, commits `b0539fc`..`9580b2d`) as a user-facing feature in the app.
**Companion doc:** `dailybrief analysis v1.md` (the integration decision this builds on).

---

## 1. The core decision — Brief is not a Show, but renders as one

A Brief has no `format`, no `episode` row, and lives in `harness.*`. Forcing it into the
`Show` type (or faking it into `/episodes`) would lie in a shared contract, and every
episode-specific action would need guards anyway.

**Chosen: client-edge adapter.** Backend contracts stay separate and honest. One frontend
module maps `GET /brief/today` → a `Show`-shaped object tagged `kind: "brief"`. Deck and
player consume a discriminated union. Reuse without contaminating the episode contract.

Rejected: pseudo-episode in `/episodes` (dishonest contract), fully parallel type
(duplicates deck + player for an unproven feature).

## 2. Placement

- **Own pinned section above "Freshly made"** — not a member of `ShowsDeck`.
  Reasons: the deck holds swipe-driven `order`/`slotIds` captured at mount, so "always
  first" fights its rotation; and `freshShows` filters to `state === "new" | "in-progress"`
  with the whole section conditional, so a user with a brief but zero episodes would hit
  the full-screen empty state and never see it. A separate section is purely additive —
  no existing flow gets rewritten.
- Distinct cover art + no format-icon overlay, so it reads as structurally different.
- **Player is reused as-is.** Segment `duration_s` values sum cumulatively into chapter
  offsets (chapter titles = article titles, so the chapter list becomes today's TOC); the
  5 articles' `resolved_url` → domain → existing favicon pills. Hide remix, delete, and
  format/host chips.

## 3. Data acquisition — no location UI

- **City:** IP-geo lookup at prefs-save time (~one call per user, ever). Not GPS: a
  location permission prompt is steep for an unvalidated feature, and carries App Store
  privacy-declaration surface. Not a typed field: friction in onboarding.
  Prod requires reading `X-Forwarded-For` (CloudFront → ALB = 2 hops), not
  `request.client.host`, which would geolocate the load balancer.
  Prefs screen shows the detected city with a quiet "change" affordance — correction, not input.
- **Timezone:** device `Intl.DateTimeFormat().resolvedOptions().timeZone`, sent with prefs.
  Free, no permission. Required: `scheduled_time` is a bare `time` and the cron runs UTC,
  so "9 AM" is undefined without it.
- If city can't be resolved, the brief generates without the Local Pulse segment.

Deferred: GPS. If Local Pulse proves valuable, it slots in as autofill for the existing
"change city" affordance rather than a new flow.

## 4. Lifecycle

- Generation is system-controlled. No manual refresh.
- Default 9:00 AM local, changeable in profile.
- Push notification when ready (`brief_ready`, deduped via `notification_log`).
- **Rollover at user-local midnight.** Today's brief if ready, else the pending card —
  so there's no empty gap between midnight and generation. Yesterday's never shows.
- Pending card displays the user's chosen beats as chips: "here's what's coming", not dead space.
- **Never interrupt mid-playback at rollover** — the card leaves the section, audio continues.
- **"Disappears" is a presentation rule, not a delete.** Rows stay in `daily_briefs`; the
  section filters to today; profile stats read all of them. Otherwise listening minutes
  would vanish nightly (stats are computed in-memory from the shows array).
  Also keeps "brief history" available for free if adoption justifies it.

## 5. Work queue

**Backend** (1–5 unblock everything else; testable against the local stack):

1. Migration — `timezone` on `harness.users`; `play_progress` / `listened` /
   `last_played_at` on `harness.daily_briefs`.
2. `PUT /brief/preferences` — accept `scheduled_time` + `timezone` (currently hardcodes
   `"07:00"` and has no time field); derive city from request IP.
3. `GET /brief/today` — merge `get_latest_manifest()` into the response. Currently returns
   articles but *not* intro/outro text (that lives in `transcript_records.segments`), which
   the player needs for chapters and offsets.
4. `GET /brief/today/audio` — presign the stored S3 key, mirroring
   `GET /episodes/{id}/audio`. `stitched_mp3_url` is a key, not playable.
5. Progress route for resume.
6. Guard the local-news fetch when `location_name` is blank — `user_brief_runner` builds
   its query by string-interpolating the city, so a blank value yields a junk query.
7. Crons — pre-opt (warms the shared cache, must run ahead of the earliest user slot),
   then per-user `generate_brief`. Worker already runs APScheduler with two daily jobs;
   same pattern.
8. `brief_ready` notification — `core/notifications.py` pattern.

**Frontend:** adapter + context merge → pinned card (ready / pending states) → player
guards → prefs screen + post-auth gate (doesn't exist today; `_layout.tsx` routes straight
to tabs) → profile settings row.

## 6. Open / deferred

- GPS location (see §3).
- Eval harness stays parked (`brief/parked.py`) — only the inline faithfulness gate is live.
- Brief history UI — data will support it; no UI planned for v1.
