"""A committed upload becomes a workspace table and a catalogued dataset (UC-04-08)."""

from __future__ import annotations

import json
import uuid

import pytest

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
