"""
core/llm_config/loader.py
Read + validate config/models.yaml. Cached singleton.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from loguru import logger

from .schema import CuriaConfig

_CONFIG_PATH_ENV = "CURIA_LLM_CONFIG"
_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "models.yaml"


def config_path() -> Path:
    override = os.getenv(_CONFIG_PATH_ENV)
    if override:
        return Path(override).resolve()
    return _DEFAULT_CONFIG_PATH


def load_config() -> CuriaConfig:
    """Read YAML + validate. Raises on schema/reference errors."""
    path = config_path()
    if not path.exists():
        raise FileNotFoundError(
            f"LLM config not found at {path}. "
            f"Set {_CONFIG_PATH_ENV} to override location, "
            f"or create config/models.yaml at the repo root."
        )
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: top-level YAML must be a mapping")
    cfg = CuriaConfig.model_validate(raw)
    logger.debug(
        f"Loaded LLM config from {path}: "
        f"{len(cfg.providers)} providers, {len(cfg.models)} models, "
        f"{len(cfg.bindings.task)} task bindings."
    )
    return cfg
