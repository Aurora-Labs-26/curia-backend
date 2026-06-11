"""
core/prompts/loader.py
Simple file-based prompt loader. Reads from prompts/{task}.txt if it exists,
falls back to the Python Signature docstring.

Edit a .txt file to change prompts without touching Python.
DSPy/GEPA can also write optimized prompts back to these files.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from loguru import logger

# Default: project_root/prompts/
PROMPTS_DIR = Path(os.getenv(
    "CURIA_PROMPTS_DIR",
    str(Path(__file__).resolve().parent.parent.parent / "prompts"),
))


def load_prompt(task: str) -> Optional[str]:
    """Load prompt instructions from prompts/{task}.txt. Returns None if file doesn't exist."""
    path = PROMPTS_DIR / f"{task}.txt"
    if path.exists():
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return text
    return None


def save_prompt(task: str, instructions: str) -> Path:
    """Write prompt instructions to prompts/{task}.txt. Creates dir if needed."""
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)
    path = PROMPTS_DIR / f"{task}.txt"
    path.write_text(instructions.strip() + "\n", encoding="utf-8")
    logger.info(f"[prompts] saved {task} → {path}")
    return path


def with_prompt(sig_cls: type, task: str, show: str | None = None) -> type:
    """
    Return sig_cls with instructions overridden from prompts/{task}.txt.
    If show is provided, checks prompts/{task}_{show}.txt first (show-specific override).
    Falls back to prompts/{task}.txt, then to the original class unchanged.
    """
    custom = (load_prompt(f"{task}_{show}") if show else None) or load_prompt(task)
    if not custom:
        return sig_cls
    # Don't re-subclass if the text hasn't changed
    if custom == (sig_cls.__doc__ or "").strip():
        return sig_cls
    return type(f"Custom{sig_cls.__name__}", (sig_cls,), {"__doc__": custom})


def list_prompt_tasks() -> list[str]:
    """List all .txt files in the prompts directory (without extension)."""
    if not PROMPTS_DIR.exists():
        return []
    return sorted(p.stem for p in PROMPTS_DIR.glob("*.txt"))
