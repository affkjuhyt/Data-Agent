"""EV-08: a golden set written as YAML loads into eval.eval_set / eval.eval_item against PostgreSQL,
with the golden fields; a reload updates the same rows, retires what left the file and reports an
answer that changed with the event an edit through the API emits."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from services.api.kernel.auth import Principal
from services.api.modules.eval import cases_yaml as cy

from .conftest import Seed

pytestmark = [pytest.mark.integration]

FILE = """
eval_set: Câu hỏi NYC Taxi
pass_threshold: 0.85
min_items: 2
snapshot: {dataset: tlc, snapshot: TLC-2024Q1, sources_sha256: abc}
cases:
  - id: tlc001
    question: Số chuyến taxi vàng quý 1/2024
    expected: {kind: data, metric: SO_CHUYEN_VANG, values: [9554720]}
    verification_status: sql_run_on_snapshot
    reference_sql: SELECT count(*) FROM yellow_trip
  - id: tlc002
    question: Tip trung bình của chuyến trả tiền mặt
    category: refusal
    expected: {kind: refusal}
    expected_action: refuse_missing_source
    failure_category: missing_source
  - id: tlc003
    question: Tỷ lệ tip
    expected: {kind: data, metric: TY_LE_TIP_THE_VANG, values: [22.39]}
    rubric: numeric_tolerance(0.5)
"""

# FILE a week later: tlc001 verified by two people (not a contract change), tlc002 dropped,
# tlc003's answer corrected.
CHANGED = """
eval_set: Câu hỏi NYC Taxi
snapshot: {dataset: tlc, snapshot: TLC-2024Q1, sources_sha256: abc}
cases:
  - id: tlc001
    question: Số chuyến taxi vàng quý 1/2024
    expected: {kind: data, metric: SO_CHUYEN_VANG, values: [9554720]}
    verification_status: human_verified
  - id: tlc003
    question: Tỷ lệ tip
    expected: {kind: data, metric: TY_LE_TIP_THE_VANG, values: [22.4]}
    rubric: numeric_tolerance(0.5)
"""


class Space:
    def __init__(self, seed: Seed) -> None:
        self.seed = seed
        self.org = seed.org()
        self.user = seed.row(
            "org.app_user", org_id=self.org, external_id="kc-qa", username="qa", display_name="QA"
        )
        self.id = seed.row(
            "org.data_space", org_id=self.org, code="DS-TLC", name="NYC Taxi", steward_id=self.user
        )
        self.principal = Principal(
            user_id=self.user, org_id=self.org, external_id="kc-qa", data_space_id=self.id
        )

    async def load(self, text: str, dry_run: bool = False) -> cy.Report:
        return await cy.load(cy.parse(text), self.principal, source="tlc.yaml", dry_run=dry_run)

    def items(self) -> dict[str, tuple[Any, ...]]:
        with self.seed.conn.cursor() as cur:
            cur.execute(
                "SELECT i.input->>'question', i.expected_action, i.failure_category, i.rubric,"
                " i.verification_status, i.snapshot_ref->>'snapshot', i.status, i.category"
                " FROM eval.eval_item i JOIN eval.eval_set s ON s.id = i.eval_set_id"
                " WHERE s.data_space_id = %s",
                (str(self.id),),
            )
            return {r[0]: r[1:] for r in cur.fetchall()}

    def events(self) -> list[tuple[str, Any]]:
        with self.seed.conn.cursor() as cur:
            cur.execute(
                "SELECT event_type, payload FROM eval.outbox WHERE org_id = %s ORDER BY created_at",
                (str(self.org),),
            )
            return list(cur.fetchall())


async def test_a_golden_set_loads_with_its_fields(seed: Seed) -> None:
    space = Space(seed)
    dry = await space.load(FILE, dry_run=True)
    assert dry.created == 3 and space.items() == {}
    report = await space.load(FILE)
    assert (report.created, report.updated, report.retired) == (3, 0, 0)
    items = space.items()
    assert items["Số chuyến taxi vàng quý 1/2024"] == (
        "answer",
        "none",
        "exact",
        "sql_run_on_snapshot",
        "TLC-2024Q1",
        "active",
        "correct_result",
    )
    assert items["Tip trung bình của chuyến trả tiền mặt"][:3] == (
        "refuse_missing_source",
        "missing_source",
        "exact",
    )
    assert items["Tỷ lệ tip"][2:4] == ("numeric_tolerance(0.5)", "draft")
    with seed.conn.cursor() as cur:
        cur.execute(
            "SELECT pass_threshold, min_items FROM eval.eval_set WHERE data_space_id = %s",
            (str(space.id),),
        )
        threshold, min_items = cur.fetchone()
        assert (float(threshold), min_items) == (0.85, 2)


async def test_a_reload_updates_retires_and_reports_a_changed_answer(seed: Seed) -> None:
    space = Space(seed)
    await space.load(FILE)
    again = await space.load(FILE)
    assert (again.created, again.updated, again.unchanged) == (0, 0, 3)

    report = await space.load(CHANGED)
    # tlc001: verified only (no contract change); tlc002: left the file; tlc003: a new answer.
    assert (report.updated, report.retired, report.answer_changed) == (2, 1, ["tlc003"])
    items = space.items()
    assert items["Tip trung bình của chuyến trả tiền mặt"][5] == "retired"
    assert items["Số chuyến taxi vàng quý 1/2024"][3] == "human_verified"
    (event,) = (e for e in space.events() if e[0] == "eval_item.answer_changed")
    assert event[1]["changed_fields"] == ["expected"]
    assert event[1]["reason"] == "loaded from tlc.yaml"


async def test_an_item_id_is_stable_across_loads_and_organisations(seed: Seed) -> None:
    org = uuid.uuid4()
    assert cy.item_id(org, "S", "a1") == cy.item_id(org, "S", "a1")
    assert cy.item_id(org, "S", "a1") != cy.item_id(uuid.uuid4(), "S", "a1")
