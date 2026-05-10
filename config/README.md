# config/

Single source of truth for every model interaction in Curia. One file: [`models.yaml`](models.yaml).

## What this controls

| You want to… | Edit |
|---|---|
| Switch the transcript model from Sonnet 4.6 to GPT-4o | `bindings.task.transcript: gpt-4o` (after adding `gpt-4o` under `models:` and an `openai` provider) |
| Use cheaper LLM in dev, real LLM in prod | `bindings.environment.dev.transcript: haiku-4-5` |
| Make the `narrative_drift` show use a slower, higher-quality outline model | `bindings.show.narrative_drift.outline: sonnet-4-6` |
| Change Kenji's ElevenLabs voice | `bindings.speaker.kenji.voice_id: <new_voice_id>` |
| Bump max_tokens for transcripts only | `bindings.task.transcript: { model: sonnet-4-6, overrides: { max_tokens: 12000 } }` |
| Add a brand-new model alias | Add an entry under `models:` and reference it from a binding |

## How resolution works

When code asks for a model, it specifies a *task* and optional *scope*:

```python
from core.llm_config import resolve

lm = resolve.llm("transcript", show="narrative_drift", user_id=u_id)
```

The resolver walks scopes in this order, most specific first:

```
user → cohort → show → environment(CURIA_ENV) → task default → error
```

The first match wins. If no binding is found, you get a clear error pointing at the missing key.

## Three sections in `models.yaml`

```
providers:    # auth + endpoint info per backend (anthropic, voyage, elevenlabs, ...)
models:       # named aliases — each maps to (provider, model_id, defaults, dimension/kind)
bindings:     # which task uses which model, in which scope
```

`providers` and `models` are the catalogue. `bindings` is the policy.

## Editing safely

The whole file is validated by Pydantic on load. If you reference a model alias that doesn't exist or a provider that isn't declared, the app fails to start with a clear message — that's intentional, you can't ship a broken config silently.

Things the validator catches:
- Binding references unknown model alias
- Model references unknown provider
- Speaker binding missing `voice_id`
- Speaker binding pointing at a non-tts model
- Unknown top-level keys (typo protection)

## Adding a new provider

1. Add an entry under `providers:`:
   ```yaml
   openai:
     type: openai
     api_key_env: OPENAI_API_KEY
   ```
2. Add a model under `models:`:
   ```yaml
   gpt-4o:
     provider: openai
     model_id: gpt-4o
     kind: llm
     defaults: { max_tokens: 4096, temperature: 0.7 }
   ```
3. Use the alias in any binding.

DSPy supports OpenAI/Cohere natively via LiteLLM, so no Python changes needed for those providers — the LLM adapter already routes them.

## Adding a new task

A "task" is just a string key in `bindings.task`. To add prompt optimization or a new pipeline step:

```yaml
bindings:
  task:
    my_new_task: haiku-4-5
```

Then in code:
```python
with dspy.context(lm=resolve.llm("my_new_task")):
    ...
```

## Per-environment overrides

Set `CURIA_ENV=dev|staging|prod` in the runtime environment. Bindings under `bindings.environment.<env>` override task defaults when that env is active. Useful for:
- Cheaper models in dev to save cost while iterating
- A/B testing a new model against the current production model

## Per-user / per-cohort overrides (future)

Reserved keys (`bindings.cohort`, `bindings.user`) are validated but unused in v1. They become live when:
- KB / clustering ships → cohorts get their own optimized prompts
- Per-user GEPA optimization ships → power users get personally-tuned prompts

The resolver already walks user → cohort → show → env → task. Filling in those keys is the only thing standing between v1 and per-user model selection.

## Speaker bindings

Speakers are looked up by name (lowercase), e.g. `bindings.speaker.kenji`. Each must include `voice_id` and a `model` that has `kind: tts`. The TTS model's `defaults` (e.g. `output_format`, `stability`) apply unless the speaker binding overrides them via `overrides:`.

## Where this fits in the bigger picture

- The DSPy refactor turned every prompt into a Signature — *what to ask the model*
- This config layer controls *which model* gets asked
- Together: every model interaction in the codebase is "DSPy Signature + config-resolved LM"
- Future personalization (per-user prompts, optimized artifacts) plugs in through user/cohort scopes here, with no further architectural changes
