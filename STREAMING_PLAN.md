# Streaming Playback Plan — play as soon as the script lands

**Goal:** an episode becomes playable the moment its script is written. The user taps Play and audio streams while TTS is still synthesizing the rest. Duration/chapters and other derived data appear when full generation completes. Music-bed mixing is explicitly **out of scope** for v1.

---

## 1. Current state (reviewed 2026-06-13)

### Backend (`curia-v2`)

- **Pipeline** (`studio/generator.py`, ~1180 lines): one monolithic `process_episode` — LLM script → per-segment TTS (`segment_NNNN.wav` in a tmpdir) → ffmpeg stitch → `EPISODES_DIR/{id}.mp3` → R2 upload (`audio/{id}.mp3` via `core/storage/blob.py`) → single DB update setting `status='ready'`, `audio_url`, transcript, outline, etc.
- **Status machine:** `queued → ready | failed` (set in `_set_episode_status` / final UPDATE around `generator.py:1064`).
- **Episode table** already has: `duration_seconds`, `description`, `chapters JSONB` (migration 0019), `audio_url` (0024), `length_minutes` (target length), server-side `play_progress`.
- **Audio serving** (`api/routes/episodes.py:259`): `GET /episodes/{id}/audio` returns `{url: <presigned R2, 1h>}` for R2 episodes, or streams the local file with Range support. Token via `?token=` query param is already supported because **RNTP cannot send custom headers** (noted in frontend code).
- **Prior art:** `api/routes/stream.py` + `core/audio/stream_manager.py` — a WebSocket endpoint that synthesizes segment-by-segment on demand and streams WAV bytes. It requires `status='ready'`, and WS binary audio cannot feed react-native-track-player. Treat as legacy/demo; its `core/audio/splitter.py` / `merger.py` utilities are reusable.

### Frontend (`curia-frontend`)

- **Playback** (`context/PlaybackContext.tsx`): on play, fetches `GET /episodes/{id}/audio`, hands the resulting URL to TrackPlayer. Notably, duration handling **already has estimate-then-correct semantics**: it seeds `durationSecs = show.duration * 60` (from `length_minutes`) and replaces it with TrackPlayer's real duration once the asset loads (`PlaybackContext.tsx:112–124`).
- **Scrubber** (`app/now-playing/[id].tsx`, `app/(tabs)/player/[id].tsx`): driven by `progress` (0–1 fraction) + `duration`; chapters positioned by `startMinute / duration`.
- **Resume:** `play_progress` is a *fraction*, persisted server-side, applied as `seedProgress * duration`.
- **Cards** already display a duration-ish number sourced from `length_minutes` (target length).

### What the review changed vs. the naive design

1. **Estimated duration already half-exists.** Cards show `length_minutes` today. We refine, not invent: add a script-derived `duration_estimate_seconds` (chars ÷ calibrated chars-per-second per voice) for better accuracy in the player; cards can keep `length_minutes`.
2. **Auth shapes the HLS design.** RNTP can't send headers and presigned URLs expire in 1h. The playlist must be served by the API (`?token=`) and reference **presigned segment URLs**, regenerated on each playlist fetch. Don't try to host the playlist itself on R2.
3. **Resume-as-fraction is a footgun.** `play_progress` is a fraction of a duration that *changes* (estimate → real). Migrate to storing **position in seconds**; fraction is derived for UI.
4. **The old WS streaming endpoint is superseded** for the app. Mark deprecated in v1 of this plan; delete after HLS ships.

---

## 2. Target architecture

### State machine

```
queued → script_ready → synthesizing → ready
   \________\_______________\__________→ failed (with stage context)
```

- `script_ready`: transcript/outline/title/description committed. Episode visible & playable in the app. `duration_estimate_seconds` set.
- `synthesizing`: first audio chunk published; `stream_url` live. Listeners may be playing.
- `ready`: final MP3 on R2 (`audio_url`), exact `duration_seconds`, `chapters` populated.
- `failed` records which stage died. Script-stage failure ⇒ retry from scratch; audio-stage failure ⇒ retry synthesis only (script is kept).

### TTS trigger

**Eager:** synthesis starts immediately when the script lands (same worker job, next stage). Lazy-on-first-play is a future cost optimization, not v1.

### Delivery: HLS event playlist, API-served

- Synthesis loop publishes each finished segment: WAV → AAC HLS chunk → R2 under `audio/{episode_id}/live/seg_NNNN.aac` (or `.ts`).
- New endpoint `GET /episodes/{id}/stream.m3u8?token=…`:
  - returns an `#EXT-X-PLAYLIST-TYPE:EVENT` playlist listing all published chunks as **presigned R2 URLs** (regenerated per request — sidesteps both auth-header and expiry problems),
  - `Cache-Control: no-store` while synthesizing,
  - appends `#EXT-X-ENDLIST` once synthesis completes (playlist becomes a normal VOD; same URL keeps working forever).
