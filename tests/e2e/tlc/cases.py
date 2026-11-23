"""The golden TLC cases (EV-05): JSONL in model/eval/cases/golden/tlc/ is their source.

    python tests/e2e/tlc/cases.py export          # [{"id", "sql"}] of the cases with gold SQL
    python tests/e2e/tlc/cases.py apply FILE      # FILE: what reconcile --sql answered
    python tests/e2e/tlc/cases.py yaml            # deploy/docker/eval/ds-tlc-cases.yaml

`apply` writes each case's result into its expected (the rows, and the values an answer gives:
the one value of a one-cell result, else the rows) with the result's sha256, and moves the case
to sql_run_on_snapshot (DATASET_AND_EVALUATION §5.5 step 2). A case whose SQL is "n/a" (a
clarification, a refusal with no evidence query) stays as written. `yaml` writes the file
`make eval-cases-import` loads, one case per JSONL case, the gold SQL as its reference_sql.

`make tlc-cases` runs the three against the cluster's tlc_trips. Needs PyYAML (the dev-tools image).
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys
from typing import Any

import yaml

REPO = pathlib.Path(__file__).resolve().parents[3]
CASES = REPO / "model" / "eval" / "cases" / "golden" / "tlc"
GROUPS = ("structured", "multiturn", "analytical", "hybrid")
OUT = REPO / "deploy" / "docker" / "eval" / "ds-tlc-cases.yaml"
SOURCES = REPO / "tests" / "e2e" / "tlc" / "sources.json"
# The fields eval.eval_item takes from a case (api/modules/eval/cases_yaml.CaseDef).
LOADED = (
    "id",
    "question",
    "lang",
    "history",
    "category",
    "expected",
    "as_of",
    "expected_action",
    "failure_category",
    "rubric",
    "verification_status",
)


def load() -> dict[str, list[dict[str, Any]]]:
    return {
        g: [
            json.loads(line)
            for line in (CASES / f"{g}.jsonl").read_text().splitlines()
            if line.strip()
        ]
        for g in GROUPS
    }


def save(groups: dict[str, list[dict[str, Any]]]) -> None:
    for g, cases in groups.items():
        text = "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases)
        (CASES / f"{g}.jsonl").write_text(text)


def snapshot() -> dict[str, Any]:
    """The pinned snapshot the answers are of: its id and the sha256 of its files' hashes."""
    sources = json.loads(SOURCES.read_text())
    digest = hashlib.sha256(
        "".join(f"{n}:{f['sha256']}\n" for n, f in sorted(sources["files"].items())).encode()
    ).hexdigest()
    return {"dataset": "tlc", "snapshot": sources["snapshot"], "sources_sha256": digest}


def export() -> list[dict[str, str]]:
    return [
        {"id": c["id"], "sql": c["gold_sql"]}
        for cases in load().values()
        for c in cases
        if c.get("gold_sql") not in (None, "", "n/a")
    ]


def apply(results: dict[str, dict[str, Any]]) -> int:
    groups = load()
    done = 0
    for cases in groups.values():
        for c in cases:
            found = results.get(c["id"])
            if found is None:
                continue
            rows = found["rows"]
            expected = dict(c["expected"])
            if c["expected_action"] == "answer" or expected.get("kind") == "data":
                # One cell is one value; anything else is the rows, members first.
                expected["values"] = [rows[0][0]] if len(rows) == 1 and len(rows[0]) == 1 else rows
            else:
                expected["evidence_rows"] = rows
            expected["result_sha256"] = found["sha256"]
            c["expected"] = expected
            c["verification_status"] = "sql_run_on_snapshot"
            done += 1
    save(groups)
    return done


def to_yaml() -> str:
    cases = []
    for g, group in load().items():
        for c in group:
            out = {k: c[k] for k in LOADED if k in c}
            if c.get("gold_sql") not in (None, "", "n/a"):
                out["reference_sql"] = c["gold_sql"]
            out["note"] = f"{g}" + (f": {c['note']}" if c.get("note") else "")
            cases.append(out)
    head = (
        "# DS-TLC golden cases (EV-05): generated from model/eval/cases/golden/tlc/*.jsonl by\n"
        "# tests/e2e/tlc/cases.py; edit the JSONL, never this file. `make eval-cases-import` loads it\n"
        "# into the Data Space's eval set; tests/unit/repo/test_golden_cases_schema.py keeps the two the same.\n"
    )
    body = {
        "eval_set": "Câu hỏi NYC Taxi",
        "min_items": 60,
        "snapshot": snapshot(),
        "cases": cases,
    }
    return head + yaml.safe_dump(body, allow_unicode=True, sort_keys=False, width=100)


def main(argv: list[str]) -> int:
    if argv[:1] == ["export"]:
        print(json.dumps(export(), ensure_ascii=False))
    elif argv[:1] == ["apply"] and len(argv) == 2:
        print(
            f"{apply(json.loads(pathlib.Path(argv[1]).read_text()))} cases run on the snapshot",
            file=sys.stderr,
        )
    elif argv[:1] == ["yaml"]:
        OUT.write_text(to_yaml())
    else:
        print(__doc__, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
