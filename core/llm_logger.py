"""
core/llm_logger.py
Intercept and log all LLM calls -- inputs, outputs, tokens, latency, cost.
Works by wrapping the dspy.LM instances returned by the resolver.
"""

import json
import time
from functools import wraps

from loguru import logger

llm_log = logger.bind(log_type="llm")


def log_llm_call(task: str, model_id: str, provider: str):
    """Decorator factory for logging LLM prediction calls."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.time()
            llm_log.info(
                f"LLM_CALL_START | task={task} model={model_id} provider={provider}"
            )

            # Log inputs (truncate very long ones)
            input_data = {}
            for k, v in kwargs.items():
                val_str = str(v)
                input_data[k] = (
                    val_str[:2000] + "..." if len(val_str) > 2000 else val_str
                )
            llm_log.debug(
                f"LLM_INPUT | task={task} | {json.dumps(input_data, default=str)}"
            )

            try:
                result = func(*args, **kwargs)
                elapsed = time.time() - start

                # Log output
                output_str = str(result)
                llm_log.info(
                    f"LLM_CALL_END | task={task} model={model_id} "
                    f"duration={elapsed:.2f}s output_len={len(output_str)}"
                )
                llm_log.debug(
                    f"LLM_OUTPUT | task={task} | "
                    f"{output_str[:3000]}{'...' if len(output_str) > 3000 else ''}"
                )
                return result
            except Exception as e:
                elapsed = time.time() - start
                llm_log.error(
                    f"LLM_CALL_FAIL | task={task} model={model_id} "
                    f"duration={elapsed:.2f}s error={e}"
                )
                raise

        return wrapper

    return decorator