- Player consumes it natively (AVPlayer on iOS, ExoPlayer on Android). Seekable range grows automatically as chunks land. Synthesis is much faster than real-time (Lightning), so the frontier always outruns the listener.
- `GET /episodes/{id}/audio` becomes the dispatch point: `ready` ⇒ presigned MP3 (today's behavior); `script_ready`/`synthesizing` ⇒ `{url: <stream.m3u8 with token>, streaming: true}`. **The client stays dumb: it plays whatever URL it gets.**

---

## 3. Backend phases

**Phase B1 — state machine split** (shippable alone; invisible to old clients)
- Alembic: new statuses + `duration_estimate_seconds INTEGER`, `failed_stage TEXT`.
- Split `process_episode` into `generate_script` (commits `script_ready` + estimate) and `produce_audio` (synthesizing → ready). Worker chains them.
- API compatibility shim: map `script_ready`/`synthesizing` → `queued` for app versions that don't know them (version header or rollout flag).

**Phase B2 — incremental publishing**
- In the per-segment loop (`generator.py` ~574): after each segment WAV, transcode to HLS chunk (ffmpeg, no music bed) and upload to `audio/{id}/live/`. On first chunk: `status='synthesizing'`.
- Track published chunks + their durations in a small DB column or sidecar JSON (the playlist source of truth).
- Per-segment retry ×2–3; on terminal failure: write ENDLIST early, set `failed` (stage=audio), keep chunks for diagnosis.

**Phase B3 — playlist endpoint**
- `GET /episodes/{id}/stream.m3u8` per §2. Reuses `_audio_user_id` token auth.
- `GET /episodes/{id}/audio` dispatch logic.

**Phase B4 — finalization**
- After last segment: existing stitch → MP3 → R2 (unchanged), compute exact `duration_seconds`, derive `chapters` from outline segment boundaries, write ENDLIST, single UPDATE to `ready`.
- Retention decision (default: keep `live/` chunks 7 days, then GC; `ready` clients use the MP3).

**Phase B5 — API schema**
- `EpisodeSummary`/`EpisodeDetail` (+ list endpoint): expose `status` (new values), `duration_estimate_seconds`, nullable exact fields. Clients must tolerate nulls for `chapters`/`duration_seconds`.
- Resume: new writes store `play_position_seconds`; keep `play_progress` populated for old clients during transition.

---

## 4. Frontend phases

**Phase F1 — episode cards**
- `script_ready`/`synthesizing`: card fully rendered (title/description/artwork), Play enabled, duration slot shows `~{estimate}` (tilde styling) or `length_minutes` as today, plus a subtle "generating" indicator.
- `ready`: exact duration (existing behavior).
- `queued`: unchanged.

**Phase F2 — PlaybackContext**
- No transport changes needed: it already fetches `/audio` and plays the returned URL; HLS URL flows through the same path.
- Duration: seed from `duration_estimate_seconds` (better than `length_minutes * 60`); when TrackPlayer reports real duration (HLS frontier or final MP3), prefer it — the estimate-then-correct logic at `PlaybackContext.tsx:112` mostly already does this. Subtlety: for an *event* playlist the player-reported duration = current frontier, not the whole episode — keep UI total = estimate while `status != 'ready'`.
- Resume in seconds (see B5); applying `seekTo(seconds)` directly removes the fraction/duration coupling.

**Phase F3 — player screen (now-playing)**
- Scrubber, synthesizing mode: track length = estimated total; **bright region = synthesized frontier** (from player's seekable range), dimmed beyond — YouTube-buffer style. Seek clamped to frontier.
- Right time label: `~total` (estimate) instead of exact remaining; chapters drawer hidden with "Chapters appear when generation finishes."
- Lock screen/CarPlay: elapsed-only while synthesizing (comes from player state).

**Phase F4 — the upgrade moment**
- While player screen shows a `synthesizing` episode: poll episode detail ~15s (or reuse existing refresh).
- On `ready`: in-place metadata refresh — exact duration, chapters, scrubber end snaps. **No source swap mid-playback** (same URL still serves the full episode after ENDLIST). Switch to the MP3 URL lazily on next session.
- Feed upgrades on normal list refresh.

**Phase F5 — edge UX**
- Audio-stage failure mid-listen: playlist ends early; show "Generation hit a snag — rebuilding" on the episode instead of a silent stop.
- Download/offline: `ready` only.
- Regenerated episodes: keep saved position, accept ±seconds fuzz (TTS is nondeterministic).

---

## 5. Resume semantics (close & return)

Synthesis is server-driven — closing the app changes nothing server-side. Resume is the same three steps in every state: fetch status → load URL from `/audio` → `seekTo(saved seconds)`.

- Return while still `synthesizing`: playlist grew; frontier is further ahead; position (always ≤ frontier, since you listened there) seeks instantly.
- Return after completion: normal `ready` episode, same timeline, same position.
- Return after failure: "rebuilding" state; position kept (approximate after regen).

---

## 6. Validation before building

1. **Measure script-land → first-chunk-playable** on a real episode (synthesize seg 1 → transcode → upload → playlist fetch). Target ≤3s; if ≫, add a "warming up" interstitial to F3.
2. **RNTP + HLS event playlist smoke test** on both platforms (iOS AVPlayer is safe; verify ExoPlayer behavior with a growing playlist, and that the rntp 4.1.2 patch doesn't affect HLS).
3. **Confirm Lightning real-time factor** under production concurrency (frontier must outrun playback).
4. Calibrate chars-per-second per voice from existing `ready` episodes for `duration_estimate_seconds`.

## 7. Out of scope / later

- Music bed & crossfades in the streamed path (final MP3 keeps them if the stitch already does; the live stream is voice-only until incremental mixing is designed).
- Lazy synthesis-on-first-play (cost optimization).
- Deleting the legacy WS endpoint (`api/routes/stream.py`) — after HLS ships.
- Live chapter data before completion (outline-based provisional chapters are possible later: boundaries are known per segment as it publishes).
