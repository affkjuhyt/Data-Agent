"""What may be used as the name of a file staged into the sandbox.

One rule, one place. Two callers need it and they need the same answer:

  * main.py validates it when the request arrives, so a malformed name is a 422
    the caller can read rather than a job that fails somewhere in a worker.
  * docker_runner._stage_files validates it again at the moment the name becomes
    the target of a shell redirect inside the container — the point where being
    wrong actually costs something.

Written out twice, the two spellings drift, and the one that drifts is never the
one anyone remembers to update. Kept here so both call the same function, and so
the policy evaluation (model/eval) can score the rule that actually runs instead
of a third copy of it.
"""
from __future__ import annotations

# A staged name is extracted into SANDBOX_INPUT_DIR and interpolated into a
# redirect target. Anything that can leave that directory is refused: not just
# "..", but any separator, on either platform's spelling.
_SEPARATORS = ("/", "\\")
_RESERVED = (".", "..")


def validate_input_name(name: str) -> str:
    """Return `name` when it is a single path segment; raise ValueError if not.

    ValueError rather than a bespoke exception type: pydantic renders it as a
    field error at the request boundary, and docker_runner wraps it in its own
    SandboxRunError where a run-time failure is the right shape.
    """
    if not isinstance(name, str) or not name or name in _RESERVED:
        raise ValueError(f"input file name must be a bare filename: {name!r}")
    if any(separator in name for separator in _SEPARATORS):
        raise ValueError(f"input file name must be a bare filename: {name!r}")
    return name


__all__ = ["validate_input_name"]
