"""
core/prompt_watcher.py
Track changes to prompt .txt files. Logs diffs when files are modified.
"""

import hashlib
from pathlib import Path

from loguru import logger

_hashes: dict[str, str] = {}


def _hash_file(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.md5(path.read_bytes()).hexdigest()


def check_prompt_changes(prompts_dir: Path) -> list[str]:
    """
    Check all .txt files in prompts_dir for changes since last check.
    Returns list of changed task names. Logs diffs.
    """
    changed = []
    for txt_file in sorted(prompts_dir.glob("*.txt")):
        task = txt_file.stem
        current_hash = _hash_file(txt_file)
        prev_hash = _hashes.get(task)

        if prev_hash is None:
            # First time seeing this file
            _hashes[task] = current_hash
            continue

        if current_hash != prev_hash:
            content = txt_file.read_text(encoding="utf-8")
            logger.info(
                f"PROMPT_CHANGED | task={task} | "
                f"old_hash={prev_hash[:8]} new_hash={current_hash[:8]} | "
                f"length={len(content)} chars"
            )
            logger.debug(f"PROMPT_CONTENT | task={task} | {content[:1000]}")
            _hashes[task] = current_hash
            changed.append(task)

    return changed


def init_prompt_hashes(prompts_dir: Path) -> None:
    """Initialize hashes for all existing prompt files (call at startup)."""
    for txt_file in sorted(prompts_dir.glob("*.txt")):
        _hashes[txt_file.stem] = _hash_file(txt_file)
    logger.info(
        f"Prompt watcher initialized: {len(_hashes)} files tracked in {prompts_dir}"
    )
