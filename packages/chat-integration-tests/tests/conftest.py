"""Pytest configuration for :mod:`chat_integration_tests`.

Adds the ``tests/`` directory to :data:`sys.path` so sibling helper
modules like ``_dispatch_matrix`` are importable whether pytest is
invoked from the package root or the repo root.
"""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
