"""The NYC 311 snapshot collector (tests/e2e/nyc311/collect.py, EV-01), against a fake portal.

The gold answers of the real-data evaluation are defined on the snapshot, so what matters here is
that a snapshot is reproducible: the same rows give the same manifest whatever the page size or
order they came in, a pull that stopped resumes without losing or doubling rows, and a second
pull that does not reproduce the first is refused rather than written over it.
"""

from __future__ import annotations

import datetime
import gzip
import importlib.util
import json
import pathlib
import urllib.error
from typing import Any

import pytest

HERE = pathlib.Path(__file__).resolve().parents[2] / "e2e" / "nyc311"
_spec = importlib.util.spec_from_file_location("nyc311_collect", HERE / "collect.py")
assert _spec is not None and _spec.loader is not None
collect = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(collect)

pytestmark = [pytest.mark.unit]

WINDOW = collect.Window(datetime.date(2025, 1, 1), datetime.date(2025, 4, 1))
NOW = datetime.datetime(2026, 11, 16, 12, tzinfo=datetime.UTC)


def _row(key: int, month: int, **extra: Any) -> dict[str, Any]:
    row = {
        "unique_key": str(key),
        "created_date": f"2025-{month:02d}-15T08:00:00.000",
        "complaint_type": "Noise - Residential",
        "borough": "BROOKLYN",
        ":id": f"row-{key}",
        ":updated_at": f"2025-{month:02d}-20T00:00:00.000Z",
        ":@computed_region_efsh_h5xi": "17616",
        "closed_date": None,
    }
    row.update(extra)
    return row


ROWS = [_row(k, 1 + k % 3) for k in range(1, 8)]


class Portal:
    def __init__(self, rows: list[dict[str, Any]], *, fail_at: int | None = None) -> None:
        self.rows = rows
        self.fail_at = fail_at
        self.calls: list[dict[str, str]] = []

    def __call__(self, url: str, params: dict[str, str]) -> list[dict[str, Any]]:
        self.calls.append(params)
        assert WINDOW.where() in params["$where"]
        if "count(*)" in params["$select"]:
            return [{"n": "3"}]
        assert params["$order"] == "unique_key"
        offset, limit = int(params["$offset"]), int(params["$limit"])
        if self.fail_at is not None and offset >= self.fail_at:
            raise collect.Refused("connection dropped")
        ordered = sorted(self.rows, key=lambda r: int(r["unique_key"]))
        return ordered[offset : offset + limit]


def _pull(tmp: pathlib.Path, portal: Portal, page_size: int = 3) -> dict[str, Any]:
    return collect.pull(
        "S3-TEST",
        WINDOW,
        portal,
        cache=tmp / "cache",
        manifests=tmp / "manifests",
        page_size=page_size,
        now=lambda: NOW,
    )


def test_a_snapshot_holds_every_row_once_by_month_without_computed_regions(
    tmp_path: pathlib.Path,
) -> None:
    manifest = _pull(tmp_path, Portal(ROWS))
    assert manifest["row_count"] == 7 and manifest["duplicate_keys_seen"] == 0
    assert manifest["terms_status"] == "to be confirmed"
    assert manifest["max_updated_at"] == "2025-03-20T00:00:00.000Z"
    assert [f["path"] for f in manifest["files"]] == [
        "data/2025-01.jsonl.gz",
        "data/2025-02.jsonl.gz",
        "data/2025-03.jsonl.gz",
    ]
    stored = [
        json.loads(line)
        for f in manifest["files"]
        for line in gzip.decompress((tmp_path / "cache" / "S3-TEST" / f["path"]).read_bytes())
        .decode()
        .splitlines()
    ]
    assert sorted(int(r["unique_key"]) for r in stored) == list(range(1, 8))
    assert all("computed_region" not in k and ":id" not in r for r in stored for k in r)
    assert all("closed_date" not in r for r in stored)  # null dropped


