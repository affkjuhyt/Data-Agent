"""Generate the SQLAlchemy models of the V3 database (backend/libs/dataagent_db/models/) from
definitions.json, so the ORM and the DDL come from one definition and cannot drift.

The models map columns, keys and same-schema foreign keys. Rules (CHECK, triggers, RLS, grants) live
in the database only: the DDL in backend/alembic/sql is the schema, and Alembic never autogenerates
from these classes.

    python -m csdl.gen_orm        (from backend/schema)
"""

from __future__ import annotations

import json
import keyword
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE = HERE.parents[1] / "libs" / "dataagent_db" / "models"

# PostgreSQL type → (SQLAlchemy type expression, Python annotation, imports needed)
SCALAR = {
    "uuid": ("UUID(as_uuid=True)", "uuid.UUID"),
    "text": ("Text", "str"),
    "timestamptz": ("DateTime(timezone=True)", "datetime.datetime"),
    "jsonb": ("JSONB", "Any"),
    "integer": ("Integer", "int"),
    "smallint": ("SmallInteger", "int"),
    "bigint": ("BigInteger", "int"),
    "boolean": ("Boolean", "bool"),
    "date": ("Date", "datetime.date"),
    "time": ("Time", "datetime.time"),
    "interval": ("Interval", "datetime.timedelta"),
    "bytea": ("LargeBinary", "bytes"),
    "inet": ("INET", "str"),
    "tstzrange": ("TSTZRANGE", "Any"),
    "daterange": ("DATERANGE", "Any"),
}

OUTBOX = [
    ("id", "uuid", True, None),
    ("org_id", "uuid", True, None),
    ("aggregate_type", "text", True, None),
    ("aggregate_id", "uuid", True, None),
    ("event_type", "text", True, None),
    ("payload", "jsonb", True, "'{}'"),
    ("created_at", "timestamptz", True, "now()"),
    ("published_at", "timestamptz", False, None),
]


def class_name(table: str) -> str:
    return "".join(part.capitalize() for part in table.split("_"))


def attr_name(column: str) -> str:
    # A column named like a Python keyword or a declarative reserved word gets a trailing "_".
    if keyword.iskeyword(column) or column in {"metadata", "registry"}:
        return f"{column}_"
    return column


def sa_type(pg: str) -> tuple[str, str]:
    is_array = pg.endswith("[]")
    base = pg[:-2] if is_array else pg
    m = re.match(r"numeric(?:\((\d+),(\d+)\))?$", base)
    if m:
        sa = f"Numeric({m.group(1)}, {m.group(2)})" if m.group(1) else "Numeric"
        py = "decimal.Decimal"
    else:
        sa, py = SCALAR[base]
    if is_array:
        return f"ARRAY({sa})", f"list[{py}]"
    return sa, py


def column_line(
    name: str,
    pg: str,
    not_null: bool,
    default: str | None,
    *,
    pk: bool = False,
    fk: str | None = None,
    comment: str | None = None,
) -> str:
    sa, py = sa_type(pg)
    annot = py if not_null or pk else f"{py} | None"
    args = [sa]
    if fk:
        args.append(fk)
    if pk:
        args.append("primary_key=True")
        if name == "id":
            args.append("default=uuid7")
    elif not not_null:
        args.append("nullable=True")
    if default is not None:
        args.append(f"server_default=sql_text({default!r})")
    attr = attr_name(name)
    if attr != name:
        args.insert(0, repr(name))
    if comment:
        args.append(f"comment={comment!r}")
    return f"    {attr}: Mapped[{annot}] = mapped_column({', '.join(args)})"


def fk_targets(t: dict) -> dict[str, str]:
    """column → 'ForeignKey("schema.table.id", ondelete=…)' for single-column FKs."""
    out = {}
    for c in t["constraints"]:
        if c["kind"] != "fk":
            continue
        m = re.match(
            r"FOREIGN KEY \(([a-z_]+)\) REFERENCES ([a-z_]+\.[a-z_]+)\s*\(([a-z_]+)\)"
            r"(?:\s+ON DELETE (CASCADE|SET NULL|RESTRICT))?(\s+DEFERRABLE INITIALLY DEFERRED)?",
            c["sql"],
        )
        if not m:
            continue
        args = [repr(f"{m.group(2)}.{m.group(3)}")]
        if m.group(4):
            args.append(f"ondelete={m.group(4)!r}")
        if m.group(5):
            args += ["deferrable=True", "initially='DEFERRED'"]
        out[m.group(1)] = f"ForeignKey({', '.join(args)})"
    return out


