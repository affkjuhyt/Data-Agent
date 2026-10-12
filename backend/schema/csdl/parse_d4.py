"""Read the database design (tab 3 and tab 3b of the design doc) into definitions.json.

The design is the source of truth (docs/v3, "3 · Thiết kế cơ sở dữ liệu" and "3b · Chi tiết bảng
(D4)"); `source/tab3.md` and `source/tab3b.md` are its markdown exports. This parser turns the parts
written as SQL — column tables, CHECK / UNIQUE / FOREIGN KEY / EXCLUDE clauses, index lists,
partitioning — into data. What D4 says in words (triggers, row-level security beyond org isolation,
column grants, a few CHECKs) is kept per table as `notes`; each note is implemented by hand in
`routines/<schema>.sql`, which marks it with `-- covers: <schema.table>#<n>`, and parse_report.md
lists any note nothing covers.

    python -m csdl.parse_d4        (from backend/schema)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "source"

AUDIT_SCHEMAS = {"audit"}

# Common columns (D1 item 6). The design names them per table; their types are fixed here.
COMMON = {
    "id": {"type": "uuid", "not_null": True, "default": None},
    "org_id": {"type": "uuid", "not_null": True, "default": None},
    "created_at": {"type": "timestamptz", "not_null": True, "default": "now()"},
    "created_by": {"type": "uuid", "not_null": False, "default": None},
    "updated_at": {"type": "timestamptz", "not_null": True, "default": "now()"},
    "updated_by": {"type": "uuid", "not_null": False, "default": None},
}

TYPE_RE = re.compile(
    r"^(text|uuid|timestamptz|jsonb|integer|boolean|numeric(?:\(\d+,\d+\))?|date|smallint|bigint|"
    r"bytea|interval|time|inet|tstzrange|daterange)(\[\])?$"
)
VIET = re.compile(r"[àáảãạăằắẳẵặâầấẩẫậđèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵ]", re.I)


def unescape(text: str) -> str:
    """The export escapes markdown specials; the design's SQL needs them back."""
    text = text.replace("&#91;", "[").replace("&#93;", "]")
    return re.sub(r"\\([_*\[\]~|{}<>`#+\-.!()])", r"\1", text)


def split_top(text: str, sep: str = ";") -> list[str]:
    """Split on `sep` outside parentheses and quotes."""
    out, cur, depth, quote = [], "", 0, False
    for ch in text:
        if ch == "'":
            quote = not quote
        elif not quote:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == sep and depth == 0:
                out.append(cur)
                cur = ""
                continue
        cur += ch
    out.append(cur)
    return [s.strip() for s in out if s.strip()]


def balanced_prefix(text: str) -> int:
    """Length of the leading parenthesised group of `text` (which starts with '(')."""
    depth, quote = 0, False
    for i, ch in enumerate(text):
        if ch == "'":
            quote = not quote
        elif not quote:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i + 1
    return -1


# ─── Tab 3: schemas (D2) and roles (D5.2) ──────────────────────────────────────


def md_tables(text: str, after_heading: str) -> list[list[str]]:
    start = text.index(after_heading)
    rows = []
    for line in text[start:].splitlines()[1:]:
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if set("".join(cells)) <= set("- "):
                continue
            rows.append(cells)
        elif rows and line.strip():
            break
    return rows


def parse_tab3(text: str) -> dict:
    schemas = []
    for cells in md_tables(text, "## D2.")[1:]:
        name = cells[0]
        if not re.fullmatch(r"[a-z_]+", name):
            continue
        schemas.append(
            {
                "name": name,
                "owner_service": cells[1],
                "content": cells[2],
                "modules": cells[3],
                "table_count": int(cells[4]),
                "database": "dataagent_audit" if name in AUDIT_SCHEMAS else "dataagent",
            }
        )
    reads = {}
    for cells in md_tables(text, "### D5.2")[1:]:
        role = cells[0]
        if not role.startswith("svc_"):
            continue
        found = re.findall(r"\b([a-z]+)\b", cells[1].split("(")[0])
        reads[role] = [s for s in found if s in {x["name"] for x in schemas}]
        # "exec (chỉ exec.publication qua exec_read …)" inside the parenthesis is a schema too.
        if "exec.publication" in cells[1]:
            reads[role].append("exec")
    return {"schemas": schemas, "cross_reads": reads}


# ─── Tab 3b: tables (D4) ───────────────────────────────────────────────────────


