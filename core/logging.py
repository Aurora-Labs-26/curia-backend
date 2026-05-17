"""
core/logging.py
Centralized logging configuration. Call setup_logging() once at startup.

Logs go to:
  - Terminal: human-readable, colorized (loguru default)
  - logs/curia.log: structured JSON, rotating (10MB per file, 5 backups)
  - logs/llm.log: LLM-specific calls (inputs, outputs, tokens, latency)
  - logs/worker.log: worker job lifecycle
"""

import os
import sys
from pathlib import Path

from loguru import logger

LOGS_DIR = Path(
    os.getenv("CURIA_LOGS_DIR", str(Path(__file__).resolve().parent.parent / "logs"))
)

_initialized = False


def setup_logging(service: str = "api") -> None:
    """
    Configure loguru with multiple sinks.
    service: 'api' or 'worker' -- appears in log records.
    """
    global _initialized
    if _initialized:
        return
    _initialized = True

    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # Remove default handler
    logger.remove()

    # Terminal -- human readable
    logger.add(
        sys.stderr,
        level="INFO",
        format=(
            "<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan> | {message}"
        ),
        colorize=True,
    )

    # Main log file -- JSON, rotating
    logger.add(
        str(LOGS_DIR / "curia.log"),
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
        rotation="10 MB",
        retention=5,
        serialize=True,
    )

    # LLM-specific log
    logger.add(
        str(LOGS_DIR / "llm.log"),
        level="DEBUG",
        filter=lambda record: record["extra"].get("log_type") == "llm",
        rotation="10 MB",
        retention=5,
        serialize=True,
    )

    # Worker-specific log
    logger.add(
        str(LOGS_DIR / "worker.log"),
        level="DEBUG",
        filter=lambda record: record["extra"].get("log_type") == "worker",
        rotation="10 MB",
        retention=5,
        serialize=True,
    )

    logger.info(f"Logging initialized for service={service}, logs_dir={LOGS_DIR}")


# Convenience loggers with pre-bound extras
llm_logger = logger.bind(log_type="llm")
worker_logger = logger.bind(log_type="worker")
