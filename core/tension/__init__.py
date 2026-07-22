"""
core/tension/
Tension registry + Connect companion selection (see "companion selection
research v1.md" and "stance card samples v1.md" for the design + validation).

Public surface:
    from core.tension import upsert_tension, link_source
    from core.tension import find_cast, connect_eligible, validate_and_angle
"""
from .registry import SNAP_THRESHOLD, link_source, upsert_tension
from .connect import connect_eligible, find_cast, validate_and_angle

__all__ = [
    "SNAP_THRESHOLD", "upsert_tension", "link_source",
    "find_cast", "connect_eligible", "validate_and_angle",
]
