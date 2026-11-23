"""SEC EDGAR reports of twenty companies, FY2021-2024, pulled into a fixed snapshot (EV-02).

Dataset B of the real-data evaluation (docs/blueprint/DATASET_AND_EVALUATION.md §3): annual and
quarterly reports as filed, with their amendments, read three ways so gold answers can be checked
across sources: the filing itself (inline XBRL), the XBRL facts EDGAR extracted from it
(companyfacts) and the quarterly Financial Statement Data Sets (FSDS).

    python collect.py --user-agent "<Company> <email>" [--cik 320193,19617 | --cik FILE]
                      [--fy-from 2021 --fy-to 2024] [--forms 10-K,10-Q,10-K/A,10-Q/A]
                      [--out DIR] [--rate 8] [--snapshot E20-FY21-24] [--filed-to YYYY-MM-DD]
    python collect.py --user-agent "…" --verify      HEAD every source again, list what changed

The scope (companies, years, forms) defaults to scope.json. For each company: `submissions` (the
recent filings and the older pages that reach back to the first fiscal year), the filings whose
period falls in a fiscal year of the range, each one's primary document (the inline XBRL report)
and MetaLinks.json, `companyfacts` (facts of periods in the range, from every filing that reported
them: a fact repeats once per filing and only the latest carries `frame`, §3.3, so later filings
are kept), and the FSDS quarters the filings were filed in, cut to the companies (`sub` of their
CIKs, `num` and `pre` of those submissions, `tag` whole). Everything lands in --out as Parquet
(sub, num, tag, pre: text as filed; companyfacts_fact; submissions_filing) next to the raw files,
and the manifest (one entry per filing, every file's sha256, the hash of the whole) goes to
model/eval/snapshots/.

A fiscal year is the year it ends in, read from the company's fiscalYearEnd with slack for 52/53-
week years (AAPL's 0926 ended 2023-09-30): WMT's quarter to 2021-04-30 is FY2022. An amendment
(10-K/A, 10-Q/A) `supersedes` the latest earlier filing of the same period and base form.

EDGAR's fair access policy: at most 10 requests a second (--rate, default 8) and a User-Agent
naming the requester; a 403 is EDGAR blocking the client, and the pull stops there rather than
retry into a longer block. 429 and 5xx are retried with backoff. A rerun reuses what --out holds
(filings and FSDS files are immutable); a pull whose content differs from a snapshot already in
the manifest is refused: gold answers are tied to it, so a changed source gets a new snapshot id.

Runs in the dev-tools image (httpx, pyarrow): `make edgar`.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import decimal
import hashlib
import io
import json
import math
import os
import pathlib
import re
import sys
import time
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

HERE = pathlib.Path(__file__).resolve().parent
SCOPE = HERE / "scope.json"
MANIFESTS = HERE.parents[2] / "model" / "eval" / "snapshots"
DATA = "https://data.sec.gov"
WWW = "https://www.sec.gov"
FSDS = f"{WWW}/files/dera/data/financial-statement-data-sets"
TICKERS = f"{WWW}/files/company_tickers.json"
MAX_RATE = 10.0
RETRIES = 5
# 52/53-week years end up to a week either side of fiscalYearEnd; two weeks is safe and far from
# the three months between period ends.
SLACK = datetime.timedelta(days=14)
FSDS_TABLES = ("sub", "num", "pre", "tag")  # tag last: it keeps what num and pre use
_USER_AGENT = re.compile(r"\S.*\s\S+@\S+\.\S+")
_ACCN = re.compile(r"\d{10}-\d{2}-\d{6}")
_DOCUMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_PAGE = re.compile(r"CIK\d{10}-submissions-\d{3}\.json")
SNAPSHOT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


class Refused(Exception):
    """A pull that cannot go on: EDGAR refused, a source is malformed, or a snapshot would change."""


class Blocked(Refused):
    """403: EDGAR blocks this client. Retrying only lengthens the block."""


@dataclass(frozen=True)
class Fetched:
    url: str
    sha256: str
    bytes: int
    last_modified: str | None


class Client:
    """EDGAR at no more than `rate` requests a second, under a User-Agent that names the requester.
    Downloads go through a part file, so a stopped pull never leaves a truncated file behind."""

    def __init__(
        self,
        user_agent: str,
        rate: float = 8.0,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not _USER_AGENT.fullmatch(user_agent.strip()):
            raise Refused("the User-Agent must name the requester: '<Company> <email>'")
        if not 0 < rate <= MAX_RATE:
            raise Refused(f"--rate {rate}: EDGAR allows at most {MAX_RATE:g} requests a second")
        self._http = httpx.Client(
            headers={"User-Agent": user_agent.strip()},
            timeout=httpx.Timeout(120.0),
            transport=transport,
            follow_redirects=True,
        )
        self._gap = 1.0 / rate
        self._next = -math.inf
        self._sleep = sleep
        self._clock = clock
        self.requests = 0

    def _pace(self) -> None:
        now = self._clock()
        if now < self._next:
            self._sleep(self._next - now)
            now = self._next
        self._next = now + self._gap
        self.requests += 1

    def _send(
        self, method: str, url: str, target: pathlib.Path | None = None
    ) -> tuple[int, httpx.Headers, bytes, Fetched | None]:
        for attempt in range(RETRIES + 1):
            self._pace()
            try:
                with self._http.stream(method, url) as response:
                    status = response.status_code
                    if status == 403:
                        raise Blocked(
                            f"403 for {url}: EDGAR blocks this client (rate or User-Agent); "
                            "stop and try again later"
                        )
                    if status in (429, 500, 502, 503, 504) and attempt < RETRIES:
                        response.close()
                        self._sleep(2.0**attempt)
                        continue
                    if status != 200 or target is None or method != "GET":
                        return status, response.headers, response.read(), None
                    digest = hashlib.sha256()
                    size = 0
                    part = target.with_name(target.name + ".part")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with part.open("wb") as out:
                        for chunk in response.iter_bytes(1 << 20):
                            digest.update(chunk)
                            size += len(chunk)
                            out.write(chunk)
                    part.replace(target)
                    fetched = Fetched(
                        url, digest.hexdigest(), size, response.headers.get("last-modified")
                    )
                    return status, response.headers, b"", fetched
            except httpx.TransportError as exc:
                if attempt == RETRIES:
                    raise Refused(f"EDGAR did not answer {url}: {exc}") from exc
                self._sleep(2.0**attempt)
        raise AssertionError("unreachable")

    def download(
        self, url: str, target: pathlib.Path, *, missing_ok: bool = False
    ) -> Fetched | None:
        status, _, body, fetched = self._send("GET", url, target)
        if status == 404 and missing_ok:
            return None
        if fetched is None:
            raise Refused(f"{status} for {url}: {body[:200]!r}")
        return fetched

    def last_modified(self, url: str) -> str | None:
        status, headers, _, _ = self._send("HEAD", url)
        if status != 200:
            raise Refused(f"{status} for HEAD {url}")
        value = headers.get("last-modified")
        return str(value) if value is not None else None


class Store:
    """--out: raw files as EDGAR served them, with what was recorded when each was fetched, so a
    rerun reuses them (idempotent by URL; a filing's files by (cik, accn))."""

    def __init__(self, root: pathlib.Path, client: Client) -> None:
        self.root = root
        self.client = client
        self._index_path = root / "raw" / "fetched.json"
        self._index: dict[str, dict[str, Any]] = (
            json.loads(self._index_path.read_text()) if self._index_path.exists() else {}
        )

    def get(self, url: str, relative: str, *, missing_ok: bool = False) -> Fetched | None:
        target = self.root / "raw" / relative
        known = self._index.get(relative)
        if known is not None and known["url"] == url:
            if known.get("missing"):
                return None
            if target.exists():
                return Fetched(**known)
        fetched = self.client.download(url, target, missing_ok=missing_ok)
        self._index[relative] = asdict(fetched) if fetched else {"url": url, "missing": True}
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        self._index_path.write_text(json.dumps(self._index, indent=1, sort_keys=True))
        return fetched

    def path(self, relative: str) -> pathlib.Path:
        return self.root / "raw" / relative

    def json(self, url: str, relative: str) -> Any:
        self.get(url, relative)
        return json.loads(self.path(relative).read_bytes(), parse_float=decimal.Decimal)


@dataclass(frozen=True)
class Company:
    cik: int
    ticker: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class Filing:
    cik: int
    accn: str
    form: str
    filing_date: str
    report_date: str
    primary_document: str
    is_inline_xbrl: bool
    size: int
    fy: int
    supersedes: str | None = None


def _date(year: int, month: int, day: int) -> datetime.date:
    """fiscalYearEnd 0229 in a common year is its last day of February."""
    while True:
        try:
            return datetime.date(year, month, day)
        except ValueError:
            day -= 1


def fiscal_year(period_end: datetime.date, fiscal_year_end: str) -> int:
    """The fiscal year a period ending on `period_end` belongs to: the year that fiscal year ends
    in (WMT, 0131: the quarter to 2021-04-30 is FY2022; AAPL, 0926: the year to 2023-09-30 is
    FY2023, a 53-week year ending four days late)."""
    if not re.fullmatch(r"\d{4}", fiscal_year_end or ""):
        raise Refused(f"fiscalYearEnd {fiscal_year_end!r} is not MMDD")
    month, day = int(fiscal_year_end[:2]), int(fiscal_year_end[2:])
    for year in (period_end.year - 1, period_end.year, period_end.year + 1):
        if period_end <= _date(year, month, day) + SLACK:
            return year
    raise AssertionError("unreachable")


def submission_rows(document: dict[str, Any]) -> list[dict[str, Any]]:
    """The columnar filings block of a submissions document (recent, or an older page) as rows."""
    block = document.get("filings", {}).get("recent", document)
    columns = {k: v for k, v in block.items() if isinstance(v, list)}
    count = len(columns.get("accessionNumber", []))
    if any(len(v) != count for v in columns.values()):
        raise Refused("a submissions block whose columns differ in length")
    return [{k: v[i] for k, v in columns.items()} for i in range(count)]


def older_pages(document: dict[str, Any], since: datetime.date) -> list[str]:
    """The older submissions pages holding filings filed on or after `since`."""
    names = []
    for page in document.get("filings", {}).get("files", []):
        name = str(page.get("name", ""))
        if not _PAGE.fullmatch(name):
            raise Refused(f"submissions page {name!r} is not a page name")
        if str(page.get("filingTo", "")) >= since.isoformat():
            names.append(name)
    return names


def select(
    rows: Iterable[dict[str, Any]],
    cik: int,
    fiscal_year_end: str,
    *,
    forms: Sequence[str],
    fy_from: int,
    fy_to: int,
    filed_to: datetime.date | None = None,
) -> list[Filing]:
    """The filings of the scope's forms whose period falls in [fy_from, fy_to], amendments linked."""
    chosen: dict[str, Filing] = {}
    for row in rows:
        form = str(row.get("form", ""))
        report = str(row.get("reportDate") or "")
        if form not in forms or not report:
            continue
        accn = str(row["accessionNumber"])
        if not _ACCN.fullmatch(accn):
            raise Refused(f"accession number {accn!r} is malformed")
        filed = str(row["filingDate"])
        if filed_to is not None and filed > filed_to.isoformat():
            continue
        fy = fiscal_year(datetime.date.fromisoformat(report), fiscal_year_end)
        if not fy_from <= fy <= fy_to:
            continue
        chosen[accn] = Filing(
            cik=cik,
            accn=accn,
            form=form,
            filing_date=filed,
            report_date=report,
            primary_document=str(row.get("primaryDocument") or ""),
            is_inline_xbrl=bool(row.get("isInlineXBRL")),
            size=int(row.get("size") or 0),
            fy=fy,
        )
    return link_amendments(list(chosen.values()))


def link_amendments(filings: Sequence[Filing]) -> list[Filing]:
    """An amendment supersedes the latest earlier filing of its period and base form (the
    original, or the amendment before it: Plug Power's 10-K/A chain)."""
    ordered = sorted(filings, key=lambda f: (f.filing_date, f.accn))
    out: list[Filing] = []
    for n, filing in enumerate(ordered):
        if filing.form.endswith("/A"):
            base = filing.form[:-2]
            earlier = [
                g
                for g in ordered[:n]
                if g.form in (base, filing.form) and g.report_date == filing.report_date
            ]
            if earlier:
                filing = replace(filing, supersedes=earlier[-1].accn)
        out.append(filing)
    return out


def facts(
    document: dict[str, Any],
    cik: int,
    fiscal_year_end: str,
    *,
    fy_from: int,
    fy_to: int,
    filed_to: datetime.date | None = None,
) -> list[dict[str, Any]]:
    """companyfacts flattened: every fact whose period ends in a fiscal year of the range, from
    every filing that reported it (the repeats are the restatement trail, §3.3)."""
    rows = []
    for taxonomy, tags in sorted(document.get("facts", {}).items()):
        for tag, body in sorted(tags.items()):
            for unit, items in sorted(body.get("units", {}).items()):
                for item in items:
                    end = str(item.get("end") or "")
                    filed = str(item.get("filed") or "")
                    if not end or (filed_to is not None and filed > filed_to.isoformat()):
                        continue
                    period_fy = fiscal_year(datetime.date.fromisoformat(end), fiscal_year_end)
                    if not fy_from <= period_fy <= fy_to:
                        continue
                    rows.append(
                        {
                            "cik": cik,
                            "taxonomy": taxonomy,
                            "tag": tag,
                            "unit": unit,
                            "start": item.get("start"),
                            "end": end,
                            "val": str(item["val"]),
                            "accn": item.get("accn"),
                            "fy": item.get("fy"),
                            "fp": item.get("fp"),
                            "form": item.get("form"),
                            "filed": filed or None,
                            "frame": item.get("frame"),
                        }
                    )
    return rows


def quarter(day: str) -> str:
    date = datetime.date.fromisoformat(day)
    return f"{date.year}q{(date.month - 1) // 3 + 1}"


def cut_fsds(
    archive: pathlib.Path, ciks: set[int], name: str
) -> dict[str, tuple[list[str], list[list[str]]]]:
    """One FSDS quarter cut to the companies: sub of their CIKs, num and pre of those submissions,
    tag of the (tag, version) they use (header, rows), each row ending with its quarter. tag
    whole is every filer's custom tags too, 1.4 million rows a year where one company uses ~2 000."""

    def read(member: str) -> Iterable[tuple[list[str], list[str]]]:
        with zipfile.ZipFile(archive) as z, z.open(member) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
            # num.segments is quoted when it holds a tab ("InvestmentIdentifier=…\t…;", 57 rows
            # of 2022q4); a quote inside a field (tag.doc) stays a character.
            csv.field_size_limit(1 << 24)  # tag.doc runs long; the default stops at 128 KiB
            reader = csv.reader(text, delimiter="\t")
            header = next(reader)
            for row in reader:
                if row:
                    yield header, row

    out: dict[str, tuple[list[str], list[list[str]]]] = {}
    submissions: set[str] = set()
    used: set[tuple[str, str]] = set()
    for table in FSDS_TABLES:
        header: list[str] = []
        kept: list[list[str]] = []
        for header, row in read(f"{table}.txt"):
            if len(row) != len(header):
                raise Refused(
                    f"{name}/{table}.txt: a row of {len(row)} fields, header {len(header)}"
                )
            if table == "sub":
                if int(row[header.index("cik")]) not in ciks:
                    continue
                submissions.add(row[header.index("adsh")])
            elif table in ("num", "pre"):
                if row[header.index("adsh")] not in submissions:
                    continue
                used.add((row[header.index("tag")], row[header.index("version")]))
            elif (row[header.index("tag")], row[header.index("version")]) not in used:
                continue
            kept.append([*row, name])
        out[table] = ([*header, "fsds_quarter"], kept)
    return out


def _rows_sha256(columns: Sequence[str], rows: Iterable[Sequence[Any]]) -> str:
    digest = hashlib.sha256(json.dumps(list(columns)).encode())
    for row in rows:
        digest.update(json.dumps(list(row), default=str, separators=(",", ":")).encode() + b"\n")
    return digest.hexdigest()


def write_table(
    path: pathlib.Path, columns: Sequence[str], rows: list[list[Any]], types: dict[str, pa.DataType]
) -> dict[str, Any]:
    rows = sorted(rows, key=lambda r: [("" if v is None else str(v)) for v in r])
    schema = pa.schema([(c, types.get(c, pa.string())) for c in columns])
    # Column by column: twenty companies' num is about a million rows, too many to copy as dicts.
    table = pa.Table.from_arrays(
        [pa.array([r[i] for r in rows], type=schema.field(i).type) for i in range(len(columns))],
        schema=schema,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path, compression="zstd")
    return {
        "path": path.name,
        "rows": len(rows),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "rows_sha256": _rows_sha256(columns, rows),
    }


def _checked(snapshot: str) -> str:
    if not SNAPSHOT_ID.fullmatch(snapshot):
        raise Refused(f"snapshot id {snapshot!r}: letters, digits, '.', '_' and '-' only")
    return snapshot


def _cik10(cik: int) -> str:
    return f"{cik:010d}"


FACT_COLUMNS = [
    "cik", "taxonomy", "tag", "unit", "start", "end", "val", "accn", "fy", "fp", "form", "filed",
    "frame",
]  # fmt: skip
FILING_COLUMNS = [
    "cik", "accn", "form", "filing_date", "report_date", "primary_document", "is_inline_xbrl",
    "size", "fy", "supersedes",
]  # fmt: skip
INTEGERS = {"cik": pa.int64(), "fy": pa.int32(), "size": pa.int64(), "is_inline_xbrl": pa.bool_()}


def pull(
    companies: Sequence[Company],
    client: Client,
    *,
    out: pathlib.Path,
    snapshot: str,
    fy_from: int,
    fy_to: int,
    forms: Sequence[str],
    filed_to: datetime.date | None = None,
    manifests: pathlib.Path = MANIFESTS,
    now: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(datetime.UTC),
    log: Callable[[str], None] = lambda line: print(line, file=sys.stderr),
) -> dict[str, Any]:
    _checked(snapshot)
    store = Store(out, client)
    tickers = store.json(TICKERS, "company_tickers.json")
    by_ticker = {str(v["ticker"]): int(v["cik_str"]) for v in tickers.values()}
    since = datetime.date(fy_from - 1, 1, 1)
    sources: list[Fetched] = []
    filings: list[Filing] = []
    fact_rows: list[list[Any]] = []
    entries: list[dict[str, Any]] = []
    for company in companies:
        cik10 = _cik10(company.cik)
        url = f"{DATA}/submissions/CIK{cik10}.json"
        document = store.json(url, f"submissions/CIK{cik10}.json")
        sources.append(store.get(url, f"submissions/CIK{cik10}.json"))  # type: ignore[arg-type]
        rows = submission_rows(document)
        for page in older_pages(document, since):
            page_url = f"{DATA}/submissions/{page}"
            rows += submission_rows(store.json(page_url, f"submissions/{page}"))
            sources.append(store.get(page_url, f"submissions/{page}"))  # type: ignore[arg-type]
        fye = str(document.get("fiscalYearEnd") or "")
        chosen = select(
            rows, company.cik, fye, forms=forms, fy_from=fy_from, fy_to=fy_to, filed_to=filed_to
        )
        filings += chosen
        facts_url = f"{DATA}/api/xbrl/companyfacts/CIK{cik10}.json"
        facts_doc = store.json(facts_url, f"companyfacts/CIK{cik10}.json")
        sources.append(store.get(facts_url, f"companyfacts/CIK{cik10}.json"))  # type: ignore[arg-type]
        company_facts = facts(
            facts_doc, company.cik, fye, fy_from=fy_from, fy_to=fy_to, filed_to=filed_to
        )
        fact_rows += [[r[c] for c in FACT_COLUMNS] for r in company_facts]
        annual = {
            f.fy: f.accn for f in sorted(chosen, key=lambda f: f.filing_date) if f.form == "10-K"
        }
        mapped = by_ticker.get(company.ticker or "")
        entries.append(
            {
                "ticker": company.ticker,
                "cik": company.cik,
                "name": document.get("name"),
                "fiscal_year_end": fye,
                "filings": len(chosen),
                "by_form": {
                    form: sum(1 for f in chosen if f.form == form)
                    for form in forms
                    if any(f.form == form for f in chosen)
                },
                "facts": len(company_facts),
                # The CIK is checked, not assumed: the ticker file's CIK beside it, and an annual
                # report for every fiscal year (XOM's ticker moved to a new holding company).
                "verification": {
                    "ticker_map_cik": mapped,
                    "matches_ticker_map": mapped == company.cik,
                    "annual_reports": {str(fy): annual.get(fy) for fy in range(fy_from, fy_to + 1)},
                    "complete": all(fy in annual for fy in range(fy_from, fy_to + 1)),
                },
                **({"note": company.note} if company.note else {}),
            }
        )
        log(f"{company.ticker or company.cik}: {len(chosen)} filings, {len(company_facts)} facts")

    documents: list[dict[str, Any]] = []
    for filing in sorted(filings, key=lambda f: (f.cik, f.filing_date, f.accn)):
        folder = f"{WWW}/Archives/edgar/data/{filing.cik}/{filing.accn.replace('-', '')}"
        local = f"filings/{filing.cik}/{filing.accn}"
        primary = None
        if _DOCUMENT.fullmatch(filing.primary_document):
            primary = store.get(
                f"{folder}/{filing.primary_document}", f"{local}/{filing.primary_document}"
            )
        metalinks = store.get(
            f"{folder}/MetaLinks.json", f"{local}/MetaLinks.json", missing_ok=True
        )
        documents.append(
            {
                **{k: v for k, v in asdict(filing).items() if k not in ("size",)},
                "primary": asdict(primary) if primary else None,
                "metalinks": asdict(metalinks) if metalinks else None,
            }
        )

    ciks = {c.cik for c in companies}
    fsds: dict[str, tuple[list[str], dict[tuple[str, ...], list[str]]]] = {}
    quarters = []
    for name in sorted({quarter(f.filing_date) for f in filings}):
        fetched = store.get(f"{FSDS}/{name}.zip", f"fsds/{name}.zip")
        assert fetched is not None
        quarters.append({"quarter": name, **asdict(fetched)})
        cut = cut_fsds(store.path(f"fsds/{name}.zip"), ciks, name)
        for table, (header, cut_rows) in cut.items():
            _, kept = fsds.setdefault(table, (header, {}))
            if fsds[table][0][:-1] != header[:-1]:
                raise Refused(f"{name}/{table}.txt changed its columns: {header}")
            for row in cut_rows:
                # tag is the taxonomy, repeated each quarter: the first (tag, version) is kept.
                key = tuple(row[:2]) if table == "tag" else tuple(row)
                kept.setdefault(key, row)
        log(f"FSDS {name}: cut")

    data = out / "data"
    tables = {
        "submissions_filing": write_table(
            data / "submissions_filing.parquet",
            FILING_COLUMNS,
            [[getattr(f, c) for c in FILING_COLUMNS] for f in filings],
            INTEGERS,
        ),
        "companyfacts_fact": write_table(
            data / "companyfacts_fact.parquet", FACT_COLUMNS, fact_rows, INTEGERS
        ),
    }
    for table in FSDS_TABLES:
        header, kept = fsds.get(table, ([], {}))
        if header:
            tables[table] = write_table(data / f"{table}.parquet", header, list(kept.values()), {})
    content = hashlib.sha256()
    for table_name, written in sorted(tables.items()):
        content.update(f"{table_name}\t{written['rows_sha256']}\n".encode())
    for entry in documents:
        primary = entry["primary"]
        content.update(f"{entry['accn']}\t{primary['sha256'] if primary else ''}\n".encode())
    manifest = {
        "snapshot_id": snapshot,
        "source": "U.S. SEC EDGAR",
        "terms": "https://www.sec.gov/os/accessing-edgar-data",
        "pulled_at": now().isoformat(),
        "scope": {
            "fy_from": fy_from,
            "fy_to": fy_to,
            "forms": list(forms),
            "filed_to": filed_to.isoformat() if filed_to else None,
        },
        "companies": entries,
        "filing_count": len(documents),
        "amendment_count": sum(1 for f in filings if f.form.endswith("/A")),
        "content_sha256": content.hexdigest(),
        "tables": tables,
        "fsds": quarters,
        "sources": sorted((asdict(s) for s in sources if s), key=lambda s: s["url"]),
        "filings": documents,
        "requests": client.requests,
    }
    manifests.mkdir(parents=True, exist_ok=True)
    target = manifests / f"edgar-{snapshot}.manifest.json"
    if target.exists():
        before: dict[str, Any] = json.loads(target.read_text())
        if before.get("content_sha256") != manifest["content_sha256"]:
            raise Refused(
                f"{target.name} holds other content ({before.get('filing_count')} filings, "
                f"{before.get('content_sha256')}); the gold answers are tied to it: pull a new "
                "snapshot id instead"
            )
        return before
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    return manifest


def verify(snapshot: str, client: Client, *, manifests: pathlib.Path = MANIFESTS) -> dict[str, Any]:
    """HEAD every source of the snapshot again: what EDGAR changed since (submissions and
    companyfacts move every night; a filing or an FSDS quarter should not)."""
    manifest = json.loads((manifests / f"edgar-{_checked(snapshot)}.manifest.json").read_text())
    recorded = [
        *manifest["sources"],
        *manifest["fsds"],
        *(d[k] for d in manifest["filings"] for k in ("primary", "metalinks") if d[k]),
    ]
    changed = []
    for source in recorded:
        now = client.last_modified(source["url"])
        if now != source["last_modified"]:
            changed.append({"url": source["url"], "was": source["last_modified"], "now": now})
    return {"snapshot_id": snapshot, "checked": len(recorded), "changed": changed}


def companies_of(value: str | None, scope: dict[str, Any]) -> list[Company]:
    """--cik: a comma list of CIKs, a file of one CIK per line, or a scope JSON; none: scope.json."""
    known = {int(c["cik"]): c for c in scope["companies"]}
    if not value:
        ciks = list(known)
    else:
        path = pathlib.Path(value)
        if path.is_file():
            text = path.read_text()
            ciks = (
                [int(c["cik"]) for c in json.loads(text)["companies"]]
                if path.suffix == ".json"
                else [int(line) for line in text.split() if line.strip()]
            )
        else:
            ciks = [int(part) for part in value.split(",") if part.strip()]
    return [
        Company(cik, known.get(cik, {}).get("ticker"), known.get(cik, {}).get("note"))
        for cik in dict.fromkeys(ciks)
    ]


def main(argv: list[str] | None = None) -> int:
    scope = json.loads(SCOPE.read_text())
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--user-agent",
        default=os.getenv("EDGAR_USER_AGENT"),
        required=not os.getenv("EDGAR_USER_AGENT"),
        help="'<Company> <email>' (SEC fair access); default EDGAR_USER_AGENT",
    )
    parser.add_argument(
        "--cik", help="CIKs: '320193,19617', or a file of them (default scope.json)"
    )
    parser.add_argument("--fy-from", type=int, default=scope["fy_from"])
    parser.add_argument("--fy-to", type=int, default=scope["fy_to"])
    parser.add_argument("--forms", default=",".join(scope["forms"]))
    parser.add_argument("--filed-to", type=datetime.date.fromisoformat)
    parser.add_argument("--out", type=pathlib.Path, default=HERE / ".cache")
    parser.add_argument(
        "--rate", type=float, default=8.0, help=f"requests a second, ≤ {MAX_RATE:g}"
    )
    parser.add_argument("--snapshot", default=scope["snapshot"])
    parser.add_argument("--manifests", type=pathlib.Path, default=MANIFESTS)
    parser.add_argument("--verify", action="store_true", help="HEAD the sources, list changes")
    args = parser.parse_args(argv)
    try:
        client = Client(args.user_agent, args.rate)
        if args.verify:
            out = verify(args.snapshot, client, manifests=args.manifests)
        else:
            if args.fy_to < args.fy_from:
                parser.error("--fy-to must not be before --fy-from")
            out = pull(
                companies_of(args.cik, scope),
                client,
                out=args.out,
                snapshot=args.snapshot,
                fy_from=args.fy_from,
                fy_to=args.fy_to,
                forms=[f.strip() for f in args.forms.split(",") if f.strip()],
                filed_to=args.filed_to,
                manifests=args.manifests,
            )
            out = {k: v for k, v in out.items() if k not in ("filings", "sources", "fsds")}
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
