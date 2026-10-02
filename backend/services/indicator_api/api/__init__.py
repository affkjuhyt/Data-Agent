"""HTTP routes of indicator-api, typed with Contract v1 (libs/contracts/v1)."""

from __future__ import annotations

from typing import Any, NoReturn

from fastapi import HTTPException

# Declared on every route so the published OpenAPI says what the skeleton answers.
NOT_IMPLEMENTED_RESPONSE: dict[int | str, dict[str, Any]] = {
    501: {"description": "Route published, implementation pending"}
}


def not_implemented() -> NoReturn:
    # The route table is fixed by NOP-162; each body is filled by the issue that
    # owns its domain (Observation, Governance, Capability). Contract v1 has no
    # error code for "not built yet", so this is a plain 501, not an ErrorBody.
    raise HTTPException(status_code=501, detail="not implemented")
