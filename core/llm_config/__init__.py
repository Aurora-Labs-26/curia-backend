"""
core/llm_config
===============

Config-driven model registry. Single source of truth for every model interaction
(LLMs, embeddings, TTS).

Public API:
    from core.llm_config import resolve

    lm        = resolve.llm("transcript", show="narrative_drift")
    embedder  = resolve.embedder()
    tts       = resolve.tts(speaker="kenji")

Resolution order (most specific scope wins):
    user → cohort → show → environment → task default

YAML lives at config/models.yaml.
Schema is validated on load via Pydantic.
"""

from .resolver import resolve, get_config, reload_config

__all__ = ["resolve", "get_config", "reload_config"]
