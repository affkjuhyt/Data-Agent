"""tool_executor helpers — the small text munging between a model and the sandbox."""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "services" / "workflow-runtime")

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


def _strip():
    from nodes.sandbox.tool_executor._utils import _strip_code_block
    return _strip_code_block


class TestStripCodeBlock:
    def test_trailing_semicolon_is_removed(self):
        # The sandbox wraps the statement in SELECT * FROM (…) LIMIT n — a
        # trailing ";" inside the subquery is a syntax error on every retry.
        assert _strip()("SELECT 1;") == "SELECT 1"
        assert _strip()("SELECT 1 ;  \n") == "SELECT 1"
        assert _strip()("SELECT 1;;") == "SELECT 1"

    def test_code_fence_and_semicolon(self):
        assert _strip()("```sql\nSELECT a\nFROM t;\n```") == "SELECT a\nFROM t"
        assert _strip()("```\nSELECT 1\n```") == "SELECT 1"

    def test_inner_semicolons_are_untouched(self):
        # only the terminal one goes — a literal containing ';' must survive
        assert _strip()("SELECT ';' AS s") == "SELECT ';' AS s"
