"""EV-02 (tests/e2e/edgar/collect.py): the SEC pull keeps EDGAR's fair access rules, picks the
filings of the fiscal years asked with their amendments linked, and cuts FSDS to the companies."""

from __future__ import annotations

import datetime
import importlib.util
import json
import pathlib
import sys
import zipfile
from typing import Any

import httpx
import pyarrow.parquet as pq
import pytest

pytestmark = [pytest.mark.unit]

HERE = pathlib.Path(__file__).parents[2] / "e2e" / "edgar"
_spec = importlib.util.spec_from_file_location("edgar_collect", HERE / "collect.py")
assert _spec is not None and _spec.loader is not None
edgar = importlib.util.module_from_spec(_spec)
sys.modules["edgar_collect"] = edgar  # dataclasses resolve their module by name
_spec.loader.exec_module(edgar)

D = datetime.date
UA = "DataAgent-eval eval@example.org"


def test_the_scope_is_twenty_companies_with_xom_under_its_predecessor_cik() -> None:
    scope = json.loads((HERE / "scope.json").read_text())
    assert (scope["snapshot"], scope["fy_from"], scope["fy_to"]) == ("E20-FY21-24", 2021, 2024)
    assert len({c["cik"] for c in scope["companies"]}) == 20
    xom = next(c for c in scope["companies"] if c["ticker"] == "XOM")
    assert xom["cik"] == 34088 and "2115436" in xom["note"]


def test_a_fiscal_year_is_the_year_it_ends_in() -> None:
    assert edgar.fiscal_year(D(2021, 4, 30), "0131") == 2022  # WMT Q1 FY2022
    assert edgar.fiscal_year(D(2022, 1, 31), "0131") == 2022
    assert edgar.fiscal_year(D(2023, 9, 30), "0926") == 2023  # AAPL's 53-week year
    assert edgar.fiscal_year(D(2022, 9, 24), "0926") == 2022
    assert edgar.fiscal_year(D(2020, 12, 26), "0926") == 2021  # AAPL Q1 FY2021
    assert edgar.fiscal_year(D(2022, 1, 1), "1231") == 2021  # a 52-week year ending late
    assert edgar.fiscal_year(D(2021, 6, 30), "1231") == 2021
    assert edgar.fiscal_year(D(2023, 2, 28), "0229") == 2023
    with pytest.raises(edgar.Refused):
        edgar.fiscal_year(D(2021, 6, 30), "")


def _row(accn: str, form: str, report: str, filed: str, doc: str = "x.htm") -> dict[str, Any]:
    return {
        "accessionNumber": accn,
        "form": form,
        "reportDate": report,
        "filingDate": filed,
        "primaryDocument": doc,
        "isInlineXBRL": 1,
        "size": 10,
    }


ROWS = [
    _row("0000000001-21-000001", "10-K", "2020-12-31", "2021-02-20"),  # FY2020: out
    _row("0000000001-22-000001", "10-K", "2021-12-31", "2022-02-20"),
    _row("0000000001-22-000002", "10-Q", "2022-03-31", "2022-05-01"),
    _row("0000000001-22-000003", "8-K", "2022-03-31", "2022-05-01"),  # form: out
    _row("0000000001-23-000001", "10-K/A", "2021-12-31", "2023-03-14"),
    _row("0000000001-25-000001", "10-K/A", "2021-12-31", "2025-04-30"),
    _row("0000000001-22-000009", "10-Q", "", "2022-08-01"),  # no period: out
]


def test_the_filings_of_the_range_are_chosen_and_amendments_chain() -> None:
    chosen = edgar.select(
        ROWS, 1, "1231", forms=["10-K", "10-Q", "10-K/A", "10-Q/A"], fy_from=2021, fy_to=2024
    )
    by_accn = {f.accn: f for f in chosen}
    assert set(by_accn) == {
        "0000000001-22-000001",
        "0000000001-22-000002",
        "0000000001-23-000001",
        "0000000001-25-000001",
    }
    assert by_accn["0000000001-22-000001"].supersedes is None
    assert by_accn["0000000001-23-000001"].supersedes == "0000000001-22-000001"
    # The second amendment supersedes the first, not the original.
    assert by_accn["0000000001-25-000001"].supersedes == "0000000001-23-000001"
    assert {f.fy for f in chosen} == {2021, 2022}
    capped = edgar.select(
        ROWS, 1, "1231", forms=["10-K", "10-K/A"], fy_from=2021, fy_to=2024,
        filed_to=D(2024, 12, 31),
    )  # fmt: skip
    assert [f.accn for f in capped] == ["0000000001-22-000001", "0000000001-23-000001"]


