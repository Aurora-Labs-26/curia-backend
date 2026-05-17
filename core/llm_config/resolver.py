"""
core/llm_config/resolver.py

The public face of the config layer. Three entrypoints:

    resolve.llm(task, *, show=None, user_id=None, cohort=None) -> dspy.LM
    resolve.embedder(task="embedding")                          -> Embedder
    resolve.tts(speaker)                                        -> TTSAdapter

Resolution order (most specific scope wins):
    user → cohort → show → environment(CURIA_ENV) → task default → error

The config is loaded once on first access and cached.
Call reload_config() to force a re-read (e.g., for tests).
"""

from __future__ import annotations

import os
from typing import Optional

import dspy
from loguru import logger

from .adapters import Embedder, TTSAdapter, build_embedder, build_llm, build_tts
from .loader import load_config
from .schema import BindingValue, CuriaConfig, ModelConfig, ProviderConfig


# ---------------------------------------------------------------------------
# Cached config
# ---------------------------------------------------------------------------

_config: CuriaConfig | None = None


def get_config() -> CuriaConfig:
    global _config
    if _config is None:
        _config = load_config()
    return _config


def reload_config() -> CuriaConfig:
    global _config
    _config = load_config()
    return _config


def _env() -> str:
    return os.getenv("CURIA_ENV", "dev")


# ---------------------------------------------------------------------------
# Binding resolution
# ---------------------------------------------------------------------------


def _resolve_binding(
    task: str,
    *,
    show: Optional[str] = None,
    user_id: Optional[str] = None,
    cohort: Optional[str] = None,
) -> BindingValue:
    """
    Walk the scope hierarchy (most specific first) until we find a binding for `task`.
    Raises if none is found.
    """
    cfg = get_config()
    b = cfg.bindings

    # 1. user-specific
    if user_id and user_id in b.user and task in b.user[user_id]:
        return b.user[user_id][task]
    # 2. cohort-specific
    if cohort and cohort in b.cohort and task in b.cohort[cohort]:
        return b.cohort[cohort][task]
    # 3. show-specific
    if show and show in b.show and task in b.show[show]:
        return b.show[show][task]
    # 4. environment
    env = _env()
    if env in b.environment and task in b.environment[env]:
        return b.environment[env][task]
    # 5. task default
    if task in b.task:
        return b.task[task]

    raise ValueError(
        f"No binding for task '{task}' in scope "
        f"(env={env}, show={show}, cohort={cohort}, user_id={user_id}). "
        f"Define one under bindings.task in config/models.yaml."
    )


def _resolve_speaker_binding(speaker: str) -> BindingValue:
    cfg = get_config()
    if speaker not in cfg.bindings.speaker:
        raise ValueError(
            f"No speaker binding for '{speaker}'. "
            f"Known speakers: {list(cfg.bindings.speaker)}"
        )
    return cfg.bindings.speaker[speaker]


def _merge_settings(model: ModelConfig, binding: BindingValue) -> dict:
    """Model defaults overridden by binding-level overrides."""
    return {**model.defaults, **binding.overrides}


def _provider_and_model(binding: BindingValue) -> tuple[ProviderConfig, ModelConfig]:
    cfg = get_config()
    model = cfg.models[binding.model]
    provider = cfg.providers[model.provider]
    return provider, model


# ---------------------------------------------------------------------------
# Public surface — `resolve.llm(...)`, etc.
# ---------------------------------------------------------------------------


class _Resolver:
    """Façade exposing llm / embedder / tts. Use the singleton `resolve`."""

    def llm(
        self,
        task: str,
        *,
        show: Optional[str] = None,
        user_id: Optional[str] = None,
        cohort: Optional[str] = None,
    ) -> dspy.LM:
        binding = _resolve_binding(task, show=show, user_id=user_id, cohort=cohort)
        provider, model = _provider_and_model(binding)
        settings = _merge_settings(model, binding)
        logger.debug(
            f"resolve.llm(task={task}, show={show}, user_id={user_id}, "
            f"cohort={cohort}) -> {model.model_id}"
        )
        from core.logging import llm_logger
        llm_logger.info(
            f"LLM_RESOLVE | task={task} model={model.model_id} "
            f"provider={provider.type} show={show}"
        )
        return build_llm(provider=provider, model=model, settings=settings)

    def embedder(
        self,
        task: str = "embedding",
        *,
        show: Optional[str] = None,
        user_id: Optional[str] = None,
        cohort: Optional[str] = None,
    ) -> Embedder:
        binding = _resolve_binding(task, show=show, user_id=user_id, cohort=cohort)
        provider, model = _provider_and_model(binding)
        settings = _merge_settings(model, binding)
        from core.logging import llm_logger
        llm_logger.info(
            f"EMBEDDER_RESOLVE | task={task} model={model.model_id} "
            f"provider={provider.type}"
        )
        return build_embedder(provider=provider, model=model, settings=settings)

    def tts(self, *, speaker: str) -> TTSAdapter:
        binding = _resolve_speaker_binding(speaker)
        provider, model = _provider_and_model(binding)
        settings = _merge_settings(model, binding)
        if not binding.voice_id:
            raise ValueError(
                f"Speaker binding '{speaker}' has no voice_id "
                f"(this should have been caught by schema validation)."
            )
        from core.logging import llm_logger
        llm_logger.info(
            f"TTS_RESOLVE | speaker={speaker} model={model.model_id} "
            f"provider={provider.type} voice_id={binding.voice_id}"
        )
        return build_tts(
            provider=provider,
            model=model,
            settings=settings,
            voice_id=binding.voice_id,
        )


resolve = _Resolver()
