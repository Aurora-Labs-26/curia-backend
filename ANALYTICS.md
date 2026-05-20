# Curia — Analytics Plan

**Provider:** PostHog (primary) — MCP server available, generous free tier, self-hostable.
**Architecture:** Provider-agnostic adapter layer — swap or fan-out to multiple SDKs without touching call sites.

---

## Adapter Architecture

All event calls go through `services/analytics.ts` — never raw SDK calls at call sites.

```
call site
  └── Analytics.episodePlayed({ ... })        ← typed, required fields enforced
        └── AnalyticsProvider (interface)
              ├── PostHogProvider              ← active
              ├── MixpanelProvider             ← plug in later
              └── ConsoleProvider              ← dev/debug, zero-cost
```

To add a second SDK (e.g. Mixpanel for accuracy comparison): implement the interface, add it to the provider list in `analytics.ts` — zero changes at call sites.
To swap entirely: swap the provider in one place.

---

## Global Properties (Super Properties)

Attached automatically to **every** event via PostHog's `register()` on session start.

### Identity
| Property | Type | Notes |
|---|---|---|
| `user_id` | string | Firebase UID — must match backend `distinct_id` exactly |
| `user_created_at` | ISO timestamp | Enables cohort analysis |

### Device & Platform
| Property | Type | Example |
|---|---|---|
| `platform` | `ios` \| `android` | |
| `app_version` | string | `"2.2.1"` |
| `build_number` | string | For bug correlation |
| `device_model` | string | `"iPhone 15 Pro"`, `"Pixel 8a"` |
| `os_version` | string | `"iOS 18.3"`, `"Android 14"` |

### Location & Locale
| Property | Type | Notes |
|---|---|---|
| `country` | string | PostHog auto-resolves from IP |
| `timezone` | string | `"Asia/Kolkata"` — critical for time-of-day analysis |
| `locale` | string | `"en-IN"`, `"en-US"` |

### Session
| Property | Type | Notes |
|---|---|---|
| `session_id` | UUID | New per app foreground — reconstruct full session |

### User State Snapshot (via `identify()`, updated each session start)
| Property | Type | Notes |
|---|---|---|
| `total_sources` | number | Enables power-user segmentation |
| `total_episodes` | number | |
| `signup_method` | `google` \| `apple` \| `email` | |
| `signup_cohort_week` | string | e.g. `"2026-W21"` — derived from `user_created_at`, used for retention slicing |

> **Note:** Call `identify()` on both sign-in AND on app launch when a session already exists (returning user). Without this, returning users won't have user properties attached until their next explicit sign-in.

---

## Events by Phase

### Phase 0 — App Launch

> PostHog auto-captures `$app_opened` with first-launch detection. Do NOT fire a custom `app_opened` event — it will double-count. Disable PostHog autocapture for this event if you need custom properties; otherwise rely on `$app_opened`.

| Event | Key Properties | Notes |
|---|---|---|
| `sign_up_started` | `method: google\|apple\|email` | |
| `sign_up_completed` | `method` | Funnel entry point |
| `sign_in_completed` | `method`, `is_returning_user: bool` | |

---

### Phase 1 — Source Ingestion

Activation step — first source added = user "gets it".

| Event | Key Properties |
|---|---|
| `source_add_initiated` | `trigger: share_extension\|in_app_plus\|deep_link` |
| `source_url_validated` | `valid: bool`, `rejection_reason?: blocked_domain\|video\|social\|paywall\|...` |
| `source_save_attempted` | `url_domain: string`, `trigger` |
| `source_save_succeeded` | `url_domain`, `trigger`, `is_duplicate: bool` |
| `source_save_failed` | `url_domain`, `trigger`, `error_type: network\|paywall\|scrape_failed\|...` |
| `share_customize_tapped` | `trigger` |
| `share_dismissed` | `trigger`, `auto_dismissed: bool`, `time_held_ms: number`, `customized: bool` |
| `source_deleted` | `url_domain` |

**Slice charts by:** `url_domain` (which publishers produce good episodes), `trigger` (share extension vs in-app adoption)

---

### Phase 2 — Re-engagement (Notifications)

The re-engagement loop — untracked means you can't explain low D7.

| Event | Key Properties |
|---|---|
| `notification_received` | `episode_id`, `notification_type: episode_ready\|...` |
| `notification_tapped` | `episode_id`, `notification_type`, `latency_ms` (time from received → tap) |

---

### Phase 3 — Episode Generation

Mostly passive (backend-driven), but user sees state changes. Backend fires these events server-side using the PostHog Python SDK.

> **Backend attribution:** Backend must use Firebase UID as `distinct_id` on all server-side events — pulled from the JWT on each API request. This must match the frontend's `identify()` call exactly. Mismatch breaks all backend→frontend event joins in PostHog.

| Event | Key Properties |
|---|---|
| `episode_generation_started` | `show_id`, `show_format`, `speaker`, `source_count`, `length_minutes`, `has_angle_override: bool` |
| `episode_generation_completed` | `show_id`, `show_format`, `speaker`, `actual_length_minutes`, `duration_ms` |
| `episode_generation_failed` | `show_id`, `show_format`, `error_type: tts_limit\|llm_error\|scrape_failed\|...` |
| `episode_deleted` | `episode_id`, `show_format`, `speaker` |

**Slice charts by:** `show_format` (which formats fail more), `error_type`, `source_count`

---

### Phase 4 — Listening

