"""A schema version proposed by the model and judged here (KB-04, UC-14-02, UC-14-03, BR-151/152).

Two things start it. A source without an approved schema gets its sample pages from an inference
run (POST /v1/crawl-sources/{id}/schema-inference); a run whose extraction with the active version
comes out incomplete records a `schema_drift` (minor: the run is kept and a remap is proposed;
major: the source pauses, the run is quarantined and a person is asked, BR-152). Either way this
module, which holds the originals, sends `acquire.schema.inference_requested` with bounded excerpts
of some of the pages and keeps the others back; worker-agent asks the model (the KB LLM steps run
there, crawl_schemas.py) and answers with `acquire.schema.draft_proposed`.

The model only proposes (invariant 5). What it returns is data: the selectors are compiled, never
run as code, every field checked by name and type, and when an approved version exists the fields
are that version's own, with the model choosing only where to read them. The draft is then run on
the pages kept back and becomes a `testing` version open to its owner's approval only at
`MIN_ACCURACY` of required fields (BR-151, the bar :approve holds too); below it, it is kept as a
`rejected` version with its accuracy, which no one can approve. Nothing here approves or activates
a version, and production extraction keeps using the active one until a person approves another.

Page content is untrusted (prompt injection): the excerpt keeps tags, a few naming attributes and
short escaped text, never a script, style, comment or link target, and it is bounded in size. What
a page says can at most make the draft wrong, which the held-out test then measures.
"""

from __future__ import annotations

import datetime
import decimal
import html as html_escape
import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any

from lxml import etree, html
from lxml.cssselect import CSSSelector
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import acquire
from libs.tabular import profile as ingest_profile
from services.crawler import extract
from services.crawler.events import Event
from services.crawler.storage import RawStore

log = logging.getLogger(__name__)

ROLE = "svc_acquire"
INFERENCE_REQUESTED = "acquire.schema.inference_requested"
DRAFT_PROPOSED = "acquire.schema.draft_proposed"
DRIFT_DETECTED = "acquire.schema.drift_detected"
INFERRED = "acquire.schema.inferred"
# The payload of hitl.requested is the contract of services/api/modules/inbox/public.py, which the
# crawler cannot import; the keys below are that function's.
HITL_REQUESTED = "hitl.requested"

