"""sys.path entries for the modules the scorers evaluate.

The scorers import `libs.*` from backend/, which pytest puts on sys.path through its pythonpath
config but a bare `python -m model.eval.run` does not. Kept apart from the test suite's own path
setup because the eval harness ships in the ragas image, where the test package is not present.

The scorers import the REAL modules deliberately. An eval that re-implemented the rule it is
measuring would measure the re-implementation.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def ensure() -> None:
    """Put the repository root and backend/ on sys.path once."""
    for directory in (REPO_ROOT, REPO_ROOT / "backend"):
        entry = str(directory)
        if entry not in sys.path:
            sys.path.insert(0, entry)
