"""sys.path entries for the service modules the scorers evaluate.

Several services live in hyphenated directories (`services/sandbox-executor`,
`services/workflow-runtime`) which are not importable package names, and they
use bare imports that assume their own directory is on sys.path. Same problem
tests/unit/_loader.py solves for the test suite; kept separate because the eval
harness ships in the ragas image, where the test package is not present.

The scorers import the REAL modules deliberately. An eval that re-implemented
the rule it is measuring would measure the re-implementation.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
SANDBOX_EXECUTOR = REPO_ROOT / "services" / "sandbox-executor"
WORKFLOW_RUNTIME = REPO_ROOT / "services" / "workflow-runtime"


def ensure(*directories: Path) -> None:
    """Prepend each directory to sys.path once."""
    for directory in (REPO_ROOT, *directories):
        entry = str(directory)
        if entry not in sys.path:
            sys.path.insert(0, entry)


def load_module(name: str, path: Path) -> ModuleType:
    """Load one module from its file, WITHOUT executing its package __init__.

    `nodes/sandbox/tool_executor/_utils.py` holds a pure path check inside a
    package whose __init__ imports the whole node graph, and through it litellm.
    Importing it normally would make an offline evaluation of a stdlib-only
    function depend on a model client being installed — so the file is loaded
    directly. It is still the real module, not a copy of the rule.
    """
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
