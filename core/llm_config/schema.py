"""
core/llm_config/schema.py
Pydantic models for the YAML config at config/models.yaml.
"""

from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

ProviderType = Literal[
    "anthropic",      # LLM
    "voyage",         # embedding
    "elevenlabs",     # TTS
    "openai",         # LLM
    "cohere",         # LLM
    "smallest",       # TTS (Smallest.ai)
    "google_tts",     # TTS (Google Cloud Text-to-Speech)
    "xai",            # TTS (xAI / Grok — stub: verify API availability before live use)
    "edge_tts",       # TTS (Microsoft Edge TTS — free, no API key required)
]


class ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ProviderType
    api_key_env: str
    base_url: Optional[str] = None


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

ModelKind = Literal["llm", "embedding", "tts"]


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model_id: str
    kind: ModelKind
    defaults: dict = Field(default_factory=dict)
    dimension: Optional[int] = None  # only meaningful for kind=embedding


# ---------------------------------------------------------------------------
# Bindings
# ---------------------------------------------------------------------------


class BindingValue(BaseModel):
    """
    A single binding: which model alias + optional per-binding overrides.

    YAML can express a binding two ways:
        # short form
        transcript: sonnet-4-6
        # long form
        transcript:
          model: sonnet-4-6
          overrides: { temperature: 0.95 }
          voice_id: <eleven_voice_id>          # only for speaker bindings
    """

    model_config = ConfigDict(extra="forbid")

    model: str
    overrides: dict = Field(default_factory=dict)
    voice_id: Optional[str] = None


def _coerce_binding(value: Union[str, dict, BindingValue]) -> BindingValue:
    if isinstance(value, BindingValue):
        return value
    if isinstance(value, str):
        return BindingValue(model=value)
    if isinstance(value, dict):
        return BindingValue(**value)
    raise TypeError(f"Cannot coerce {type(value)} to BindingValue")


class Bindings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # task → BindingValue
    task: dict[str, BindingValue] = Field(default_factory=dict)

    # environment → task → BindingValue
    environment: dict[str, dict[str, BindingValue]] = Field(default_factory=dict)

    # show → task → BindingValue
    show: dict[str, dict[str, BindingValue]] = Field(default_factory=dict)

    # speaker_name → BindingValue (must include voice_id)
    speaker: dict[str, BindingValue] = Field(default_factory=dict)

    # cohort_id → task → BindingValue
    cohort: dict[str, dict[str, BindingValue]] = Field(default_factory=dict)

    # user_id → task → BindingValue
    user: dict[str, dict[str, BindingValue]] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _normalize_short_form(cls, data: dict) -> dict:
        """
        Allow YAML to write `transcript: sonnet-4-6` (string) or full BindingValue dict.
        Coerce all binding values to dicts so Pydantic can build BindingValue.
        """
        if not isinstance(data, dict):
            return data

        def _norm_task_dict(d):
            if not isinstance(d, dict):
                return d
            out = {}
            for k, v in d.items():
                if isinstance(v, str):
                    out[k] = {"model": v}
                else:
                    out[k] = v
            return out

        # task and speaker are flat (key → binding)
        if "task" in data:
            data["task"] = _norm_task_dict(data["task"])
        if "speaker" in data:
            data["speaker"] = _norm_task_dict(data["speaker"])

        # environment, show, cohort, user are nested (scope_key → task → binding)
        for nested_key in ("environment", "show", "cohort", "user"):
            if nested_key in data and isinstance(data[nested_key], dict):
                data[nested_key] = {
                    sk: _norm_task_dict(sv) for sk, sv in data[nested_key].items()
                }
        return data


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------


class CuriaConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: dict[str, ProviderConfig]
    models: dict[str, ModelConfig]
    bindings: Bindings

    @model_validator(mode="after")
    def _validate_references(self) -> "CuriaConfig":
        """Ensure every binding references a known model and every model references a known provider."""
        # Models must reference known providers.
        for alias, m in self.models.items():
            if m.provider not in self.providers:
                raise ValueError(
                    f"Model '{alias}' references unknown provider '{m.provider}'. "
                    f"Known: {list(self.providers)}"
                )

        # Every binding's `.model` must be a known model alias.
        all_models = set(self.models)

        def _check(scope_label: str, bindings: dict[str, BindingValue]):
            for task, bv in bindings.items():
                if bv.model not in all_models:
                    raise ValueError(
                        f"Binding {scope_label}/{task} references unknown model "
                        f"'{bv.model}'. Known: {sorted(all_models)}"
                    )

        _check("task", self.bindings.task)
        _check("speaker", self.bindings.speaker)
        for env, b in self.bindings.environment.items():
            _check(f"environment/{env}", b)
        for show, b in self.bindings.show.items():
            _check(f"show/{show}", b)
        for cohort, b in self.bindings.cohort.items():
            _check(f"cohort/{cohort}", b)
        for user, b in self.bindings.user.items():
            _check(f"user/{user}", b)

        # Speaker bindings must declare voice_id and reference a tts model.
        for spk, bv in self.bindings.speaker.items():
            if not bv.voice_id:
                raise ValueError(f"Speaker binding '{spk}' must include voice_id")
            if self.models[bv.model].kind != "tts":
                raise ValueError(
                    f"Speaker binding '{spk}' references model '{bv.model}' "
                    f"which is kind={self.models[bv.model].kind}, expected 'tts'"
                )
        return self
