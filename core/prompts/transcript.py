"""
core/prompts/transcript.py
DSPy module for episode transcript generation.

Reads briefing + outline + speaker + examples. Returns a JSON array of spoken units.

Model: Sonnet (transcript is the highest-quality call in the pipeline).
Caller scopes via `with dspy.context(lm=resolve.llm("transcript", show=show_name))`
(see core/llm_config/). Default binding is `sonnet-4-6` per config/models.yaml.
"""

from __future__ import annotations

import dspy

from core.prompts.loader import with_prompt


class GenerateTranscript(dspy.Signature):
    """You are a single-host podcast scriptwriter. Your only output is a valid JSON array of spoken lines. No prose, no markdown, no explanation."""

    briefing: str = dspy.InputField(
        desc=(
            "The full briefing packet as JSON, wrapped in <briefing> tags. "
            "Contains: format, format_config (voice_style, rules, energy_curve), "
            "episode_constraints (target_words, segment_count, intro_budget_words, outro_budget_words), "
            "editorial_direction, source_primitives, and optional listener_context."
        )
    )

    speaker: str = dspy.InputField(
        desc=(
            "Speaker definition wrapped in <speaker> tags. "
            "Contains: name, backstory, and speech_patterns. "
            "speech_patterns controls sentence rhythm and quirks — apply subtly, not literally."
        )
    )

    outline: str = dspy.InputField(
        desc=(
            "Episode outline as JSON, wrapped in <outline> tags. "
            "Contains: title, thread, and segments array. "
            "Each segment has: segment number, title, purpose, primitives_used, transition. "
            "Execute this structure exactly — do not redesign it."
        )
    )

    quality_constraints: str = dspy.InputField(
        desc=(
            "Hard quality constraints wrapped in <constraints> tags. "
            "Every rule here is non-negotiable. Treat violations as failures."
        )
    )

    examples: str = dspy.InputField(
        desc=(
            "Reference transcript examples wrapped in <examples> tags. "
            "These demonstrate the target voice, sentence rhythm, and line length. "
            "Do not reproduce them — use them to calibrate style only."
        )
    )

    transcript_json: str = dspy.OutputField(
        desc='JSON array of {"speaker": str, "text": str}. No wrapper object, no markdown fences.'
    )


generate_transcript = dspy.Predict(with_prompt(GenerateTranscript, "transcript"))


# ---------------------------------------------------------------------------
# Examples block — injected as the `examples` input.
# ---------------------------------------------------------------------------

TRANSCRIPT_EXAMPLES = """\
Style references only. Do not reproduce. Use these to calibrate: how long to hold a
build before landing it, when a fragment earns its place, how ideas arrive through
scenes not labels, and how warmth sounds when it's specific.

--- LONG SETUP, SHORT LANDING ---
Hold the build. Make the listener wait. The short sentence only lands because
the ear has been working for several sentences before it.

"Chess was supposed to be the exception. Then Go was supposed to be different.
Too intuitive, too spatial, impossible for brute force. AlphaGo won anyway."

"If your evals are calibrated for the wrong reality, then everything downstream
is wrong. Your training signal is wrong. Your safety metrics are wrong. Your
reinforcement learning is targeting proxies that will break the moment the model
crosses into a new regime. The ones that don't figure this out will be the ones
that get surprised."

"The church broke up clan families to block inheritance. Smaller households
needed new forms of association. Charter towns formed. Universities formed.
Guilds formed. And these inadvertent side effects of a marriage policy became
the core institutions of Western society."

--- CONSEQUENCE CHAINS WITHOUT FANFARE ---
Let each sentence be the next consequence of the previous one. No "and this is
significant." No announcement. The next sentence is just what happened next.
Real experts talk this way — the chain carries the argument.

"When plagues would hit, the church explained it as too much cousin marriage.
The pressure to break up clan families led to nuclear households. Nuclear
households needed voluntary associations. Those associations became guilds,
universities, charter towns. The industrial revolution came out of that."

"They found most people in the US and Zurich offer 50/50 and reject low offers.
That fit the intuition. So he ran the same experiment with the Macha Ganga,
expecting the same result. They didn't feel inclined to reject at all.
And people gave pretty low offers."

--- SCENE DOING THE WORK OF ABSTRACTION ---
Show the action; let the concept follow. TTS cannot show diagrams. The only
way an abstract idea lands is if the listener can picture something happening.

"You tap a few buttons, hit submit, and it feels like the future. What you
don't know is that the second you submit, the founder is sprinting out the back
door, running two blocks to buy the exact ingredients you just ordered."

"If you pay per horseshoe, and you have a clock, you start wondering: how many
can I make in an hour? Maybe five. Well, not if I can make six. That's when you
start thinking about the world differently."

--- SENTENCE LENGTH VARIATION ---
Uniform sentence length creates a drone. TTS cannot use vocal dynamics to break
it. Vary deliberately: long sentence to carry the idea, short sentence to land
it. Fragment as the final beat.

"Training a model is just an optimization process. And that process is only as
good as the objective you give it. The objective comes from the evaluation.
From what we decide to measure."

"The discussion suggests this new layer won't be an app at all. The instinct
shared in one reply: it ultimately lives near the operating system. Baked into
your phone. Humming along in the background. Seamless."

--- MOMENTUM THROUGH SHORT STACKING ---
Short sentences in sequence build pace. Use when the material needs energy —
a reveal, a turn, an accumulation heading toward a surprise.

"The more you engage with negative content, the more the algorithm serves you.
Which heightens the anxiety. Which leads you to mute, block, or abandon news
entirely. Doomscrolling by design."

"You're in a marathon, on a hot day. Racing against a horse. Who wins?
You could very well win."

--- UNDERCUT / REFRAME ---
Set the expectation clearly. Then cut it. The flat delivery is the technique —
no drama, no announcement. The reversal is the sentence that follows.

"Going from 100 to 110 doesn't feel like a breakthrough. But let the math
play out."

"He ran the experiment expecting confirmation. What he found was that they
didn't feel inclined to reject at all. And people gave pretty low offers."

--- COMMITTED METAPHOR ---
Introduce the image, then walk all the way through it. Don't hint at a metaphor
and move on. A metaphor earns its place by being concrete enough to follow step
by step — the listener builds a picture, and the picture carries the idea.

"Think of the inside of your body as a water park. The lymphatic system is the
lazy river. The hookworms fall through the skin cells, drop into the river, float
along with the current. They reach the lymphatic duct by your collarbone and get
shot into the bloodstream."

"The knowledge-based system is scaffolding. It holds the building up during
construction. When the building is finished, scaffolding comes down. The mistake
is treating it like load-bearing wall."

--- ESCALATING SPECIFICITY AS THE LANDING ---
The most specific version of a thing is often the punchline. Don't editorialize
around it. Accumulate the setup, then let the precise detail close it.

"I contacted every laboratory supply company in the world. Every parasitology
research center. They all said the same thing. No. Various flavors of no."

"He ran the experiment expecting confirmation. What he found: they didn't feel
inclined to reject at all. And people gave pretty low offers."

--- WARMTH THAT IS EPISODE-SPECIFIC ---
The outro earns trust by naming something from this episode specifically — a
detail, a moment, an image. Generic warmth costs nothing and delivers nothing.
Episode-specific warmth is the difference between a sign-off and a reason to return.

"If we eliminated all cancer deaths tomorrow, it would only add about three
years to average life expectancy. Aging is a multi-headed beast."

"The story isn't 'will the invaders destroy us?' anymore. It's 'what does it
mean to be human in a world we can barely comprehend?' Which is a much
scarier question."
"""
