"""The file of an audit export and its signature (UC-11-03 step 4, BR-138, AC-11-03-06)."""

from __future__ import annotations

import csv
import datetime
import io
import json
import uuid

import pytest

from services.worker_agent.audit_exports import (
    COLUMNS,
    SigningKeyMissing,
    sign,
    signing_key,
    verify,
    write_file,
)

pytestmark = [pytest.mark.unit]

ORG = uuid.UUID("018f0000-0000-7000-8000-000000000001")


def _row(**over: object) -> dict[str, object]:
    row: dict[str, object] = {c: None for c in COLUMNS}
    row.update(
        id=uuid.UUID(int=1),
        seq=7,
        occurred_at=datetime.datetime(2026, 10, 1, 8, 0, tzinfo=datetime.UTC),
        actor_kind="user",
        service="api",
        action="query.executed",
        outcome="ok",
        after={"note": "=HYPERLINK(1)", "rows": 3},
        prev_hash="0" * 64,
        hash="a" * 64,
    )
    row.update(over)
    return row


def test_a_csv_has_the_log_columns_and_no_cell_a_spreadsheet_would_run() -> None:
    body = write_file("csv", [_row(action="=cmd|'/c calc'!A1")])
    text = body.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == list(COLUMNS)
    got = dict(zip(rows[0], rows[1], strict=True))
    assert got["action"].startswith("'=")
    assert got["seq"] == "7" and got["occurred_at"] == "2026-10-01T08:00:00+00:00"
    assert json.loads(got["after"]) == {"note": "=HYPERLINK(1)", "rows": 3}


def test_a_jsonl_file_is_one_object_per_row_in_column_order() -> None:
    body = write_file("jsonl", [_row(), _row(seq=8)])
    lines = body.decode().splitlines()
    assert [json.loads(line)["seq"] for line in lines] == [7, 8]
    assert list(json.loads(lines[0])) == list(COLUMNS)
    assert json.loads(lines[0])["after"] == {"note": "=HYPERLINK(1)", "rows": 3}


def test_an_empty_export_is_still_a_file_with_its_header() -> None:
    assert write_file("csv", []).decode("utf-8-sig").strip() == ",".join(COLUMNS)
    assert write_file("jsonl", []) == b""


def test_the_signature_names_the_file_digest_and_is_checked_against_it() -> None:
    body = b"seq,action\n1,a\n"
    signature = sign(ORG, body, b"master")
    assert signature.startswith("v1;alg=hmac-sha256;sha256=")
    assert verify(ORG, body, signature, b"master")
    assert not verify(ORG, body + b" ", signature, b"master")
    assert not verify(ORG, body, signature, b"another key")
    # Each organisation signs with its own key: one org's signature is not another's.
    assert not verify(uuid.UUID(int=2), body, signature, b"master")
    assert not verify(ORG, body, "garbage", b"master")


def test_the_key_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUDIT_EXPORT_SIGNING_KEY", " s3cret \n")
    assert signing_key() == b"s3cret"
    monkeypatch.setenv("AUDIT_EXPORT_SIGNING_KEY", "")
    with pytest.raises(SigningKeyMissing):
        signing_key()
