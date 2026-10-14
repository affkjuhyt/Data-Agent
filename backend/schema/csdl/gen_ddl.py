"""Generate dataagent.sql and dataagent_audit.sql from definitions.json (tab 3 D5).

Each file is one transaction run by a cluster admin: it creates the missing roles, then everything
else as `da_owner`, so no service role owns a table and row-level security always applies.
What D4 writes in words (triggers, owner-only policies, column grants) comes from
`routines/<schema>.sql`, appended after the generated part of its schema.

    python -m csdl.gen_ddl        (from backend/schema)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[1] / "alembic" / "sql"
ROUTINES = HERE / "routines"

OUTBOX_COLUMNS = [
    ("id", "uuid NOT NULL"),
    ("org_id", "uuid NOT NULL"),
    ("aggregate_type", "text NOT NULL"),
    ("aggregate_id", "uuid NOT NULL"),
    ("event_type", "text NOT NULL"),
    ("payload", "jsonb NOT NULL DEFAULT '{}'"),
    ("created_at", "timestamptz NOT NULL DEFAULT now()"),
    ("published_at", "timestamptz"),
]
ORG_GUC = "NULLIF(current_setting('app.org_id', true), '')::uuid"


def q(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def ident_slug(expr: str) -> str:
    words = re.findall(r"[a-z_][a-z0-9_]*", expr.lower())
    skip = {"desc", "asc", "simple", "to_tsvector", "coalesce", "lower", "nulls", "first", "last"}
    return "_".join(w for w in words if w not in skip)[:40].strip("_") or "expr"


class Names:
    """Index names follow D1 item 14 (<table>_<columns>_idx), made unique per schema."""

    def __init__(self) -> None:
        self.used: set[str] = set()

    def take(self, schema: str, base: str) -> str:
        base = base[:59]
        name, n = base, 2
        while (schema, name) in self.used:
            name = f"{base[:56]}_{n}"
            n += 1
        self.used.add((schema, name))
        return name


def role_block(roles: list[str]) -> str:
    lines = ["DO $$", "BEGIN"]
    for r in roles:
        lines.append(
            f"  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = {q(r)}) THEN "
            f"CREATE ROLE {r} NOLOGIN NOSUPERUSER NOBYPASSRLS; END IF;"
        )
    lines += ["END $$;", ""]
    return "\n".join(lines)


def column_sql(col: dict, common: dict | None = None) -> str:
    spec = common or col
    parts = [f"{col['name']} {spec['type']}"]
    if spec.get("not_null"):
        parts.append("NOT NULL")
    if spec.get("default") is not None:
        parts.append(f"DEFAULT {spec['default']}")
    return " ".join(parts)


def table_columns(t: dict, common: dict) -> list[tuple[str, str]]:
    """id and org_id first, then the table's own columns, then the audit columns."""
    out = []
    for name in ("id", "org_id"):
        if name in t["common_columns"]:
            out.append((name, column_sql({"name": name}, common[name])))
    for col in t["columns"]:
        out.append((col["name"], column_sql(col)))
    for name in ("created_at", "created_by", "updated_at", "updated_by"):
        if name in t["common_columns"]:
            out.append((name, column_sql({"name": name}, common[name])))
    return out


def create_table(t: dict, common: dict) -> list[str]:
    qn = t["qualified"]
    cols = table_columns(t, common)
    body = [f"  {sql}" for _, sql in cols]
    part = t["partition"]
    pk = f"id, {part['column']}" if part else "id"
    body.append(f"  CONSTRAINT {t['name']}_pkey PRIMARY KEY ({pk})")
    for c in t["constraints"]:
        if c["kind"] in ("check", "unique", "exclude"):
            body.append(f"  {c['sql']}")
    tail = f" PARTITION BY RANGE ({part['column']})" if part else ""
    out = [f"CREATE TABLE {qn} (", ",\n".join(body), f"){tail};"]
    if part:
        out.append(f"CREATE TABLE {qn}_default PARTITION OF {qn} DEFAULT;")
    if t["description"]:
        out.append(f"COMMENT ON TABLE {qn} IS {q(t['description'])};")
    for col in t["columns"]:
        if col["comment"]:
            out.append(f"COMMENT ON COLUMN {qn}.{col['name']} IS {q(col['comment'])};")
    return out


def index_sql(t: dict, ix: dict, names: Names) -> str:
    expr = ix["expr"]
    slug = ident_slug(expr)
    name = names.take(t["schema"], f"{t['name']}_{slug}_{'key' if ix['unique'] else 'idx'}")
    method = "" if ix["method"] == "btree" else f" USING {ix['method']}"
    where = f" WHERE {ix['where']}" if ix["where"] else ""
    unique = "UNIQUE " if ix["unique"] else ""
    # An expression index takes its own parentheses; a column list gets them here.
    target = (
        expr if expr.startswith("(") and expr.endswith(")") and "(" in expr[1:] else f"({expr})"
    )
    return f"CREATE {unique}INDEX {name} ON {t['qualified']}{method} {target}{where};"


