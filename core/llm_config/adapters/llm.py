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
from loguru import logger

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

    lm: dspy.LM | None = None

    if provider.type == "anthropic":
        lm = dspy.LM(
            f"anthropic/{model.model_id}",
            api_key=api_key,
            **settings,
        )

    elif provider.type == "openai":
        lm = dspy.LM(
            f"openai/{model.model_id}",
            api_key=api_key,
            **settings,
        )

    elif provider.type == "cohere":
        lm = dspy.LM(
            f"cohere/{model.model_id}",
            api_key=api_key,
            **settings,
        )

    elif provider.type == "vllm":
        base_url = provider.base_url
        if not base_url:
            raise RuntimeError(
                "vLLM provider requires base_url in config "
                "(e.g. http://localhost:8001/v1)"
            )
        lm = dspy.LM(
            f"openai/{model.model_id}",
            api_key=api_key or "dummy",  # vLLM may not need a real key
            api_base=base_url,
            **settings,
        )

    elif provider.type == "xai_llm":
        lm = dspy.LM(
            f"openai/{model.model_id}",
            api_key=api_key,
            api_base=provider.base_url or "https://api.x.ai/v1",
            **settings,
        )

    elif provider.type == "gemini":
        lm = dspy.LM(
            f"google/{model.model_id}",
            api_key=api_key,
            **settings,
        )

    elif provider.type == "openrouter":
        lm = dspy.LM(
            f"openrouter/{model.model_id}",
            api_key=api_key,
            api_base=provider.base_url or "https://openrouter.ai/api/v1",
            **settings,
        )

    else:
        raise ValueError(
            f"Provider type '{provider.type}' not supported for LLM "
            f"(model={model.model_id}). Add a branch in build_llm() to enable it."
        )

    llm_log = logger.bind(log_type="llm")
    llm_log.info(
        f"LLM_BUILT | provider={provider.type} model={model.model_id} "
        f"settings_keys={list(settings.keys())}"
    )
    return lm
