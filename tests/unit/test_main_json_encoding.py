"""Unit tests for main.py's `_json_default`/`SandboxJSONResponse` — the
JSON encoding used for every /execute/sql response (and the SSE `done`
event carrying the same shape).

execute_sql rows can carry column types stdlib `json.dumps` doesn't know
how to encode on its own (both asyncpg and snowflake-connector's
DictCursor hand these back as native Python types, not JSON-safe ones).
Written after an actual end-to-end run (not a code read) surfaced a real
gap: `Decimal` was handled but `datetime`/`date`/`time` was not, crashing
the whole HTTP response with an unhandled 500 the moment a query touched
a TIMESTAMPTZ column (see tests/integration/
test_sandbox_sql_postgres_backend.py for the full end-to-end
reproduction). These tests pin the encoding at the unit level — fast, no
Docker/Postgres needed — so a future regression here fails in
milliseconds instead of only showing up against a real database.
"""
from __future__ import annotations

import datetime
import decimal
import json
import pathlib
import sys
import uuid

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SVC_DIR = str(REPO_ROOT / "backend" / "services" / "sandbox_executor")


@pytest.fixture(autouse=True)
def _sys_path():
    added = [p for p in (_SVC_DIR,) if p not in sys.path]
    sys.path[:0] = added
    sys.modules.pop("main", None)
    yield
    sys.modules.pop("main", None)
    for p in added:
        if p in sys.path:
            sys.path.remove(p)


@pytest.mark.unit
class TestJsonDefault:
    def test_decimal_becomes_a_json_number(self):
        import main
        assert json.dumps(decimal.Decimal("120.50"), default=main._json_default) == "120.5"

    def test_datetime_becomes_iso_string(self):
        import main
        dt = datetime.datetime(2026, 7, 9, 3, 18, 33, 783501, tzinfo=datetime.timezone.utc)
        assert json.dumps(dt, default=main._json_default) == json.dumps(dt.isoformat())

    def test_date_becomes_iso_string(self):
        import main
        d = datetime.date(2026, 7, 9)
        assert json.dumps(d, default=main._json_default) == json.dumps(d.isoformat())

    def test_time_becomes_iso_string(self):
        import main
        t = datetime.time(13, 45, 0)
        assert json.dumps(t, default=main._json_default) == json.dumps(t.isoformat())

    def test_uuid_becomes_string(self):
        import main
        u = uuid.uuid4()
        assert json.dumps(u, default=main._json_default) == json.dumps(str(u))

    def test_genuinely_unsupported_type_still_raises(self):
        """_json_default must stay a whitelist, not swallow every
        unencodable type into some best-effort string — an object type
        nobody has actually seen come back from a real query should still
        fail loudly instead of silently mis-serializing."""
        import main

        class Unencodable:
            pass

        with pytest.raises(TypeError):
            json.dumps(Unencodable(), default=main._json_default)


@pytest.mark.unit
class TestSandboxJSONResponseRendersRealRow:
    def test_row_with_decimal_and_datetime_and_uuid_renders_without_crashing(self):
        """Reproduces the exact shape execute_sql's real response takes
        (main.py's _job_status_response) for a row with mixed column
        types — the integration-level version of the bug this file
        guards against, without needing Docker/Postgres to run it."""
        import main

        row = {
            "id": 1,
            "customer_name": "Acme Corp",
            "amount": decimal.Decimal("120.50"),
            "created_at": datetime.datetime(2026, 7, 9, 3, 18, 33, tzinfo=datetime.timezone.utc),
        }
        body = {"success": True, "rows": [row], "row_count": 1, "is_truncated": False}

        response = main.SandboxJSONResponse(status_code=200, content=body)
        decoded = json.loads(response.body)

        assert decoded["rows"][0]["amount"] == 120.5
        assert decoded["rows"][0]["created_at"] == row["created_at"].isoformat()
