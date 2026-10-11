"""Design coverage checklist: what of the V3 system design exists in the repo.

    python3 tests/design_checklist.py           # rewrite docs/v3/10-design-checklist.md
    python3 tests/design_checklist.py --check   # also fail on audit findings or a stale file

Several agents build the design at once (docs/v3/README.md), so "what is done" cannot live in
anyone's head. The design's items come from docs/v3/checklist/design-snapshot.json (use cases with
their acceptance criteria, the traceability matrix, business rules, screens, ADRs, taken from the
design doc) plus the A2 snapshot and the generated DDL. Against them this reads two kinds of truth:

  * evidence the code gives by itself: an A2 endpoint served by backend/services/api (a route built
    with `endpoint("METHOD", "/v1/...")`), a table created in backend/alembic/sql/dataagent*.sql, a
    test that names an acceptance criterion (`pytest.mark.req("AC-01-01-03")` or the id in a test
    name or comment), code that names a business rule (BR-142);
  * what people and agents declare in docs/v3/checklist/status.tsv (id, status, owner, note), for
    what code cannot show: a screen matching Figma, an ADR followed, a criterion checked by hand.

The audit flags a declaration the evidence contradicts (a use case marked done with an endpoint,
a table or a required criterion missing), criteria marked done without a test or a note, unknown
ids, and places where the design contradicts itself (a matrix API absent from A2, a matrix table
absent from the DDL).

Stdlib only, Python 3.10+: it reads files and runs no service code, like tests/run_suites.py.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / "docs/v3/checklist/design-snapshot.json"
STATUS = ROOT / "docs/v3/checklist/status.tsv"
OUTPUT = ROOT / "docs/v3/10-design-checklist.md"
A2 = ROOT / "backend/services/api/catalogue/a2_endpoints.json"
# The audit schema lives in its own database (ADR-01), hence its own DDL file.
DDL_FILES = (
    ROOT / "backend/alembic/sql/dataagent.sql",
    ROOT / "backend/alembic/sql/dataagent_audit.sql",
)
API_CODE = ROOT / "backend/services/api"

STATUSES = ("todo", "doing", "partial", "done", "blocked", "na")
# T0 of tab 8: a use case passes when every Chính, Quy tắc and Ngoại lệ criterion passes and every
# Phi chức năng one meets its threshold; Thay thế is logged, it does not block.
REQUIRED_KINDS = ("Chính", "Quy tắc", "Ngoại lệ", "Phi chức năng")

# Where evidence may come from. Docs only describe; they never count as evidence.
CODE_DIRS = ("backend", "frontend/src", "model", "deploy")
TEST_DIRS = ("tests", "frontend/src", "model/eval")
SKIP_PARTS = {"node_modules", "__pycache__", ".git", "dist", ".venv", "generated"}
TEXT_SUFFIXES = {".py", ".sql", ".ts", ".tsx", ".js", ".yaml", ".yml", ".json", ".md", ".toml"}

_ENDPOINT_CALL = re.compile(r'endpoint\(\s*"(GET|POST|PUT|PATCH|DELETE)"\s*,\s*"([^"]+)"')
_CREATE_TABLE = re.compile(r"^CREATE TABLE (\w+)\.(\w+)\s*\(", re.M)
_AC = re.compile(r"\bAC-\d\d-\d\d-\d\d\b")
_BR = re.compile(r"\bBR-(\d{1,3})\b")
_SCR = re.compile(r"\bSCR-\d\d\b")
_API_REF = re.compile(r"(GET|POST|PUT|PATCH|DELETE)\s+(/\S+)")


@dataclass
class Status:
    status: str = "todo"
    owner: str = ""
    note: str = ""


@dataclass
class Evidence:
    served: set[tuple[str, str]] = field(default_factory=set)
    tables: set[str] = field(default_factory=set)  # bare table names (11.1 omits the schema)
    qualified_tables: set[str] = field(default_factory=set)
    ac_tests: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    br_code: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    scr_code: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))


def _files(dirs: tuple[str, ...]) -> list[Path]:
    out = []
    for d in dirs:
        base = ROOT / d
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if p.is_file() and p.suffix in TEXT_SUFFIXES and not SKIP_PARTS.intersection(p.parts):
                out.append(p)
    return out


def _is_test(path: Path) -> bool:
    rel = path.relative_to(ROOT).as_posix()
    return rel.startswith(("tests/", "model/eval/")) or ".test." in path.name


def gather_evidence() -> Evidence:
    ev = Evidence()
    for p in API_CODE.rglob("*.py"):
        for method, path in _ENDPOINT_CALL.findall(p.read_text(encoding="utf-8")):
            ev.served.add((method, path))
    for ddl in (f for f in DDL_FILES if f.exists()):
        for schema, table in _CREATE_TABLE.findall(ddl.read_text(encoding="utf-8")):
            ev.tables.add(table)
            ev.qualified_tables.add(f"{schema}.{table}")
    for p in set(_files(CODE_DIRS)) | set(_files(TEST_DIRS)):
        if p == SNAPSHOT or p.is_relative_to(ROOT / "docs"):
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        rel = p.relative_to(ROOT).as_posix()
        if _is_test(p):
            for ac in _AC.findall(text):
                ev.ac_tests[ac].add(rel)
        for n in _BR.findall(text):
            ev.br_code[f"BR-{int(n):02d}" if int(n) < 100 else f"BR-{int(n)}"].add(rel)
        for scr in _SCR.findall(text):
            ev.scr_code[scr].add(rel)
    return ev


def load_status() -> tuple[dict[str, Status], list[str]]:
    statuses: dict[str, Status] = {}
    problems: list[str] = []
    if not STATUS.exists():
        return statuses, problems
    for n, line in enumerate(STATUS.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.startswith("#") or line.startswith("id\t"):
            continue
        parts = line.split("\t")
        item, status = parts[0].strip(), (parts[1].strip() if len(parts) > 1 else "")
        if status not in STATUSES:
            problems.append(
                f"status.tsv:{n}: {item}: trạng thái '{status}' không thuộc {', '.join(STATUSES)}"
            )
            continue
        if item in statuses:
            problems.append(f"status.tsv:{n}: {item} khai hai lần")
        statuses[item] = Status(
            status,
            parts[2].strip() if len(parts) > 2 else "",
            parts[3].strip() if len(parts) > 3 else "",
        )
    return statuses, problems


def _norm_api(ref: str) -> tuple[str, str] | None:
    m = _API_REF.match(ref.strip())
    return (m.group(1), m.group(2).rstrip(".,;")) if m else None


@dataclass
class UcRow:
    uc: dict
    status: Status
    apis: list[tuple[str, str]]
    apis_served: int
    tables: list[str]
    tables_present: int
    required: list[dict]
    required_covered: int
    findings: list[str]


def _ac_covered(ac_id: str, ev: Evidence, st: dict[str, Status]) -> bool:
    s = st.get(ac_id)
    return bool(ev.ac_tests.get(ac_id)) or (s is not None and s.status in ("done", "na"))


def audit(
    snap: dict, a2: dict, ev: Evidence, st: dict[str, Status]
) -> tuple[list[UcRow], list[str], list[str]]:
    a2_keys = {(e["method"], e["path"]) for e in a2["endpoints"]}
    known = {u["id"] for u in snap["use_cases"]}
    known |= {
        c["id"] for u in snap["use_cases"] for c in u["criteria"] if c["id"].startswith("AC-")
    }
    known |= {b["id"] for b in snap["business_rules"]} | {s["id"] for s in snap["screens"]}
    known |= {a["id"] for a in snap["adrs"]}
    findings: list[str] = []
    design_gaps: list[str] = []
    rows = []
    for uc in snap["use_cases"]:
        s = st.get(uc["id"], Status())
        apis = [a for a in (_norm_api(x) for x in uc["apis"]) if a]
        for a in apis:
            if a not in a2_keys:
                design_gaps.append(
                    f"{uc['id']}: ma trận 11.1 ghi `{a[0]} {a[1]}` nhưng A2 không có"
                )
        tables = [t.split(".")[-1] for t in uc["tables"]]
        for t in tables:
            if t not in ev.tables:
                design_gaps.append(f"{uc['id']}: ma trận 11.1 ghi bảng `{t}` nhưng DDL không có")
        required = [c for c in uc["criteria"] if c["type"] in REQUIRED_KINDS]
        covered = sum(1 for c in required if _ac_covered(c["id"], ev, st))
        served = sum(1 for a in apis if a in ev.served)
        present = sum(1 for t in tables if t in ev.tables)
        uc_findings = []
        if s.status == "done":
            if served < len(apis):
                uc_findings.append(
                    f"{uc['id']} khai done nhưng mới phục vụ {served}/{len(apis)} endpoint của ma trận"
                )
            if present < len(tables):
                uc_findings.append(
                    f"{uc['id']} khai done nhưng DDL thiếu bảng ({present}/{len(tables)})"
                )
            if covered < len(required):
                uc_findings.append(
                    f"{uc['id']} khai done nhưng {len(required) - covered}/{len(required)} tiêu chí bắt buộc chưa có test hay khai done"
                )
        findings += uc_findings
        rows.append(UcRow(uc, s, apis, served, tables, present, required, covered, uc_findings))
    for item, s in st.items():
        if item not in known:
            findings.append(f"status.tsv khai `{item}` nhưng bản chụp thiết kế không có mã này")
        if _AC.fullmatch(item) and s.status == "done" and not ev.ac_tests.get(item) and not s.note:
            findings.append(
                f"{item} khai done nhưng không test nào nhắc tới và không có ghi chú cách kiểm"
            )
    for b in snap["business_rules"]:
        s = st.get(b["id"])
        if s and s.status == "done" and not ev.br_code.get(b["id"]):
            findings.append(f"{b['id']} khai done nhưng không file mã nào nhắc tới")
    return rows, findings, sorted(set(design_gaps))


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _frac(n: int, d: int) -> str:
    return "—" if d == 0 else f"{n}/{d}"


def _evidence(files: set[str], limit: int = 3) -> str:
    if not files:
        return ""
    shown = sorted(files)[:limit]
    more = f" +{len(files) - limit}" if len(files) > limit else ""
    return ", ".join(f"`{f}`" for f in shown) + more


def render(
    snap: dict,
    a2: dict,
    ev: Evidence,
    st: dict[str, Status],
    rows: list[UcRow],
    findings: list[str],
    design_gaps: list[str],
    status_problems: list[str],
) -> str:
    out: list[str] = []
    w = out.append
    ucs = snap["use_cases"]
    all_ac = [c for u in ucs for c in u["criteria"] if c["id"].startswith("AC-")]
    req_ac = [c for c in all_ac if c["type"] in REQUIRED_KINDS]
    a2_rows = a2["endpoints"]
    a2_keys = {(e["method"], e["path"]) for e in a2_rows}
    served_a2 = ev.served & a2_keys

    def count_status(ids: list[str]) -> str:
        c = defaultdict(int)
        for i in ids:
            c[st.get(i, Status()).status] += 1
        return ", ".join(f"{k} {c[k]}" for k in STATUSES if c[k])

    w("# 08. Checklist độ phủ thiết kế")
    w("")
    w(
        "> Tệp sinh bởi `make design-checklist` (`tests/design_checklist.py`); đừng sửa tay. Nguồn: bản chụp tài liệu"
    )
    w("> thiết kế hệ thống ở `docs/v3/checklist/design-snapshot.json` (tab 2, 4, 6, 8), A2 ở")
    w(
        "> `backend/services/api/catalogue/a2_endpoints.json`, DDL ở `backend/alembic/sql/dataagent*.sql`, và trạng thái"
    )
    w(
        "> người và agent khai ở `docs/v3/checklist/status.tsv`. Cách dùng ở [checklist/README.md](checklist/README.md)."
    )
    w("")
    w("## Tổng quan")
    w("")
    w("| Hạng mục | Tổng | Bằng chứng tự động | Trạng thái đã khai |")
    w("| --- | --- | --- | --- |")
    w(
        f"| Use case (tab 8) | {len(ucs)} | {sum(1 for r in rows if r.required and r.required_covered == len(r.required))} UC đủ tiêu chí bắt buộc | {count_status([u['id'] for u in ucs])} |"
    )
    w(
        f"| Tiêu chí nghiệm thu | {len(all_ac)} ({len(req_ac)} bắt buộc) | {sum(1 for c in all_ac if ev.ac_tests.get(c['id']))} có test nhắc mã AC | {count_status([c['id'] for c in all_ac])} |"
    )
    w(
        f"| Endpoint A2 (tab 4) | {len(a2_keys)} (A2 ghi {len(a2_rows)} dòng; POST /v1/data-products ở hai nhóm) | {len(served_a2)} được `api` phục vụ | — |"
    )
    w(f"| Bảng (tab 3/3b) | {len(ev.qualified_tables)} trong DDL | DDL sinh từ thiết kế | — |")
    w(
        f"| Quy tắc nghiệp vụ | {len(snap['business_rules'])} | {sum(1 for b in snap['business_rules'] if ev.br_code.get(b['id']))} có mã nhắc tới | {count_status([b['id'] for b in snap['business_rules']])} |"
    )
    w(
        f"| Màn hình (SCR) | {len(snap['screens'])} | {sum(1 for s in snap['screens'] if ev.scr_code.get(s['id']))} có mã nhắc tới | {count_status([s['id'] for s in snap['screens']])} |"
    )
    w(
        f"| ADR (tab 6) | {len(snap['adrs'])} | — | {count_status([a['id'] for a in snap['adrs']])} |"
    )
    w("")
    w("## Audit")
    w("")
    if findings or status_problems:
        w(
            f"{len(findings) + len(status_problems)} điểm khai trái với bằng chứng (`--check` thất bại cho tới khi sửa):"
        )
        w("")
        for f in status_problems + findings:
            w(f"- {_cell(f)}")
    else:
        w("Không có điểm khai nào trái với bằng chứng.")
    w("")
    w(
        f"Tài liệu thiết kế tự lệch ({len(design_gaps)} chỗ): ma trận 11.1 nhắc endpoint hoặc bảng mà A2 hay DDL không có. Sửa ở"
    )
    w("tài liệu (hoặc A2, DDL) rồi chụp lại; không phải lỗi của mã.")
    w("")
    if design_gaps:
        w("<details><summary>Danh sách</summary>")
        w("")
        for g in design_gaps:
            w(f"- {_cell(g)}")
        w("")
        w("</details>")
    w("")
    w("## Use case theo phân hệ")
    w("")
    w(
        "Endpoint và bảng lấy từ ma trận 11.1; tiêu chí bắt buộc là Chính, Quy tắc, Ngoại lệ, Phi chức năng (tab 8 T0),"
    )
    w("tính là có khi một test nhắc mã AC hoặc AC được khai `done`/`na`.")
    by_module: dict[str, list[UcRow]] = defaultdict(list)
    for r in rows:
        by_module[r.uc["module"] or r.uc["id"][:5]].append(r)
    for module in sorted(by_module, key=lambda m: m[:3]):
        w("")
        w(f"### {module}")
        w("")
        w("| UC | Tên | Trạng thái | Endpoint | Bảng | Tiêu chí bắt buộc | Người làm, ghi chú |")
        w("| --- | --- | --- | --- | --- | --- | --- |")
        for r in by_module[module]:
            flag = " ⚠" if r.findings else ""
            note = " · ".join(x for x in (r.status.owner, r.status.note) if x)
            w(
                f"| {r.uc['id']} | {_cell(r.uc['title'])} | {r.status.status}{flag} | {_frac(r.apis_served, len(r.apis))} | {_frac(r.tables_present, len(r.tables))} | {_frac(r.required_covered, len(r.required))} | {_cell(note)} |"
            )
    w("")
    w("## Endpoint A2 theo nhóm")
    w("")
    w("| Nhóm | Đã phục vụ | Còn thiếu |")
    w("| --- | --- | --- |")
    groups: dict[str, list[dict]] = defaultdict(list)
    for e in a2_rows:
        groups[e["group"]].append(e)
    for g, eps in groups.items():
        keys = {(e["method"], e["path"]) for e in eps}
        missing = sorted(keys - ev.served)
        shown = ", ".join(f"`{m} {p}`" for m, p in missing[:4]) + (
            f" +{len(missing) - 4}" if len(missing) > 4 else ""
        )
        w(f"| {g} | {len(keys) - len(missing)}/{len(keys)} | {shown} |")
    w("")
    w("## Quy tắc nghiệp vụ")
    w("")
    w("| BR | UC | Trạng thái | Mã nhắc tới |")
    w("| --- | --- | --- | --- |")
    for b in snap["business_rules"]:
        s = st.get(b["id"], Status())
        w(
            f"| {b['id']} | {', '.join(b['use_cases'])} | {s.status} | {_evidence(ev.br_code.get(b['id'], set()))} |"
        )
    w("")
    w("## Màn hình")
    w("")
    w("| SCR | Màn | Phân hệ | UC | Trạng thái | Ghi chú |")
    w("| --- | --- | --- | --- | --- | --- |")
    for s in snap["screens"]:
        x = st.get(s["id"], Status())
        w(
            f"| {s['id']} | {_cell(s['name'])} | {s['module']} | {_cell(s['use_cases'])} | {x.status} | {_cell(' · '.join(v for v in (x.owner, x.note) if v))} |"
        )
    w("")
    w("## ADR")
    w("")
    w("| ADR | Quyết định | Trạng thái | Ghi chú |")
    w("| --- | --- | --- | --- |")
    for a in snap["adrs"]:
        x = st.get(a["id"], Status())
        decision = a["decision"] if len(a["decision"]) <= 140 else a["decision"][:137] + "…"
        w(
            f"| {a['id']} | {_cell(decision)} | {x.status} | {_cell(' · '.join(v for v in (x.owner, x.note) if v))} |"
        )
    w("")
    w("## Tiêu chí nghiệm thu theo UC")
    for r in rows:
        crit = [c for c in r.uc["criteria"] if c["id"].startswith("AC-")]
        done = sum(1 for c in crit if _ac_covered(c["id"], ev, st))
        w("")
        w(f"<details><summary>{r.uc['id']} {_cell(r.uc['title'])} · {done}/{len(crit)}</summary>")
        w("")
        w("| AC | Loại | Tiêu chí | Cách kiểm | Trạng thái | Test |")
        w("| --- | --- | --- | --- | --- | --- |")
        for c in crit:
            x = st.get(c["id"], Status())
            text = c["text"] if len(c["text"]) <= 220 else c["text"][:217] + "…"
            w(
                f"| {c['id']} | {c['type']} | {_cell(text)} | {_cell(c['check'])} | {x.status} | {_evidence(ev.ac_tests.get(c['id'], set()), 2)} |"
            )
        clar = [c for c in r.uc["criteria"] if not c["id"].startswith("AC-")]
        for c in clar:
            w(f"| — | Cần làm rõ | {_cell(c['text'])} | | | |")
        w("")
        w("</details>")
    w("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--check", action="store_true", help="fail on audit findings or when the file is stale"
    )
    args = parser.parse_args(argv)
    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    a2 = json.loads(A2.read_text(encoding="utf-8"))
    ev = gather_evidence()
    st, status_problems = load_status()
    rows, findings, design_gaps = audit(snap, a2, ev, st)
    text = render(snap, a2, ev, st, rows, findings, design_gaps, status_problems)
    stale = not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != text
    if args.check:
        for f in status_problems + findings:
            print(f"audit: {f}")
        if stale:
            print(f"{OUTPUT.relative_to(ROOT)} is stale: run `make design-checklist` and commit it")
        return 1 if (stale or findings or status_problems) else 0
    OUTPUT.write_text(text, encoding="utf-8")
    print(
        f"wrote {OUTPUT.relative_to(ROOT)}: {len(findings) + len(status_problems)} audit findings, {len(design_gaps)} design gaps"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
