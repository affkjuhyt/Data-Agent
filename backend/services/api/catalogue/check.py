"""Compare the api's generated OpenAPI with A2 (ADR-48).

    python -m services.api.catalogue.check            # report, exit 1 on a violation
    python -m services.api.catalogue.check --json     # the same as JSON

A violation is:
  * an operation that is not in A2 (extra), or one whose tag, x-capability or
    x-use-cases differ from its A2 row: the code drifted from the spec;
  * an A2 endpoint missing from a group listed in COMPLETE_GROUPS.

A2 has 378 endpoints and the api is built group by group, so a missing
endpoint only fails once its group is declared complete here. Add a group to
COMPLETE_GROUPS in the commit that serves its last endpoint; from then on
dropping one is a failure.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from typing import Any

from services.api.catalogue import endpoints

COMPLETE_GROUPS: frozenset[str] = frozenset(
    {
        "Hỏi đáp",
        "Truy vấn",
        "Phiên làm việc",
        "Cần bạn và đánh giá",
        "Agent",
        "Kỹ năng",
        "Biến đổi dữ liệu",
        "Lớp ngữ cảnh",
        "Xung đột",
        "Theo dõi, báo cáo, thông báo",
        "Dự báo",
        "Nguồn và danh mục",
        "Nạp dữ liệu",
        "Tài liệu",
        "Gói ngành",
        "Kỳ chốt",
        "Thu thập",
        "Lớp riêng lãnh đạo",
    }
)

# Served outside A2 on purpose: probes and the generated spec itself.
NOT_API = frozenset({"/healthz", "/readyz", "/v1/openapi.json"})

_METHODS = ("get", "post", "put", "patch", "delete")


@dataclass
class Report:
    implemented: int = 0
    total: int = 0
    extra: list[str] = field(default_factory=list)
    drift: list[str] = field(default_factory=list)
    missing_in_complete_groups: list[str] = field(default_factory=list)
    missing_by_group: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not (self.extra or self.drift or self.missing_in_complete_groups)


def operations(spec: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (method.upper(), path): op
        for path, item in spec.get("paths", {}).items()
        if path not in NOT_API
        for method, op in item.items()
        if method in _METHODS
    }


def compare(spec: dict[str, Any], complete_groups: frozenset[str] = COMPLETE_GROUPS) -> Report:
    a2 = endpoints()
    served = operations(spec)
    report = Report(total=sum(len(rows) for rows in a2.values()))
    for key, op in sorted(served.items()):
        rows = a2.get(key)
        label = f"{key[0]} {key[1]}"
        if not rows:
            report.extra.append(label)
            continue
        report.implemented += len(rows)
        tags = op.get("tags", [])
        if not any(r.group in tags for r in rows):
            report.drift.append(f"{label}: tags {tags} != A2 group {[r.group for r in rows]}")
        if not any(list(r.capability) == op.get("x-capability") for r in rows):
            report.drift.append(
                f"{label}: x-capability {op.get('x-capability')} != A2 {[list(r.capability) for r in rows]}"
            )
        if not any([f"UC-{u}" for u in r.use_cases] == op.get("x-use-cases") for r in rows):
            report.drift.append(f"{label}: x-use-cases differ from A2")
        if not op.get("operationId"):
            report.drift.append(f"{label}: no operationId")
    for key, rows in a2.items():
        if key in served:
            continue
        for row in rows:
            report.missing_by_group[row.group] = report.missing_by_group.get(row.group, 0) + 1
            if row.group in complete_groups:
                report.missing_in_complete_groups.append(f"{key[0]} {key[1]} ({row.group})")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from services.api.app import app

    report = compare(app.openapi())
    if args.json:
        print(json.dumps({**asdict(report), "ok": report.ok}, ensure_ascii=False, indent=1))
    else:
        print(f"A2 coverage: {report.implemented}/{report.total} endpoints")
        for title, items in (
            ("Not in A2", report.extra),
            ("Drift from A2", report.drift),
            ("Missing from a complete group", report.missing_in_complete_groups),
        ):
            for item in items:
                print(f"  {title}: {item}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