def parse_column(cells: list[str], notes: list[str]) -> dict:
    name, spec, meaning = cells[0], cells[1], cells[2] if len(cells) > 2 else ""
    parts = split_top(spec, ",")
    col_type = parts[0].strip()
    if not TYPE_RE.match(col_type):
        raise ValueError(f"unknown type {col_type!r} for {name}")
    col = {
        "name": name,
        "type": col_type,
        "not_null": False,
        "default": None,
        "unique": False,
        "comment": meaning,
        "fk": None,
        "logic_ref": None,
        "pii": "dữ liệu cá nhân" in meaning.lower(),
    }
    rest = spec[len(parts[0]) :]
    if "bắt buộc" in rest:
        col["not_null"] = True
    if "duy nhất" in rest:
        col["unique"] = True
    m = re.search(r"mặc định (.+?)(?:, duy nhất)?$", rest.strip())
    if m:
        col["default"] = m.group(1).strip()
    m = re.search(r"\bFK ([a-z_]+)(?:\.([a-z_]+))?", meaning)
    if m:
        col["fk"] = m.group(2) and f"{m.group(1)}.{m.group(2)}" or m.group(1)
    m = re.search(r"logic → ([a-z_]+\.[a-z_]+)", meaning)
    if m:
        col["logic_ref"] = m.group(1)
    return col


INDEX_START = re.compile(r"^(duy nhất\s+)?(GIN\s+|GiST\s+|BRIN\s+)?")


def parse_indexes(text: str, table: str, notes: list[str]) -> list[dict]:
    """'(a, b); duy nhất (c) WHERE x = 'y', để …; GIN (tags); GIN to_tsvector(…)'.

    An entry written in words ("GIN trên a và b") stops the parse; it and the rest go to `notes`.
    """
    out = []
    rest = text.strip().rstrip(".")
    while rest:
        rest = rest.lstrip(" ;,")
        if not rest:
            break
        m = INDEX_START.match(rest)
        unique = bool(m.group(1))
        method = (m.group(2) or "").strip().lower() or "btree"
        rest = rest[m.end() :]
        if rest.startswith("("):
            n = balanced_prefix(rest)
            expr = rest[1 : n - 1]
            rest = rest[n:]
        else:
            fm = re.match(r"([a-z_]+\()", rest)
            if not fm:
                notes.append(f"Chỉ mục: {(m.group(0) + rest).strip()}")
                break
            n = balanced_prefix(rest[len(fm.group(1)) - 1 :]) + len(fm.group(1)) - 1
            expr = f"({rest[:n]})"
            rest = rest[n:]
        where = None
        wm = re.match(r"\s+WHERE\s+", rest)
        if wm:
            body = rest[wm.end() :]
            # The predicate runs to the next top-level ';' or ', ' + a word that is not SQL.
            depth, quote, end = 0, False, len(body)
            for i, ch in enumerate(body):
                if ch == "'":
                    quote = not quote
                elif not quote:
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                    elif (
                        depth == 0
                        and ch in ";,"
                        or depth == 0
                        and body[i:].startswith((" cho ", " để "))
                    ):
                        end = i
                        break
            where = body[:end].strip()
            rest = body[end:]
        comment = None
        cm = re.match(r"^(?:,\s*|\s+)((?:cho|để|mỗi)\b[^;]*?)(?=;|$|,\s*(?:\(|duy nhất|GIN))", rest)
        if cm:
            comment = cm.group(1).strip()
            rest = rest[cm.end() :]
        elif rest.startswith(", ") and not re.match(r",\s*(\(|duy nhất|GIN)", rest):
            tail = re.match(r",([^;]*)", rest)
            comment = tail.group(1).strip()
            rest = rest[tail.end() :]
        out.append(
            {"unique": unique, "method": method, "expr": expr, "where": where, "comment": comment}
        )
    return out


FORMAL = re.compile(
    r"^(CHECK|UNIQUE(?: NULLS NOT DISTINCT)?|FOREIGN KEY|EXCLUDE(?: USING \w+)?)\s*\("
)


