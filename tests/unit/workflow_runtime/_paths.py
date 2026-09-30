"""sys.path entries for the tests that import workflow_runtime modules by bare name.

The runtime is split into layers (api, application, domain, infrastructure); a bare
`import graph` or `from nodes.x import y` resolves only when the directory holding
that module is on sys.path, so a test that used to put the one service dir there now
puts all four.
"""
from __future__ import annotations

from .._loader import SERVICES

RUNTIME_DIR = SERVICES / "workflow_runtime"
RUNTIME_LAYER_DIRS = tuple(
    str(RUNTIME_DIR / layer) for layer in ("api", "application", "domain", "infrastructure")
)
