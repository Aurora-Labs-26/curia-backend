"""
tests/conftest.py
Shared pytest fixtures.

For now: just ensures the project root is on sys.path so `from core...` etc.
work without the package being installed.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
