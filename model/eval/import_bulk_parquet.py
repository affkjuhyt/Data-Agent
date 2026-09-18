"""One-off import of a HuggingFace parquet dataset into the eval corpus's
JSONL shape (`corpus.load_cases`'s format: one JSON object per line, each
with a unique `id`).

This is a dataset-acquisition tool, not a `KBSource` connector: it never
touches the scheduled crawl worker, Bronze, or any tenant-scoped storage —
see the crawl-feature plan's "Explicitly deferred" (`bulk-parquet`). It reads
columns straight through; a dataset whose columns don't already match what a
scorer expects (see `cases/*.jsonl` for the shape each tier reads) needs
`--id-field`/`--rename` to line up, not a code change here.

Requires `pyarrow` (not in any requirements.txt — this script is never run
inside a built image, only by hand: `pip install pyarrow` first).

    python -m model.eval.import_bulk_parquet --dataset ./corpus.parquet \\
        --out model/eval/cases/policy.jsonl --id-field question_id
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_rows(dataset_path: Path) -> list[dict]:
    import pyarrow.parquet as pq

    table = pq.read_table(dataset_path)
    return table.to_pylist()


def _to_case(row: dict, id_field: str, rename: dict[str, str], row_index: int) -> dict:
    case = {rename.get(k, k): v for k, v in row.items()}
    case_id = case.pop(id_field, None) if id_field != "id" else case.get("id")
    case["id"] = str(case_id) if case_id is not None else f"row-{row_index}"
    return case


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a parquet dataset into an eval corpus JSONL file.")
    parser.add_argument("--dataset", required=True, help="Path to a local .parquet file")
    parser.add_argument("--out", required=True, help="Output .jsonl path, e.g. model/eval/cases/policy.jsonl")
    parser.add_argument("--id-field", default="id", help="Source column to use as the case id (default: 'id')")
    parser.add_argument(
        "--rename", action="append", default=[], metavar="SRC=DST",
        help="Rename a source column before writing (repeatable)",
    )
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        print(f"no such file: {dataset_path}", file=sys.stderr)
        return 2

    rename = dict(pair.split("=", 1) for pair in args.rename)
    rows = _load_rows(dataset_path)
    if not rows:
        print(f"{dataset_path} has no rows", file=sys.stderr)
        return 2

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as f:
        for i, row in enumerate(rows):
            case = _to_case(row, args.id_field, rename, i)
            f.write(json.dumps(case, ensure_ascii=False) + "\n")

    print(f"wrote {len(rows)} case(s) to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
