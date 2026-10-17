"""Deterministic checks of an SQL candidate before it runs (01 §3.4 "unit test tất định").

The checker gives instructions and the model only fixes: each problem is a sentence the repair
prompt hands back. Fail fast, in order: it parses as one query; it only reads (query/guard); it
reads only the linked tables; it names its columns (no SELECT *); every column it names exists.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.qualify import qualify

from services.worker_agent.query.guard import SqlRejected, check_read_only
from services.worker_agent.sqlgen.schema import Linked

_TYPES = {
    "string": "TEXT",
    "integer": "BIGINT",
    "decimal": "DECIMAL",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
}


def problems(sql: str, linked: Linked, dialect: str) -> list[str]:
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except ParseError as exc:
        return [f"SQL không phân tích được: {str(exc).splitlines()[0]}"]
    # What sqlglot cannot read as SQL it keeps as a bare command ("SELEC …").
    if isinstance(tree, exp.Command):
        return ["SQL không phân tích được thành một câu truy vấn SELECT."]
    try:
        check_read_only(sql, dialect)
    except SqlRejected as exc:
        return [f"Chỉ được viết một câu SELECT chỉ đọc ({exc})."]
    if not isinstance(tree, exp.Query):
        return ["Chỉ được viết một câu SELECT chỉ đọc."]
    allowed = {t.qualified: t for t in linked.tables}
    used = {f"{t.db}.{t.name}" if t.db else t.name for t in tree.find_all(exp.Table)}
    outside = sorted(u for u in used if u not in allowed)
    if outside:
        return [
            f"Bảng {', '.join(outside)} không có trong phạm vi; chỉ dùng: {', '.join(sorted(allowed))}."
        ]
    # A star reads every column, including those the model was never shown (personal data):
    # SELECT *, t.*, COUNT(DISTINCT t.*). Only COUNT(*) counts rows without reading them.
    for star in tree.find_all(exp.Star):
        if not isinstance(star.parent, exp.Count):
            return ["Không dùng SELECT * hay bảng.*; hãy nêu rõ các cột cần lấy."]
    mapping: dict[str, dict[str, dict[str, str]]] = {}
    for t in linked.tables:
        mapping.setdefault(t.schema, {})[t.name] = {
            c.name: _TYPES.get(c.kind, "TEXT") for c in t.columns
        }
    try:
        qualify(tree.copy(), schema=mapping, dialect=dialect, validate_qualify_columns=True)
    except OptimizeError as exc:
        return [f"Cột không có trong bảng: {exc}. Chỉ dùng các cột đã liệt kê."]
    return []
