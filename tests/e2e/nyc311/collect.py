"""NYC 311 Service Requests (erm2-nwe9), pulled into a fixed snapshot the golden answers are defined on.

The real-data evaluation (docs/blueprint/DATASET_AND_EVALUATION.md §2, EV-01) needs a dataset that
does not move under its answers. Socrata keeps no versions and edits rows in place (status,
closed_date, resolution_description change after a request is filed), so a snapshot is pulled
once for a created_date window, hashed row by row, and every gold answer is computed on it:

    python -I collect.py pull --snapshot S3-2025Q1 --start 2025-01-01 --end 2025-04-01
    python -I collect.py drift --snapshot S3-2025Q1

`pull` pages through the SODA 2.1 endpoint ordered by unique_key, keeps each page on disk as it
comes (a run that stops resumes from the last page written), then writes the rows as one
gzipped JSON-lines file per created_date month and a manifest: the window, when it was pulled,
the newest :updated_at seen, one sha256 per row and per file, and the hash of the whole. A second
pull of the same snapshot id that does not reproduce that hash is refused: the answers are tied to
the first, so a changed source gets a new id. `drift` counts the rows the source changed since.

Rows are stored as the API returned them, canonicalised (sorted keys, no nulls) and without the
undocumented :@computed_region_* columns; typing happens when they are loaded (EV-03), which also
turns them into Parquet through libs/snapshot once it exists (DF-04).

The portal asks for an app token (SOCRATA_APP_TOKEN, sent as X-App-Token) and throttles the
shared pool without one. The client says who it is; it does not pose as a browser. Licence: the
catalogue record names none and nyc.gov's terms reserve their rights, so the manifest records
`terms_status: to be confirmed` and the snapshot stays inside the organisation (§2.2).

Standard library only and Python 3.10, like tests/e2e/adventureworks/load.py: it runs on a host
with no project dependencies, which is where the portal can be reached from.
"""

from __future__ import annotations

import argparse
import datetime
import gzip
import hashlib
import io
import json
import os
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

DATASET = "erm2-nwe9"
SOURCE = f"https://data.cityofnewyork.us/resource/{DATASET}.json"
LANDING = f"https://data.cityofnewyork.us/d/{DATASET}"
HERE = pathlib.Path(__file__).resolve().parent
CACHE = HERE / ".cache"
MANIFESTS = HERE.parents[2] / "model" / "eval" / "snapshots"
PAGE_SIZE = 50_000
RETRIES = 5
USER_AGENT = "DataAgent-eval/1.0 (NYC 311 snapshot for internal evaluation)"
TERMS = "to be confirmed"
MONTH = re.compile(r"\d{4}-\d{2}")
SNAPSHOT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_DAY = datetime.timedelta(days=1)


class Refused(Exception):
    """A pull that would overwrite a snapshot with different rows, or a page the portal refuses."""


@dataclass(frozen=True)
class Window:
    start: datetime.date
    end: datetime.date  # exclusive

    def where(self) -> str:
        return (
            f"created_date >= '{self.start.isoformat()}T00:00:00' "
            f"AND created_date < '{self.end.isoformat()}T00:00:00'"
        )


Fetch = Callable[[str, dict[str, str]], list[dict[str, Any]]]


def canonical(row: dict[str, Any]) -> dict[str, Any]:
    """The row as stored: sorted keys, no null or empty value, no computed region column."""
    return {
        k: row[k]
        for k in sorted(row)
        if row[k] not in (None, "") and "computed_region" not in k and k != ":id"
    }


def row_hash(row: dict[str, Any]) -> str:
    text = json.dumps(canonical(row), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def http_fetch(token: str | None, sleep: Callable[[float], None] = time.sleep) -> Fetch:
    """A page fetcher: retries 429, 5xx and dropped connections with 2^n backoff, up to RETRIES;
    any other refusal (403 and the like) stops the pull with the portal's answer."""

    def fetch(url: str, params: dict[str, str]) -> list[dict[str, Any]]:
        request = urllib.request.Request(
            f"{url}?{urllib.parse.urlencode(params)}",
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                **({"X-App-Token": token} if token else {}),
            },
        )
        for attempt in range(RETRIES + 1):
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    body = json.loads(response.read().decode("utf-8"))
                if not isinstance(body, list):
                    raise Refused(f"unexpected answer: {str(body)[:200]}")
                return body
            except urllib.error.HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt == RETRIES:
                    raise Refused(f"{exc.code} from the portal: {exc.read()[:200]!r}") from exc
            except urllib.error.URLError as exc:
                if attempt == RETRIES:
                    raise Refused(f"the portal did not answer: {exc.reason}") from exc
            sleep(2.0**attempt)
        raise AssertionError("unreachable")

    return fetch


