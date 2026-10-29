"""Pure rules of the catalog module: OpenBao references, source status changes.

No database and no FastAPI here, so each rule is tested on its own.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

# Kinds a federated query can reach in place; the others are ingest only (UC-04-01 step 3, the
# CHECK on catalog.source). Used to pick the default mode, the CHECK still decides.
QUERYABLE_KINDS = frozenset(
    {
        "postgresql",
        "oracle",
        "sqlserver",
        "mysql",
        "snowflake",
        "bigquery",
        "databricks",
        "clickhouse",
    }
)


def default_mode(kind: str) -> str:
    return "federated" if kind in QUERYABLE_KINDS else "ingest"


# --- OpenBao references (BR-14) -----------------------------------------------------------------

# tab 1 §8.5: source accounts live in KV v2 `secret/`, under `tenants/{tenant_id}/` so one
# organisation's paths never overlap another's. svc roles read any path the deploy grants, so
# the org boundary of a reference is enforced here, where the reference enters the system.
_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


class SecretRefOutsideOrg(ValueError):
    """The reference is not a path under the caller organisation's OpenBao namespace."""


def tenant_prefix(org_id: uuid.UUID) -> str:
    return f"secret/tenants/{org_id}/"


def check_secret_ref(ref: str, org_id: uuid.UUID) -> str:
    """The reference, if it names a secret of `org_id`; raises SecretRefOutsideOrg otherwise.

    `..`, empty segments and anything but [A-Za-z0-9._-] in a segment are refused, so a path
    that starts with the prefix cannot climb out of it."""
    prefix = tenant_prefix(org_id)
    if not ref.startswith(prefix):
        raise SecretRefOutsideOrg(f"secret_ref must start with {prefix}")
    rest = ref[len(prefix) :]
    segments = rest.split("/")
    if not rest or any(s in ("", ".", "..") or not _SEGMENT.match(s) for s in segments):
        raise SecretRefOutsideOrg("secret_ref is not a plain path under the organisation")
    return ref


# --- source status (PATCH /v1/sources/{id}) -----------------------------------------------------


def status_change(current: str, target: str, *, tested: bool) -> str | None:
    """Why a caller may not move a source from `current` to `target`, or None when allowed.

    Only a passed connection test makes a source active (UC-04-01 step 2): a person pauses,
    resumes a paused source that had passed its test, or archives; archived is final."""
    if current == target:
        return None
    if current == "archived":
        return "An archived source cannot change status."
    if target == "archived":
        return None
    if target == "paused":
        if current in ("active", "needs_fix"):
            return None
        return f"A {current} source cannot be paused."
    if target == "active":
        if current == "paused" and tested:
            return None
        return "Only a paused source that passed its connection test can be resumed; test it first."
    return f"Status {target} is set by the system, not by a person."


# --- PII masking (UC-04-02 step 4) --------------------------------------------------------------

MASK = "***"


def mask_profile_values(top_values: Any) -> Any:
    """Top values of a personal-data column with each value masked, counts kept.

    The scan masks before it stores; this holds even for a profile written before the column
    was labelled."""
    if not isinstance(top_values, list):
        return None if top_values is None else MASK
    return [{**item, "value": MASK} if isinstance(item, dict) else MASK for item in top_values]
