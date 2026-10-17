"""Bulk-ingest the downloaded banking archives through the V3 ingest API.

Raw ZIP files stay immutable on disk. Each CSV/TSV member is converted to bounded
CSV parts, uploaded one at a time, and committed through the same ingest path as
the UI. A manifest makes the operation resumable after a network or worker failure.
"""

from __future__ import annotations

import argparse
import csv
import http.client
import io
import json
import os
import pathlib
import re
import time
import uuid
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit


DEFAULT_ROOT = pathlib.Path("/private/tmp/data-agent-banking")
DEFAULT_WORK = pathlib.Path("/private/tmp/data-agent-banking-bulk-work")
DEFAULT_MANIFEST = pathlib.Path("/private/tmp/data-agent-banking-bulk-ingest.json")
DEFAULT_CHUNK_BYTES = 128 * 1024 * 1024
DEFAULT_ANALYSIS_TIMEOUT = 300


@dataclass(frozen=True)
class Member:
    archive: pathlib.Path
    member_name: str
    category: str
    delimiter: str


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def _members(root: pathlib.Path, max_gdelt_days: int | None) -> Iterator[Member]:
    cfpb = root / "cfpb" / "complaints.csv.zip"
    if cfpb.exists():
        yield Member(cfpb, "complaints.csv", "customer_complaints", ",")

    gdelt = sorted((root / "gdelt-gkg").glob("*.zip"))
    if max_gdelt_days is not None:
        gdelt = gdelt[:max_gdelt_days]
    for archive in gdelt:
        with zipfile.ZipFile(archive) as source:
            for name in sorted(info.filename for info in source.infolist() if not info.is_dir()):
                yield Member(archive, name, "market_news_and_events", "\t")

    for archive in sorted((root / "sec-notes").glob("*.zip")):
        with zipfile.ZipFile(archive) as source:
            for name in sorted(info.filename for info in source.infolist() if name_is_table(info.filename)):
                yield Member(archive, name, "regulatory_financial_filings", "\t")


def name_is_table(name: str) -> bool:
    return name.lower().endswith((".tsv", ".csv"))


def _chunks(member: Member, work: pathlib.Path, max_bytes: int) -> Iterator[tuple[pathlib.Path, int, int]]:
    with zipfile.ZipFile(member.archive) as archive:
        with archive.open(member.member_name) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")
            reader = csv.reader(text, delimiter=member.delimiter)
            header = next(reader)
            chunk_index = 0
            output: pathlib.Path | None = None
            handle: io.TextIOWrapper | None = None
            writer: Any = None
            rows = 0
            size = 0

            def open_chunk(index: int) -> tuple[pathlib.Path, io.TextIOWrapper, int, Any]:
                name = f"{_safe_name(member.category)}__{_safe_name(member.archive.name)}__{_safe_name(member.member_name)}__part-{index:05d}.csv"
                path = work / name
                path.parent.mkdir(parents=True, exist_ok=True)
                stream = path.open("w", encoding="utf-8", newline="")
                csv_writer = csv.writer(stream)
                csv_writer.writerow(header)
                stream.flush()
                return path, stream, stream.tell(), csv_writer

            try:
                for row in reader:
                    if handle is None:
                        output, handle, size, writer = open_chunk(chunk_index)
                    assert writer is not None
                    writer.writerow(row)
                    rows += 1
                    if rows > 1 and rows % 256 == 0 and handle.tell() > max_bytes:
                        handle.close()
                        assert output is not None
                        yield output, rows, output.stat().st_size
                        chunk_index += 1
                        output, handle, size, writer = open_chunk(chunk_index)
                        rows = 0
                if handle is not None:
                    handle.close()
                    assert output is not None
                    if rows:
                        yield output, rows, output.stat().st_size
                    else:
                        output.unlink(missing_ok=True)
            finally:
                text.detach()