def table_class(t: dict, common: dict) -> list[str]:
    part = t["partition"]
    fks = fk_targets(t)
    lines = [f"class {class_name(t['name'])}(Base):"]
    doc = t["description"].replace('"""', "'''")
    if doc:
        lines.append(f'    """{doc}"""')
        lines.append("")
    lines.append(f"    __tablename__ = {t['name']!r}")
    lines.append(f"    __table_args__ = ({{'schema': {t['schema']!r}}},)")
    lines.append("")
    order = []
    for name in ("id", "org_id"):
        if name in t["common_columns"]:
            spec = common[name]
            order.append(
                column_line(name, spec["type"], spec["not_null"], spec["default"], pk=name == "id")
            )
    for col in t["columns"]:
        is_pk = bool(part and part["column"] == col["name"])
        order.append(
            column_line(
                col["name"],
                col["type"],
                col["not_null"],
                col["default"],
                pk=is_pk,
                fk=fks.get(col["name"]),
                comment=col["comment"] or None,
            )
        )
    for name in ("created_at", "created_by", "updated_at", "updated_by"):
        if name in t["common_columns"]:
            spec = common[name]
            is_pk = bool(part and part["column"] == name)
            order.append(
                column_line(name, spec["type"], spec["not_null"], spec["default"], pk=is_pk)
            )
    return lines + order


def outbox_class(schema: str) -> list[str]:
    lines = [
        "class Outbox(Base):",
        '    """Events written in the same transaction as the change; Debezium publishes them (D5.3)."""',
        "",
        "    __tablename__ = 'outbox'",
        f"    __table_args__ = ({{'schema': {schema!r}}},)",
        "",
    ]
    for name, pg, not_null, default in OUTBOX:
        lines.append(column_line(name, pg, not_null, default, pk=name == "id"))
    return lines


SA_NAMES = [
    "ARRAY",
    "BigInteger",
    "Boolean",
    "Date",
    "DateTime",
    "ForeignKey",
    "Integer",
    "Interval",
    "LargeBinary",
    "Numeric",
    "SmallInteger",
    "Text",
    "Time",
]
PG_NAMES = ["DATERANGE", "INET", "JSONB", "TSTZRANGE", "UUID"]


def module(schema: dict, tables: list[dict], common: dict) -> str:
    classes = [class_name(t["name"]) for t in tables]
    blocks = ["\n".join(table_class(t, common)) for t in tables]
    # Every schema has an outbox (tab 3 D1 item 13), audit's included since 12/10/2026.
    classes.append("Outbox")
    blocks.append("\n".join(outbox_class(schema["name"])))
    body = "\n\n\n".join(blocks)
    # Import only what the classes use, so the generated file is ruff-clean (F401).
    used = lambda name: re.search(rf"\b{name}\b", body) is not None  # noqa: E731
    std = [m for m in ("datetime", "decimal", "uuid") if used(m)]
    lines = [
        f'"""Schema `{schema["name"]}`: {schema["content"]}. GENERATED by backend/schema/csdl/gen_orm.py; do not edit."""',
        "",
        # The design's text is copied as is, its multiplication and dash symbols included.
        "# ruff: noqa: E501, RUF001, RUF002",
        "from __future__ import annotations",
        "",
        *[f"import {m}" for m in std],
    ]
    if used("Any"):
        lines.append("from typing import Any")
    lines.append("")
    sa = [n for n in SA_NAMES if used(n)]
    # `text` is aliased: a column may be called `text`, which would shadow it in the class body.
    if used("sql_text"):
        sa.append("text as sql_text")
    lines.append("from sqlalchemy import (\n" + "".join(f"    {n},\n" for n in sa) + ")")
    pg = [n for n in PG_NAMES if used(n)]
    if pg:
        lines.append(f"from sqlalchemy.dialects.postgresql import {', '.join(pg)}")
    lines.append("from sqlalchemy.orm import Mapped, mapped_column")
    lines.append("")
    lines.append("from ..base import Base" + (", uuid7" if used("uuid7") else ""))
    lines.append("")
    lines.append(f"__all__ = [{', '.join(repr(c) for c in classes)}]")
    return "\n".join(lines) + "\n\n\n" + body + "\n"


def main() -> int:
    d = json.loads((HERE / "definitions.json").read_text())
    PACKAGE.mkdir(parents=True, exist_ok=True)
    names = []
    for s in d["schemas"]:
        tables = [t for t in d["tables"] if t["schema"] == s["name"]]
        mod_name = attr_name(s["name"])
        (PACKAGE / f"{mod_name}.py").write_text(module(s, tables, d["common_columns"]))
        names.append(mod_name)
    init = [
        '"""Every table of the V3 database, one module per schema. GENERATED by gen_orm.py."""',
        "",
        "from . import " + ", ".join(sorted(names)),
        "",
        f"__all__ = {sorted(names)!r}",
        "",
    ]
    (PACKAGE / "__init__.py").write_text("\n".join(init))
    # The repo lint ratchet rejects unformatted new files; format the output when ruff is around
    # (make fmt formats it otherwise).
    if shutil.which("ruff"):
        subprocess.run(["ruff", "check", "-q", "--fix", "--select", "I", str(PACKAGE)], check=True)
        subprocess.run(["ruff", "format", "-q", str(PACKAGE)], check=True)
    print(f"{len(names)} modules, {len(d['tables'])} tables")
    return 0


if __name__ == "__main__":
    sys.exit(main())
