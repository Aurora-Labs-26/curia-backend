"""
core/tts.py
Thin facade for TTS. Real implementation lives in core/llm_config/adapters/tts.py.

Two public entrypoints:

    synthesize_for_speaker(text, speaker, output_path)
        Resolves speaker → ElevenLabs voice via config/models.yaml. PREFERRED.

    synthesize_line(text, voice_id, output_path)
        Legacy signature: takes voice_id directly. Used by studio/generator.py
        when iterating transcript lines whose speaker name is already mapped
        to a voice_id at the call site. Builds a transient adapter per call.

Both delegate to core/llm_config/adapters/tts.py, which handles ElevenLabs
HTTP + the stub fallback when ELEVENLABS_API_KEY is missing.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from .llm_config import resolve
from .llm_config.adapters import build_tts
from .llm_config.resolver import get_config

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../.env"))


def synthesize_for_speaker(text: str, speaker: str, output_path: str) -> None:
    """Resolve speaker name → configured voice/model and synthesize."""
    adapter = resolve.tts(speaker=speaker)
    adapter.synthesize(text=text, output_path=output_path)


def synthesize_line(text: str, voice_id: str, output_path: str) -> None:
    """
    Legacy entrypoint. `voice_id` is an ElevenLabs voice ID string.
    Picks the first TTS-kind model in the config to use as the underlying engine.
    """
    cfg = get_config()
    tts_model_alias = next(
        (alias for alias, m in cfg.models.items() if m.kind == "tts"),
        None,
    )
    if tts_model_alias is None:
        raise RuntimeError(
            "No TTS-kind model defined in config/models.yaml — "
            "cannot synthesize. Add one under `models:` with kind=tts."
        )
    model = cfg.models[tts_model_alias]
    provider = cfg.providers[model.provider]
    adapter = build_tts(
        provider=provider,
        model=model,
        settings=model.defaults,
        voice_id=voice_id,
    )
    adapter.synthesize(text=text, output_path=output_path)