def pages(
    fetch: Fetch, window: Window, *, page_size: int, offset: int = 0
) -> Iterator[tuple[int, list[dict[str, Any]]]]:
    """(offset, rows) for every page of the window, stable because ordered by unique_key."""
    while True:
        rows = fetch(
            SOURCE,
            {
                "$select": ":*, *",
                "$where": window.where(),
                "$order": "unique_key",
                "$limit": str(page_size),
                "$offset": str(offset),
            },
        )
        if not rows:
            return
        yield offset, rows
        if len(rows) < page_size:
            return
        offset += page_size


def _gzip(lines: Sequence[str]) -> bytes:
    """Deterministic gzip (no name, mtime 0): the same rows give the same bytes and sha256."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, filename="") as gz:
        for line in lines:
            gz.write(line.encode("utf-8") + b"\n")
    return buf.getvalue()


def _checked(snapshot: str) -> str:
    """The id names a directory and a manifest file: no separators, no dots in front."""
    if not SNAPSHOT_ID.fullmatch(snapshot):
        raise Refused(f"snapshot id {snapshot!r}: letters, digits, '.', '_' and '-' only")
    return snapshot


def _work(snapshot: str, cache: pathlib.Path) -> pathlib.Path:
    path = cache / _checked(snapshot)
    (path / "pages").mkdir(parents=True, exist_ok=True)
    return path


def pull(
    snapshot: str,
    window: Window,
    fetch: Fetch,
    *,
    cache: pathlib.Path = CACHE,
    manifests: pathlib.Path = MANIFESTS,
    page_size: int = PAGE_SIZE,
    now: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(datetime.timezone.utc),  # noqa: UP017 (runs on 3.10 hosts)
) -> dict[str, Any]:
    work = _work(snapshot, cache)
    progress = work / "progress.json"
    state = json.loads(progress.read_text()) if progress.exists() else {}
    if state and state.get("window") != [window.start.isoformat(), window.end.isoformat()]:
        raise Refused(f"{snapshot} was started for another window: {state['window']}")
    pulled_at = state.get("pulled_at") or now().isoformat()
    offset = int(state.get("next_offset", 0))
    for at, rows in pages(fetch, window, page_size=page_size, offset=offset):
        (work / "pages" / f"{at:012d}.json").write_text(json.dumps(rows, ensure_ascii=False))
        state = {
            "window": [window.start.isoformat(), window.end.isoformat()],
            "pulled_at": pulled_at,
            "next_offset": at + len(rows),
        }
        progress.write_text(json.dumps(state))
    return finish(snapshot, window, pulled_at, work, manifests=manifests)


def finish(
    snapshot: str,
    window: Window,
    pulled_at: str,
    work: pathlib.Path,
    *,
    manifests: pathlib.Path,
) -> dict[str, Any]:
    _checked(snapshot)
    rows: dict[str, dict[str, Any]] = {}
    duplicates = 0
    newest = ""
    for page in sorted((work / "pages").glob("*.json")):
        for row in json.loads(page.read_text()):
            key = str(row.get("unique_key", ""))
            if not key:
                raise Refused(f"a row without unique_key in {page.name}")
            # A key seen twice is the source moving while it was paged: the later page wins.
            duplicates += key in rows
            rows[key] = row
            newest = max(newest, str(row.get(":updated_at", "")))
    by_month: dict[str, list[str]] = {}
    hashes: list[str] = []
    for key in sorted(rows, key=lambda k: (len(k), k)):
        row = canonical(rows[key])
        hashes.append(f"{key}\t{row_hash(row)}")
        # The month names a file, and it comes from the portal: anything but YYYY-MM inside the
        # window (a path, a malformed date) refuses the pull rather than becoming a snapshot.
        month = str(row.get("created_date", ""))[:7]
        if not MONTH.fullmatch(month) or not (
            window.start.isoformat()[:7] <= month <= (window.end - _DAY).isoformat()[:7]
        ):
            raise Refused(
                f"row {key} has created_date {row.get('created_date')!r}, outside the window"
            )
        by_month.setdefault(month, []).append(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
    files = []
    data = work / "data"
    data.mkdir(exist_ok=True)
    for month, lines in sorted(by_month.items()):
        blob = _gzip(lines)
        (data / f"{month}.jsonl.gz").write_bytes(blob)
        files.append(
            {
                "path": f"data/{month}.jsonl.gz",
                "rows": len(lines),
                "sha256": hashlib.sha256(blob).hexdigest(),
            }
        )
    (work / "rows.tsv").write_text("\n".join(hashes) + "\n" if hashes else "")
    manifest = {
        "snapshot_id": snapshot,
        "dataset": DATASET,
        "source_url": SOURCE,
        "landing": LANDING,
        "window": {
            "created_date_from": window.start.isoformat(),
            "created_date_to": window.end.isoformat(),
        },
        "pulled_at": pulled_at,
        "max_updated_at": newest or None,
        "row_count": len(rows),
        "duplicate_keys_seen": duplicates,
        "rows_sha256": hashlib.sha256("\n".join(hashes).encode("utf-8")).hexdigest(),
        "files": files,
        "terms_status": TERMS,
        "attribution": "NYC OpenData, 311 Service Requests from 2020 to Present (erm2-nwe9)",
    }
    manifests.mkdir(parents=True, exist_ok=True)
    target = manifests / f"nyc311-{snapshot}.manifest.json"
    if target.exists():
        before: dict[str, Any] = json.loads(target.read_text())
        if before.get("rows_sha256") != manifest["rows_sha256"]:
            raise Refused(
                f"{target.name} holds other rows ({before.get('row_count')} rows, "
                f"{before.get('rows_sha256')}); the gold answers are tied to it: pull a new "
                "snapshot id instead"
            )
        return before
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def drift(
    snapshot: str,
    fetch: Fetch,
    *,
    cache: pathlib.Path = CACHE,
    manifests: pathlib.Path = MANIFESTS,
    now: Callable[[], datetime.datetime] = lambda: datetime.datetime.now(datetime.timezone.utc),  # noqa: UP017 (runs on 3.10 hosts)
) -> dict[str, Any]:
    """How many rows of the snapshot's window the source changed since it was pulled."""
    _checked(snapshot)
    manifest = json.loads((manifests / f"nyc311-{snapshot}.manifest.json").read_text())
    window = Window(
        datetime.date.fromisoformat(manifest["window"]["created_date_from"]),
        datetime.date.fromisoformat(manifest["window"]["created_date_to"]),
    )
    since = manifest["max_updated_at"] or manifest["pulled_at"]
    rows = fetch(
        SOURCE,
        {"$select": "count(*) AS n", "$where": f"{window.where()} AND :updated_at > '{since}'"},
    )
    out = {
        "snapshot_id": snapshot,
        "checked_at": now().isoformat(),
        "since": since,
        "changed_rows": int(rows[0]["n"]) if rows else 0,
    }
    work = _work(snapshot, cache)
    (work / "drift.json").write_text(json.dumps(out, indent=2) + "\n")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("pull", help="pull a created_date window into a snapshot")
    p.add_argument("--snapshot", required=True, help="e.g. S3-2025Q1")
    p.add_argument("--start", required=True, type=datetime.date.fromisoformat)
    p.add_argument("--end", required=True, type=datetime.date.fromisoformat, help="exclusive")
    p.add_argument("--page-size", type=int, default=PAGE_SIZE)
    p.add_argument("--cache", type=pathlib.Path, default=CACHE)
    d = sub.add_parser("drift", help="count the rows the source changed since the snapshot")
    d.add_argument("--snapshot", required=True)
    d.add_argument("--cache", type=pathlib.Path, default=CACHE)
    args = parser.parse_args(argv)
    fetch = http_fetch(os.getenv("SOCRATA_APP_TOKEN") or None)
    try:
        if args.command == "pull":
            if args.end <= args.start:
                parser.error("--end must be after --start")
            out = pull(
                args.snapshot,
                Window(args.start, args.end),
                fetch,
                cache=args.cache,
                page_size=args.page_size,
            )
        else:
            out = drift(args.snapshot, fetch, cache=args.cache)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({k: v for k, v in out.items() if k != "files"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