def test_a_malformed_accession_number_stops_the_pull() -> None:
    with pytest.raises(edgar.Refused):
        edgar.select(
            [_row("../../etc", "10-K", "2021-12-31", "2022-01-01")],
            1, "1231", forms=["10-K"], fy_from=2021, fy_to=2021,
        )  # fmt: skip


def test_older_submission_pages_are_read_back_to_the_first_year() -> None:
    document = {
        "filings": {
            "recent": {"accessionNumber": ["0000000001-25-000001"], "form": ["10-K"]},
            "files": [
                {"name": "CIK0000000001-submissions-001.json", "filingTo": "2024-06-30"},
                {"name": "CIK0000000001-submissions-002.json", "filingTo": "2019-12-31"},
            ],
        }
    }
    assert edgar.older_pages(document, D(2020, 1, 1)) == ["CIK0000000001-submissions-001.json"]
    assert edgar.submission_rows(document) == [
        {"accessionNumber": "0000000001-25-000001", "form": "10-K"}
    ]
    bad = {"filings": {"files": [{"name": "../x.json", "filingTo": "2025-01-01"}]}}
    with pytest.raises(edgar.Refused):
        edgar.older_pages(bad, D(2020, 1, 1))


def test_facts_of_periods_in_range_are_kept_from_every_filing_that_reported_them() -> None:
    revenue = [
        {"start": "2022-10-01", "end": "2023-09-30", "val": 383285000000, "accn": "a-23",
         "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2023-11-03"},
        {"start": "2022-10-01", "end": "2023-09-30", "val": 383285000000, "accn": "a-25",
         "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-10-31", "frame": "CY2023"},
        {"start": "2019-09-29", "end": "2020-09-26", "val": 274515000000, "accn": "a-20",
         "fy": 2020, "fp": "FY", "form": "10-K", "filed": "2020-10-30"},
    ]  # fmt: skip
    document = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": revenue}}}}}
    rows = edgar.facts(document, 320193, "0926", fy_from=2021, fy_to=2024)
    assert [(r["accn"], r["frame"], r["val"]) for r in rows] == [
        ("a-23", None, "383285000000"),
        ("a-25", "CY2023", "383285000000"),
    ]
    capped = edgar.facts(document, 320193, "0926", fy_from=2021, fy_to=2024, filed_to=D(2024, 1, 1))
    assert [r["accn"] for r in capped] == ["a-23"]


def _zip(path: pathlib.Path) -> None:
    v = "us-gaap/2022"
    tables = {
        "sub.txt": "adsh\tcik\tname\tform\n0000000001-22-000001\t1\tONE\t10-K\n"
        "0000000002-22-000001\t2\tTWO\t10-K\n",
        "num.txt": f"adsh\ttag\tversion\tsegments\tvalue\n0000000001-22-000001\tRevenues\t{v}\t\t100\n"
        f'0000000001-22-000001\tLoan\t{v}\t"InvestmentIdentifier=A Co\t, Secured Debt;"\t7\n'
        f"0000000002-22-000001\tOther\t{v}\t\t200\n",
        "pre.txt": f"adsh\treport\ttag\tversion\n0000000002-22-000001\t2\tOther\t{v}\n",
        "tag.txt": f'tag\tversion\tdoc\nRevenues\t{v}\tAmount of revenue "as filed"\n'
        f"Loan\t{v}\tA loan\nOther\t{v}\tUsed by company 2 only\n",
    }
    with zipfile.ZipFile(path, "w") as z:
        for name, text in tables.items():
            z.writestr(name, text)


def test_fsds_is_cut_to_the_companies(tmp_path: pathlib.Path) -> None:
    archive = tmp_path / "2022q1.zip"
    _zip(archive)
    cut = edgar.cut_fsds(archive, {1}, "2022q1")
    assert cut["sub"] == (
        ["adsh", "cik", "name", "form", "fsds_quarter"],
        [["0000000001-22-000001", "1", "ONE", "10-K", "2022q1"]],
    )
    # A segments value holding a tab comes quoted, as one field.
    assert [r[1:4] for r in cut["num"][1]] == [
        ["Revenues", "us-gaap/2022", ""],
        ["Loan", "us-gaap/2022", "InvestmentIdentifier=A Co\t, Secured Debt;"],
    ]
    assert cut["pre"][1] == []
    # tag: the (tag, version) company 1 uses, text as filed (a quote inside is a character).
    assert [r[0] for r in cut["tag"][1]] == ["Revenues", "Loan"]
    assert cut["tag"][1][0][2] == 'Amount of revenue "as filed"'