def fk_sql(t: dict, c: dict, n: int) -> str:
    cols = re.match(r"FOREIGN KEY \(([^)]*)\)", c["sql"]).group(1)
    name = f"{t['name']}_{ident_slug(cols)}_fkey"[:63]
    return f"ALTER TABLE {t['qualified']} ADD CONSTRAINT {name} {c['sql']};"


def rls_sql(t: dict) -> list[str]:
    qn = t["qualified"]
    if t["qualified"] == "org.organization":
        cond = f"id = {ORG_GUC}"
    elif "org_id" in t["common_columns"]:
        cond = f"org_id = {ORG_GUC}"
    else:
        return []
    force = " FORCE ROW LEVEL SECURITY;" if t["schema"] == "exec" else ""
    out = [f"ALTER TABLE {qn} ENABLE ROW LEVEL SECURITY;"]
    if force:
        out.append(f"ALTER TABLE {qn}{force}")
    out.append(f"CREATE POLICY org_isolation ON {qn} USING ({cond}) WITH CHECK ({cond});")
    return out


def outbox(schema: str) -> list[str]:
    cols = ",\n".join(f"  {n} {s}" for n, s in OUTBOX_COLUMNS)
    qn = f"{schema}.outbox"
    return [
        f"CREATE TABLE {qn} (\n{cols},\n  CONSTRAINT outbox_pkey PRIMARY KEY (id)\n);",
        f"COMMENT ON TABLE {qn} IS 'Sự kiện chờ Debezium đẩy sang Kafka (D5.3), ghi cùng giao dịch với thay đổi nghiệp vụ.';",
        f"CREATE INDEX outbox_unpublished_idx ON {qn} (created_at) WHERE published_at IS NULL;",
        f"ALTER TABLE {qn} ENABLE ROW LEVEL SECURITY;",
        f"CREATE POLICY org_isolation ON {qn} USING (org_id = {ORG_GUC}) WITH CHECK (org_id = {ORG_GUC});",
        # exec: every table, its outbox included, is FORCE RLS (D4.24, D5.1).
        *([f"ALTER TABLE {qn} FORCE ROW LEVEL SECURITY;"] if schema == "exec" else []),
        # Debezium reads outbox rows from the WAL, past RLS (ADR-06). Until it runs, a polling
        # relay publishes them: it sees every organisation's outbox rows, may only mark one
        # published, and has no grant on any other table.
        f"GRANT USAGE ON SCHEMA {schema} TO svc_outbox_relay;",
        f"GRANT SELECT, UPDATE (published_at) ON {qn} TO svc_outbox_relay;",
        f"CREATE POLICY outbox_relay_read ON {qn} FOR SELECT TO svc_outbox_relay USING (true);",
        f"CREATE POLICY outbox_relay_publish ON {qn} FOR UPDATE TO svc_outbox_relay "
        "USING (published_at IS NULL) WITH CHECK (published_at IS NOT NULL);",
    ]


