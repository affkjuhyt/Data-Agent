"""SecurityVisitor (sandbox_schema_docs.docx §1.6) — AST-based validation of
agent-authored Python. Covers the doc's own obfuscation example
(getattr(__builtins__, 'ev'+'al')), which a literal reading of the doc's
SecurityVisitor code sample does NOT catch (getattr isn't in its
BLOCKED_CALLS) — see security_visitor.py's BLOCKED_CALLS comment.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "sandbox-executor")


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    yield
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


@pytest.mark.unit
@pytest.mark.parametrize("code", [
    "import pandas as pd",
    "import numpy as np",
    "import plotly.graph_objects as go",
    "import matplotlib.pyplot as plt",
    "import json, math, datetime, statistics",
    "x = 1 + 1\nresult = {'a': x}",
])
def test_allowed_code_passes(code):
    from security_visitor import validate_python_code
    validate_python_code(code)  # must not raise


@pytest.mark.unit
@pytest.mark.parametrize("code,reason", [
    ("import os", "blocked module"),
    ("import sys", "blocked module"),
    ("import subprocess", "blocked module"),
    ("import socket", "blocked module"),
    ("import requests", "not whitelisted"),
    ("from os import path", "blocked module via from-import"),
    ("from os.path import join", "blocked module via from-import dotted"),
    ("import seaborn", "not whitelisted (not data-analysis core set)"),
    ("eval('1+1')", "blocked call"),
    ("exec('x=1')", "blocked call"),
    ("compile('x', '<s>', 'exec')", "blocked call"),
    ("open('/etc/passwd')", "blocked call"),
    ("input()", "blocked call"),
    ("__import__('os')", "blocked call"),
])
def test_blocked_code_raises(code, reason):
    from security_visitor import validate_python_code, SecurityError
    with pytest.raises(SecurityError):
        validate_python_code(code)


@pytest.mark.unit
def test_doc_obfuscation_example_is_blocked():
    """The exact example the doc cites as something AST validation catches
    (§1.6): getattr(__builtins__, 'ev'+'al')('1+1'). A naive implementation
    using only the doc's own BLOCKED_CALLS would miss this — getattr() must
    be blocked too."""
    from security_visitor import validate_python_code, SecurityError
    with pytest.raises(SecurityError):
        validate_python_code("getattr(__builtins__, 'ev'+'al')('1+1')")


@pytest.mark.unit
@pytest.mark.parametrize("code", [
    "().__class__.__bases__[0].__subclasses__()",
    "x = __builtins__",
    "y = ().__class__.__mro__",
    "setattr(x, 'y', 1)",
])
def test_sandbox_escape_attribute_chains_are_blocked(code):
    from security_visitor import validate_python_code, SecurityError
    with pytest.raises(SecurityError):
        validate_python_code(code)


@pytest.mark.unit
def test_syntax_error_propagates():
    from security_visitor import validate_python_code
    with pytest.raises(SyntaxError):
        validate_python_code("def f(:\n  pass")