def formal_constraint(item: str) -> dict | None:
    item = item.strip().strip("`").strip().rstrip(".").strip("`")
    m = FORMAL.match(item)
    if not m:
        return None
    n0 = balanced_prefix(item[m.end() - 1 :])
    # Words inside the clause ("(a, b có giá trị AND …)") make it prose, whatever its shape.
    if n0 < 0 or VIET.search(re.sub(r"'[^']*'", "", item[: m.end() - 1 + n0])):
        return None
    n = balanced_prefix(item[m.end() - 1 :])
    if n < 0:
        return None
    tail = item[m.end() - 1 + n :].strip()
    kind = m.group(1).split()[0].lower()
    if kind == "foreign":
        rm = re.match(
            r"^REFERENCES\s+[a-z_.]+\s*\([a-z_, ]+\)(\s+ON DELETE (CASCADE|SET NULL|RESTRICT))?"
            r"(\s+DEFERRABLE INITIALLY DEFERRED)?$",
            tail,
        )
        if tail and not rm:
            return None
        return {"kind": "fk", "sql": item}
    if tail and (VIET.search(tail) or not tail.startswith(",")):
        # "CHECK (…), tức …": a gloss after the SQL; keep the SQL.
        if tail.startswith(",") or VIET.search(tail):
            item = item[: m.end() - 1 + n]
        else:
            return None
    return {"kind": {"check": "check", "unique": "unique", "exclude": "exclude"}[kind], "sql": item}


def parse_tail(table: dict, tail: str, problems: list[str]) -> None:
    """The paragraph(s) under a table's column list."""
    notes = table["notes"]
    for para in [p.strip() for p in tail.split("\n") if p.strip()]:
        para = para.strip()
        if para.startswith("Cột chung:"):
            m = re.match(
                r"Cột chung: ([a-z_, ]+?)(?: \(([^)]*(?:\([^)]*\)[^)]*)*)\))?\.(.*)$", para, re.S
            )
            if not m:
                problems.append(f"{table['qualified']}: common columns not understood")
                continue
            table["common_columns"] = [c.strip() for c in m.group(1).split(",")]
            if m.group(2):
                table["common_note"] = m.group(2)
            para = m.group(3).strip()
        if not para:
            continue
        # Sentences that belong to the machine: Ràng buộc / Chỉ mục / Phân vùng / Bản ghi bất biến.
        for sentence in re.split(
            r"(?<=[.)\]'`])\s+(?=(?:Ràng buộc|Chỉ mục|Phân vùng|Bản ghi bất biến|Trigger|RLS|svc_|Quyền|Bổ sung)\b)",
            para,
        ):
            s = sentence.strip()
            if s.startswith("Phân vùng theo tháng"):
                m = re.match(r"Phân vùng theo tháng trên ([a-z_]+)\.?\s*(.*)$", s, re.S)
                table["partition"] = {"by": "month", "column": m.group(1)}
                s = m.group(2).strip()
                if not s:
                    continue
            if s.startswith("Bản ghi bất biến"):
                table["immutable"] = True
                s = s[len("Bản ghi bất biến") :].lstrip(". ")
                if not s:
                    continue
            if s.startswith("Ràng buộc:") or re.match(
                r"Bổ sung [^:]*:\s*(CHECK|UNIQUE|FOREIGN)", s
            ):
                body = s.split(":", 1)[1]
                for item in split_top(body):
                    c = formal_constraint(item)
                    if c:
                        table["constraints"].append(c)
                    elif re.match(
                        r"^(FOREIGN KEY) \(([a-z_]+)\)(, \(([a-z_]+)\))*$", item.strip().rstrip(".")
                    ):
                        for col in re.findall(r"\(([a-z_]+)\)", item):
                            table["constraints"].append({"kind": "fk_column", "column": col})
                    else:
                        notes.append(item.strip().rstrip("."))
                continue
            if s.startswith("Chỉ mục:"):
                # The index list ends with its sentence; what follows is prose for routines/.
                body, _, after = s[len("Chỉ mục:") :].partition(". ")
                table["indexes"].extend(parse_indexes(body, table["qualified"], notes))
                if after.strip():
                    notes.append(after.strip())
                continue
            notes.append(s)


