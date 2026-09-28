"""Helpers to import service modules into unit tests.

Several services live in hyphenated directories (``backend/services/auth_api``,
``backend/services/workflow_runtime``) which are NOT valid
Python package names, and some use bare imports (``from shared.auth import ...``,
``import llm_client``) that assume the service's own directory is on ``sys.path``.

``load_module_from_path`` loads such a module by file path, with the extra
``sys.path`` entries the module needs, and optional stand-in modules for heavy
optional dependencies (opentelemetry, neo4j, …) that aren't installed in the
unit-test environment. This keeps unit tests pure-Python and Docker-free.

The suites live one directory down (``tests/unit/<service>/``) and import this
as ``from .._loader import ...``.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES = REPO_ROOT / "backend" / "services"


def make_stub(name: str, **attrs: object) -> types.ModuleType:
    """A stand-in module carrying ``attrs``, not registered anywhere."""
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


def stub_module(name: str, **attrs: object) -> types.ModuleType:
    """Register a stand-in module under ``name`` so ``import name`` finds it.

    Used to satisfy a target module's import of an optional heavy dependency
    without installing it. Returns the stub so callers can assert on it. The
    registration is permanent for the process; inside a test prefer
    ``monkeypatch.setitem(sys.modules, name, make_stub(name, ...))``.
    """
    mod = make_stub(name, **attrs)
    sys.modules[name] = mod
    return mod


def stub_if_missing(monkeypatch: Any, *specs: tuple[str, Mapping[str, object]]) -> None:
    """Install a placeholder for each module that is not importable here.

    Only absent modules are stubbed — where the real one is installed it stays,
    so a stub can never mask a genuine incompatibility with it. ``monkeypatch``
    undoes the registration after the test.
    """
    for name, attrs in specs:
        try:
            __import__(name)
            continue
        except ImportError:
            pass
        module = make_stub(name, **attrs)
        monkeypatch.setitem(sys.modules, name, module)
        parent, _, child = name.rpartition(".")
        if parent and parent in sys.modules:
            monkeypatch.setattr(sys.modules[parent], child, module, raising=False)


def load_module_from_path(
    name: str,
    path: Path,
    *,
    extra_syspath: Sequence[Path] = (),
    stubs: Mapping[str, Mapping[str, object]] | None = None,
) -> types.ModuleType:
    """Load the module at ``path`` under ``name``.

    ``extra_syspath`` dirs are prepended to ``sys.path`` (so bare imports inside
    the module resolve). ``stubs`` maps a module name to the attributes its
    stand-in should expose, registered before the target is executed.
    """
    for entry in extra_syspath:
        entry_str = str(entry)
        if entry_str not in sys.path:
            sys.path.insert(0, entry_str)

    # The stubs only have to satisfy the target's own import statements; once
    # it has run, it holds references to them. Left in sys.modules they would
    # shadow the real module of that name for every test collected afterwards
    # (a stubbed `graph` or `db` broke 25 unrelated tests), so the previous
    # entries are put back.
    saved = {stub_name: sys.modules.get(stub_name) for stub_name in (stubs or {})}
    for stub_name, attrs in (stubs or {}).items():
        stub_module(stub_name, **attrs)

    try:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader, f"cannot build import spec for {path}"
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    finally:
        for stub_name, previous in saved.items():
            if previous is None:
                sys.modules.pop(stub_name, None)
            else:
                sys.modules[stub_name] = previous
    return module
