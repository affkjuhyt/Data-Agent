"""Classify banking archives and create upload-ready CSV samples."""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import pathlib
import zipfile
from collections.abc import Iterable, Iterator
from typing import Any

DEFAULT_ROOT = pathlib.Path("/private/tmp/data-agent-banking")
DEFAULT_OUTPUT = pathlib.Path("/private/tmp/data-agent-banking-upload")


def _classify(relative_path: str) -> dict[str, Any]:
    if relative_path.startswith("cfpb/"):
        return {"category": "customer_complaints", "role": "consumer-protection and service-quality signals", "upload_mode": "structured_csv"}
    if relative_path.startswith("gdelt-gkg/"):
        return {"category": "market_news_and_events", "role": "external market, geopolitical and macro context", "upload_mode": "structured_csv"}
    if relative_path.startswith("sec-notes/"):
        return {"category": "regulatory_financial_filings", "role": "issuer filings, XBRL facts and accounting context", "upload_mode": "structured_csv"}
    return {"category": "unclassified", "role": "needs review", "upload_mode": "manual_review"}


def _inventory(root: pathlib.Path) -> dict[str, Any]:
    artifacts = []
    for path in sorted(root.rglob("*.zip")):
        relative = path.relative_to(root).as_posix()
        with zipfile.ZipFile(path) as archive:
            members = [{"name": info.filename, "uncompressed_size": info.file_size} for info in archive.infolist()]
        artifacts.append({"relative_path": relative, "compressed_size": path.stat().st_size, **_classify(relative), "members": members})
    return {"root": str(root), "artifacts": artifacts}


def _rows_from_zip(archive_path: pathlib.Path, member_name: str, delimiter: str, max_rows: int) -> tuple[list[str], Iterator[list[str]]]:
    archive = zipfile.ZipFile(archive_path)
    member = archive.open(member_name)
    text = io.TextIOWrapper(member, encoding="utf-8-sig", errors="replace", newline="")
    reader = csv.reader(text, delimiter=delimiter)
    header = next(reader)

    def rows() -> Iterator[list[str]]:
        try:
            for index, row in enumerate(reader):
                if index >= max_rows:
                    break
                yield row
        finally:
            text.close()
            archive.close()

    return header, rows()


def _write_csv(path: pathlib.Path, header: Iterable[str], rows: Iterable[list[str]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    count = 0
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)
            count += 1
    os.replace(temporary, path)
    return count


def _prepare(root: pathlib.Path, output: pathlib.Path, args: argparse.Namespace) -> dict[str, Any]:
    gdelt_paths = sorted((root / "gdelt-gkg").glob("*.zip"))
    sec_archive = root / "sec-notes" / "2025q2_notes.zip"
    cfpb_archive = root / "cfpb" / "complaints.csv.zip"
    if not gdelt_paths or not cfpb_archive.exists() or not sec_archive.exists():
        raise SystemExit("Expected CFPB, GDELT and SEC archives are missing from --root")

    files = []
    header, rows = _rows_from_zip(cfpb_archive, "complaints.csv", ",", args.max_cfpb_rows)
    path = output / "customer_complaints" / "cfpb_complaints_sample.csv"
    files.append({"path": str(path), "rows": _write_csv(path, header, rows), "category": "customer_complaints"})

    latest_gdelt = gdelt_paths[-1]
    member_name = latest_gdelt.name.removesuffix(".zip")
    header, rows = _rows_from_zip(latest_gdelt, member_name, "\t", args.max_gdelt_rows)
    date = latest_gdelt.name.split(".", 1)[0]
    path = output / "market_news_and_events" / f"{date}_gkg_sample.csv"
    files.append({"path": str(path), "rows": _write_csv(path, header, rows), "category": "market_news_and_events"})

    for member_name, output_name in (("sub.tsv", "sec_submissions_sample.csv"), ("num.tsv", "sec_numeric_facts_sample.csv")):
        header, rows = _rows_from_zip(sec_archive, member_name, "\t", args.max_sec_rows)
        path = output / "regulatory_financial_filings" / output_name
        files.append({"path": str(path), "rows": _write_csv(path, header, rows), "category": "regulatory_financial_filings"})

    output.mkdir(parents=True, exist_ok=True)
    catalog = {"source_root": str(root), "upload_root": str(output), "files": files}
    (output / "DATASET_CATALOG.json").write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    return catalog


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inventory", "prepare"))
    parser.add_argument("--root", type=pathlib.Path, default=pathlib.Path(os.environ.get("BANKING_DATA_ROOT", DEFAULT_ROOT)))
    parser.add_argument("--output", type=pathlib.Path, default=pathlib.Path(os.environ.get("BANKING_UPLOAD_ROOT", DEFAULT_OUTPUT)))
    parser.add_argument("--max-cfpb-rows", type=int, default=50_000)
    parser.add_argument("--max-gdelt-rows", type=int, default=10_000)
    parser.add_argument("--max-sec-rows", type=int, default=50_000)
    args = parser.parse_args()
    result = _inventory(args.root) if args.command == "inventory" else _prepare(args.root, args.output, args)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
