from __future__ import annotations

import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "sandbox_executor")


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


@pytest.mark.unit
def test_short_text_is_not_truncated():
    from output import truncate_output
    text, truncated = truncate_output("hello", max_chars=10)
    assert text == "hello"
    assert truncated is False


@pytest.mark.unit
def test_long_text_is_truncated_to_max_chars():
    from output import truncate_output
    text, truncated = truncate_output("a" * 100, max_chars=10)
    assert text == "a" * 10
    assert truncated is True


@pytest.mark.unit
def test_none_text_returns_empty_string():
    from output import truncate_output
    text, truncated = truncate_output(None, max_chars=10)
    assert text == ""
    assert truncated is False


@pytest.mark.unit
def test_exact_boundary_is_not_truncated():
    from output import truncate_output
    text, truncated = truncate_output("a" * 10, max_chars=10)
    assert text == "a" * 10
    assert truncated is False
