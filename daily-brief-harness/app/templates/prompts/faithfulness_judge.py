# ---------------------------------------------------------
# M3 JUDGE: FAITHFULNESS (article-sourced + bookend-sourced variants)
# ---------------------------------------------------------
# Bookend variant: checked against the structured brief inputs
# (name/location/weather/story picks) using FAITHFULNESS_SEVERITY_TAXONOMY —
# critical/moderate/stylistic, gate on zero critical. Combined into one LLM
# call (2026-07-14) that judges intro and outro together instead of two
# separate calls — intro/outro always come from the same eval_meta_segments
# row (generated together, same inputs_used), so there's nothing to lose by
# judging them side by side. eval_judges_service.py still logs two separate
# eval_judge_outputs rows (segment_type=intro / outro) from the one call's
# response, so every downstream consumer (dashboard, rollup counts) is
# unaffected — only the LLM call count changed, not the output shape.
#
# Article variant (redesigned 2026-07-14, see faithfulness-judge-plan.md):
# the source article is reference context, not ground truth — claims are
# checked against the real world via the model's own web search tool
# (enabled on this call site only, see llm_service.py step_id==10), not
# against whether they appear in the one source article. Uses its own
# FAITHFULNESS_SEVERITY_TAXONOMY_ARTICLE (critical/moderate/unverifiable/
# stylistic) — do not reuse the bookend taxonomy here, and do not point the
# bookend judge at this one: there's no "real world" to web-search a
# listener's name or local weather against.
#
# No dedicated M2 gold labels exist for faithfulness — validation is a
# human spot-check in the Gold Set "Judges" sub-tab, not a computed kappa.

# ---------------------------------------------------------

FAITHFULNESS_SEVERITY_TAXONOMY = """
Classify every factual claim in the segment against the source,
using exactly three severity levels:

1. "critical" — a fabricated fact, entity, number, date, or quote that is not present in, or is directly contradicted by, the source. This includes invented statistics, wrong dates, misattributed quotes, or people/organizations that don't appear in the source.
2. "moderate" — an unsupported inference or editorializing claim that goes beyond what the source actually says, but isn't a fabrication (a reasonable-sounding extrapolation, a claim of significance the source doesn't make, unwarranted causal language).
3. "stylistic" — conversational framing, connective tissue, or paraphrase that carries no independent factual claim at all (e.g. "which is kind of a big deal", "here's the thing"). These are never flagged as claims — do not include them in your claims list.

Only list claims that are "critical" or "moderate".
If the segment has no critical or moderate claims,
return an empty claims list."""

# ---------------------------------------------------------
# Article-only taxonomy — widened to add "unverifiable" for claims that are
# plausibly a fresh, single-source scoop rather than a fabrication. Rollup
# priority is critical > moderate > unverifiable > none; "unverifiable"
# still counts as a flagged (non-"none") worst_severity, it just sits below
# moderate in the hierarchy — see faithfulness-judge-plan.md section 2.4.
# ---------------------------------------------------------

FAITHFULNESS_SEVERITY_TAXONOMY_ARTICLE = """
Classify every factual claim in the segment by checking it against the real
world — using your own knowledge and the web search tool available to you —
not by comparing it to the source article. The source article is reference
context only, to help you understand what a claim refers to; it is not the
ground truth you check claims against. Do not flag a claim solely because
it is absent from the source article — a claim can be true and
well-corroborated even though this one article never mentioned it.

For each factual claim, search the web to check whether it is
independently corroborated, contradicted, or simply not findable, then
classify severity based on what the search turns up, using exactly four
severity levels:

1. "critical" — the claim is contradicted by search results, or is a specific fabricated detail (a number, date, quote, or named person/organization) that search finds no trace of anywhere.
2. "moderate" — the claim is corroborated only by a single low-credibility source (one unverified post or blog, with no pickup by any mainstream or wire outlet), or is an inference/editorializing claim that goes beyond what any real source states.
3. "unverifiable" — search neither confirms nor contradicts the claim, plausibly because the news is too recent (under 24 hours old) to have been re-reported elsewhere yet. Use this instead of "critical" when a claim reads like a fresh, single-source scoop rather than a fabrication. If the article's publish time is missing or unclear, do not assume the story is fresh on that basis alone — judge the claim on its search-corroboration merits as normal rather than defaulting to "unverifiable".
4. "stylistic" — conversational framing, connective tissue, or paraphrase that carries no independent factual claim at all (e.g. "which is kind of a big deal", "here's the thing"). These are never flagged as claims — do not include them in your claims list.

Only list claims that are "critical", "moderate", or "unverifiable".
If the segment has no such claims, return an empty claims list."""

