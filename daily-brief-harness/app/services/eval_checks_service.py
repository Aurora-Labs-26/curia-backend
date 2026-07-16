"""
M1 of daily-brief-eval-buildplan.md: deterministic (zero-LLM-cost) quality
checks over the data eval_logging_service.py already captured. Pure Python,
no network/LLM calls — regex/word-count/substring checks only. Results are
written to harness.eval_results as pass/fail/flag per check per run.

Like eval_logging_service, this must never break a real brief generation:
run_checks_for_run() catches its own exceptions and logs a warning instead of
raising. It's meant to be called right after finish_eval_run(), on both the
success and failure path, in preopt_runner.py/user_brief_runner.py.

Kind-aware: a "preopt" run only ever produces segment transcripts (no
meta-segments, since Pre-Opt never generates intro/outro), so
personalization/duration checks are skipped for it. A "regenerate_bookends"
run only ever produces a fresh meta-segments row (its 5 articles are reused
untouched, so nothing new is logged to eval_segment_transcripts for it — see
user_brief_runner._regenerate_bookends_for_brief) — so segment/structure/
duration checks are skipped for it, but personalization/TTS-readiness checks
on the fresh intro/outro still run.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from app.services import eval_logging_service
from app.services.pipeline import SEGMENT_WORD_RANGES

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TTS-readiness: markdown/URL artifacts that would read aloud badly verbatim
# ---------------------------------------------------------------------------

_MD_LINK_RE = re.compile(r"\[[^\]]+\]\([^)]+\)")
_MD_BOLD_ITALIC_RE = re.compile(r"(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|__[^_]+__|(?<!\w)_[^_\s][^_]*_(?!\w))")
_MD_HEADER_RE = re.compile(r"(?m)^#{1,6}\s")
_BACKTICK_RE = re.compile(r"`")
_RAW_URL_RE = re.compile(r"https?://\S+")


def check_tts_markdown_artifacts(text: str, label: str) -> Dict[str, Any]:
    text = text or ""
    found = []
    if _MD_LINK_RE.search(text):
        found.append("markdown_link")
    if _MD_BOLD_ITALIC_RE.search(text):
        found.append("bold_italic")
    if _MD_HEADER_RE.search(text):
        found.append("header")
    if _BACKTICK_RE.search(text):
        found.append("backtick")
    if _RAW_URL_RE.search(text):
        found.append("raw_url")
    status = "fail" if found else "pass"
    return {
        "check_name": "tts_markdown_artifacts",
        "check_type": "deterministic",
        "result_status": status,
        "detail": f"{label}: found {', '.join(found)}" if found else None,
    }


# ---------------------------------------------------------------------------
# TTS-readiness: number/date/acronym normalization — flag-for-review only,
# per the buildplan ("this can be a flag-for-review list rather than a hard
# fail initially") — these are common, often legitimate in spoken news.
# ---------------------------------------------------------------------------

_DOLLAR_AMOUNT_RE = re.compile(r"\$[\d,]+(?:\.\d+)?\s?[BMK]\b")
_QUARTER_RE = re.compile(r"\bQ[1-4]\b")
_ACRONYM_RE = re.compile(r"\b[A-Z]{2,6}\b")


def check_tts_normalization_flags(text: str, label: str) -> Dict[str, Any]:
    text = text or ""
    found = []
    if _DOLLAR_AMOUNT_RE.search(text):
        found.append("unexpanded_dollar_amount")
    if _QUARTER_RE.search(text):
        found.append("quarter_reference")
    acronyms = sorted(set(_ACRONYM_RE.findall(text)))
    if acronyms:
        found.append(f"acronyms:{','.join(acronyms)}")
    return {
        "check_name": "tts_normalization_flags",
        "check_type": "deterministic",
        "result_status": "flag" if found else "pass",
        "detail": f"{label}: {'; '.join(found)}" if found else None,
    }


# ---------------------------------------------------------------------------
# Structure/degradation: empty text or a leaked LLM refusal/error string
# ---------------------------------------------------------------------------

_ERROR_LEAK_PHRASES = [
    "as an ai", "as a language model", "i don't have access", "i do not have access",
    "i couldn't find", "i could not find", "i cannot access", "i'm unable to",
    "i am unable to", "i don't have the ability", "i do not have the ability",
]


def check_no_empty_or_error_leak(text: str, label: str) -> Dict[str, Any]:
    stripped = (text or "").strip()
    if not stripped:
        return {
            "check_name": "no_empty_or_error_leak",
            "check_type": "deterministic",
            "result_status": "fail",
            "detail": f"{label}: empty text",
        }
    lowered = stripped.lower()
    for phrase in _ERROR_LEAK_PHRASES:
        if phrase in lowered:
            return {
                "check_name": "no_empty_or_error_leak",
                "check_type": "deterministic",
                "result_status": "fail",
                "detail": f"{label}: contains error-leak phrase '{phrase}'",
            }
    return {"check_name": "no_empty_or_error_leak", "check_type": "deterministic", "result_status": "pass"}


# ---------------------------------------------------------------------------
# Budget/duration
# ---------------------------------------------------------------------------


# How much slack beyond [low, high] still counts as acceptable — the user's
# own stated tolerance for this check, not a hard product requirement.
_WORD_BUDGET_TOLERANCE = 0.10


def check_segment_word_budget(segment_type: str, word_count: Optional[int]) -> Dict[str, Any]:
    low, high = SEGMENT_WORD_RANGES.get(segment_type, SEGMENT_WORD_RANGES["standard"])
    if word_count is None:
        return {
            "check_name": "segment_word_budget",
            "check_type": "deterministic",
            "result_status": "flag",
            "detail": f"{segment_type}: word_count missing",
        }
    tolerant_low = low * (1 - _WORD_BUDGET_TOLERANCE)
    tolerant_high = high * (1 + _WORD_BUDGET_TOLERANCE)
    in_range = tolerant_low <= word_count <= tolerant_high
    return {
        "check_name": "segment_word_budget",
        "check_type": "deterministic",
        "result_status": "pass" if in_range else "flag",
        "result_score": float(word_count),
        "detail": (
            None
            if in_range
            else f"{segment_type}: {word_count} words, expected [{low}, {high}] (±{int(_WORD_BUDGET_TOLERANCE * 100)}% tolerance)"
        ),
    }


# Matches cache_service.estimate_duration_s's default wpm — no real TTS is
# wired in yet (legacy/tts_service.py, archived), so this is an estimate, not
# a measured rate. Revisit once real TTS output exists (see M0/M1 buildplan
# open questions).
_WPM_ESTIMATE = 150
_TARGET_DURATION_MIN_S = 5 * 60
_TARGET_DURATION_MAX_S = 6 * 60


def check_total_duration_band(total_word_count: int) -> Dict[str, Any]:
    duration_s = (total_word_count / _WPM_ESTIMATE) * 60
    in_band = _TARGET_DURATION_MIN_S <= duration_s <= _TARGET_DURATION_MAX_S
    return {
        "check_name": "total_duration_band",
        "check_type": "deterministic",
        "result_status": "pass" if in_band else "flag",
        "result_score": round(duration_s, 1),
        "detail": (
            None
            if in_band
            else f"estimated duration {round(duration_s, 1)}s outside 300-360s band "
            f"(estimated @ {_WPM_ESTIMATE}wpm — no real TTS output yet)"
        ),
    }


# ---------------------------------------------------------------------------
# Structure: exactly 1 lead, <=3 standard, <=1 local
# ---------------------------------------------------------------------------


def check_structure_counts(segment_types: List[str]) -> List[Dict[str, Any]]:
    lead_count = segment_types.count("lead")
    standard_count = segment_types.count("standard")
    local_count = segment_types.count("local")
    return [
        {
            "check_name": "structure_lead_count",
            "check_type": "deterministic",
            "result_status": "pass" if lead_count == 1 else "fail",
            "result_score": float(lead_count),
            "detail": None if lead_count == 1 else f"expected exactly 1 lead segment, found {lead_count}",
        },
        {
            "check_name": "structure_standard_count",
            "check_type": "deterministic",
            "result_status": "pass" if standard_count <= 3 else "fail",
            "result_score": float(standard_count),
            "detail": None if standard_count <= 3 else f"expected at most 3 standard segments, found {standard_count}",
        },
        {
            "check_name": "structure_local_count",
            "check_type": "deterministic",
            "result_status": "pass" if local_count <= 1 else "fail",
            "result_score": float(local_count),
            "detail": None if local_count <= 1 else f"expected at most 1 local segment, found {local_count}",
        },
    ]


# ---------------------------------------------------------------------------
# Personalization correctness (Intro only)
# ---------------------------------------------------------------------------


def check_personalization_name(intro_text: str, display_name: str) -> Dict[str, Any]:
    ok = bool(display_name) and display_name.strip().lower() in (intro_text or "").lower()
    return {
        "check_name": "personalization_name",
        "check_type": "deterministic",
        "result_status": "pass" if ok else "fail",
        "detail": None if ok else f"display_name '{display_name}' not found in intro",
    }


def check_personalization_location(intro_text: str, location_name: str) -> Dict[str, Any]:
    ok = bool(location_name) and location_name.strip().lower() in (intro_text or "").lower()
    return {
        "check_name": "personalization_location",
        "check_type": "deterministic",
        "result_status": "pass" if ok else "fail",
        "detail": None if ok else f"location_name '{location_name}' not found in intro",
    }


_TEMP_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*°?\s*[CF]?")


def check_personalization_weather(intro_text: str, weather: str) -> Dict[str, Any]:
    if not weather:
        return {"check_name": "personalization_weather", "check_type": "deterministic", "result_status": "pass"}
    intro_text = intro_text or ""
    lowered_intro = intro_text.lower()
    condition_words = [w for w in re.findall(r"[A-Za-z]+", weather) if len(w) > 3]
    condition_hit = any(w.lower() in lowered_intro for w in condition_words)
    temp_hit = False
    temp_match = _TEMP_RE.search(weather)
    if temp_match:
        temp_int_part = temp_match.group(1).split(".")[0]
        temp_hit = temp_int_part in intro_text
    ok = condition_hit or temp_hit
    return {
        "check_name": "personalization_weather",
        "check_type": "deterministic",
        "result_status": "pass" if ok else "flag",
        "detail": None if ok else f"weather input '{weather}' not clearly reflected in intro",
    }


_GREETING_KEYWORDS = {
    "morning": ["morning"],
    "afternoon": ["afternoon"],
    "evening": ["evening", "night"],
    "night": ["night", "evening"],
}


def _time_bucket(local_time: str) -> Optional[str]:
    """local_time like "2026-07-10 08:20" (see weather_service.get_weather_and_local_time)."""
    try:
        hh = int(local_time.strip().split(" ")[1].split(":")[0])
    except Exception:
        return None
    if 5 <= hh < 12:
        return "morning"
    if 12 <= hh < 17:
        return "afternoon"
    if 17 <= hh < 22:
        return "evening"
    return "night"


def check_personalization_time_greeting(intro_text: str, local_time: str) -> Dict[str, Any]:
    bucket = _time_bucket(local_time or "")
    if bucket is None:
        return {"check_name": "personalization_time_greeting", "check_type": "deterministic", "result_status": "pass"}
    lowered = (intro_text or "").lower()
    ok = any(kw in lowered for kw in _GREETING_KEYWORDS[bucket])
    return {
        "check_name": "personalization_time_greeting",
        "check_type": "deterministic",
        "result_status": "pass" if ok else "flag",
        "detail": None if ok else f"expected a '{bucket}'-appropriate greeting for local_time={local_time}",
    }


# ---------------------------------------------------------------------------
# Ops health
# ---------------------------------------------------------------------------


def check_ops_run_completed(status: str, error: Optional[str]) -> Dict[str, Any]:
    ok = status == "completed"
    return {
        "check_name": "ops_run_completed",
        "check_type": "deterministic",
        "result_status": "pass" if ok else "fail",
        "detail": error if not ok else None,
    }


def check_ops_cost_summary(segments: List[Dict[str, Any]], meta: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    total_latency = sum((s.get("latency_ms") or 0) for s in segments)
    total_input_tokens = sum((s.get("input_tokens") or 0) for s in segments)
    total_output_tokens = sum((s.get("output_tokens") or 0) for s in segments)
    if meta:
        total_latency += meta.get("latency_ms") or 0
        total_input_tokens += meta.get("input_tokens") or 0
        total_output_tokens += meta.get("output_tokens") or 0
    return {
        "check_name": "ops_cost_summary",
        "check_type": "deterministic",
        "result_status": "pass",
        "result_score": float(total_latency),
        "detail": f"total_latency_ms={total_latency}, total_input_tokens={total_input_tokens}, total_output_tokens={total_output_tokens}",
    }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def run_checks_for_run(run_id: Optional[str]) -> None:
    """Reads one run's logged data and writes every applicable check's result
    to harness.eval_results. Never raises — a bug here must not affect the
    real pipeline call it's attached to."""
    if run_id is None:
        return
    try:
        run = await eval_logging_service.get_run_for_checks(run_id)
        if run is None:
            return

        results: List[Dict[str, Any]] = [check_ops_run_completed(run["status"], run["error"])]

        segments = run["segments"]
        for seg in segments:
            text, seg_type = seg["text"], seg["segment_type"]
            results.append(check_tts_markdown_artifacts(text, seg_type))
            results.append(check_tts_normalization_flags(text, seg_type))
            results.append(check_no_empty_or_error_leak(text, seg_type))
            results.append(check_segment_word_budget(seg_type, seg["word_count"]))

        if segments:
            results.extend(check_structure_counts([s["segment_type"] for s in segments]))

        meta = run["meta"]
        total_word_count = sum((s["word_count"] or 0) for s in segments)
        if meta:
            inputs = meta["inputs_used"] or {}
            intro_text = meta["intro_text"] or ""
            for label, text in [
                ("intro", meta["intro_text"]),
                ("outro", meta["outro_text"]),
            ]:
                results.append(check_tts_markdown_artifacts(text, label))
                results.append(check_tts_normalization_flags(text, label))
                results.append(check_no_empty_or_error_leak(text, label))
                total_word_count += len((text or "").split())

            results.append(check_personalization_name(intro_text, inputs.get("display_name", "")))
            results.append(check_personalization_location(intro_text, inputs.get("location_name", "")))
            results.append(check_personalization_weather(intro_text, inputs.get("weather", "")))
            results.append(check_personalization_time_greeting(intro_text, inputs.get("local_time", "")))

        # Only a full "generate_brief" run has both fresh segments AND fresh
        # bookends together — Pre-Opt never produces bookends, and a
        # regenerate_bookends run reuses (doesn't re-log) its segments, so
        # `segments` is empty there and this total would be misleadingly short.
        if run["kind"] == "generate_brief" and meta and segments:
            results.append(check_total_duration_band(total_word_count))

        results.append(check_ops_cost_summary(segments, meta))

        for r in results:
            await eval_logging_service.log_eval_result(
                run_id,
                r["check_name"],
                r["check_type"],
                result_status=r.get("result_status"),
                result_score=r.get("result_score"),
                detail=r.get("detail"),
            )
    except Exception as e:
        logger.warning(f"eval_checks_service.run_checks_for_run failed (run_id={run_id}): {e}")
