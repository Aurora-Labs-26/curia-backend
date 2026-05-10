"""Adapters that turn (provider + model + settings) into ready-to-use clients."""

from .llm import build_llm
from .embedding import Embedder, build_embedder
from .tts import TTSAdapter, build_tts

__all__ = ["build_llm", "Embedder", "build_embedder", "TTSAdapter", "build_tts"]