def grants(schema: str, tables: list[dict], with_outbox: bool) -> list[str]:
    svc, read = f"svc_{schema}", f"{schema}_read"
    out = [f"GRANT USAGE ON SCHEMA {schema} TO {svc}, {read};"]
    if with_outbox:
        out.append(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {schema}.outbox TO {svc};")
    for t in tables:
        # Immutable records (D1 item 6): no UPDATE or DELETE for the owning service.
        verbs = "SELECT, INSERT" if t["immutable"] else "SELECT, INSERT, UPDATE, DELETE"
        out.append(f"GRANT {verbs} ON {t['qualified']} TO {svc};")
        out.append(f"GRANT SELECT ON {t['qualified']} TO {read};")
    return out


PARTITION_FN = """
-- D5.3: monthly partitions for the current month and the next `months_ahead`, for every table
-- partitioned by month (default partitions catch rows before a month exists).
CREATE OR REPLACE FUNCTION public.da_ensure_month_partitions(months_ahead integer DEFAULT 3)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $fn$
DECLARE
  parent record;
  m date;
  part text;
  created integer := 0;
BEGIN
  FOR parent IN
    SELECT n.nspname AS schema_name, c.relname AS table_name
    FROM pg_partitioned_table p
    JOIN pg_class c ON c.oid = p.partrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
  LOOP
    FOR i IN 0..months_ahead LOOP
      m := (date_trunc('month', now() AT TIME ZONE 'UTC') + make_interval(months => i))::date;
      part := format('%s_%s', parent.table_name, to_char(m, 'YYYYMM'));
      IF to_regclass(format('%I.%I', parent.schema_name, part)) IS NULL THEN
        EXECUTE format(
          'CREATE TABLE %I.%I PARTITION OF %I.%I FOR VALUES FROM (%L) TO (%L)',
          parent.schema_name, part, parent.schema_name, parent.table_name,
          m::timestamptz, (m + interval '1 month')::timestamptz);
        created := created + 1;
      END IF;
    END LOOP;
  END LOOP;
  RETURN created;
END
$fn$;
"""


ONLY: set[str] | None = None  # --only-routines a,b: build with just these routines (parallel work)


def routine(schema: str) -> str:
    path = ROUTINES / f"{schema}.sql"
    if ONLY is not None and schema not in ONLY:
        return ""
    return path.read_text() if path.exists() else ""


def coverage(d: dict) -> list[str]:
    """Notes of definitions.json that no routines file marks with `-- covers: <table>#<n>`."""
    marked = set()
    for f in ROUTINES.glob("*.sql"):
        marked |= set(re.findall(r"--\s*covers:\s*([a-z_]+\.[a-z_]+#\d+)", f.read_text()))
    return [
        f"{t['qualified']}#{i}: {note}"
        for t in d["tables"]
        for i, note in enumerate(t["notes"])
        if f"{t['qualified']}#{i}" not in marked
    ]


def build(d: dict, database: str) -> str:
    common = d["common_columns"]
    schemas = [s["name"] for s in d["schemas"] if s["database"] == database]
    tables = [t for t in d["tables"] if t["schema"] in schemas]
    names = Names()
    roles = ["da_owner"] + [f"svc_{s}" for s in schemas] + [f"{s}_read" for s in schemas]
    if "exec" in schemas:
        roles.append("svc_exec_worker")
    if "audit" in schemas:
        # worker-agent releases approved exports; it must not read or append the log (D5.2).
        roles.append("svc_audit_worker")
    # Every schema has an outbox (tab 3 D1 item 13), audit's included since 12/10/2026.
    roles.append("svc_outbox_relay")
    out = [
        f"-- {database}.sql: GENERATED by backend/schema/csdl/gen_ddl.py from definitions.json.",
        "-- Do not edit: change the design (tab 3/3b) or routines/, then regenerate.",
        "-- Run as a cluster admin in one transaction; objects are created as da_owner.",
        "",
        role_block(roles),
        "CREATE EXTENSION IF NOT EXISTS pgcrypto;",
        "DO $$ BEGIN EXECUTE format('GRANT CREATE ON DATABASE %I TO da_owner', current_database()); END $$;",
        # Fresh PostgreSQL databases do not grant CREATE on public to non-owner roles.
        # The generated routine set includes SECURITY DEFINER helpers in public, so the
        # trusted schema owner needs this privilege before SET LOCAL ROLE da_owner.
        "GRANT CREATE ON SCHEMA public TO da_owner;",
    ]
    # Role membership needs the admin, so D5.2's cross-schema reads are granted before SET ROLE.
    for svc, reads in d["cross_reads"].items():
        if svc.removeprefix("svc_") not in schemas:
            continue
        for r in reads:
            if r in schemas and r != svc.removeprefix("svc_"):
                out.append(f"GRANT {r}_read TO {svc};")
    out.append(PARTITION_FN)
    out.append("ALTER FUNCTION public.da_ensure_month_partitions(integer) OWNER TO da_owner;")
    out.append("")
    out.append("SET LOCAL ROLE da_owner;")
    for s in schemas:
        out.append(f"CREATE SCHEMA {s};")
    # Functions a CHECK calls must exist before the tables (routines/<schema>.pre.sql).
    for s in schemas:
        pre = ROUTINES / f"{s}.pre.sql"
        # Always included, even with --only-routines: generated CHECKs call these functions.
        if pre.exists():
            out += [f"-- ─── routines/{s}.pre.sql ───", pre.read_text().rstrip(), ""]
    out.append("")
    for s in schemas:
        st = [t for t in tables if t["schema"] == s]
        out.append(f"-- ═══ schema {s} ═══")
        for t in st:
            out += create_table(t, common)
            out.append("")
        out += outbox(s)
        for t in st:
            for ix in t["indexes"]:
                out.append(index_sql(t, ix, names))
        out.append("")
    for t in tables:
        for i, c in enumerate(c for c in t["constraints"] if c["kind"] == "fk"):
            out.append(fk_sql(t, c, i))
    out.append("")
    for t in tables:
        out += rls_sql(t)
    out.append("")
    for s in schemas:
        out += grants(s, [t for t in tables if t["schema"] == s], True)
    out.append("")
    for s in schemas:
        body = routine(s)
        if body:
            out += [f"-- ─── routines/{s}.sql ───", body.rstrip(), ""]
    out.append("SELECT public.da_ensure_month_partitions(3);")
    out.append("RESET ROLE;")
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    global ONLY
    out = OUT
    args = iter(argv)
    for a in args:
        if a == "--only-routines":
            ONLY = set(next(args).split(","))
        elif a == "--out":
            out = Path(next(args))
    d = json.loads((HERE / "definitions.json").read_text())
    out.mkdir(parents=True, exist_ok=True)
    for database in ("dataagent", "dataagent_audit"):
        sql = build(d, database)
        (out / f"{database}.sql").write_text(sql)
        print(f"{database}.sql: {sql.count('CREATE TABLE')} CREATE TABLE, {len(sql)} bytes")
    missing = coverage(d)
    (HERE / "coverage.md").write_text(
        "# Notes not yet implemented in routines/\n\n"
        + ("\n".join(f"- {m}" for m in missing) or "None.")
        + "\n"
    )
    print(f"{len(missing)} notes not covered (coverage.md)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
