"""A Data Space's eval set as a YAML file, loaded into eval.eval_set and eval.eval_item (EV-08).

The golden sets of the real-data evaluation (docs/blueprint/DATASET_AND_EVALUATION.md §5) are
written as files and reviewed in git; this puts one into the product, where an eval run reads it:

    eval_set: Câu hỏi NYC Taxi            # the set, by name within the Data Space
    pass_threshold: 0.85                  # optional, the set's own defaults otherwise
    min_items: 60
    snapshot: {dataset: tlc, snapshot: TLC-2024Q1, sources_sha256: …}   # every case's, unless its own
    cases:
      - id: tlc001                        # stable: the item's id is derived from it
        question: Số chuyến taxi vàng quý 1/2024      # or input: {…}
        category: correct_result
        expected: {kind: data, metric: SO_CHUYEN_VANG, values: [9554720]}
        expected_action: answer           # the EV-08 fields; the table's defaults when absent
        failure_category: none
        rubric: exact
        verification_status: sql_run_on_snapshot
        as_of: 2024-03-31
        reference_sql: SELECT …           # kept in the file for its tests, not loaded
        note: …                           # idem

Loading is idempotent: an item's id is a uuid5 of the organisation, the set and the case id, so a
second load updates the same rows. A case that left the file is retired, never deleted (its runs
point at it). A case whose answer changed emits eval_item.answer_changed, as an edit through the
API does, with the file as its reason.

Run where the api runs (it reads DATAAGENT_DB_URL__SVC_EVAL):

    python -m services.api.modules.eval.cases_yaml import FILE --org <id> --space <code> --as <user> [--dry-run]

(`make eval-cases-import` runs it in the api pod.)
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import datetime
import decimal
import sys
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select

from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import eval as evaluation
from libs.dataagent_db.models import org as org_models
from services.api.kernel.auth import Principal
from services.api.modules.eval.schemas import (
    RUBRIC,
    Category,
    ExpectedAction,
    FailureCategory,
    VerificationStatus,
)

ROLE = "svc_eval"
# uuid5 namespace of loaded items; fixed, so ids survive every reload.
NAMESPACE = uuid.UUID("5b1d7e2c-4f0a-4b8e-9c3d-2a6f8e1b0c47")
# The fields of an item that make up its answer: changing one is a contract change (routes.py).
CONTRACT = (
    "input",
    "expected",
    "as_of",
    "tolerance",
    "expected_action",
    "failure_category",
    "rubric",
    "snapshot_ref",
)


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CaseDef(_Item):
    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
    question: str | None = Field(default=None, min_length=1)
    input: dict[str, Any] | None = None
    category: Category = "correct_result"
    expected: dict[str, Any]
    as_of: datetime.date | None = None
    tolerance: decimal.Decimal | None = Field(default=None, ge=0)
    expected_action: ExpectedAction = "answer"
    failure_category: FailureCategory = "none"
    rubric: str = Field(default="exact", pattern=RUBRIC)
    verification_status: VerificationStatus = "draft"
    snapshot_ref: dict[str, Any] | None = None
    reference_sql: str | None = None
    note: str | None = None
    lang: str | None = None
    history: list[dict[str, Any]] | None = None

    @model_validator(mode="after")
    def _one_input(self) -> CaseDef:
        if (self.question is None) == (self.input is None):
            raise ValueError("a case has a question or an input, one of them")
        return self

    def as_input(self) -> dict[str, Any]:
        if self.input is not None:
            return self.input
        out: dict[str, Any] = {"question": self.question}
        if self.lang:
            out["lang"] = self.lang
        if self.history:
            out["history"] = self.history
        return out


class CasesFile(_Item):
    eval_set: str = Field(min_length=1, max_length=200)
    pass_threshold: decimal.Decimal | None = Field(default=None, gt=0, le=1)
    min_items: int | None = Field(default=None, ge=1)
    snapshot: dict[str, Any] | None = None
    cases: list[CaseDef] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_ids(self) -> CasesFile:
        twice = [k for k, n in Counter(c.id for c in self.cases).items() if n > 1]
        if twice:
            raise ValueError(f"case ids used twice: {twice}")
        return self


class _DryRun(Exception):
    """Leaves the transaction without its writes (the dry run), after the database checked them."""


class ImportProblem(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


def parse(text: str) -> CasesFile:
    try:
        return CasesFile.model_validate(yaml.safe_load(text))
    except ValidationError as e:
        raise ImportProblem(
            [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()]
        ) from e


def item_id(org_id: uuid.UUID, set_name: str, case_id: str) -> uuid.UUID:
    return uuid.uuid5(NAMESPACE, f"{org_id}:{set_name}:{case_id}")


@dataclass
class Report:
    dry_run: bool
    eval_set: str = ""
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    retired: int = 0
    answer_changed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "eval_set": self.eval_set,
            "items": {
                "created": self.created,
                "updated": self.updated,
                "unchanged": self.unchanged,
                "retired": self.retired,
            },
            "answer_changed": self.answer_changed,
        }


def _row(case: CaseDef, default_snapshot: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "category": case.category,
        "input": case.as_input(),
        "as_of": case.as_of,
        "expected": case.expected,
        "tolerance": case.tolerance,
        "expected_action": case.expected_action,
        "failure_category": case.failure_category,
        "rubric": case.rubric,
        "verification_status": case.verification_status,
        "snapshot_ref": case.snapshot_ref if case.snapshot_ref is not None else default_snapshot,
        "status": "active",
    }


def _same(old: Any, new: Any) -> bool:
    if isinstance(old, decimal.Decimal) or isinstance(new, decimal.Decimal):
        return (old is None and new is None) or (
            old is not None and new is not None and decimal.Decimal(old) == decimal.Decimal(new)
        )
    return bool(old == new)


async def load(
    file: CasesFile, principal: Principal, *, source: str, dry_run: bool = False
) -> Report:
    """Write the file's set and items for the principal's Data Space; nothing when dry_run."""
    report = Report(dry_run=dry_run, eval_set=file.eval_set)
    with contextlib.suppress(_DryRun):
        await _write(file, principal, source, report)
    return report


async def _write(file: CasesFile, principal: Principal, source: str, report: Report) -> None:
    assert principal.data_space_id is not None
    async with scoped_session(ROLE, principal.org_id, principal.user_id) as session:
        eval_set = await session.scalar(
            select(evaluation.EvalSet)
            .where(
                evaluation.EvalSet.org_id == principal.org_id,
                evaluation.EvalSet.data_space_id == principal.data_space_id,
                evaluation.EvalSet.subject_type == "context",
                evaluation.EvalSet.name == file.eval_set,
            )
            .with_for_update()
        )
        if eval_set is None:
            eval_set = evaluation.EvalSet(
                id=uuid.uuid4(),
                org_id=principal.org_id,
                data_space_id=principal.data_space_id,
                subject_type="context",
                name=file.eval_set,
                status="active",
                created_by=principal.user_id,
                updated_by=principal.user_id,
            )
            session.add(eval_set)
        if file.pass_threshold is not None:
            eval_set.pass_threshold = file.pass_threshold
        if file.min_items is not None:
            eval_set.min_items = file.min_items
        await session.flush()
        existing = {
            item.id: item
            for item in await session.scalars(
                select(evaluation.EvalItem).where(evaluation.EvalItem.eval_set_id == eval_set.id)
            )
        }
        now = datetime.datetime.now(datetime.UTC)
        loaded: set[uuid.UUID] = set()
        for case in file.cases:
            iid = item_id(principal.org_id, file.eval_set, case.id)
            loaded.add(iid)
            row = _row(case, file.snapshot)
            item = existing.get(iid)
            if item is None:
                session.add(
                    evaluation.EvalItem(
                        id=iid,
                        org_id=principal.org_id,
                        eval_set_id=eval_set.id,
                        origin="manual",
                        created_by=principal.user_id,
                        updated_by=principal.user_id,
                        **row,
                    )
                )
                report.created += 1
                continue
            changed = [k for k, v in row.items() if not _same(getattr(item, k), v)]
            if not changed:
                report.unchanged += 1
                continue
            for key in changed:
                setattr(item, key, row[key])
            item.updated_by, item.updated_at = principal.user_id, now
            report.updated += 1
            contract = sorted(set(changed) & set(CONTRACT))
            if contract:
                report.answer_changed.append(case.id)
                session.add(
                    evaluation.Outbox(
                        id=uuid.uuid4(),
                        org_id=principal.org_id,
                        aggregate_type="eval_item",
                        aggregate_id=iid,
                        event_type="eval_item.answer_changed",
                        payload={
                            "eval_item_id": str(iid),
                            "eval_set_id": str(eval_set.id),
                            "reason": f"loaded from {source}",
                            "changed_fields": contract,
                        },
                    )
                )
        for iid, item in existing.items():
            if iid not in loaded and item.origin == "manual" and item.status == "active":
                item.status = "retired"
                item.updated_by, item.updated_at = principal.user_id, now
                report.retired += 1
        await session.flush()
        if report.dry_run:
            raise _DryRun


async def _principal(org_id: uuid.UUID, space_code: str, username: str) -> Principal:
    async with scoped_session(ROLE, org_id, None) as session:
        space = await session.scalar(
            select(org_models.DataSpace).where(
                org_models.DataSpace.org_id == org_id, org_models.DataSpace.code == space_code
            )
        )
        user = await session.scalar(
            select(org_models.AppUser).where(
                org_models.AppUser.org_id == org_id, org_models.AppUser.username == username
            )
        )
    if space is None:
        raise SystemExit(f"no Data Space {space_code} in organisation {org_id}")
    if user is None:
        raise SystemExit(f"no user {username} in organisation {org_id}")
    return Principal(
        user_id=user.id, org_id=org_id, external_id=user.external_id or "", data_space_id=space.id
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cases_yaml", description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("import")
    p.add_argument("file", help="the YAML file, or - for stdin")
    p.add_argument("--org", type=uuid.UUID, required=True)
    p.add_argument("--space", required=True)
    p.add_argument("--as", dest="username", required=True)
    p.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    try:
        file = parse(text)
    except ImportProblem as e:
        print("\n".join(e.problems), file=sys.stderr)
        return 1

    async def run() -> Report:
        principal = await _principal(args.org, args.space, args.username)
        source = "stdin" if args.file == "-" else Path(args.file).name
        return await load(file, principal, source=source, dry_run=args.dry_run)

    print(yaml.safe_dump(asyncio.run(run()).as_dict(), allow_unicode=True, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
