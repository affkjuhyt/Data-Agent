"""Required-environment helper.

Services used to default their DSN to a database that did not exist
("postgresql://aeroflow:aeroflow_secret@postgres:5432/aeroflow"), so a missing
variable produced a connection attempt to the wrong place instead of an error.

Removing the default outright is also wrong: reading it at import time makes the
module unimportable without the variable, which breaks unit tests that never
open a connection. So read lazily and raise at the point of use.
"""
from __future__ import annotations

import os


def require_env(name: str, *, hint: str = "") -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        suffix = f" {hint}" if hint else ""
        raise RuntimeError(
            f"{name} is not set. It has no default because the previous default "
            f"named a database that does not exist.{suffix}"
        )
    return value
