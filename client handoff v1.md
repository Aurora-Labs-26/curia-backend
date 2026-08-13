# Client Handoff v1 — backend contracts awaiting app work

**Date:** 2026-08-12 · **For:** Aditya · **Backend state:** everything in §1–§3
is LIVE on prod now (build against it today); §2 is written and tested on `v3`
but **deploys only when your build is ready** — tell Arihant/Claude to run the
coordinated deploy.

---

## 1. Offline-first / subway readiness (LIVE — highest priority)

The product promise: a user who opened the app on WiFi that morning can ride
the subway with zero bars and everything works. Backend enablers are live on
**both briefs and episodes** — the client patterns are identical, implement
once.

### 1a. Prefetch audio, never stream
- Brief: the `brief_ready` FCM data message now carries
  `{"type": "brief_ready", "brief_id": "...", "date": "YYYY-MM-DD"}`.
  On receipt, background-download via `GET /brief/today/audio` (returns
  `{"url": <presigned>}` on prod; the URL supports HTTP Range for resumable
  downloads). Store locally; playback is a local-file operation.
- Episodes: no episode-ready push exists (deliberate). Prefetch new/unplayed
  episodes on app-open / WiFi via the list + `GET /episodes/{id}/audio`.
  Episode audio is immutable once generated — cache forever, evict by LRU.
- iOS: `content-available` background push + `URLSession` background
  downloads. Android: FCM data message + WorkManager.

### 1b. Progress outbox with `client_ts`
- Include `client_ts` (ISO-8601, the moment the user actually paused) in every
  progress write:
  - `PUT /brief/{brief_id}/progress`   `{"play_progress": 0.62, "listened": false, "client_ts": "..."}`
  - `PUT /episodes/{episode_id}/progress`  same shape
- Queue writes offline; replay on reconnect **in any order** — the server
  stores the client event time and guards out stale events.
- **Outbox rule: any 2xx = delivered, drop it.** A superseded (stale) event
  returns 204, not an error. Retry only on 5xx / network failure.
  Malformed `client_ts` is a 422 (client bug — don't retry).
- Omitting `client_ts` keeps the old blind-overwrite behavior (backward
  compatible), but the outbox MUST send it or replays can rewind progress.

### 1c. Cached reads + ETag revalidation
- `GET /brief/today`, `GET /episodes`, `GET /episodes/{id}` all return an
  `ETag` header. Cache body+ETag; send `If-None-Match` on refresh; on **304**
  render from cache (empty body).
- Render cached data instantly on foreground, revalidate behind — never a
  spinner for data seen recently.

### What cannot work offline (by design)
Triggering fresh generation, prefs changes, onboarding. The offline promise is
"consume what exists + record progress."

---

## 2. Location toggle — device coordinates ONLY (ON `v3`, NOT YET DEPLOYED)

Product decision (Arihant, 2026-08-11): the "local news" toggle is the single
location control. No picker, no typed city, no IP guessing.

- **Toggle ON**: OS location permission → GPS fix →
  `PUT /brief/preferences` with `"latitude": <float>, "longitude": <float>`
  (both together; range-validated). Server reverse-geocodes and stores the
  DEVICE coordinates. Response `{"location_name": "Mumbai, Maharashtra,
  India"}` is your display label ("Local news: Mumbai").
- **Toggle OFF**: PUT with **explicit** `"latitude": null, "longitude": null`.
  Omitting the fields means "keep stored" — OFF must send nulls deliberately.
- **Remove from the app**: any city picker UI, any `GET /brief/cities` calls
  (endpoint will 404 after this deploys), any `location_name` in PUT bodies
  (ignored after this deploys — free text can no longer enter the system).
- **Errors**: 422 = fix resolved to no locality (show "couldn't detect a
  city"); 503 = geocoder hiccup (retry silently later). Neither stores
  anything.
- `GET /preferences` still returns `location_name` for the label.
- Known trade (accepted): no manual override — a wrong reverse-geocoded
  locality is only fixed by re-toggling from a better fix.
- **Sequencing**: ship your build using coordinates FIRST (the GPS path
  already works on prod today); then the backend deploy removes the legacy
  surface.

---

## 3. Playback polish (LIVE — consume directly)

- **Chapters**: `GET /brief/today` → `segments[].start_s` / `duration_s` are
  exact stitched-audio offsets (measured at stitch time, not word-count
  estimates). Chapter list + seek should key off them directly. The brief now
  has a 1.5s music lead-in before the greeting — `start_s` values account for
  it.
- **Resume**: `play_progress` / `listened` / `last_played_at` come back on
  brief and episode payloads — restore position from them.

---

## 4. Small but real

- Send the device IANA timezone (`Intl.DateTimeFormat().resolvedOptions().timeZone`)
  with prefs. Deprecated aliases (e.g. `Asia/Calcutta`) are accepted.
- "Daily Brief" → **"Daily Roundup"** in any remaining app copy (backend
  prompts + push text already renamed).
- Prefs edit sheet: `PUT /brief/preferences` REPLACES the topic set (
  deselection works); omit location fields when only changing beats/schedule.

---

## Quick contract reference

| Endpoint | Offline-relevant behavior |
|---|---|
| `PUT /brief/preferences` | coords→reverse-geocode; nulls→clear; omit→keep (§2, after deploy) |
| `GET /brief/preferences` | prefill; includes `location_name`, `scheduled_time`, `timezone` |
| `GET /brief/today` | pending/generating/ready states; segments+timings; **ETag** |
| `GET /brief/today/audio` | presigned URL (Range OK) or `?token=` for native players |
| `PUT /brief/{id}/progress` | `client_ts` replay guard; 2xx = delivered |
| `GET /episodes`, `GET /episodes/{id}` | **ETag/304** |
| `GET /episodes/{id}/audio` | presigned URL (Range OK), `?token=` fallback |
| `PUT /episodes/{id}/progress` | `client_ts` replay guard; 2xx = delivered |
