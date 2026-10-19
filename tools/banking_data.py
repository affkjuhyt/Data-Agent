"""Download and verify reproducible public banking-data snapshots.

The crawler keeps archives outside the repository, resumes partial downloads,
and writes a manifest with observed sizes and SHA-256 checksums.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path

DEFAULT_ROOT = Path(os.environ.get("BANKING_DATA_ROOT", "/private/tmp/data-agent-banking"))
USER_AGENT = "Data-Agent banking-data crawler/1.0 (research; contact@example.com)"


@dataclass(frozen=True)
class Artifact:
    source: str
    url: str
    relative_path: str
    expected_size: int | None = None
    sha256: str | None = None


def _request(url: str, headers: dict[str, str] | None = None) -> urllib.request.Request:
    request_headers = {"User-Agent": USER_AGENT}
    if headers:
        request_headers.update(headers)
    return urllib.request.Request(url, headers=request_headers)


def _date_range(start: date, end: date) -> Iterable[date]:
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def artifacts(profile: str) -> list[Artifact]:
    if profile == "test":
        gdelt_start, gdelt_end = date(2026, 7, 9), date(2026, 10, 16)
        sec_periods = ("2025q2",)
    elif profile == "lite":
        gdelt_start, gdelt_end = date(2026, 10, 1), date(2026, 10, 16)
        sec_periods = ("2025q3",)
    elif profile == "standard":
        gdelt_start, gdelt_end = date(2026, 1, 1), date(2026, 10, 16)
        sec_periods = ("2025q1", "2025q2", "2025q3")
    else:
        raise ValueError(f"unknown profile: {profile}")

    result = [
        Artifact(
            source="cfpb",
            url="https://files.consumerfinance.gov/ccdb/complaints.csv.zip",
            relative_path="cfpb/complaints.csv.zip",
        )
    ]
    result.extend(
        Artifact(
            source="gdelt_gkg",
            url=f"https://data.gdeltproject.org/gkg/{day:%Y%m%d}.gkg.csv.zip",
            relative_path=f"gdelt-gkg/{day:%Y%m%d}.gkg.csv.zip",
        )
        for day in _date_range(gdelt_start, gdelt_end)
    )
    result.extend(
        Artifact(
            source="sec_notes",
            url=(
                "https://www.sec.gov/files/dera/data/"
                f"financial-statement-notes-data-sets/{period}_notes.zip"
            ),
            relative_path=f"sec-notes/{period}_notes.zip",
        )
        for period in sec_periods
    )
    return result


def _head(artifact: Artifact) -> int | None:
    try:
        request = urllib.request.Request(
            artifact.url,
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
            method="HEAD",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            value = response.headers.get("Content-Length")
            return int(value) if value else None
    except (OSError, urllib.error.URLError, ValueError) as exc:
        print(f"WARN HEAD failed {artifact.url}: {exc}", file=sys.stderr)
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(artifact: Artifact, root: Path) -> Artifact:
    destination = root / artifact.relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        return Artifact(
            source=artifact.source,
            url=artifact.url,
            relative_path=artifact.relative_path,
            expected_size=destination.stat().st_size,
            sha256=_sha256(destination),
        )
    partial = destination.with_suffix(destination.suffix + ".part")
    current_size = partial.stat().st_size if partial.exists() else 0
    headers = {"Accept": "*/*"}
    if current_size:
        headers["Range"] = f"bytes={current_size}-"

    try:
        response = urllib.request.urlopen(_request(artifact.url, headers), timeout=60)
    except urllib.error.HTTPError as exc:
        if exc.code != 416 or not partial.exists():
            raise
        response = None

    if response is None:
        partial.replace(destination)
    else:
        supports_resume = response.status == 206 and current_size > 0
        if current_size and not supports_resume:
            current_size = 0
            response.close()
            response = urllib.request.urlopen(_request(artifact.url), timeout=60)
        mode = "ab" if supports_resume else "wb"
        with response, partial.open(mode) as stream:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                stream.write(block)
        partial.replace(destination)

    return Artifact(
        source=artifact.source,
        url=artifact.url,
        relative_path=artifact.relative_path,
        expected_size=destination.stat().st_size,
        sha256=_sha256(destination),
    )


def _verify_zip(path: Path) -> str | None:
    if path.suffix != ".zip":
        return None
    with zipfile.ZipFile(path) as archive:
        return archive.testzip()


def _write_manifest(root: Path, profile: str, items: list[Artifact]) -> None:
    manifest = {
        "profile": profile,
        "generated_at": date.today().isoformat(),
        "artifacts": [asdict(item) for item in items],
    }
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=root, delete=False) as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    temporary.replace(root / "MANIFEST.json")


def plan(profile: str, root: Path) -> int:
    items = artifacts(profile)
    total = 0
    for item in items:
        size = _head(item)
        total += size or 0
        print(f"{item.source:12} {size or '?':>12} {item.relative_path} {item.url}")
    print(f"ARTIFACTS {len(items)}")
    print(f"KNOWN_BYTES {total}")
    print(f"ROOT {root}")
    return 0


def download(profile: str, root: Path) -> int:
    completed: list[Artifact] = []
    for index, item in enumerate(artifacts(profile), start=1):
        print(f"[{index}] downloading {item.url}", flush=True)
        completed.append(_download(item, root))
    _write_manifest(root, profile, completed)
    print(f"Wrote {root / 'MANIFEST.json'}")
    return 0


def verify(root: Path) -> int:
    manifest_path = root / "MANIFEST.json"
    if not manifest_path.exists():
        print(f"missing {manifest_path}", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures = 0
    for item in manifest["artifacts"]:
        path = root / item["relative_path"]
        if not path.exists():
            print(f"MISSING {path}")
            failures += 1
            continue
        actual_size = path.stat().st_size
        actual_hash = _sha256(path)
        zip_error = _verify_zip(path)
        if actual_size != item["expected_size"] or actual_hash != item["sha256"] or zip_error:
            print(f"FAIL {path} zip_error={zip_error}")
            failures += 1
        else:
            print(f"OK   {path}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "download", "verify"))
    parser.add_argument("--profile", default="test", choices=("test", "lite", "standard"))
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    if args.command == "plan":
        return plan(args.profile, args.root)
    if args.command == "download":
        return download(args.profile, args.root)
    return verify(args.root)


if __name__ == "__main__":
    raise SystemExit(main())