Where retention signals live. Remix sits here because it's triggered from the player.

#### `listen_session_id` lifecycle

New UUID on each play press. Persists through pause/resume within the same play session. If the app is killed and the user resumes later, that's a new `listen_session_id` with `is_resume: true` and `resume_from_pct > 0`. This lets you compute true per-session listen time from `elapsed_ms` across events.

#### Abandonment definition

Do NOT fire `episode_abandoned` as a real-time event — it creates a naming conflict with legitimate pauses. Instead, derive abandonment in PostHog as a funnel: `episode_play_started` without a matching `episode_completed` within 7 days. Use `abandoned_at_pct` only if you need it for segmentation, not as a trigger.

| Event | Key Properties |
|---|---|
| `episode_play_started` | `episode_id`, `show_id`, `show_format`, `speaker`, `source_count`, `episode_length_min`, `has_angle_override`, `is_remixed`, `is_first_episode`, `is_resume: bool`, `resume_from_pct`, `player_mode: mini\|full_screen` |
| `episode_paused` | `episode_id`, `listen_session_id`, `progress_pct`, `elapsed_ms` |
| `episode_completed` | `episode_id`, `listen_session_id`, `show_format`, `speaker`, `elapsed_ms` |
| `chapter_tapped` | `episode_id`, `chapter_index`, `chapter_title`, `show_format` |
| `episode_remixed` | `episode_id`, `show_id`, `changed_fields: string[]` (e.g. `["speaker","format"]`) |

**Slice charts by:** `show_format`, `speaker`, `episode_length_min`, `is_first_episode` (AHA moment), `country`, `timezone`

---

### Phase 5 — Navigation (session-level)

| Event | Key Properties |
|---|---|
| `shows_tab_viewed` | _(super props only)_ |
| `pile_tab_viewed` | _(super props only)_ |
| `now_playing_opened` | `episode_id`, `trigger: mini_player\|notification` |

---

## Key Metrics

### Activation
| Metric | Derivation |
|---|---|
| Activation rate | % users: `source_save_succeeded where is_duplicate=false` within first session |
| Time to first source | `sign_up_completed` → first `source_save_succeeded` (minutes) — immediate, user-controlled |
| Time to first listen | `sign_up_completed` → first `episode_play_started` (minutes) — includes pipeline latency |
| Share extension adoption | `source_add_initiated[trigger=share_extension]` / total initiations |

> `time_to_first_source` is the true activation signal — it's immediate and fully in the user's control. `time_to_first_listen` includes scrape + LLM + TTS latency that the user waits on passively.

### Funnel Health
| Metric | Derivation |
|---|---|
| Source scrape success rate | `source_save_succeeded` / `source_save_attempted` |
| Episode generation success rate | `episode_generation_completed` / `episode_generation_started` |
| Customize rate | `share_customize_tapped` / `source_save_succeeded` |
| Notification tap rate | `notification_tapped` / `notification_received` |

### Engagement
| Metric | Derivation |
|---|---|
| Episode completion rate | `episode_completed` / `episode_play_started` |
| Abandonment rate | `episode_play_started` with no `episode_completed` within 7 days |
| Remix rate | `episode_remixed` / `episode_play_started` |
| Sources per user per week | `source_save_succeeded where is_duplicate=false` grouped by user × week |

### Retention
| Metric | Derivation |
|---|---|
| D1 / D7 / D30 retention | % users with ≥1 `episode_play_started` on day N after signup — PostHog retention chart |
| Weekly active listeners | Distinct users with ≥1 `episode_play_started` per week |

> Retention is defined as returning to **listen**, not just opening the app. Configure PostHog retention chart with `episode_play_started` as the returning event.

### Power User Segmentation
Slice any metric by `total_sources` bucket. **Provisional thresholds — revisit after first 100 users.**
- New: 0–4 sources
- Growing: 5–19 sources
- Power: 20+ sources

---

## Key Slicing Combos for Core Charts

| Chart | Metric | Slice by |
|---|---|---|
| Episode completion funnel | Completion rate | `show_format` |
| Speaker engagement | Avg listen depth (progress at last pause) | `speaker` |
| Source quality | Scrape success rate | `url_domain` |
| Retention cohorts | D7 return rate | `signup_cohort_week`, `platform` |
| AHA moment | Time to first source | `signup_method`, `platform` |
| Format preference | Plays per format | `show_format`, `country` |
| Share funnel drop-off | Customize rate | `trigger` |
| Publisher signal | Episode completion rate | `url_domain` |
| Re-engagement | Notification tap rate | `notification_type` |

---

## Implementation Plan

**Order (highest signal first):**

1. **PostHog setup** — install `posthog-react-native`, wrap app in `PostHogProvider`, configure super properties, call `identify()` on session start (not just sign-in)
2. **`services/analytics.ts`** — typed adapter interface + PostHog implementation + Console dev provider; disable PostHog autocapture for `$app_opened` if custom properties needed
3. **Source events** — `source_add_initiated` → `source_save_succeeded/failed` → `share_dismissed`
4. **Listening events** — `episode_play_started`, `episode_paused`, `episode_completed` with `listen_session_id`
5. **Notification events** — `notification_received`, `notification_tapped`
6. **Episode generation events (backend)** — PostHog Python SDK, Firebase UID as `distinct_id`
7. **Onboarding events** — `sign_up_completed`, `sign_in_completed`
8. **Navigation events** — tab views (lowest priority)
