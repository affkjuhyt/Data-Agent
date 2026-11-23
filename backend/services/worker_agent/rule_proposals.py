"""Data-quality rules proposed from a column's profile (DF-11).

Deterministic, no LLM: a profile scan reads each column's nulls, distinct values, category and
value patterns, and from them proposes the checks the data already passes on the sample. Each is
an ingest.quality_rule with origin agent_suggested and status draft: it runs on nothing until a
steward turns it active (PATCH /v1/quality-rules/{id}), and one the steward disabled, or any
rule of the same kind on the same column, is not proposed again.

What the sample shows is not what the source promises: a rule is a proposal for that reason, and
the thresholds below keep coincidences out (a handful of rows is unique by chance).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from libs import quality_vocab
from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import ingest

ROLE = "svc_ingest"
# Below this share of nulls a column is proposed not_null.
NOT_NULL_BELOW = 0.001
# The fewest sampled rows a unique or in_list proposal rests on.
MIN_ROWS = 20
IN_SET_MAX = 20
REGEX_SHARE = 0.99
_UNIQUE_TYPES = frozenset({"string", "integer"})
_IN_SET_TYPES = frozenset({"string", "integer"})
# Both Python's re (the api validates a rule with it) and PostgreSQL's ARE (the check runs with
# ~) read these the same way; a letter class written [^\W\d_] is Python's only.
_PORTABLE = re.compile(r"^(?:\\d|\[A-Z\]|\[a-z\]|\{\d+\}|\\[^\w\s]|\\ |[^\\\[\]{}])*$")


@dataclass(frozen=True)
class Proposal:
    dataset_id: uuid.UUID
    column_name: str
    kind: str
    params: dict[str, Any]


def propose(
    dataset_id: uuid.UUID,
    column_name: str,
    normalized_type: str,
    *,
    is_nullable: bool,
    is_key: bool,
    personal: bool,
    profile: dict[str, Any],
) -> list[Proposal]:
    """The rules a column's profile supports, in a fixed order."""
    rows = int(profile.get("sample_rows") or 0)
    if rows <= 0:
        return []
    out: list[Proposal] = []
    null_ratio = float(profile.get("null_ratio") or 0)
    distinct = int(profile.get("distinct_est") or 0)
    non_null = rows - round(null_ratio * rows)

    def add(kind: str, params: dict[str, Any]) -> None:
        quality_vocab.validate(kind, column_name, params)
        out.append(Proposal(dataset_id, column_name, kind, params))

    # A column the source already declares NOT NULL, or a key, needs no rule saying so.
    if is_nullable and not is_key and null_ratio < NOT_NULL_BELOW:
        add("not_null", {})
    if (
        not is_key
        and normalized_type in _UNIQUE_TYPES
        and non_null >= MIN_ROWS
        and null_ratio == 0
        and distinct == rows
    ):
        add("unique", {})
    category = profile.get("category_values")
    # A personal column's values are never written into a rule.
    if (
        not personal
        and normalized_type in _IN_SET_TYPES
        and 1 < distinct <= IN_SET_MAX
        and non_null >= MIN_ROWS
        and distinct < non_null
        and isinstance(category, list)
        and len(category) == distinct
        and all(isinstance(v, dict) and v.get("value") != "***" for v in category)
    ):
        add("in_list", {"values": sorted(str(v["value"]) for v in category)})
    patterns = profile.get("value_patterns") or []
    if (
        normalized_type == "string"
        and patterns
        and float(patterns[0]["share"]) >= REGEX_SHARE
        and _PORTABLE.match(str(patterns[0]["pattern"]))
        # A single repeated value says nothing an in_list does not.
        and distinct > 1
    ):
        add("match_regex", {"regex": f"^{patterns[0]['pattern']}$", "mostly": REGEX_SHARE})
    return out


async def write(
    org_id: uuid.UUID,
    data_space_id: uuid.UUID,
    proposals: list[Proposal],
    by: uuid.UUID | None,
) -> int:
    """Write the proposals a dataset has no rule of the same kind on the same column for, in any
    status; returns how many were written."""
    if not proposals:
        return 0
    written = 0
    async with scoped_session(ROLE, org_id, by) as session:
        dataset_ids = sorted({p.dataset_id for p in proposals})
        existing = {
            (r.dataset_id, r.column_name, r.kind)
            for r in (
                await session.execute(
                    select(
                        ingest.QualityRule.dataset_id,
                        ingest.QualityRule.column_name,
                        ingest.QualityRule.kind,
                    ).where(
                        ingest.QualityRule.org_id == org_id,
                        ingest.QualityRule.dataset_id.in_(dataset_ids),
                    )
                )
            ).all()
        }
        for p in proposals:
            key = (p.dataset_id, p.column_name, p.kind)
            if key in existing:
                continue
            existing.add(key)
            session.add(
                ingest.QualityRule(
                    id=uuid7(),
                    org_id=org_id,
                    data_space_id=data_space_id,
                    dataset_id=p.dataset_id,
                    column_name=p.column_name,
                    kind=p.kind,
                    params=p.params,
                    severity="low",
                    origin="agent_suggested",
                    status="draft",
                    created_by=by,
                    updated_by=by,
                )
            )
            written += 1
    return written