def parse_tab3b(text: str, problems: list[str]) -> list[dict]:
    tables = []
    schema_intro: dict[str, str] = {}
    for section in re.split(r"\n### D4\.\d+ ", text)[1:]:
        head, *blocks = re.split(r"\n#### ", section)
        sm = re.match(r"Schema ([a-z_]+)", head)
        schema = sm.group(1)
        schema_intro[schema] = "\n".join(head.splitlines()[1:]).strip()
        for block in blocks:
            lines = block.splitlines()
            qualified = lines[0].strip()
            s, name = qualified.split(".")
            if s != schema:
                problems.append(f"{qualified} sits under schema {schema}")
            desc, cols, tail, in_table = [], [], [], False
            for line in lines[1:]:
                if line.startswith("|"):
                    in_table = True
                    cells = [c.strip() for c in line.strip().strip("|").split("|")]
                    if cells[0] in ("Cột", "") or set("".join(cells)) <= set("- "):
                        continue
                    cols.append(cells)
                elif not in_table:
                    if line.strip():
                        desc.append(line.strip())
                elif line.strip() and not line.startswith("#"):
                    tail.append(line)
            description = " ".join(desc)
            uc = re.findall(r"UC-\d\d-\d\d", description)
            table = {
                "schema": schema,
                "name": name,
                "qualified": qualified,
                "description": description,
                "use_cases": sorted(set(uc)),
                "columns": [],
                "common_columns": [],
                "constraints": [],
                "indexes": [],
                "partition": None,
                "immutable": False,
                "notes": [],
            }
            for cells in cols:
                try:
                    table["columns"].append(parse_column(cells, table["notes"]))
                except ValueError as exc:
                    problems.append(f"{qualified}: {exc}")
            parse_tail(table, "\n".join(tail), problems)
            # Some tables state append-only in their description rather than "Bản ghi bất biến"
            # (period.published_result, BR-42): the owning role keeps only INSERT and SELECT.
            if re.search(r"chỉ có quyền INSERT và SELECT", description):
                table["immutable"] = True
            tables.append(table)
    return tables, schema_intro


def resolve(tables: list[dict], problems: list[str]) -> None:
    """Shorthand FKs ('FOREIGN KEY (x), (y)') take their target from the column's 'FK t'."""
    names = {t["qualified"] for t in tables}
    for t in tables:
        cols = {c["name"]: c for c in t["columns"]}
        fixed = []
        for c in t["constraints"]:
            if c["kind"] != "fk_column":
                fixed.append(c)
                continue
            col = cols.get(c["column"])
            target = col and col["fk"]
            if not target:
                problems.append(f"{t['qualified']}: FOREIGN KEY ({c['column']}) has no target")
                continue
            q = target if "." in target else f"{t['schema']}.{target}"
            if q not in names:
                problems.append(f"{t['qualified']}: FK target {q} unknown")
                continue
            fixed.append({"kind": "fk", "sql": f"FOREIGN KEY ({c['column']}) REFERENCES {q}(id)"})
        t["constraints"] = fixed
        # Columns that say "FK t" without a FOREIGN KEY clause still get one (same schema only).
        declared = " ".join(c["sql"] for c in t["constraints"] if c["kind"] == "fk")
        for col in t["columns"]:
            if not col["fk"] or f"({col['name']})" in declared:
                continue
            q = col["fk"] if "." in col["fk"] else f"{t['schema']}.{col['fk']}"
            if q.split(".")[0] != t["schema"] or q not in names:
                continue
            t["constraints"].append(
                {
                    "kind": "fk",
                    "sql": f"FOREIGN KEY ({col['name']}) REFERENCES {q}(id)",
                    "implied": True,
                }
            )


def main() -> int:
    problems: list[str] = []
    tab3 = unescape((SOURCE / "tab3.md").read_text())
    tab3b = unescape((SOURCE / "tab3b.md").read_text())
    meta = parse_tab3(tab3)
    tables, intros = parse_tab3b(tab3b, problems)
    resolve(tables, problems)

    by_schema: dict[str, int] = {}
    for t in tables:
        by_schema[t["schema"]] = by_schema.get(t["schema"], 0) + 1
    for s in meta["schemas"]:
        if by_schema.get(s["name"]) != s["table_count"]:
            problems.append(
                f"D2 says {s['name']} has {s['table_count']} tables, D4 has {by_schema.get(s['name'])}"
            )

    definitions = {
        "source": "Data Agent V3 design doc, tab 3 and tab 3b (exports in source/)",
        "schemas": [dict(s, intro=intros.get(s["name"], "")) for s in meta["schemas"]],
        "cross_reads": meta["cross_reads"],
        "common_columns": COMMON,
        "tables": tables,
    }
    (HERE / "definitions.json").write_text(
        json.dumps(definitions, ensure_ascii=False, indent=1) + "\n"
    )

    n_notes = sum(len(t["notes"]) for t in tables)
    report = [
        "# parse_d4 report",
        "",
        f"{len(tables)} tables in {len(by_schema)} schemas; "
        f"{sum(len(t['columns']) for t in tables)} own columns; "
        f"{sum(len(t['constraints']) for t in tables)} constraints; "
        f"{sum(len(t['indexes']) for t in tables)} indexes; {n_notes} notes for routines/.",
        "",
        "## Problems",
        "",
        *([f"- {p}" for p in problems] or ["None."]),
        "",
    ]
    (HERE / "parse_report.md").write_text("\n".join(report))
    print(report[2])
    for p in problems:
        print("PROBLEM", p, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
