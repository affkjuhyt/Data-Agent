"""Reviews that end in another module's object: data products (UC-07-13), source authorities
(UC-07-05) and document validity (UC-05-05). Same shape as semantic_review: the module's event
opens one inbox proposal, proposal.decided applies it with the owning role.

- data_product.submitted: the product's sample questions become the items of its eval set (made
  on first submit), the set is run, and only a run at the bar (BR-175: at least `min_items` items,
  pass rate at least `min_pass_rate`) opens a data_product proposal for the owner; the product is
  pending_review meanwhile. Below the bar it stays a draft and the run says why. Accepted, the
  product is active, activated by the reviewer (who may not be its author, a CHECK).
- source_authority.drafted: a source_authority proposal; accepted, the draft is approved and the
  database supersedes the ranking it replaces (routines/conflict.sql).
- document.validity_proposed: a document_validity proposal with the change and, when the worker
  found it, the passage that says so; accepted, the document takes the validity, and the
  document it supersedes ends the day before the new one starts (AC-05-05-01).
"""

from __future__ import annotations

import datetime
import decimal
import logging
import uuid
from typing import Any

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from libs.dataagent_db import scoped_session, uuid7
from libs.dataagent_db.models import conflict, context, document
from libs.dataagent_db.models import eval as ev
from services.worker_agent import evaluation
from services.worker_agent.events import Event, Handler

logger = logging.getLogger(__name__)

PRODUCT = "data_product.submitted"
AUTHORITY = "source_authority.drafted"
VALIDITY = "document.validity_proposed"
KINDS = ("data_product", "source_authority", "document_validity")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


async def _open(event: Event, **kwargs: Any) -> uuid.UUID:
    from services.worker_agent import proposals

    return await proposals._open(event, **kwargs)


def _expected(raw: Any) -> dict[str, Any]:
    """A sample question's expected answer as an eval item expects it."""
    if isinstance(raw, dict):
        return raw
    return {"value": raw}


async def _product_set(
    event: Event, product: context.DataProduct, questions: list[dict[str, Any]], actor: uuid.UUID
) -> uuid.UUID:
    """The product's eval set, made on first submit; its items are the sample questions now."""
    async with scoped_session(evaluation.ROLE, event.org_id, actor) as session:
        set_id = product.eval_set_id
        if set_id is None:
            set_id = uuid7()
            session.add(
                ev.EvalSet(
                    id=set_id,
                    org_id=event.org_id,
                    data_space_id=product.data_space_id,
                    subject_type="context",
                    name=f"Data product {product.code}",
                    min_items=len(questions),
                    created_by=actor,
                )
            )
            await session.flush()
        await session.execute(
            update(ev.EvalItem)
            .where(ev.EvalItem.eval_set_id == set_id, ev.EvalItem.status == "active")
            .values(status="retired")
        )
        for q in questions:
            session.add(
                ev.EvalItem(
                    id=uuid7(),
                    org_id=event.org_id,
                    eval_set_id=set_id,
                    input={"question": str(q.get("text") or q.get("question") or "")},
                    expected=_expected(q.get("expected")),
                    origin="manual",
                    created_by=actor,
                )
            )
    return set_id


async def product_submitted(event: Event) -> None:
    p = event.payload
    product_id = uuid.UUID(str(p["data_product_id"]))
    async with scoped_session("svc_context", event.org_id) as session:
        product = await session.get(context.DataProduct, product_id)
        if product is None or product.status != "draft":
            return
        actor = product.updated_by or product.created_by
        questions = [q for q in (product.sample_questions or []) if q.get("expected") is not None]
    if actor is None:
        return
    set_id = await _product_set(event, product, questions, actor)
    run_id = await evaluation.start_run(
        org_id=event.org_id,
        space_id=product.data_space_id,
        user_id=actor,
        trigger="proposal",
        trigger_ref=product_id,
        subject_version=f"data_product:{product_id}:v{product.version}",
        run_id=uuid.UUID(str(p["eval_run_id"])) if p.get("eval_run_id") else None,
        eval_set_id=set_id,
    )
    assert run_id is not None  # the set exists: it was just made or kept
    summary = await evaluation.run_set(
        org_id=event.org_id, space_id=product.data_space_id, run_id=run_id, user_id=actor
    )
    bar = decimal.Decimal(str(p.get("min_pass_rate") or "0.95"))
    enough = summary.item_count >= int(p.get("min_items") or 10)
    if not enough or summary.pass_rate is None or summary.pass_rate < bar:
        async with scoped_session("svc_context", event.org_id, actor) as session:
            await session.execute(
                update(context.DataProduct)
                .where(context.DataProduct.id == product_id)
                .values(eval_set_id=set_id, updated_at=_now())
            )
        logger.info(
            "worker-agent | data product %s below the bar: %s/%s passed",
            product_id,
            summary.passed,
            summary.item_count,
        )
        return
    pid = await _open(
        event,
        data_space_id=product.data_space_id,
        kind="data_product",
        target_service="context",
        target_ref=product_id,
        title=f"Kích hoạt data product {product.name}",
        diff={
            "data_product_id": str(product_id),
            "code": product.code,
            "version": product.version,
            "eval": {
                "items": summary.item_count,
                "passed": summary.passed,
                "pass_rate": str(summary.pass_rate),
            },
            "assignee_id": str(p.get("assignee_id") or product.owner_id),
        },
        proposed_by=actor,
        eval_run_id=run_id,
    )
    async with scoped_session("svc_context", event.org_id, actor) as session:
        await session.execute(
            update(context.DataProduct)
            .where(context.DataProduct.id == product_id, context.DataProduct.status == "draft")
            .values(eval_set_id=set_id, status="pending_review", proposal_id=pid, updated_at=_now())
        )