# BR-151: the share of required fields a version must extract right before it may be approved.
MIN_ACCURACY = decimal.Decimal("0.95")
# BR-152: below this share of required cells, or with a key field gone, a drift is major.
MAJOR_BELOW = decimal.Decimal("0.80")
# Pages shown to the model; the rest of the sample is kept back to test its draft.
SHOWN_PAGES = 3
EXCERPT_CHARS = 6_000
TEXT_CHARS = 80
ATTR_CHARS = 60
# Siblings of one shape shown before the excerpt says how many more there are.
SAME_SIBLINGS = 4
KEPT_ATTRIBUTES = ("class", "id", "itemprop", "role")
DROPPED_TAGS = frozenset(
    {"script", "style", "noscript", "template", "iframe", "svg", "head", "link", "meta", "object"}
)
MAX_SELECTOR = 300
MAX_FIELDS = 30
TYPES = frozenset({"text", "number", "decimal", "integer", "percent", "money", "date"})
# The types whose domain an active version must declare (CHECK value_constraints_ok, BR-168).
NUMERIC_DOMAIN = frozenset({"number", "percent", "money"})
_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
_JSON_PATH = re.compile(r"^\$?(\.?[A-Za-z_][A-Za-z0-9_-]*|\[\*\]|\[\d+\])*$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
# One namespace for the version a proposal becomes: a redelivered draft finds its version.
_VERSION_NS = uuid.UUID("6b1f3c1e-2f0a-4d4e-9c57-5e1d0b0a4b04")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


# ---------------------------------------------------------------- what the model sees


def content_kind(content_type: str) -> str | None:
    kind = content_type.split(";")[0].strip().lower()
    if kind.endswith("json"):
        return "json"
    if "xml" in kind and "html" not in kind:
        return "xml"
    if "html" in kind or kind.startswith("text/"):
        return "html"
    return None


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def _tag(name: Any) -> str:
    # A page may name its elements anything; the excerpt keeps a plain name only.
    return re.sub(r"[^a-z0-9-]", "", etree.QName(name).localname.lower())[:30] or "x"


def _outline(node: Any, out: list[str], budget: list[int], depth: int = 0) -> None:
    """Tags with their naming attributes and short escaped text, depth first, until the budget is
    spent; a run of same-shaped siblings is cut to its first few and a count."""
    if budget[0] <= 0 or not isinstance(node.tag, str):
        return
    tag = _tag(node)
    if tag in DROPPED_TAGS:
        return
    attrs = "".join(
        f' {name}="{html_escape.escape(_short(str(node.get(name)), ATTR_CHARS), quote=True)}"'
        for name in KEPT_ATTRIBUTES
        if node.get(name)
    )
    text = html_escape.escape(_short(node.text or "", TEXT_CHARS), quote=False)
    line = f"{'  ' * min(depth, 12)}<{tag}{attrs}>{text}"
    out.append(line)
    budget[0] -= len(line) + 1
    shapes: dict[tuple[str, str | None], int] = {}
    for child in node:
        if not isinstance(child.tag, str):
            continue  # comments and processing instructions: never shown
        shape = (str(child.tag), child.get("class"))
        shapes[shape] = shapes.get(shape, 0) + 1
        if shapes[shape] <= SAME_SIBLINGS:
            _outline(child, out, budget, depth + 1)
    for (child_tag, _), count in shapes.items():
        if count > SAME_SIBLINGS and budget[0] > 0:
            more = f"{'  ' * min(depth + 1, 12)}[+{count - SAME_SIBLINGS} <{_tag(child_tag)}> cùng dạng]"
            out.append(more)
            budget[0] -= len(more) + 1


def _json_outline(value: Any, depth: int = 0) -> Any:
    if depth > 6:
        return "…"
    if isinstance(value, dict):
        return {
            str(k)[:ATTR_CHARS]: _json_outline(v, depth + 1) for k, v in list(value.items())[:40]
        }
    if isinstance(value, list):
        head = [_json_outline(v, depth + 1) for v in value[:SAME_SIBLINGS]]
        return head + (
            [f"[+{len(value) - SAME_SIBLINGS} cùng dạng]"] if len(value) > SAME_SIBLINGS else []
        )
    if isinstance(value, str):
        return _short(value, TEXT_CHARS)
    return value


def excerpt(content: bytes, content_type: str) -> str | None:
    """A bounded outline of the page for the model, or None when it cannot be read as HTML, XML
    or JSON. Deterministic: the same page always gives the same excerpt."""
    kind = content_kind(content_type)
    try:
        if kind == "json":
            text = json.dumps(_json_outline(json.loads(content)), ensure_ascii=False, indent=1)
            return text[:EXCERPT_CHARS]
        if kind == "xml":
            root = etree.fromstring(content, parser=etree.XMLParser(resolve_entities=False))
        elif kind == "html":
            root = html.fromstring(extract.html_text(content, content_type))
        else:
            return None
    except (ValueError, etree.XMLSyntaxError):
        return None
    body = root.find("body") if kind == "html" and root.find("body") is not None else root
    out: list[str] = []
    _outline(body, out, [EXCERPT_CHARS])
    return "\n".join(out)[:EXCERPT_CHARS]


def split(page_ids: list[uuid.UUID]) -> tuple[list[uuid.UUID], list[uuid.UUID], bool]:
    """(shown, held out, disjoint). Half the sample at most is shown, up to SHOWN_PAGES; a source
    of one page is tested on that page, of which the model saw only a bounded excerpt."""
    if len(page_ids) < 2:
        return list(page_ids), list(page_ids), False
    shown = min(SHOWN_PAGES, len(page_ids) // 2)
    return list(page_ids[:shown]), list(page_ids[shown:]), True


@dataclass
class Sample:
    page_id: uuid.UUID
    content: bytes
    content_type: str


def request_payload(
    *,
    request_id: uuid.UUID,
    source: dict[str, Any],
    run_id: uuid.UUID,
    pages: list[Sample],
    fields: list[dict[str, Any]],
    drift_id: uuid.UUID | None = None,
    from_version_id: uuid.UUID | None = None,
    hints: str | None = None,
) -> dict[str, Any] | None:
    """The acquire.schema.inference_requested payload, or None when no page can be shown."""
    readable = [p for p in pages if content_kind(p.content_type) is not None]
    shown_ids, held_out, disjoint = split([p.page_id for p in readable])
    by_id = {p.page_id: p for p in readable}
    shown = []
    for page_id in shown_ids:
        page = by_id[page_id]
        text = excerpt(page.content, page.content_type)
        if text:
            shown.append(
                {
                    "page_id": str(page_id),
                    "kind": content_kind(page.content_type),
                    "excerpt": text,
                }
            )
    if not shown:
        return None
    return {
        "request_id": str(request_id),
        "source_id": str(source["id"]),
        "data_space_id": str(source["data_space_id"]),
        "run_id": str(run_id),
        "drift_id": str(drift_id) if drift_id else None,
        "from_version_id": str(from_version_id) if from_version_id else None,
        "goal": _short(str(source.get("goal") or ""), 500),
        "hints": _short(hints, 4_000) if hints else None,
        "content_kind": shown[0]["kind"],
        "pages": shown,
        "held_out_page_ids": [str(p) for p in held_out],
        "held_out_disjoint": disjoint,
        # With an approved version the model only finds these fields again (a remap).
        "fields": [
            {
                "name": f["name"],
                "type": f.get("type") or "text",
                "required": bool(f.get("required")),
                "is_key": bool(f.get("key") or f.get("is_key")),
                "unit": f.get("unit"),
            }
            for f in fields
        ],
    }


# ---------------------------------------------------------------- drift (BR-152)


@dataclass
class Drift:
    severity: str  # minor, major
    completeness: decimal.Decimal
    signals: dict[str, Any]


def drift(
    fields: list[dict[str, Any]],
    records: list[extract.Record],
    pages_read: int,
    pages_with_records: int,
) -> Drift | None:
    """The run's structural drift from its schema version, or None. Completeness is the share of
    required cells that came out with a value at all (a wrong-looking value is the review queue's
    business, not a change of structure); below MIN_ACCURACY, the bar the version was approved at,
    the page no longer reads as the version expects."""
    if pages_read == 0 or not fields:
        return None
    required = [f for f in fields if f.get("required")] or fields
    keys = [f for f in fields if f.get("key") or f.get("is_key")] or [fields[0]]
    cells = len(records) * len(required)
    present = sum(r.values.get(f["name"]) is not None for r in records for f in required)
    completeness = (
        (decimal.Decimal(present) / decimal.Decimal(cells)).quantize(decimal.Decimal("0.0001"))
        if cells
        else decimal.Decimal(0)
    )
    missing = [
        f["name"]
        for f in required
        if records and sum(r.values.get(f["name"]) is None for r in records) * 2 > len(records)
    ]
    keys_lost = [f["name"] for f in keys if not records or f["name"] in missing]
    if records and completeness >= MIN_ACCURACY:
        return None
    major = not records or bool(keys_lost) or completeness < MAJOR_BELOW
    return Drift(
        "major" if major else "minor",
        completeness,
        {
            "required_completeness": str(completeness),
            "missing_fields": missing,
            "key_fields_lost": keys_lost,
            "records": len(records),
            "pages_read": pages_read,
            "pages_without_records": pages_read - pages_with_records,
        },
    )


def hitl_payload(
    *,
    request_id: uuid.UUID,
    source: dict[str, Any],
    drift_id: uuid.UUID,
    title: str,
    risk: str,
    context: dict[str, Any],
) -> dict[str, Any]:
    """hitl.requested for the source's owner, who approves its schema versions (BR-151)."""
    return {
        "hitl_request_id": str(request_id),
        "data_space_id": str(source["data_space_id"]),
        "kind": "schema_drift",
        "risk": risk,
        "title": title,
        "context": {k: str(v) if isinstance(v, uuid.UUID) else v for k, v in context.items()},
        "options": ["approved_new", "reverted", "manual_fix"],
        "source_service": "acquire",
        "source_ref": str(drift_id),
        "created_by": None,
        "assignee_id": str(source["owner_id"]),
        "assignee_group": None,
        "assignee_capability": None,
        "approval_steps": [],
        "excluded_approver_ids": [],
        "due_at": None,
        "parent_run_label": None,
    }


def emit(
    session: AsyncSession,
    org_id: uuid.UUID,
    table: str,
    row_id: uuid.UUID,
    kind: str,
    payload: dict[str, Any],
) -> None:
    session.add(
        acquire.Outbox(
            id=uuid7(),
            org_id=org_id,
            aggregate_type=table,
            aggregate_id=row_id,
            event_type=kind,
            payload={k: str(v) if isinstance(v, uuid.UUID) else v for k, v in payload.items()},
        )
    )


# ---------------------------------------------------------------- the draft, checked


class InvalidDraft(ValueError):
    pass


def _expression(value: Any, kind: str, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidDraft(f"{what} is empty")
    expr: str = value.strip()
    if len(expr) > MAX_SELECTOR or _CONTROL.search(expr):
        raise InvalidDraft(f"{what} is too long or has control characters")
    if kind == "json":
        if not _JSON_PATH.match(expr):
            raise InvalidDraft(f"{what} {expr!r} is not a $.a.b[*] path")
        return expr
    try:
        # Compiled only: evaluating it is extract's job, on stored pages. cssselect raises
        # SelectorSyntaxError (a SyntaxError) or, for a pseudo-class it cannot translate,
        # ExpressionError (a RuntimeError).
        if expr.startswith(("/", "(", "./")):
            etree.XPath(expr)
        else:
            CSSSelector(expr)
    except (etree.XPathError, SyntaxError, ValueError, RuntimeError) as exc:
        raise InvalidDraft(f"{what} {expr!r} does not parse: {str(exc)[:100]}") from exc
    return expr


def _number(value: Any) -> decimal.Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return decimal.Decimal(str(value))
    except decimal.InvalidOperation as exc:
        raise InvalidDraft(f"{value!r} is not a number") from exc


def checked_draft(
    draft: Any, kind: str, approved_fields: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]], list[str]]:
    """(record_selector, fields, names dropped) of a draft that may be tested, or InvalidDraft.

    With an approved version (`approved_fields`) a field keeps everything of the approved one but
    its locator: a name it does not have is dropped, and a required field missing from the draft
    makes it invalid. Without one, every field is named, typed and, when numeric, bounded."""
    if not isinstance(draft, dict):
        raise InvalidDraft("the draft is not an object")
    selector = _expression(draft.get("record_selector"), kind, "record_selector")
    proposed = draft.get("fields")
    if not isinstance(proposed, list) or not proposed or len(proposed) > MAX_FIELDS:
        raise InvalidDraft(f"the draft needs 1 to {MAX_FIELDS} fields")
    approved = {f["name"]: f for f in approved_fields}
    fields: list[dict[str, Any]] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for item in proposed:
        if not isinstance(item, dict):
            raise InvalidDraft("a field is not an object")
        name = item.get("name")
        if not isinstance(name, str) or not _NAME.match(name):
            raise InvalidDraft(f"field name {str(name)[:40]!r} is not snake_case")
        if name in seen:
            raise InvalidDraft(f"field {name} is proposed twice")
        seen.add(name)
        locator = _expression(item.get("locator"), kind, f"locator of {name}")
        if approved:
            if name not in approved:
                dropped.append(name)
                continue
            fields.append({**approved[name], "locator": locator})
            continue
        kind_of = item.get("type")
        if kind_of not in TYPES:
            raise InvalidDraft(f"field {name} has type {str(kind_of)[:20]!r}")
        field: dict[str, Any] = {
            "name": name,
            "type": kind_of,
            "required": item.get("required") is True,
            "is_key": item.get("is_key") is True,
            "locator": locator,
        }
        unit = item.get("unit")
        if unit is not None:
            if not isinstance(unit, str) or len(unit) > 40 or _CONTROL.search(unit):
                raise InvalidDraft(f"unit of {name} is not a short text")
            field["unit"] = unit
        constraints = item.get("value_constraints") or {}
        if not isinstance(constraints, dict):
            raise InvalidDraft(f"value_constraints of {name} is not an object")
        low, high = _number(constraints.get("min")), _number(constraints.get("max"))
        if low is not None and high is not None and low > high:
            raise InvalidDraft(f"field {name} has min above max")
        if kind_of in NUMERIC_DOMAIN and (low is None or high is None or not unit):
            # An active version must bound it (CHECK value_constraints_ok): unbounded, the draft
            # could never be approved, so it is not proposed.
            raise InvalidDraft(f"numeric field {name} needs min, max and unit")
        if low is not None or high is not None:
            field["value_constraints"] = {
                **({"min": float(low)} if low is not None else {}),
                **({"max": float(high)} if high is not None else {}),
                **({"unit": unit} if unit else {}),
            }
        fields.append(field)
    if approved:
        lost = [n for n, f in approved.items() if f.get("required") and n not in seen]
        if lost:
            raise InvalidDraft("required fields not found again: " + ", ".join(lost))
    elif not any(f["required"] for f in fields):
        # With nothing required every cell of every field counts, so text fields that read
        # anything would pass the test: a proposal says what a record cannot go without.
        raise InvalidDraft("no field is required")
    if not fields:
        raise InvalidDraft("no field of the approved version is in the draft")
    return selector, fields, dropped


# ---------------------------------------------------------------- the draft, tested


def version_id_for(request_id: str) -> uuid.UUID:
    return uuid.uuid5(_VERSION_NS, request_id)


def hitl_request_id(drift_id: uuid.UUID) -> uuid.UUID:
    """One Cần bạn request per drift, whichever step opens it."""
    return uuid.uuid5(_VERSION_NS, f"hitl:{drift_id}")


async def _record(
    session: AsyncSession,
    org_id: uuid.UUID,
    source: acquire.CrawlSource,
    drift_row: acquire.SchemaDrift | None,
    outcome: dict[str, Any],
) -> None:
    """Why a proposal went where it went: on the drift, when there is one, and as an event."""
    if drift_row is not None:
        drift_row.signals = {**(drift_row.signals or {}), "proposal": outcome}
    emit(
        session,
        org_id,
        "crawl_source",
        source.id,
        INFERRED,
        {
            "source_id": source.id,
            "data_space_id": source.data_space_id,
            "drift_id": drift_row.id if drift_row else None,
            **outcome,
        },
    )


async def handle_draft(event: Event, store: RawStore) -> None:
    """acquire.schema.draft_proposed: check the model's draft, test it on the pages kept back,
    and keep it as a version: `testing` (open to approval) at MIN_ACCURACY, `rejected` below."""
    from services.crawler import runs, schema_tests  # runs imports this module

    org_id, p = event.org_id, event.payload
    request_id = str(p.get("request_id") or event.event_id)
    version_id = version_id_for(request_id)
    source_id = uuid.UUID(str(p["source_id"]))
    drift_id = uuid.UUID(str(p["drift_id"])) if p.get("drift_id") else None
    from_id = uuid.UUID(str(p["from_version_id"])) if p.get("from_version_id") else None
    held_out = [uuid.UUID(str(x)) for x in p.get("held_out_page_ids") or []]
    async with scoped_session(ROLE, org_id) as session:
        if await session.get(acquire.CrawlSchemaVersion, version_id) is not None:
            return  # a redelivered draft: its version is already there
        source = await session.get(acquire.CrawlSource, source_id)
        if source is None:
            return
        approved = await session.get(acquire.CrawlSchemaVersion, from_id) if from_id else None
        approved_fields = list(approved.fields or []) if approved is not None else []
        reading = await ingest_profile.load(session, source.data_space_id, source.ingest_profile)
    base = {"request_id": request_id, "model": p.get("model")}
    checked: tuple[str, list[dict[str, Any]], list[str]] | None = None
    why = None
    if not p.get("draft"):
        why = {"outcome": "no_draft", "reason": str(p.get("reason") or "the model gave no draft")}
    else:
        try:
            checked = checked_draft(
                p["draft"], str(p.get("content_kind") or "html"), approved_fields
            )
        except InvalidDraft as exc:
            why = {"outcome": "invalid", "reason": str(exc)[:300]}
    accuracy, records = decimal.Decimal(0), 0
    if checked is not None:
        selector, fields, _ = checked
        pages = await runs.stored_pages(org_id, source_id, store, held_out) if held_out else []
        found: list[extract.Record] = []
        for page in pages:
            try:
                found += extract.extract(
                    page.content,
                    page.content_type,
                    selector,
                    fields,
                    reading.decimal_mark,
                    reading.date_order,
                )
            except extract.ExtractionError as exc:
                log.info("proposal %s: page %s not extracted: %s", request_id, page.id, exc)
        accuracy, records = schema_tests.required_accuracy(found, fields), len(found)
    async with scoped_session(ROLE, org_id) as session:
        source = await session.get(acquire.CrawlSource, source_id, with_for_update=True)
        assert source is not None
        drift_row = (
            await session.get(acquire.SchemaDrift, drift_id, with_for_update=True)
            if drift_id
            else None
        )
        if checked is None:
            await _record(session, org_id, source, drift_row, {**base, **(why or {})})
            log.info("proposal %s for source %s not tested: %s", request_id, source_id, why)
            return
        selector, fields, dropped = checked
        passed = records > 0 and accuracy >= MIN_ACCURACY
        number = (
            await session.scalar(
                select(func.max(acquire.CrawlSchemaVersion.version)).where(
                    acquire.CrawlSchemaVersion.source_id == source_id
                )
            )
            or 0
        ) + 1
        session.add(
            acquire.CrawlSchemaVersion(
                id=version_id,
                org_id=org_id,
                source_id=source_id,
                version=number,
                # testing with its accuracy is what :approve reads; rejected is never approvable.
                status="testing" if passed else "rejected",
                record_selector=selector,
                fields=fields,
                sample_page_ids=held_out,
                test_required_accuracy=accuracy,
                parent_version_id=from_id,
            )
        )
        await session.flush()
        outcome = {
            **base,
            "outcome": "proposed" if passed else "below_accuracy",
            "schema_version_id": str(version_id),
            "version": number,
            "test_required_accuracy": str(accuracy),
            "records": records,
            "held_out_pages": len(held_out),
            "held_out_disjoint": bool(p.get("held_out_disjoint")),
            "dropped_fields": dropped,
        }
        if not passed:
            outcome["reason"] = (
                f"{accuracy:.2%} of required fields on {len(held_out)} held-out pages; "
                f"{MIN_ACCURACY:.0%} is needed (BR-151)"
            )
        if passed and drift_row is not None and drift_row.resolution is None:
            drift_row.proposed_version_id = version_id
            if drift_row.severity == "minor":
                # A major drift asked its owner when it was found; a minor one only now, when
                # there is a tested version to approve (BR-152).
                emit(
                    session,
                    org_id,
                    "schema_drift",
                    drift_row.id,
                    HITL_REQUESTED,
                    hitl_payload(
                        request_id=hitl_request_id(drift_row.id),
                        source={
                            "data_space_id": source.data_space_id,
                            "owner_id": source.owner_id,
                        },
                        drift_id=drift_row.id,
                        title=f"Duyệt lược đồ mới cho nguồn «{source.name}» (đạt {accuracy:.0%})",
                        risk="medium",
                        context={
                            "source_id": source.id,
                            "drift_id": drift_row.id,
                            "proposed_version_id": version_id,
                            "test_required_accuracy": str(accuracy),
                        },
                    ),
                )
        await _record(session, org_id, source, drift_row, outcome)
    log.info("proposal %s for source %s: %s", request_id, source_id, outcome["outcome"])
