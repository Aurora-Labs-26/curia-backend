"""
core/errors.py
Shared exception types.

PermanentError — raised when retrying a job would never succeed (404, validation
rejection, explicit content block, etc.). The worker catches this and immediately
marks the job 'failed' without using any remaining attempts.
"""


class PermanentError(ValueError):
    """
    Raised when a job failure is deterministic and retrying is pointless.
    Examples: URL validation rejection, 404 from HEAD check, paywall block.
    """