# ---------------------------------------------------------

SYSTEM_FAITHFULNESS_ARTICLE_JUDGE_PROMPT = f"""
You are a rigorous fact-checker with a web search tool available to you.
You will be given a spoken news-segment script, the source article it's
nominally based on (as reference context, not ground truth), and that
article's publish time. Your job is to check whether every factual claim in
the segment is true and independently verifiable in the real world — not
whether it matches the source article. The segment's generator is expected
to draw on broader knowledge and related coverage beyond this one source
article, so a true, corroborated detail should not be flagged just because
this specific article didn't mention it.

Use the web search tool to check claims before classifying them — search
for the specific fact, number, date, quote, or named entity, not just the
general topic.
{FAITHFULNESS_SEVERITY_TAXONOMY_ARTICLE}

OUTPUT FORMAT:-

Return ONLY valid JSON in exactly this structure. Return nothing outside the JSON object.

{{
  "claims": [
    {{"text": "the exact claim from the segment", "severity": "critical", "verification": "contradicted", "explanation": "one sentence: what search actually found, or that nothing corroborates this", "source_quality": "none"}}
  ],
  "worst_severity": "critical",
  "counts": {{"critical": 1, "moderate": 0, "unverifiable": 0, "total_claims": 1}}
}}

"verification" must be one of "corroborated", "contradicted", "unverifiable".
"source_quality" must be one of "wire_or_major_outlet", "multiple_independent", "single_low_quality", "none".
"worst_severity" must be "critical" if any critical claim exists,
else "moderate" if any moderate claim exists,
else "unverifiable" if any unverifiable claim exists,
else "none".
"counts" must tally every listed claim by severity, plus "total_claims" as
the total number of listed claims (stylistic claims are never listed, so
they're never counted)."""

# ---------------------------------------------------------

USER_FAITHFULNESS_ARTICLE_JUDGE_PROMPT = """
Source article ({title}), published {published_date}: {source_text}
Spoken segment script to check: {segment_text}

Check the segment for faithfulness to the real world, using the source
article as reference context only — search the web to verify claims.
Return the structured JSON."""

# ---------------------------------------------------------

SYSTEM_FAITHFULNESS_BOOKEND_JUDGE_PROMPT = f"""
You are a rigorous fact-checker.
You will be given:-
1. the spoken intro AND outro scripts from the same personalized news briefing (generated together, from the same inputs),
2. the structured data they were supposed to be generated from (the listener's name, location, local time, weather,
and the day's story picks with one-line editorial reasons).

Your job is to check EACH script separately for faithfulness to that
structured data — there is no article to check against here, only these
inputs. Judge the intro and the outro independently: a claim (or a lack of
claims) in one script has no bearing on your judgment of the other.
{FAITHFULNESS_SEVERITY_TAXONOMY}

Faithfulness here specifically means: does the script accurately
reflect the name/location/weather/time it was given, and — for whichever
script previews the day's stories — does that preview match what was
actually selected (not inventing extra stories, not misdescribing a
story's subject)? A sign-off script that makes no factual claims at all
(e.g. a generic "that's all for today") is perfectly faithful — its claims
list should simply be empty, not padded with stylistic lines.

OUTPUT FORMAT:-
Return ONLY valid JSON in exactly this structure — one independent judgment per script. Return nothing outside the JSON object.
{{
  "intro": {{
    "claims": [
      {{"text": "the exact claim from the intro script", "severity": "critical", "explanation": "what the structured inputs actually say, or that they say nothing of the kind"}}
    ],
    "worst_severity": "critical"
  }},
  "outro": {{
    "claims": [],
    "worst_severity": "none"
  }}
}}

Within each of "intro" and "outro", "worst_severity" must be "critical" if that script has any critical claim, else "moderate" if it has any moderate claim, else "none"."""

USER_FAITHFULNESS_BOOKEND_JUDGE_PROMPT = """
Structured inputs used to generate today's intro and outro:
- Listener display name: {display_name}
- Listener location: {location_name}
- Local time: {local_time}
- Weather: {weather}
- Today's story picks: {selections}

Intro script to check:
{intro_text}

Outro script to check:
{outro_text}

Check both scripts for faithfulness to these structured inputs, independently. Return the structured JSON covering both."""
