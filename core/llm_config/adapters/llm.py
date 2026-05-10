"""
core/llm_config/adapters/llm.py
Provider-aware construction of dspy.LM instances.

LLMs are the *load-bearing* surface — without a working LLM, the pipeline produces
nothing. So missing API keys raise loudly here (no silent stub, unlike embedding/tts).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import dspy

if TYPE_CHECKING:
    from ..schema import ModelConfig, ProviderConfig


def build_llm(
    provider: "ProviderConfig",
    model: "ModelConfig",
    settings: dict,
) -> dspy.LM:
    """
    Construct a dspy.LM for the given (provider, model) plus runtime settings.
    `settings` = model.defaults merged with the binding's overrides.
    """
    if model.kind != "llm":
        raise ValueError(
            f"build_llm called with model kind={model.kind} (expected 'llm') "
            f"for {model.model_id}"
        )

    api_key = os.getenv(provider.api_key_env)
    if not api_key:
        raise RuntimeError(
            f"{provider.api_key_env} is not set; required to construct LLM "
            f"for model '{model.model_id}'. LLMs do not have stub fallbacks "
            f"(unlike embedding/tts) because the whole pipeline depends on them."
        )

    if provider.type == "anthropic":
        return dspy.LM(
            f"anthropic/{model.model_id}",
            api_key=api_key,
            **settings,
        )

    if provider.type == "openai":
        return dspy.LM(
            f"openai/{model.model_id}",
            api_key=api_key,
            **settings,
        )

    if provider.type == "cohere":
        return dspy.LM(
            f"cohere/{model.model_id}",
            api_key=api_key,
            **settings,
        )

    raise ValueError(
        f"Provider type '{provider.type}' not supported for LLM "
        f"(model={model.model_id}). Add a branch in build_llm() to enable it."
    )
