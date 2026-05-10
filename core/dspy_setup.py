"""
core/dspy_setup.py
Thin compatibility shim. The real LLM configuration lives in core/llm_config/.

Why this file still exists:
  - Existing imports (`from core.dspy_setup import use_haiku, use_sonnet, lm_for`)
    keep working unchanged. The shim just delegates to resolve.llm(...).
  - DSPy's global default LM is set on first access for any code that calls
    `dspy.Predict(...)` without a `with dspy.context(lm=...)` block.

For new code, prefer:
    from core.llm_config import resolve
    with dspy.context(lm=resolve.llm("transcript", show=show_name)):
        ...
"""

from __future__ import annotations

import os
from contextlib import contextmanager

import dspy
from dotenv import load_dotenv
from loguru import logger

from .llm_config import resolve

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))


_default_configured = False


def _ensure_default_lm() -> None:
    """Configure DSPy's global LM the first time anyone needs it.

    Uses whatever model is bound to the `outline` task by default — a sane
    'cheap LLM' choice. Any code that scopes via `dspy.context(lm=...)`
    overrides this.
    """
    global _default_configured
    if _default_configured:
        return
    try:
        default_lm = resolve.llm("outline")
        dspy.configure(lm=default_lm)
        _default_configured = True
        logger.debug("DSPy default LM configured from llm_config (task=outline)")
    except Exception as e:
        logger.warning(
            f"Could not configure DSPy default LM (task=outline): {e}. "
            f"Calls without an explicit dspy.context(lm=...) will fail."
        )


# Trigger default config at import time so legacy modules that just call
# `dspy.Predict(...)` continue to work.
_ensure_default_lm()


# ---------------------------------------------------------------------------
# Backward-compat helpers. Prefer `resolve.llm(task, ...)` in new code.
# ---------------------------------------------------------------------------


@contextmanager
def use_sonnet():
    """Scope to whatever model is bound to `transcript` (typically Sonnet)."""
    with dspy.context(lm=resolve.llm("transcript")):
        yield


@contextmanager
def use_haiku():
    """Scope to whatever model is bound to `outline` (typically Haiku)."""
    with dspy.context(lm=resolve.llm("outline")):
        yield


def lm_for(model_name: str) -> dspy.LM:
    """
    Legacy resolver — accepted a raw model_id string, returned an LM.
    Now redirects to the binding-driven resolver: 'haiku' → outline binding,
    'sonnet' → transcript binding. Anything else falls back to the outline binding.

    NEW CODE SHOULD USE `resolve.llm(task=..., show=..., user_id=...)` DIRECTLY.
    """
    name = (model_name or "").lower()
    if "sonnet" in name:
        return resolve.llm("transcript")
    if "haiku" in name:
        return resolve.llm("outline")
    logger.warning(
        f"lm_for({model_name!r}) — model name not recognized; using outline binding. "
        f"Migrate to `resolve.llm(task=...)` instead."
    )
    return resolve.llm("outline")
