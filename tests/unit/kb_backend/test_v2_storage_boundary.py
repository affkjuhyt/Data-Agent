"""V2 storage boundary (NOP-163): a V2 structured source — one whose active
`KBSourceConfig.config` has an `extraction_profile` — never enters the legacy
chunk path (Silver/Gold) or the v1 structured-KB path (`kb.structured.propose`
→ `kb_structured` tables), and V2 packages never import or publish to them.

Every entry point is checked, not just the one a button uses: kb_backend's
routes refuse with 409, and the Silver, Gold and structured-propose consumers
refuse again, so a replayed Kafka event is stopped too. The crawler side is
covered by the integration web-crawler worker test, where Bronze landing and
auto-promotion are exercised against real migrations.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from libs.ingestion import config as cfg
from libs.ingestion import storage_boundary
from libs.ingestion.gold import orchestrator as gold_orchestrator
from libs.ingestion.runtime import WorkerRuntime
from libs.ingestion.silver import orchestrator as silver_orchestrator
from libs.shared.storage_ownership import (
    LEGACY_IMPORTS,
    LEGACY_TOPICS,
    V2_STRUCTURED_SOURCE_ERROR,
    is_v2_structured_source,
)
from services.kb_backend.operations import document_ops
from services.kb_backend.workers.promote_silver import handler as silver_handler
from services.kb_backend.workers.structured_data import propose

pytestmark = pytest.mark.unit

DATA_ID = "11111111-1111-1111-1111-111111111111"
TENANT_ID = "22222222-2222-2222-2222-222222222222"
SOURCE_ID = "33333333-3333-3333-3333-333333333333"
PINK_SHEET_PROFILE = {
    "kind": "xlsx_table",
    "sheet": "Monthly Prices",
    "frequency": "M",
    "series": [
        {"column": "Crude oil, Brent", "series_key": "WB:PINK:BRENT:M", "unit": "usd_per_barrel"}
    ],
}


# ── the marker ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({"url": "u", "extraction_profile": PINK_SHEET_PROFILE}, True),
        ({"url": "u", "promotion_mode": "auto"}, False),
        ({"url": "u", "extraction_profile": {}}, False),  # a half-filled form is not a declaration
        ({"url": "u", "extraction_profile": None}, False),
        ({}, False),
        (None, False),
    ],
)
def test_a_source_is_v2_structured_only_when_it_declares_an_extraction_profile(
    config: dict[str, Any] | None,
    expected: bool,
) -> None:
    assert is_v2_structured_source(config) is expected


# ── document → source lookup ───────────────────────────────────────────────


class _Session:
    def __init__(self, answers: list[Any]) -> None:
        self._answers = list(answers)
        self.queries = 0

    async def scalar(self, *_: Any, **__: Any) -> Any:
        self.queries += 1
        return self._answers.pop(0)


class _Postgres:
    def __init__(self, *answers: Any) -> None:
        self.session = _Session(list(answers))
        self.scoped_to: list[str | None] = []

    @asynccontextmanager
    async def get_client(self, tenant_id: str | None = None) -> Any:
        self.scoped_to.append(tenant_id)
        yield self.session


def _lookup(postgres: _Postgres, data_id: str = DATA_ID) -> bool:
    return asyncio.run(storage_boundary.is_v2_structured_document(postgres, data_id, TENANT_ID))


def test_a_document_crawled_from_a_profiled_source_is_v2_structured() -> None:
    postgres = _Postgres(
        {"source_id": SOURCE_ID, "promotion_mode": "manual"},
        {"extraction_profile": PINK_SHEET_PROFILE},
    )
    assert _lookup(postgres) is True
    # Read on a tenant-scoped session, never one that sees every tenant's documents.
    assert postgres.scoped_to == [TENANT_ID]


def test_a_document_from_a_plain_document_source_is_not() -> None:
    assert (
        _lookup(_Postgres({"source_id": SOURCE_ID}, {"url": "u", "promotion_mode": "auto"}))
        is False
    )


def test_a_manual_upload_has_no_source_and_is_never_v2_structured() -> None:
    postgres = _Postgres({"source_type": "doc"})
    assert _lookup(postgres) is False
    assert postgres.session.queries == 1  # no config lookup without a source_id


@pytest.mark.parametrize("data_id", ["", "not-a-uuid"])
def test_an_unparseable_data_id_is_not_looked_up(data_id: str) -> None:
    postgres = _Postgres()
    assert _lookup(postgres, data_id) is False
    assert postgres.session.queries == 0


def test_a_missing_document_or_a_deleted_source_config_is_not_v2_structured() -> None:
    assert _lookup(_Postgres(None)) is False
    assert _lookup(_Postgres({"source_id": SOURCE_ID}, None)) is False


# ── Silver and Gold consumers ───────────────────────────────────────────────


def test_silver_refuses_a_v2_structured_document_before_writing_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        silver_orchestrator, "is_v2_structured_document", AsyncMock(return_value=True)
    )
    stamp = AsyncMock()
    monkeypatch.setattr(silver_orchestrator, "stamp_pipeline_status", stamp)
    mongo, minio, docling = MagicMock(), MagicMock(), MagicMock()

    result = asyncio.run(
        silver_orchestrator.promote_to_silver(
            minio=minio,
            postgres=MagicMock(),
            docling=docling,
            mongo=mongo,
            data_id=DATA_ID,
            tenant_id=TENANT_ID,
            approved_by="tester",
        )
    )

    assert result == {
        "data_id": DATA_ID,
        "chunks_count": 0,
        "layer": "bronze",
        "skipped": "v2_structured_source",
    }
    mongo.get_client.assert_not_called()  # no Mongo log, no Silver staging
    stamp.assert_not_called()
    docling.assert_not_called()
    minio.assert_not_called()


def test_gold_refuses_a_v2_structured_document(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gold_orchestrator,
        "_get_data_row",
        AsyncMock(return_value=("Pink Sheet", "KB", "production")),
    )
    monkeypatch.setattr(
        gold_orchestrator, "is_v2_structured_document", AsyncMock(return_value=True)
    )
    mongo = MagicMock()

    result = asyncio.run(
        gold_orchestrator.promote_to_gold(
            postgres=MagicMock(),
            qdrant=MagicMock(),
            graph=MagicMock(),
            mongo=mongo,
            model=MagicMock(),
            data_id=DATA_ID,
            tenant_id=TENANT_ID,
            approved_by="tester",
        )
    )

    assert result["skipped"] == "v2_structured_source"
    mongo.get_client.assert_not_called()  # no staging read, no Qdrant/graph work


@asynccontextmanager
async def _noop_lock(*_: Any, **__: Any) -> Any:
    yield


def test_a_skipped_silver_run_hands_nothing_to_gold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(silver_handler, "set_current_tenant", lambda *_: None)
    monkeypatch.setattr(silver_handler, "document_name", AsyncMock(return_value="Pink Sheet"))
    monkeypatch.setattr(silver_handler, "document_lock", _noop_lock)
    monkeypatch.setattr(silver_handler, "reset_events_for_new_run", AsyncMock())
    monkeypatch.setattr(silver_handler.PipelineEventEmitter, "create", AsyncMock())
    monkeypatch.setattr(cfg, "SILVER_STAGE_TIMEOUT_SECONDS", 30)
    monkeypatch.setattr(
        silver_handler,
        "promote_to_silver",
        AsyncMock(
            return_value={
                "data_id": DATA_ID,
                "chunks_count": 0,
                "layer": "bronze",
                "skipped": "v2_structured_source",
            }
        ),
    )
    auto_gold = AsyncMock(return_value=True)
    monkeypatch.setattr(silver_handler, "maybe_publish_auto_gold", auto_gold)
    retry = AsyncMock()
    monkeypatch.setattr(silver_handler, "route_ingestion_failure", retry)

    runtime = WorkerRuntime(logger=AsyncMock(), stage_record=AsyncMock())
    runtime.mongo = MagicMock(get_client=MagicMock(return_value=MagicMock()))
    asyncio.run(
        silver_handler.build_silver_handler(runtime)(
            {"data_id": DATA_ID, "tenant_id": TENANT_ID, "approved_by": "web-crawler-worker"},
        )
    )

    auto_gold.assert_not_called()
    retry.assert_not_called()


# ── structured-KB consumer ──────────────────────────────────────────────────


def test_structured_propose_fails_the_job_instead_of_proposing_a_kb_structured_table(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(propose, "is_v2_structured_document", AsyncMock(return_value=True))
    update_job = AsyncMock()
    monkeypatch.setattr(propose, "_update_job", update_job)
    load = AsyncMock()
    monkeypatch.setattr(propose, "_load_kb_data", load)
    docling = MagicMock()

    asyncio.run(
        propose.propose_structured_table(
            minio=MagicMock(),
            postgres=MagicMock(),
            docling=docling,
            mongo=MagicMock(),
            data_id=DATA_ID,
            job_id="44444444-4444-4444-4444-444444444444",
            tenant_id=TENANT_ID,
        )
    )

    load.assert_not_called()
    docling.parse.assert_not_called()
    update_job.assert_awaited_once()
    assert update_job.call_args.kwargs["status"] == "failed"
    assert V2_STRUCTURED_SOURCE_ERROR in update_job.call_args.kwargs["error_message"]


# ── kb_backend routes ───────────────────────────────────────────────────────


class _Producer:
    def __init__(self) -> None:
        self.produced: list[str] = []

    async def produce(self, topic: str, **_: Any) -> None:
        self.produced.append(topic)


_CTX = {"tenant_id": TENANT_ID, "user_name": "steward", "user_id": "u1"}
_RECORD = {
    "data_id": DATA_ID,
    "tenant_id": TENANT_ID,
    "name": "Pink Sheet",
    "source_type": "doc",
    "extension": "xlsx",
    "path": "p",
    "current_tier": "bronze",
    "metadata": {},
}


@pytest.mark.parametrize("target", ["silver", "gold"])
def test_kb_backend_refuses_to_queue_a_v2_structured_document(
    target: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(document_ops, "is_v2_structured_document", AsyncMock(return_value=True))
    producer = _Producer()

    with pytest.raises(HTTPException) as err:
        asyncio.run(
            document_ops.promote_document(
                postgres=MagicMock(),
                kafka_producer=producer,
                data_id=DATA_ID,
                target=target,
                status="",
                ctx=_CTX,
                record=_RECORD,
            )
        )

    assert err.value.status_code == 409
    assert V2_STRUCTURED_SOURCE_ERROR in str(err.value.detail)
    assert producer.produced == []


def test_kb_backend_still_queues_an_ordinary_document(monkeypatch: pytest.MonkeyPatch) -> None:
    """The legacy document pipeline is unchanged for everything else."""
    monkeypatch.setattr(document_ops, "is_v2_structured_document", AsyncMock(return_value=False))
    producer = _Producer()

    asyncio.run(
        document_ops.promote_document(
            postgres=MagicMock(),
            kafka_producer=producer,
            data_id=DATA_ID,
            target="silver",
            status="",
            ctx=_CTX,
            record=_RECORD,
        )
    )

    assert producer.produced == [cfg.KAFKA_TOPIC_SILVER]


# ── static check: V2 packages never touch the legacy path ─────────────────
#
# import-linter's v2-data-boundary contract covers imports between packages it
# can analyse; it cannot see a Kafka topic written as a string, nor a package
# without an __init__.py. This scan covers both. Add each new V2 package here in
# the same commit that creates it (the list tolerates packages not yet on main).

BACKEND = pathlib.Path(__file__).resolve().parents[3] / "backend"
V2_PACKAGES = (
    "libs/indicator",
    "libs/ingestion/storage_boundary.py",
    "services/indicator_api",
    "services/platform_workers/workers/series_extractor",
    "services/platform_workers/workers/semantic_extractor",
    "services/platform_workers/workers/detector",
)


def _violations(path: pathlib.Path) -> list[str]:
    found = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in LEGACY_TOPICS or "kb_structured" in node.value:
                found.append(f"{path.name}:{node.lineno} string {node.value!r}")
            continue
        else:
            continue
        for name in names:
            if any(name == bad or name.startswith(bad + ".") for bad in LEGACY_IMPORTS):
                found.append(f"{path.name}:{node.lineno} import {name}")
    return found


def _v2_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for rel in V2_PACKAGES:
        target = BACKEND / rel
        files += [target] if target.is_file() else sorted(target.rglob("*.py"))
    return files


def test_v2_packages_import_no_legacy_adapter_and_name_no_legacy_topic() -> None:
    files = _v2_files()
    assert files, "no V2 package found — the scan would pass vacuously"
    violations = [v for f in files for v in _violations(f)]
    assert violations == []


@pytest.mark.parametrize(
    "source",
    [
        "from libs.database_connector.mongo_connector import MongoClient\n",
        "import pymongo\n",
        "from services.kb_backend.workers.structured_data.propose import propose_structured_table\n",
        "from libs.ingestion.silver.orchestrator import promote_to_silver\n",
        "TOPIC = 'kb.promote.silver'\n",
        "producer.send('kb.structured.propose', {})\n",
        "SQL = 'INSERT INTO kb_structured.brent VALUES (1)'\n",
    ],
)
def test_the_scan_catches_each_kind_of_leak(source: str, tmp_path: pathlib.Path) -> None:
    planted = tmp_path / "leaky_v2_module.py"
    planted.write_text(source, encoding="utf-8")
    assert _violations(planted)
