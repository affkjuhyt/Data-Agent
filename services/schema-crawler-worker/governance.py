"""Declared governance policy for a warehouse's tables.

Owner, classification, lineage and the freshness SLA are *stated by an operator*
— they are not properties the crawler can discover. Nothing in a warehouse says
who is accountable for a table or how sensitive it is, and an LLM asked to guess
produces a label that reads like a control while being nobody's decision. So
this module only reads what somebody wrote down, and returns nothing when
nobody did.

Where it is written: `KBWarehouseConfig.config["governance"]`, deliberately NOT
inside `selected_tables`. The table-selection flow rewrites `selected_tables`
wholesale (warehouse_ops.py), so a policy stored there would be silently erased
the next time an operator changed which tables are crawled.

    "governance": {
      "defaults":       {"owner": "data-platform", "classification": "internal"},
      "freshness_sla_seconds": 86400,
      "tables": {
        "public.orders": {
          "owner": "finance",
          "classification": "confidential",
          "freshness_column": "created_at",
          "freshness_sla_seconds": 3600,
          "upstream": ["kafka:orders.cdc", "erp:SALES_ORDER"]
        }
      }
    }

Only `freshness_column` changes what the crawler *does* (it enables the
watermark query). The rest is copied to the catalog row as declared.
"""
import logging
from typing import Any

from libs.shared.freshness import normalize_classification

logger = logging.getLogger(__name__)

# Declared-policy keys a table entry may carry. An unknown key is ignored with a
# warning rather than passed through: a typo like "classifcation" would
# otherwise look accepted while leaving the table unlabelled.
_POLICY_KEYS = frozenset({
    "owner", "classification", "upstream", "freshness_column", "freshness_sla_seconds",
})

_EMPTY: dict[str, Any] = {
    "owner": "",
    "classification": "",
    "upstream": [],
    "freshness_column": "",
    "freshness_sla_seconds": None,
}


def _coerce_sla(value: Any) -> int | None:
    """Positive whole seconds, or None.

    Zero and negatives are rejected rather than clamped: an SLA of 0 would mark
    every table stale forever, which trains readers to ignore the warning — the
    one failure mode that makes the whole signal worthless.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        logger.warning("governance | freshness_sla_seconds=%r is not a number, ignoring", value)
        return None
    if seconds <= 0:
        logger.warning("governance | freshness_sla_seconds=%r must be positive, ignoring", value)
        return None
    return seconds


def _clean(entry: dict[str, Any], where: str) -> dict[str, Any]:
    unknown = set(entry) - _POLICY_KEYS
    if unknown:
        logger.warning("governance | %s has unrecognised key(s) %s, ignoring them", where, sorted(unknown))

    upstream = entry.get("upstream")
    if upstream is not None and not isinstance(upstream, list):
        logger.warning("governance | %s: upstream must be a list, got %r", where, type(upstream).__name__)
        upstream = None

    cleaned: dict[str, Any] = {}
    if "owner" in entry:
        cleaned["owner"] = str(entry.get("owner") or "").strip()
    if "classification" in entry:
        cleaned["classification"] = normalize_classification(entry.get("classification"))
    if upstream is not None:
        cleaned["upstream"] = [str(u).strip() for u in upstream if str(u).strip()]
    if "freshness_column" in entry:
        cleaned["freshness_column"] = str(entry.get("freshness_column") or "").strip()
    if "freshness_sla_seconds" in entry:
        cleaned["freshness_sla_seconds"] = _coerce_sla(entry.get("freshness_sla_seconds"))
    return cleaned


def table_policy(config: dict[str, Any], schema_name: str, table_name: str) -> dict[str, Any]:
    """Governance for one table: warehouse defaults, overlaid by its own entry.

    Lookup tries "schema.table" before the bare table name, and is
    case-insensitive — Snowflake reports identifiers upper-cased while an
    operator writes them the way they typed them in the CREATE TABLE.

    A `freshness_column` is never inherited from defaults: it names a column
    that must exist on THIS table, and a default would send the watermark query
    at every table in the warehouse looking for a column most of them lack.
    """
    governance = (config or {}).get("governance") or {}
    if not isinstance(governance, dict):
        logger.warning("governance | config[\"governance\"] must be an object, ignoring")
        return dict(_EMPTY)

    policy = dict(_EMPTY)

    defaults = governance.get("defaults")
    if isinstance(defaults, dict):
        policy.update(_clean(defaults, "governance.defaults"))
    # A warehouse-wide SLA may also be written at the top level, next to
    # "tables" — it reads more naturally there than nested under "defaults".
    if "freshness_sla_seconds" in governance:
        sla = _coerce_sla(governance.get("freshness_sla_seconds"))
        if sla is not None:
            policy["freshness_sla_seconds"] = sla
    policy["freshness_column"] = ""

    tables = governance.get("tables")
    if not isinstance(tables, dict):
        return policy

    index = {str(k).strip().lower(): v for k, v in tables.items()}
    for key in (f"{schema_name}.{table_name}".lower(), str(table_name).lower()):
        entry = index.get(key)
        if isinstance(entry, dict):
            policy.update(_clean(entry, f"governance.tables[{key!r}]"))
            break

    return policy