def test_the_client_names_itself_and_keeps_under_ten_requests_a_second() -> None:
    with pytest.raises(edgar.Refused):
        edgar.Client("DataAgent-eval", 8)  # no contact
    with pytest.raises(edgar.Refused):
        edgar.Client(UA, 11)
    seen: list[str] = []
    slept: list[float] = []
    clock = [0.0]

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, json={}, headers={"last-modified": "Mon, 01 Jan 2024"})

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        clock[0] += seconds

    client = edgar.Client(
        UA, 8, transport=httpx.MockTransport(handler), sleep=sleep, clock=lambda: clock[0]
    )
    for _ in range(3):
        client.last_modified("https://data.sec.gov/x")
    assert seen == [UA] * 3
    assert slept == [pytest.approx(0.125)] * 2 and client.requests == 3


def test_403_stops_the_pull_and_5xx_is_retried(tmp_path: pathlib.Path) -> None:
    answers = iter([503, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        if "blocked" in request.url.path:
            return httpx.Response(403, text="Request Rate Threshold Exceeded")
        return httpx.Response(next(answers), content=b"body")

    client = edgar.Client(UA, 10, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    fetched = client.download("https://www.sec.gov/ok", tmp_path / "ok")
    assert fetched is not None and (tmp_path / "ok").read_bytes() == b"body"
    with pytest.raises(edgar.Blocked):
        client.download("https://www.sec.gov/blocked", tmp_path / "b")
    assert not (tmp_path / "b").exists() and not (tmp_path / "b.part").exists()


def _edgar(tmp_path: pathlib.Path, calls: list[str]) -> httpx.MockTransport:
    archive = tmp_path / "fixture.zip"
    _zip(archive)
    submissions = {
        "name": "ONE CORP",
        "fiscalYearEnd": "1231",
        "filings": {
            "recent": {k: [r[k] for r in ROWS] for k in ROWS[0]},
            "files": [],
        },
    }
    companyfacts = {
        "facts": {"us-gaap": {"Revenues": {"units": {"USD": [
            {"start": "2021-01-01", "end": "2021-12-31", "val": 100,
             "accn": "0000000001-22-000001", "fy": 2021, "fp": "FY", "form": "10-K",
             "filed": "2022-02-20", "frame": "CY2021"},
        ]}}}}
    }  # fmt: skip

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        lm = {"last-modified": "Tue, 02 Jan 2024 00:00:00 GMT"}
        if url.endswith("company_tickers.json"):
            return httpx.Response(200, json={"0": {"cik_str": 9, "ticker": "ONE"}}, headers=lm)
        if "/submissions/" in url:
            return httpx.Response(200, json=submissions, headers=lm)
        if "/companyfacts/" in url:
            return httpx.Response(200, json=companyfacts, headers=lm)
        if url.endswith(".zip"):
            return httpx.Response(200, content=archive.read_bytes(), headers=lm)
        if url.endswith("MetaLinks.json"):
            return httpx.Response(404)
        return httpx.Response(200, content=b"<html>ixbrl</html>", headers=lm)

    return httpx.MockTransport(handler)


def test_a_pull_writes_tables_and_a_manifest_and_a_rerun_reuses_the_files(
    tmp_path: pathlib.Path,
) -> None:
    calls: list[str] = []
    client = edgar.Client(UA, 10, transport=_edgar(tmp_path, calls), sleep=lambda s: None)
    kwargs: dict[str, Any] = {
        "out": tmp_path / "out",
        "snapshot": "T1",
        "fy_from": 2021,
        "fy_to": 2024,
        "forms": ["10-K", "10-Q", "10-K/A", "10-Q/A"],
        "manifests": tmp_path / "manifests",
        "log": lambda line: None,
    }
    company = [edgar.Company(1, "ONE", None)]
    manifest = edgar.pull(company, client, **kwargs)
    assert manifest["filing_count"] == 4 and manifest["amendment_count"] == 2
    one = manifest["companies"][0]
    assert one["verification"]["ticker_map_cik"] == 9
    assert one["verification"]["matches_ticker_map"] is False
    assert one["verification"]["annual_reports"]["2021"] == "0000000001-22-000001"
    assert one["verification"]["complete"] is False  # no 10-K for 2022-2024
    assert {f["accn"]: f["supersedes"] for f in manifest["filings"]}[
        "0000000001-25-000001"
    ] == "0000000001-23-000001"
    assert all(f["metalinks"] is None and f["primary"]["sha256"] for f in manifest["filings"])
    # The quarters the filings were filed in, each read once.
    assert [q["quarter"] for q in manifest["fsds"]] == ["2022q1", "2022q2", "2023q1", "2025q2"]
    data = tmp_path / "out" / "data"
    # The fixture serves one archive for every quarter: company 1's submission, once a quarter.
    sub = pq.read_table(data / "sub.parquet").to_pylist()
    assert {(r["cik"], r["fsds_quarter"]) for r in sub} == {
        ("1", q) for q in ("2022q1", "2022q2", "2023q1", "2025q2")
    }
    assert set(pq.read_table(data / "num.parquet").column("value").to_pylist()) == {"100", "7"}
    assert pq.read_table(data / "tag.parquet").num_rows == 2  # each (tag, version) once
    facts = pq.read_table(data / "companyfacts_fact.parquet").to_pylist()
    assert facts[0]["val"] == "100" and facts[0]["frame"] == "CY2021"
    filings = pq.read_table(data / "submissions_filing.parquet")
    assert filings.schema.field("fy").type == "int32" and filings.num_rows == 4

    fetched = len(calls)
    again = edgar.pull(company, client, **kwargs)
    assert len(calls) == fetched  # every file came from --out
    assert again["content_sha256"] == manifest["content_sha256"]


def test_a_snapshot_whose_content_changed_is_refused(tmp_path: pathlib.Path) -> None:
    client = edgar.Client(UA, 10, transport=_edgar(tmp_path, []), sleep=lambda s: None)
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    (manifests / "edgar-T1.manifest.json").write_text(
        json.dumps({"content_sha256": "other", "filing_count": 3})
    )
    with pytest.raises(edgar.Refused, match="pull a new snapshot id"):
        edgar.pull(
            [edgar.Company(1)], client, out=tmp_path / "out", snapshot="T1", fy_from=2021,
            fy_to=2024, forms=["10-K"], manifests=manifests, log=lambda line: None,
        )  # fmt: skip
    with pytest.raises(edgar.Refused):
        edgar.pull(
            [edgar.Company(1)], client, out=tmp_path / "out", snapshot="../x", fy_from=2021,
            fy_to=2024, forms=["10-K"], manifests=manifests,
        )  # fmt: skip


def test_verify_lists_the_sources_edgar_changed(tmp_path: pathlib.Path) -> None:
    manifests = tmp_path
    url = "https://data.sec.gov/submissions/CIK0000000001.json"
    (manifests / "edgar-T1.manifest.json").write_text(
        json.dumps(
            {
                "sources": [{"url": url, "last_modified": "Mon, 01 Jan 2024 00:00:00 GMT"}],
                "fsds": [],
                "filings": [],
            }
        )
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"last-modified": "Tue, 02 Jan 2024 00:00:00 GMT"})

    client = edgar.Client(UA, 10, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    out = edgar.verify("T1", client, manifests=manifests)
    assert out["checked"] == 1
    assert out["changed"] == [
        {
            "url": url,
            "was": "Mon, 01 Jan 2024 00:00:00 GMT",
            "now": "Tue, 02 Jan 2024 00:00:00 GMT",
        }
    ]


def test_cik_lists_files_and_the_default_scope(tmp_path: pathlib.Path) -> None:
    scope = json.loads((HERE / "scope.json").read_text())
    assert len(edgar.companies_of(None, scope)) == 20
    picked = edgar.companies_of("320193,34088,999", scope)
    assert [(c.cik, c.ticker) for c in picked] == [(320193, "AAPL"), (34088, "XOM"), (999, None)]
    listed = tmp_path / "ciks.txt"
    listed.write_text("19617\n70858\n")
    assert [c.ticker for c in edgar.companies_of(str(listed), scope)] == ["JPM", "BAC"]
