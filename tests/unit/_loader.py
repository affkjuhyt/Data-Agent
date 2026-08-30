"""Helpers to import service modules into unit tests.

Several services live in hyphenated directories (``backend/services/auth-api``,
``backend/services/release-worker``, ``backend/services/workflow-runtime``) which are NOT valid
Python package names, and some use bare imports (``from shared.auth import ...``,
``import llm_client``) that assume the service's own directory is on ``sys.path``.

``load_module_from_path`` loads such a module by file path, with the extra
``sys.path`` entries the module needs, and optional stand-in modules for heavy
optional dependencies (opentelemetry, neo4j, …) that aren't installed in the
unit-test environment. This keeps unit tests pure-Python and Docker-free.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from collections.abc import Mapping, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES = REPO_ROOT / "backend" / "services"


def stub_module(name: str, **attrs: object) -> types.ModuleType:
    """Register a stand-in module under ``name`` so ``import name`` finds it.

    Used to satisfy a target module's import of an optional heavy dependency
    without installing it. Returns the stub so callers can assert on it.
    """
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    sys.modules[name] = mod
    return mod


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

    for stub_name, attrs in (stubs or {}).items():
        stub_module(stub_name, **attrs)

    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"cannot build import spec for {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