def test_the_manifest_does_not_depend_on_how_the_rows_were_paged(tmp_path: pathlib.Path) -> None:
    by_three = _pull(tmp_path / "a", Portal(ROWS), page_size=3)
    by_fifty = _pull(tmp_path / "b", Portal(list(reversed(ROWS))), page_size=50)
    assert by_three["rows_sha256"] == by_fifty["rows_sha256"]
    assert [f["sha256"] for f in by_three["files"]] == [f["sha256"] for f in by_fifty["files"]]


def test_a_pull_that_stopped_resumes_from_its_last_page(tmp_path: pathlib.Path) -> None:
    with pytest.raises(collect.Refused):
        _pull(tmp_path, Portal(ROWS, fail_at=3))
    resumed = Portal(ROWS)
    manifest = _pull(tmp_path, resumed)
    assert manifest["row_count"] == 7
    assert [c["$offset"] for c in resumed.calls] == ["3", "6"]


def test_a_second_pull_with_other_rows_is_refused(tmp_path: pathlib.Path) -> None:
    first = _pull(tmp_path, Portal(ROWS))
    again = collect.finish(
        "S3-TEST",
        WINDOW,
        first["pulled_at"],
        tmp_path / "cache" / "S3-TEST",
        manifests=tmp_path / "manifests",
    )
    assert again["rows_sha256"] == first["rows_sha256"]
    # The source changed a row after the snapshot: same id, other rows.
    page = tmp_path / "cache" / "S3-TEST" / "pages" / "000000000000.json"
    rows = json.loads(page.read_text())
    rows[0]["status"] = "Closed"
    page.write_text(json.dumps(rows))
    with pytest.raises(collect.Refused, match="new snapshot id"):
        collect.finish(
            "S3-TEST",
            WINDOW,
            first["pulled_at"],
            tmp_path / "cache" / "S3-TEST",
            manifests=tmp_path / "manifests",
        )


def test_another_window_under_the_same_id_is_refused(tmp_path: pathlib.Path) -> None:
    with pytest.raises(collect.Refused):
        _pull(tmp_path, Portal(ROWS, fail_at=3))
    other = collect.Window(datetime.date(2025, 1, 1), datetime.date(2025, 2, 1))
    with pytest.raises(collect.Refused, match="another window"):
        collect.pull("S3-TEST", other, Portal(ROWS), cache=tmp_path / "cache", page_size=3)


def test_drift_counts_the_rows_changed_since_the_snapshot(tmp_path: pathlib.Path) -> None:
    _pull(tmp_path, Portal(ROWS))
    portal = Portal(ROWS)
    out = collect.drift(
        "S3-TEST",
        portal,
        cache=tmp_path / "cache",
        manifests=tmp_path / "manifests",
        now=lambda: NOW,
    )
    assert out["changed_rows"] == 3 and out["since"] == "2025-03-20T00:00:00.000Z"
    assert ":updated_at > '2025-03-20T00:00:00.000Z'" in portal.calls[0]["$where"]


def test_throttling_is_retried_and_a_refusal_is_not(monkeypatch: pytest.MonkeyPatch) -> None:
    answers: list[Any] = [
        urllib.error.HTTPError("u", 429, "slow down", {}, None),  # type: ignore[arg-type]
        urllib.error.HTTPError("u", 503, "busy", {}, None),  # type: ignore[arg-type]
        b"[]",
    ]
    slept: list[float] = []

    class Response:
        def __init__(self, body: bytes) -> None:
            self.body = body

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self) -> bytes:
            return self.body

    def urlopen(request: Any, timeout: float) -> Response:
        assert request.get_header("X-app-token") == "tok"
        assert "Mozilla" not in request.get_header("User-agent")
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return Response(answer)

    monkeypatch.setattr(collect.urllib.request, "urlopen", urlopen)
    fetch = collect.http_fetch("tok", sleep=slept.append)
    assert fetch(collect.SOURCE, {"$limit": "1"}) == []
    assert slept == [1.0, 2.0]

    def forbidden(request: Any, timeout: float) -> Response:
        raise urllib.error.HTTPError("u", 403, "Forbidden", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(collect.urllib.request, "urlopen", forbidden)
    with pytest.raises(collect.Refused, match="403"):
        collect.http_fetch(None, sleep=slept.append)(collect.SOURCE, {})
