"""A committed upload becomes a workspace table and a catalogued dataset (UC-04-08)."""

from __future__ import annotations

import json
import uuid

import pytest

from libs.dataagent_db import versioning
from services.worker_agent import uploads
from services.worker_agent.events import Event

from .conftest import Seed

pytestmark = [pytest.mark.integration]


async def test_a_committed_upload_catalogues_its_columns_with_their_types(
    seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Upload test")
    user = seed.row("org.app_user", org_id=org, external_id="kc-u", username="u", display_name="U")
    space = seed.row("org.data_space", org_id=org, code="DS-UP", name="Up", steward_id=user)
    dataset = uuid.uuid4()
    upload = seed.row(
        "ingest.file_upload",
        org_id=org,
        data_space_id=space,
        uploader_id=user,
        file_name="doanh_thu.csv",
        object_key="k",
        size_bytes=1,
        column_map=json.dumps(
            [
                {"name": "region", "type": "string"},
                {"name": "net_revenue", "type": "number"},
                {"name": "month", "type": "string"},
                {"name": "day", "type": "date"},
            ]
        ),
        target_dataset_id=dataset,
        error_ratio=0,
        status="committed",
    )

    async def _read(_key: str) -> bytes:
        return b"region,net_revenue,month,day\nHN,95,2026-09,2026-09-30\n"

    monkeypatch.setattr(uploads, "_read_object", _read)
    await uploads.handle(
        Event(
            uuid.uuid4(),
            org,
            uploads.COMMITTED,
            "file_upload",
            upload,
            {"uploader_id": str(user)},
        )
    )

    # The semantic compiler reads the text month as text and the date as a date.
    with seed.conn.cursor() as cur:
        cur.execute(
            "SELECT name, data_type, normalized_type FROM catalog.column_meta "
            "WHERE dataset_id = %s ORDER BY ordinal",
            (str(dataset),),
        )
        assert cur.fetchall() == [
            ("region", "text", "string"),
            ("net_revenue", "numeric", "decimal"),
            ("month", "text", "string"),
            ("day", "date", "date"),
        ]


async def test_a_vietnamese_csv_lands_with_its_numbers_dates_and_header_row(
    seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    # GEN-02: "1.234,5" became "1.234.5" and failed, "02/03/2024" went to the server's DateStyle
    # (MDY: 3 February), and the saved header row and encoding were ignored.
    org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Upload VN")
    user = seed.row("org.app_user", org_id=org, external_id="kc-v", username="v", display_name="V")
    space = seed.row("org.data_space", org_id=org, code="DS-VN", name="VN", steward_id=user)
    upload = seed.row(
        "ingest.file_upload",
        org_id=org,
        data_space_id=space,
        uploader_id=user,
        file_name="bao_cao.csv",
        object_key="k",
        size_bytes=1,
        header_row=2,
        encoding="utf-16",
        column_map=json.dumps(
            [
                {"name": "chi_nhanh", "type": "string"},
                {"name": "doanh_thu", "type": "number"},
                {"name": "ty_le", "type": "percent"},
                {"name": "ngay", "type": "date"},
            ]
        ),
        target_dataset_id=uuid.uuid4(),
        error_ratio=0,
        status="committed",
    )
    body = (
        "Báo cáo doanh thu quý 1\n"
        "Chi nhánh;Doanh thu;Tỷ lệ;Ngày\n"
        "Hà Nội;1.234,5;12,5%;02/03/2024\n"
        "Đà Nẵng;(2.000);7%;13/02/2024\n"
    ).encode("utf-16")

    async def _read(_key: str) -> bytes:
        return body

    monkeypatch.setattr(uploads, "_read_object", _read)
    event = Event(
        uuid.uuid4(), org, uploads.COMMITTED, "file_upload", upload, {"uploader_id": str(user)}
    )
    await uploads.handle(event)

    table = seed.one(
        "SELECT validation->>'table_name' FROM ingest.file_upload WHERE id = %s", upload
    )
    rows = seed.all(
        f'SELECT chi_nhanh, doanh_thu, ty_le, ngay FROM workspace."{table}" ORDER BY ngay'
    )
    labels = seed.all(
        "SELECT name, display_name FROM catalog.column_meta c JOIN ingest.file_upload u"
        " ON u.target_dataset_id = c.dataset_id WHERE u.id = %s ORDER BY ordinal",
        upload,
    )
    # The mapped names are physical already; the headers as written are kept as labels.
    assert labels == [
        ("chi_nhanh", "Chi nhánh"),
        ("doanh_thu", "Doanh thu"),
        ("ty_le", "Tỷ lệ"),
        ("ngay", "Ngày"),
    ]
    assert [(r[0], str(r[1]), str(r[2]), r[3].isoformat()) for r in rows] == [
        ("Đà Nẵng", "-2000", "7", "2024-02-13"),
        ("Hà Nội", "1234.5", "12.5", "2024-03-02"),
    ]


async def test_an_ambiguous_column_reads_by_its_data_spaces_ingest_profile(
    seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    # GEN-11: "1,234" and "02/03/2024" fit both conventions; an en-US space reads them its way.
    org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Upload US")
    user = seed.row(
        "org.app_user", org_id=org, external_id="kc-us", username="us", display_name="U"
    )
    space = seed.row(
        "org.data_space",
        org_id=org,
        code="DS-US",
        name="US",
        steward_id=user,
        ingest_profile=json.dumps({"locale": "en-US"}),
    )
    upload = seed.row(
        "ingest.file_upload",
        org_id=org,
        data_space_id=space,
        uploader_id=user,
        file_name="sales.csv",
        object_key="k",
        size_bytes=1,
        column_map=json.dumps(
            [{"name": "amount", "type": "number"}, {"name": "day", "type": "date"}]
        ),
        target_dataset_id=uuid.uuid4(),
        error_ratio=0,
        status="committed",
    )

    async def _read(_key: str) -> bytes:
        return b'amount,day\n"1,234",02/03/2024\n'

    monkeypatch.setattr(uploads, "_read_object", _read)
    event = Event(
        uuid.uuid4(), org, uploads.COMMITTED, "file_upload", upload, {"uploader_id": str(user)}
    )
    await uploads.handle(event)
    table = seed.one(
        "SELECT validation->>'table_name' FROM ingest.file_upload WHERE id = %s", upload
    )
    [(amount, day)] = seed.all(f'SELECT amount, day FROM workspace."{table}"')
    assert (str(amount), day.isoformat()) == ("1234", "2024-02-03")


def _upload(
    seed: Seed, org: uuid.UUID, space: uuid.UUID, user: uuid.UUID, **extra: object
) -> uuid.UUID:
    return seed.row(
        "ingest.file_upload",
        org_id=org,
        data_space_id=space,
        uploader_id=user,
        file_name="chi_nhanh.csv",
        size_bytes=1,
        column_map=json.dumps(
            [{"name": "branch", "type": "string"}, {"name": "n", "type": "number"}]
        ),
        error_ratio=0,
        status="committed",
        **extra,
    )


async def _commit(org: uuid.UUID, upload: uuid.UUID, user: uuid.UUID) -> None:
    await uploads.handle(
        Event(
            uuid.uuid4(), org, uploads.COMMITTED, "file_upload", upload, {"uploader_id": str(user)}
        )
    )


def _relkind(seed: Seed, name: str) -> object:
    return seed.one(
        "SELECT c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname = 'workspace' AND c.relname = %s",
        name,
    )


@pytest.mark.parametrize("before_versioning", [False, True])
async def test_an_upload_into_a_table_writes_its_next_version_and_keeps_the_one_before(
    seed: Seed, monkeypatch: pytest.MonkeyPatch, before_versioning: bool
) -> None:
    # DF-03: the second upload used to land in a table of its own that nothing pointed at, and a
    # replace dropped what an answer had read. Now `replace` writes v2 and keeps v1 as it was,
    # `keep` appends to the current version; a table uploaded before versioning becomes v1.
    org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Versions")
    user = seed.row("org.app_user", org_id=org, external_id="kc-v", username="v", display_name="V")
    space = seed.row("org.data_space", org_id=org, code="DS-V", name="V", steward_id=user)
    files = {
        "v1": b"branch,n\nHN,1\nSG,2\n",
        "v2": b"branch,n\nDN,3\n",
        "v3": b"branch,n\nHP,4\n",
    }

    async def _read(key: str) -> bytes:
        return files[key]

    monkeypatch.setattr(uploads, "_read_object", _read)
    dataset = uuid.uuid4()
    monkeypatch.setattr(versioning, "ENABLED", not before_versioning)
    await _commit(
        org, _upload(seed, org, space, user, object_key="v1", target_dataset_id=dataset), user
    )
    monkeypatch.setattr(versioning, "ENABLED", True)
    name = seed.one("SELECT table_name FROM catalog.dataset WHERE id = %s", dataset)
    v1, v2 = versioning.physical_name(name, 1), versioning.physical_name(name, 2)

    await _commit(
        org,
        _upload(
            seed,
            org,
            space,
            user,
            object_key="v2",
            target_dataset_id=dataset,
            replace_mode="replace",
        ),
        user,
    )
    assert _relkind(seed, name) == "v" and _relkind(seed, v1) == "r"
    assert seed.one(f'SELECT count(*) FROM workspace."{v1}"') == 2
    assert seed.all(f'SELECT branch FROM workspace."{name}"') == [("DN",)]
    assert seed.one("SELECT current_version FROM catalog.dataset WHERE id = %s", dataset) == 2
    assert seed.all(
        "SELECT version, change_kind, physical_table, valid_to IS NOT NULL"
        " FROM catalog.dataset_version WHERE dataset_id = %s ORDER BY version",
        dataset,
    ) == [(1, "initial", v1, True), (2, "reload", v2, False)]

    await _commit(
        org,
        _upload(
            seed, org, space, user, object_key="v3", target_dataset_id=dataset, replace_mode="keep"
        ),
        user,
    )
    assert seed.all(f'SELECT branch FROM workspace."{name}" ORDER BY n') == [("DN",), ("HP",)]
    assert seed.one(f'SELECT count(*) FROM workspace."{v1}"') == 2
    assert seed.one("SELECT current_version FROM catalog.dataset WHERE id = %s", dataset) == 2