async def authority_drafted(event: Event) -> None:
    p = event.payload
    row_id = uuid.UUID(str(p["source_authority_id"]))
    actor = uuid.UUID(str(p["drafted_by"]))
    async with scoped_session("svc_conflict", event.org_id, actor) as session:
        row = await session.get(conflict.SourceAuthority, row_id)
        if row is None or row.status != "draft":
            return
        ranking, kind, space = [str(x) for x in row.ranking], row.data_kind, row.data_space_id
    await _open(
        event,
        data_space_id=space,
        kind="source_authority",
        target_service="conflict",
        target_ref=row_id,
        title=f"Thứ tự thẩm quyền nguồn cho {kind}",
        diff={"data_kind": kind, "ranking": ranking, "active_id": p.get("active_id")},
        proposed_by=actor,
        eval_run_id=None,
    )


async def validity_proposed(event: Event) -> None:
    p = event.payload
    document_id = uuid.UUID(str(p["document_id"]))
    actor = uuid.UUID(str(p["proposed_by"]))
    change = {k: p.get(k) for k in ("effective_from", "effective_to", "supersedes_id") if k in p}
    async with scoped_session("svc_document", event.org_id, actor) as session:
        row = await session.get(document.Document, document_id)
        if row is None or row.deleted_at is not None:
            return
        title = row.title or row.file_name
        replaced = None
        if change.get("supersedes_id"):
            old = await session.get(document.Document, uuid.UUID(str(change["supersedes_id"])))
            replaced = (old.title or old.file_name) if old else None
    await _open(
        event,
        data_space_id=uuid.UUID(str(p["data_space_id"])),
        kind="document_validity",
        target_service="document",
        target_ref=document_id,
        title=(f"{title} thay thế {replaced}" if replaced else f"Hiệu lực của {title}"),
        diff={**change, "excerpt": p.get("excerpt"), "detected": bool(p.get("detected"))},
        proposed_by=actor,
        eval_run_id=None,
    )


async def apply(event: Event, proposal: dict[str, Any]) -> bool:
    """Apply a decided proposal; False when its object is gone, has moved on, or the database
    refuses the change (a rule such as BR-175): the proposal then ends apply_failed instead of
    the event coming back for ever."""
    try:
        return await _apply(event, proposal)
    except IntegrityError as exc:
        logger.info("worker-agent | proposal %s not applied: %s", proposal["id"], exc.orig)
        return False


async def _apply(event: Event, proposal: dict[str, Any]) -> bool:
    accepted = event.payload.get("status") == "accepted"
    reviewer = uuid.UUID(str(event.payload["reviewer_id"]))
    ref = proposal["target_ref"]
    requested: dict[str, Any] = next(
        (e["requested"] for e in proposal.get("evidence") or [] if "requested" in e), {}
    )
    now = _now()
    if proposal["kind"] == "data_product":
        async with scoped_session("svc_context", event.org_id, reviewer) as session:
            row = await session.get(context.DataProduct, ref, with_for_update=True)
            if row is None or row.status != "pending_review":
                return False
            if not accepted:
                row.status, row.proposal_id = "draft", None
                return True
            if row.created_by == reviewer:
                return False  # the author does not activate their own product (CHECK)
            row.status, row.activated_by, row.activated_at = "active", reviewer, now
            row.updated_by, row.updated_at = reviewer, now
            return True
    if proposal["kind"] == "source_authority":
        async with scoped_session("svc_conflict", event.org_id, reviewer) as session:
            authority = await session.get(conflict.SourceAuthority, ref, with_for_update=True)
            if authority is None or authority.status != "draft":
                return False
            if accepted:
                authority.status, authority.approved_by, authority.approved_at = (
                    "active",
                    reviewer,
                    now,
                )
                authority.updated_by, authority.updated_at = reviewer, now
            return True
    # document_validity
    if not accepted:
        return True
    async with scoped_session("svc_document", event.org_id, reviewer) as session:
        doc_row = await session.get(document.Document, ref, with_for_update=True)
        if doc_row is None or doc_row.deleted_at is not None:
            return False
        for name in ("effective_from", "effective_to"):
            if requested.get(name):
                setattr(doc_row, name, datetime.date.fromisoformat(str(requested[name])))
        if requested.get("supersedes_id"):
            old_id = uuid.UUID(str(requested["supersedes_id"]))
            doc_row.supersedes_id = old_id
            old = await session.get(document.Document, old_id, with_for_update=True)
            if old is not None and old.effective_to is None and doc_row.effective_from is not None:
                old.effective_to = doc_row.effective_from - datetime.timedelta(days=1)
                old.updated_by, old.updated_at = reviewer, now
        doc_row.updated_by, doc_row.updated_at = reviewer, now
        return True


HANDLERS: dict[str, Handler] = {
    PRODUCT: product_submitted,
    AUTHORITY: authority_drafted,
    VALIDITY: validity_proposed,
}