class Api:
    def __init__(self, base_url: str, token: str, data_space: str) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("--api-base must be an absolute http(s) URL")
        self.base_path = parsed.path.rstrip("/")
        self.connection = (
            http.client.HTTPSConnection(parsed.netloc, timeout=600)
            if parsed.scheme == "https"
            else http.client.HTTPConnection(parsed.netloc, timeout=600)
        )
        self.headers = {"Authorization": f"Bearer {token}", "X-Data-Space": data_space}

    def json(self, method: str, path: str, body: bytes | None = None) -> tuple[int, dict[str, object]]:
        headers = {**self.headers, "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body))
        self.connection.request(method, self.base_path + path, body=body, headers=headers)
        response = self.connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        return response.status, payload

    def upload(self, path: pathlib.Path) -> dict[str, object]:
        boundary = f"----dataagent-{uuid.uuid4().hex}"
        prefix = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="'
            f'{path.name}"\r\n'
            "Content-Type: text/csv\r\n\r\n"
        ).encode()
        suffix = f"\r\n--{boundary}--\r\n".encode()
        size = path.stat().st_size
        headers = {
            **self.headers,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(prefix) + size + len(suffix)),
        }
        self.connection.putrequest("POST", self.base_path + "/v1/uploads")
        for key, value in headers.items():
            self.connection.putheader(key, value)
        self.connection.endheaders()
        self.connection.send(prefix)
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                self.connection.send(block)
        self.connection.send(suffix)
        response = self.connection.getresponse()
        payload = json.loads(response.read().decode("utf-8"))
        if response.status not in {200, 201, 202}:
            raise RuntimeError(f"upload failed HTTP {response.status}: {payload}")
        return payload

    def wait_analyzed(self, upload_id: str, timeout: int) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        while True:
            status, payload = self.json("GET", f"/v1/uploads/{upload_id}")
            if status != 200:
                raise RuntimeError(f"upload status failed HTTP {status}: {payload}")
            state = payload.get("status")
            if state == "analyzed":
                return payload
            if state in {"failed", "rejected"}:
                raise RuntimeError(f"upload analysis failed: {payload}")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"upload analysis timeout: {payload}")
            time.sleep(2)

    def commit(self, upload_id: str) -> dict[str, object]:
        # Kong's router drops the first character after a raw colon; encoded colon is required.
        status, payload = self.json("POST", f"/v1/uploads/{upload_id}%3Acommit", b"{}")
        if status not in {200, 201, 202}:
            raise RuntimeError(f"commit failed HTTP {status}: {payload}")
        return payload


def _load_manifest(path: pathlib.Path) -> dict[str, object]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"parts": {}}


def _save_manifest(path: pathlib.Path, manifest: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run(args: argparse.Namespace) -> int:
    token = args.token or os.environ.get("DATA_AGENT_TOKEN")
    if not token and not args.dry_run:
        raise SystemExit("Set DATA_AGENT_TOKEN or pass --token")
    root = args.root.resolve()
    work = args.work.resolve()
    manifest_path = args.manifest.resolve()
    manifest = _load_manifest(manifest_path)
    completed = manifest.setdefault("parts", {})
    assert isinstance(completed, dict)
    api = Api(args.api_base, token or "dry-run", args.data_space)
    seen = 0
    for member in _members(root, args.max_gdelt_days):
        for part, rows, bytes_written in _chunks(member, work, args.chunk_bytes):
            key = f"{member.archive.relative_to(root)}::{member.member_name}::{part.name}"
            seen += 1
            if key in completed and completed[key].get("status") == "committed":
                print(f"SKIP {key}", flush=True)
                part.unlink(missing_ok=True)
                continue
            print(f"INGEST {key} rows={rows} bytes={bytes_written}", flush=True)
            if args.dry_run:
                part.unlink(missing_ok=True)
                if args.max_parts and seen >= args.max_parts:
                    return 0
                continue
            try:
                upload = api.upload(part)
                upload_id = str(upload["id"])
                api.wait_analyzed(upload_id, args.analysis_timeout)
                dataset = api.commit(upload_id)
                completed[key] = {
                    "status": "committed",
                    "rows": rows,
                    "bytes": bytes_written,
                    "upload_id": upload_id,
                    "dataset_id": dataset.get("id"),
                    "display_name": dataset.get("display_name"),
                }
                _save_manifest(manifest_path, manifest)
                print(f"DONE {key} dataset={dataset.get('id')}", flush=True)
            except Exception as exc:
                completed[key] = {"status": "failed", "rows": rows, "bytes": bytes_written, "error": str(exc)}
                _save_manifest(manifest_path, manifest)
                raise
            finally:
                part.unlink(missing_ok=True)
            if args.max_parts and seen >= args.max_parts:
                return 0
    _save_manifest(manifest_path, manifest)
    print(f"COMPLETE parts={seen} manifest={manifest_path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", default=os.environ.get("DATA_AGENT_API", "http://localhost:30100"))
    parser.add_argument("--data-space", default=os.environ.get("DATA_AGENT_DATA_SPACE", "DS-DEMO"))
    parser.add_argument("--token")
    parser.add_argument("--root", type=pathlib.Path, default=DEFAULT_ROOT)
    parser.add_argument("--work", type=pathlib.Path, default=DEFAULT_WORK)
    parser.add_argument("--manifest", type=pathlib.Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--chunk-bytes", type=int, default=DEFAULT_CHUNK_BYTES)
    parser.add_argument("--analysis-timeout", type=int, default=DEFAULT_ANALYSIS_TIMEOUT)
    parser.add_argument("--max-gdelt-days", type=int)
    parser.add_argument("--max-parts", type=int, help="stop after this many parts; useful for a smoke run")
    parser.add_argument("--dry-run", action="store_true")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
