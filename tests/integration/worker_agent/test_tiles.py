"""UC-02-02: worker-agent runs a workspace's tiles in dependency order on the bank schema of the
answer tests, writes one snapshot per runnable tile, and skips the dependents of a failed one."""

from __future__ import annotations

import datetime
import json
import uuid
from typing import Any

import pytest

from services.worker_agent import tiles
from services.worker_agent.events import OutboxTableSource
from services.worker_agent.main import run_once
from services.worker_agent.query_runs import HISTORY

from . import test_answers
from .test_answers import Bank, build

warehouse = test_answers.warehouse

pytestmark = [pytest.mark.integration]


def _workspace(bank: Bank) -> uuid.UUID:
    return bank.seed.row(
        "workspace.workspace",
        org_id=bank.org,
        data_space_id=bank.space,
        owner_id=bank.user,
        title="Nợ xấu T9",
    )


def _tile(
    bank: Bank, ws: uuid.UUID, position: int, kind: str, spec: dict[str, Any], *deps: uuid.UUID
) -> uuid.UUID:
    return bank.seed.row(
        "workspace.tile",
        org_id=bank.org,
        workspace_id=ws,
        position=position,
        kind=kind,
        spec=json.dumps(spec, default=str),
        depends_on=list(deps),
    )


def _emit(bank: Bank, event_type: str, ws: uuid.UUID, order: list[uuid.UUID], **extra: Any) -> None:
    bank.seed.row(
        "workspace.outbox",
        org_id=bank.org,
        aggregate_type="workspace",
        aggregate_id=ws,
        event_type=event_type,
        payload=json.dumps(
            {
                "workspace_id": str(ws),
                "data_space_id": str(bank.space),
                "requested_by": str(bank.user),
                "tile_ids": [str(t) for t in order],
                **extra,
            },
            default=str,
        ),
    )


async def _drain() -> None:
    source = OutboxTableSource(schema="workspace", event_types=frozenset(tiles.HANDLERS))
    while await run_once(source, tiles.HANDLERS, batch=50):
        pass


def _latest(bank: Bank, tile: uuid.UUID) -> tuple[Any, ...] | None:
    return bank.seed.one(  # type: ignore[return-value]
        "SELECT s.status, s.error, cardinality(s.query_run_ids), s.summary FROM workspace.tile t"
        " JOIN workspace.tile_snapshot s ON s.id = t.latest_snapshot_id WHERE t.id = %s",
        tile,
    )


@pytest.mark.req("AC-02-01-06")
async def test_uc_02_02_a_refresh_runs_each_tile_in_order_and_skips_after_a_failure(
    seed: Any, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    ws = _workspace(bank)
    sql = _tile(
        bank, ws, 0, "sql", {"sql": "SELECT branch_code, sum(balance) AS b FROM loan GROUP BY 1"}
    )
    chart = _tile(bank, ws, 1, "chart", {"type": "bar"}, sql)
    question = _tile(bank, ws, 2, "question", {"question": "Tỷ lệ nợ xấu theo vùng tháng 9/2026"})
    table = _tile(bank, ws, 3, "table", {"dataset_id": str(bank.loans), "filters": [], "limit": 2})
    note = _tile(bank, ws, 4, "text", {"md": "Ghi chú"})
    broken = _tile(bank, ws, 5, "sql", {"sql": "SELECT * FROM pg_catalog.pg_roles"})
    after = _tile(bank, ws, 6, "chart", {"type": "line"}, broken)
    order = [sql, chart, question, table, note, broken, after]
    _emit(bank, tiles.REFRESH, ws, order, as_of=None)
    await _drain()

    status, error, runs, summary = _latest(bank, sql)  # type: ignore[misc]
    assert (status, error, runs) == ("ok", None, 1) and summary["row_count"] == 3
    assert _latest(bank, chart)[:3] == ("ok", None, 1)  # type: ignore[index]
    status, _, runs, summary = _latest(bank, question)  # type: ignore[misc]
    # The snapshot is every member's: it holds the run, not the answer's text or values.
    assert status == "ok" and runs == 1 and summary == {"figure_count": 2}
    status, _, _, summary = _latest(bank, table)  # type: ignore[misc]
    assert status == "ok" and summary["row_count"] == 2 and summary["truncated"]
    assert _latest(bank, note) is None
    status, error, _, _ = _latest(bank, broken)  # type: ignore[misc]
    assert status == "failed" and "Data Space" in error
    assert _latest(bank, after)[:2] == ("failed", tiles.DEPENDENCY_FAILED)  # type: ignore[index]
    assert seed.one(
        "SELECT last_refreshed_at IS NOT NULL FROM workspace.workspace WHERE id = %s", ws
    )
    # sql, question and table ran; the chart reused the sql tile's run, and the broken tile was
    # refused before any run was recorded.
    assert (
        seed.one(
            "SELECT count(*) FROM query.query_run WHERE caller_kind = 'tile' AND org_id = %s",
            bank.org,
        )
        == 3
    )


async def test_a_tile_run_reruns_the_tile_and_its_dependents(
    seed: Any, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    ws = _workspace(bank)
    sql = _tile(bank, ws, 0, "sql", {"sql": "SELECT count(*) AS n FROM bank.loan"})
    chart = _tile(bank, ws, 1, "chart", {"type": "bar"}, sql)
    seed.one("UPDATE workspace.tile SET stale = true WHERE id = %s RETURNING 1", chart)
    _emit(bank, tiles.TILE_RUN, ws, [sql, chart])
    await _drain()
    assert _latest(bank, chart)[:3] == ("ok", None, 1)  # type: ignore[index]
    assert seed.one("SELECT stale FROM workspace.tile WHERE id = %s", chart) is False


async def test_a_past_knowledge_time_fails_instead_of_running_live(
    seed: Any, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    ws = _workspace(bank)
    sql = _tile(bank, ws, 0, "sql", {"sql": "SELECT count(*) AS n FROM bank.loan"})
    _emit(bank, tiles.REFRESH, ws, [sql], as_of=datetime.date(2026, 1, 31))
    await _drain()
    assert _latest(bank, sql)[:2] == ("failed", HISTORY)  # type: ignore[index]
