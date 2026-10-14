"""Hand-written routines of the core schemas (qa, governance, agent, catalog, eval, workspace, monitor,
document) and the D5.4 acceptance list of tab 3: every rule the design says the database refuses."""

from __future__ import annotations

import pytest
from psycopg2.extras import Json

from tests.integration.migrate._ddl import as_admin, as_role, fails, new_id, new_org, one

pytestmark = pytest.mark.integration

CHECK, UNIQUE, NOT_NULL, DENIED = "23514", "23505", "23502", "42501"


def _insert(table: str, org, cols: dict) -> tuple[str, tuple, object]:
    cols = {"id": new_id(), **cols, "org_id": org}
    values = tuple(
        str(v) if hasattr(v, "hex") and not isinstance(v, bytes | str) else v for v in cols.values()
    )
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
    return sql, values, cols["id"]


def ins(db, table: str, org, **cols):
    sql, values, row_id = _insert(table, org, cols)
    with db.cursor() as cur:
        cur.execute(sql, values)
    return row_id


def refused(db, table: str, org, sqlstate: str = CHECK, **cols) -> str:
    sql, values, _ = _insert(table, org, cols)
    return fails(db, sql, values, code=sqlstate)


def run(db, sql: str, params: tuple = ()) -> None:
    with db.cursor() as cur:
        cur.execute(
            sql,
            tuple(
                str(p) if hasattr(p, "hex") and not isinstance(p, bytes | str) else p
                for p in params
            ),
        )


def events(db, schema: str, event_type: str, aggregate_id) -> list[dict]:
    with db.cursor() as cur:
        cur.execute(
            f"SELECT payload FROM {schema}.outbox WHERE event_type = %s AND aggregate_id = %s ORDER BY created_at",
            (event_type, str(aggregate_id)),
        )
        return [r[0] for r in cur.fetchall()]


# ─── qa: number check, answer receipts, replays (BR-171, BR-175, BR-176, BR-179) ───


def _answer(db, org, **kw):
    conv = ins(db, "qa.conversation", org, data_space_id=new_id(), user_id=new_id())
    question = ins(db, "qa.question", org, conversation_id=conv, seq=1, text="Giá xăng E5RON92?")
    plan = ins(db, "qa.plan", org, question_id=question, chips=Json([]))
    answer = ins(
        db, "qa.answer", org, question_id=question, plan_id=plan, text="21.000 đ/lít", **kw
    )
    return answer, plan


def _in_scope_answer(db, org):
    product = new_id()
    answer, plan = _answer(db, org, product_scope="in_scope", data_product_id=product)
    return answer, plan, product


def _receipt(db, org, answer, plan, product, **kw):
    return ins(
        db,
        "qa.answer_receipt",
        org,
        answer_id=answer,
        receipt_no="BN-00000000-000000",
        data_product_id=product,
        data_product_code="gia_xang",
        data_product_version=1,
        plan_id=plan,
        answered_at="2026-07-02T08:00:00Z",
        knowledge_mode="latest",
        compiler_version="semcomp 1.8.2",
        runtime_version="runtime 3.0",
        **kw,
    )


def _figure(db, org, receipt, key, masked=False):
    state = (
        {
            "value": None,
            "display_state": "masked_stale",
            "stale_note": "dữ liệu đến 25/06/2026",
            "freshness_state": "overdue",
        }
        if masked
        else {"value": 21000, "display_state": "shown", "freshness_state": "fresh"}
    )
    return ins(
        db,
        "qa.answer_receipt_figure",
        org,
        receipt_id=receipt,
        figure_key=key,
        label="Giá E5RON92",
        source_kind="query_run",
        query_run_id=new_id(),
        compiled_sql_sha256="a" * 64,
        **state,
    )


def test_answer_with_an_untraceable_number_is_refused(db):
    org = new_org(db)
    _answer(db, org, number_check=Json({"numbers_total": 3, "traced": 3, "untraceable_final": 0}))
    conv = ins(db, "qa.conversation", org, data_space_id=new_id(), user_id=new_id())
    q = ins(db, "qa.question", org, conversation_id=conv, seq=1, text="?")
    plan = ins(db, "qa.plan", org, question_id=q, chips=Json([]))
    refused(
        db,
        "qa.answer",
        org,
        question_id=q,
        plan_id=plan,
        text="x",
        number_check=Json({"numbers_total": 3, "untraceable_final": 1}),
    )


def test_in_scope_answer_must_carry_numbers(db):
    org = new_org(db)
    conv = ins(db, "qa.conversation", org, data_space_id=new_id(), user_id=new_id())
    q = ins(db, "qa.question", org, conversation_id=conv, seq=1, text="?")
    plan = ins(db, "qa.plan", org, question_id=q, chips=Json([]))
    refused(
        db,
        "qa.answer",
        org,
        question_id=q,
        plan_id=plan,
        text="x",
        kind="document",
        product_scope="in_scope",
        data_product_id=new_id(),
    )
    ins(
        db,
        "qa.answer",
        org,
        question_id=q,
        plan_id=plan,
        text="x",
        kind="data",
        product_scope="in_scope",
        data_product_id=new_id(),
    )


def test_receipt_only_for_an_in_scope_answer_and_is_numbered_per_day(db):
    org = new_org(db)
    out_answer, plan = _answer(db, org, product_scope="out_of_scope")
    fails(
        db,
        *_insert(
            "qa.answer_receipt",
            org,
            {
                "answer_id": out_answer,
                "receipt_no": "BN-00000000-000000",
                "data_product_id": new_id(),
                "data_product_code": "x",
                "data_product_version": 1,
                "plan_id": plan,
                "answered_at": "2026-07-02T08:00:00Z",
                "knowledge_mode": "latest",
                "compiler_version": "c",
                "runtime_version": "r",
            },
        )[:2],
        code=CHECK,
    )
    a1, p1, prod1 = _in_scope_answer(db, org)
    a2, p2, prod2 = _in_scope_answer(db, org)
    # Another product than the answer's is refused too.
    fails(
        db,
        *_insert(
            "qa.answer_receipt",
            org,
            {
                "answer_id": a1,
                "receipt_no": "BN-00000000-000000",
                "data_product_id": new_id(),
                "data_product_code": "x",
                "data_product_version": 1,
                "plan_id": p1,
                "answered_at": "2026-07-02T08:00:00Z",
                "knowledge_mode": "latest",
                "compiler_version": "c",
                "runtime_version": "r",
            },
        )[:2],
        code=CHECK,
    )
    r1 = _receipt(db, org, a1, p1, prod1)
    r2 = _receipt(db, org, a2, p2, prod2)
    assert (
        one(db, "SELECT receipt_no FROM qa.answer_receipt WHERE id = %s", (str(r1),))
        == "BN-20260702-000001"
    )
    assert (
        one(db, "SELECT receipt_no FROM qa.answer_receipt WHERE id = %s", (str(r2),))
        == "BN-20260702-000002"
    )


def test_receipt_cannot_be_sealed_at_creation(db):
    org = new_org(db)
    a, p, prod = _in_scope_answer(db, org)
    fails(
        db,
        *_insert(
            "qa.answer_receipt",
            org,
            {
                "answer_id": a,
                "receipt_no": "BN-00000000-000000",
                "data_product_id": prod,
                "data_product_code": "x",
                "data_product_version": 1,
                "plan_id": p,
                "answered_at": "2026-07-02T08:00:00Z",
                "knowledge_mode": "latest",
                "compiler_version": "c",
                "runtime_version": "r",
                "sealed_at": "2026-07-02T08:00:01Z",
                "receipt_sha256": "c" * 64,
            },
        )[:2],
        code=CHECK,
    )


def test_sealed_receipt_is_append_only(db):
    org = new_org(db)
    a, p, prod = _in_scope_answer(db, org)
    receipt = _receipt(db, org, a, p, prod)
    _figure(db, org, receipt, "F1")
    _figure(db, org, receipt, "F2", masked=True)
    as_role(db, "svc_qa", org)
    run(db, "UPDATE qa.answer_receipt SET sealed_at = now() WHERE id = %s", (receipt,))
    count, masked, sha = one(
        db,
        "SELECT figure_count, masked_count, receipt_sha256 FROM qa.answer_receipt WHERE id = %s",
        (str(receipt),),
    )
    assert (count, masked) == (2, 1) and len(sha) == 64
    # The digest does not depend on the reader's TimeZone (a ?verify runs in another session).
    run(db, "SET LOCAL TimeZone = 'Asia/Ho_Chi_Minh'")
    assert (
        one(
            db,
            "SELECT qa.receipt_digest(r) FROM qa.answer_receipt r WHERE r.id = %s",
            (str(receipt),),
        )
        == sha
    )
    # Figure after the seal, a second seal, any other column (column grant), editing a figure.
    fails(
        db,
        *_insert(
            "qa.answer_receipt_figure",
            org,
            {
                "receipt_id": receipt,
                "figure_key": "F3",
                "label": "x",
                "value": 1,
                "display_state": "shown",
                "source_kind": "query_run",
                "query_run_id": new_id(),
                "compiled_sql_sha256": "a" * 64,
                "freshness_state": "fresh",
            },
        )[:2],
        code=CHECK,
    )
    fails(
        db,
        "UPDATE qa.answer_receipt SET sealed_at = now() WHERE id = %s",
        (str(receipt),),
        code=CHECK,
    )
    fails(
        db,
        "UPDATE qa.answer_receipt SET compiler_version = 'x' WHERE id = %s",
        (str(receipt),),
        code=DENIED,
    )
    fails(
        db,
        "UPDATE qa.answer_receipt_figure SET value = 1 WHERE receipt_id = %s",
        (str(receipt),),
        code=DENIED,
    )
    fails(
        db,
        "DELETE FROM qa.answer_receipt_figure WHERE receipt_id = %s",
        (str(receipt),),
        code=DENIED,
    )
    # Even the database admin cannot edit a receipt or its numbers.
    as_admin(db)
    fails(
        db,
        "UPDATE qa.answer_receipt SET runtime_version = 'x' WHERE id = %s",
        (str(receipt),),
        code=CHECK,
    )
    fails(
        db,
        "UPDATE qa.answer_receipt_figure SET label = 'x' WHERE receipt_id = %s",
        (str(receipt),),
        code=CHECK,
    )


def test_receipt_is_deleted_only_past_retain_until(db):
    org = new_org(db)
    a, p, prod = _in_scope_answer(db, org)
    kept = _receipt(db, org, a, p, prod)
    _figure(db, org, kept, "F1")
    a2, p2, prod2 = _in_scope_answer(db, org)
    expired = _receipt(db, org, a2, p2, prod2, retain_until="2020-01-01")
    _figure(db, org, expired, "F1")
    as_role(db, "svc_qa", org)
    fails(db, "DELETE FROM qa.answer_receipt WHERE id = %s", (str(kept),), code=CHECK)
    run(db, "DELETE FROM qa.answer_receipt WHERE id = %s", (expired,))
    as_admin(db)
    assert (
        one(
            db,
            "SELECT count(*) FROM qa.answer_receipt_figure WHERE receipt_id = %s",
            (str(expired),),
        )
        == 0
    )
    assert (
        one(db, "SELECT count(*) FROM qa.answer_receipt_figure WHERE receipt_id = %s", (str(kept),))
        == 1
    )


def test_receipt_figure_masking_rules(db):
    org = new_org(db)
    a, p, prod = _in_scope_answer(db, org)
    receipt = _receipt(db, org, a, p, prod)
    base = {
        "receipt_id": receipt,
        "label": "x",
        "source_kind": "query_run",
        "query_run_id": new_id(),
        "compiled_sql_sha256": "a" * 64,
    }
    # A number shown although its data is overdue; a masked number without a note (BR-178).
    refused(
        db,
        "qa.answer_receipt_figure",
        org,
        figure_key="F1",
        value=1,
        display_state="shown",
        freshness_state="overdue",
        **base,
    )
    refused(
        db,
        "qa.answer_receipt_figure",
        org,
        figure_key="F2",
        display_state="masked_stale",
        freshness_state="overdue",
        **base,
    )


def test_receipt_is_visible_only_with_its_answer(db):
    org, other = new_org(db), new_org(db)
    a, p, prod = _in_scope_answer(db, other)
    hidden = _receipt(db, org, a, p, prod)  # answer of another organisation: cannot be seen with it
    a2, p2, prod2 = _in_scope_answer(db, org)
    shown = _receipt(db, org, a2, p2, prod2)
    _figure(db, org, hidden, "F1")
    _figure(db, org, shown, "F1")
    for role in ("svc_qa", "svc_agent"):
        as_role(db, role, org)
        assert one(db, "SELECT array_agg(id::text) FROM qa.answer_receipt") == [str(shown)]
        assert one(db, "SELECT count(*) FROM qa.answer_receipt_figure") == 1
        as_admin(db)


def test_replay_events_and_identical_with_differences(db):
    org = new_org(db)
    a, p, prod = _in_scope_answer(db, org)
    receipt = _receipt(db, org, a, p, prod)
    replay = ins(
        db,
        "qa.receipt_replay",
        org,
        receipt_id=receipt,
        requested_by=new_id(),
        compiler_version_used="semcomp 1.8.2",
    )
    audit = events(db, "qa", "audit.requested", replay)
    assert audit and audit[0]["action"] == "receipt.replay"
    fails(
        db,
        "UPDATE qa.receipt_replay SET status = 'completed', outcome = 'identical', differs_count = 1, "
        "finished_at = now() WHERE id = %s",
        (str(replay),),
        code=CHECK,
    )
    run(
        db,
        "UPDATE qa.receipt_replay SET status = 'completed', outcome = 'differs', differs_count = 1, "
        "finished_at = now() WHERE id = %s",
        (replay,),
    )
    assert len(events(db, "qa", "qa.receipt_replay.mismatch", replay)) == 1
    other = ins(
        db,
        "qa.receipt_replay",
        org,
        receipt_id=receipt,
        requested_by=new_id(),
        compiler_version_used="c",
    )
    run(
        db,
        "UPDATE qa.receipt_replay SET status = 'completed', outcome = 'identical', finished_at = now() "
        "WHERE id = %s",
        (other,),
    )
    assert events(db, "qa", "qa.receipt_replay.mismatch", other) == []
    as_role(db, "svc_qa", org)
    assert one(db, "SELECT count(*) FROM qa.receipt_replay") == 2


# ─── governance: model providers and routing decisions (BR-180, BR-181) ───


def _provider(db, org, kind="on_prem", **kw):
    cols = {
        "code": f"p-{new_id().hex[:8]}",
        "display_name": "Provider",
        "kind": kind,
        "endpoint": "http://vllm:8000",
    }
    if kind == "frontier":
        cols.update(
            endpoint="https://api.frontier.example",
            secret_ref="kv/data-agent/providers/frontier-a",
            data_region="ap-southeast-1",
            max_classification="public",
        )
    cols.update(kw)
    return ins(db, "governance.model_provider", org, **cols)


def _enabled_frontier(db, org, **kw):
    return _provider(
        db,
        org,
        "frontier",
        zero_retention=True,
        enabled=True,
        enabled_by=new_id(),
        approved_by=new_id(),
        approved_at="2026-07-01T00:00:00Z",
        **kw,
    )


def _routing(db, org, tasks=("answer_writing",)):
    ins(
        db,
        "governance.ai_setting",
        org,
        key="frontier_routing",
        value=Json({"enabled": True, "tasks": list(tasks)}),
    )


def _decision(**kw):
    cols = {
        "task": "answer_writing",
        "caller_kind": "question",
        "caller_id": new_id(),
        "product_scope": "out_of_scope",
        "context_labels": ["public"],
        "max_classification": "public",
        "contains_internal_data": False,
        "decision": "frontier_allowed",
        "reasons": ["allowed_out_of_scope"],
        "model_name": "model-x",
        "prompt_sha256": "b" * 64,
    }
    cols.update(kw)
    return cols


def test_model_config_route_role_follows_provider(db):
    org = new_org(db)
    onprem, frontier = _provider(db, org), _provider(db, org, "frontier")
    base = {"version": 1, "model_name": "m", "endpoint": "e"}
    refused(
        db,
        "governance.model_config",
        org,
        task="sql_generation",
        provider_id=frontier,
        route_role="frontier_candidate",
        **base,
    )
    refused(
        db,
        "governance.model_config",
        org,
        task="answer_writing",
        provider_id=frontier,
        route_role="primary",
        **base,
    )
    refused(
        db,
        "governance.model_config",
        org,
        task="answer_writing",
        provider_id=onprem,
        route_role="frontier_candidate",
        **base,
    )
    ins(
        db,
        "governance.model_config",
        org,
        task="answer_writing",
        provider_id=frontier,
        route_role="frontier_candidate",
        **base,
    )
    ins(db, "governance.model_config", org, task="planning", provider_id=onprem, **base)


def test_frontier_provider_enabling_rules(db):
    org = new_org(db)
    user = new_id()
    frontier = {
        "code": "frontier-a",
        "display_name": "A",
        "kind": "frontier",
        "endpoint": "https://api.a.example",
        "secret_ref": "kv/a",
        "data_region": "ap-southeast-1",
        "max_classification": "public",
    }
    refused(
        db,
        "governance.model_provider",
        org,
        enabled=True,
        zero_retention=False,
        enabled_by=user,
        approved_by=new_id(),
        approved_at="2026-07-01T00:00:00Z",
        **frontier,
    )
    refused(
        db,
        "governance.model_provider",
        org,
        enabled=True,
        zero_retention=True,
        enabled_by=user,
        approved_by=user,
        approved_at="2026-07-01T00:00:00Z",
        **frontier,
    )
    refused(db, "governance.model_provider", org, **{**frontier, "max_classification": "internal"})


def test_frontier_provider_change_needs_new_approval_and_is_announced(db):
    org = new_org(db)
    provider = _enabled_frontier(db, org)
    as_role(db, "svc_governance", org)
    run(
        db,
        "UPDATE governance.model_provider SET endpoint = 'https://api.b.example' WHERE id = %s",
        (provider,),
    )
    as_admin(db)
    enabled, approved_by, approved_at = one(
        db,
        "SELECT enabled, approved_by, approved_at FROM governance.model_provider WHERE id = %s",
        (str(provider),),
    )
    assert (enabled, approved_by, approved_at) == (False, None, None)
    assert len(events(db, "governance", "governance.model_provider.changed", provider)) == 2
    audit = events(db, "governance", "audit.requested", provider)
    assert (
        audit[-1]["action"] == "model_provider.changed" and audit[-1]["before"]["enabled"] is True
    )


def test_only_svc_governance_reads_providers(db):
    org = new_org(db)
    _provider(db, org)
    as_role(db, "svc_governance", org)
    assert one(db, "SELECT count(*) FROM governance.model_provider") == 1
    as_admin(db)
    as_role(db, "svc_query", org)  # holds governance_read
    fails(db, "SELECT count(*) FROM governance.model_provider", code=DENIED)


def test_routing_decision_refusals(db):
    org = new_org(db)
    onprem, frontier = _provider(db, org), _enabled_frontier(db, org)
    _routing(db, org)
    table = "governance.model_route_decision"
    allowed = {"candidate_provider_id": frontier, "chosen_provider_id": frontier}
    # Internal label in scope, personal data left, an on-site provider, a task not switched on.
    refused(
        db,
        table,
        org,
        **_decision(
            product_scope="in_scope",
            context_labels=["internal"],
            max_classification="internal",
            **allowed,
        ),
    )
    refused(db, table, org, **_decision(reasons=["pii_remaining"], **allowed))
    refused(db, table, org, **_decision(candidate_provider_id=onprem, chosen_provider_id=onprem))
    refused(db, table, org, **_decision(task="planning", **allowed))
    # on_prem_default that calls frontier, frontier_blocked without a reason, a malformed prompt hash.
    refused(
        db,
        table,
        org,
        **_decision(decision="on_prem_default", reasons=[], chosen_provider_id=frontier),
    )
    refused(
        db,
        table,
        org,
        **_decision(
            decision="frontier_blocked",
            reasons=[],
            candidate_provider_id=frontier,
            chosen_provider_id=onprem,
        ),
    )
    refused(db, table, org, **_decision(prompt_sha256="not-a-hash", **allowed))
    # A disabled frontier provider.
    as_role(db, "svc_governance", org)
    run(db, "UPDATE governance.model_provider SET enabled = false WHERE id = %s", (frontier,))
    as_admin(db)
    refused(db, table, org, **_decision(**allowed))


def test_routing_decision_allowed_paths_and_audit(db):
    org = new_org(db)
    onprem, frontier = _provider(db, org), _enabled_frontier(db, org)
    _routing(db, org)
    as_role(db, "svc_governance", org)
    call = ins(
        db,
        "governance.model_route_decision",
        org,
        **_decision(candidate_provider_id=frontier, chosen_provider_id=frontier),
    )
    ins(
        db,
        "governance.model_route_decision",
        org,
        **_decision(
            decision="frontier_blocked",
            reasons=["label_not_public"],
            product_scope="in_scope",
            context_labels=["internal"],
            max_classification="internal",
            candidate_provider_id=frontier,
            chosen_provider_id=onprem,
        ),
    )
    as_admin(db)
    audit = events(db, "governance", "audit.requested", call)
    assert (
        audit[0]["action"] == "model.frontier_call"
        and audit[0]["after"]["prompt_sha256"] == "b" * 64
    )


def test_routing_decision_results_are_filled_once(db):
    org = new_org(db)
    onprem = _provider(db, org)
    decision = ins(
        db,
        "governance.model_route_decision",
        org,
        **_decision(decision="on_prem_default", reasons=[], chosen_provider_id=onprem),
    )
    as_role(db, "svc_governance", org)
    run(
        db,
        "UPDATE governance.model_route_decision SET input_tokens = 10, output_tokens = 5, outcome = 'ok' "
        "WHERE id = %s",
        (decision,),
    )
    fails(
        db,
        "UPDATE governance.model_route_decision SET input_tokens = 11 WHERE id = %s",
        (str(decision),),
        code=CHECK,
    )
    fails(
        db,
        "UPDATE governance.model_route_decision SET decision = 'frontier_blocked' WHERE id = %s",
        (str(decision),),
        code=DENIED,
    )
    fails(
        db,
        "DELETE FROM governance.model_route_decision WHERE id = %s",
        (str(decision),),
        code=DENIED,
    )
    as_admin(db)
    fails(
        db,
        "UPDATE governance.model_route_decision SET model_name = 'y' WHERE id = %s",
        (str(decision),),
        code=CHECK,
    )
    fails(
        db,
        "DELETE FROM governance.model_route_decision WHERE id = %s",
        (str(decision),),
        code=DENIED,
    )


def test_routing_decision_budget_exceeded_event(db):
    org = new_org(db)
    frontier = _enabled_frontier(
        db,
        org,
        monthly_budget_vnd=100,
        price=Json({"model-x": {"input": 1_000_000, "output": 2_000_000}}),
    )
    _routing(db, org)
    first = ins(
        db,
        "governance.model_route_decision",
        org,
        **_decision(candidate_provider_id=frontier, chosen_provider_id=frontier),
    )
    second = ins(
        db,
        "governance.model_route_decision",
        org,
        **_decision(candidate_provider_id=frontier, chosen_provider_id=frontier),
    )
    as_role(db, "svc_governance", org)
    run(
        db,
        "UPDATE governance.model_route_decision SET input_tokens = 40, output_tokens = 10, outcome = 'ok' "
        "WHERE id = %s",
        (first,),
    )  # 40 + 20 = 60 VND, under budget
    as_admin(db)
    assert events(db, "governance", "governance.model_provider.budget_exceeded", frontier) == []
    as_role(db, "svc_governance", org)
    run(
        db,
        "UPDATE governance.model_route_decision SET input_tokens = 30, output_tokens = 10, outcome = 'ok' "
        "WHERE id = %s",
        (second,),
    )  # 60 + 50 = 110 VND, crosses 100
    as_admin(db)
    exceeded = events(db, "governance", "governance.model_provider.budget_exceeded", frontier)
    assert len(exceeded) == 1 and exceeded[0]["spent_vnd"] == 110


def test_agent_runtime_reads_routing_decisions_by_caller(db):
    org = new_org(db)
    onprem = _provider(db, org)
    caller = new_id()
    ins(
        db,
        "governance.model_route_decision",
        org,
        **_decision(
            decision="on_prem_default", reasons=[], chosen_provider_id=onprem, caller_id=caller
        ),
    )
    ins(
        db,
        "governance.model_route_decision",
        org,
        **_decision(decision="on_prem_default", reasons=[], chosen_provider_id=onprem),
    )
    as_role(db, "svc_qa", org, caller_id=str(caller))
    assert one(db, "SELECT count(*) FROM governance.model_route_decision") == 1
    as_admin(db)
    as_role(db, "svc_agent", org, caller_id="")  # no caller: nothing
    assert one(db, "SELECT count(*) FROM governance.model_route_decision") == 0
    as_admin(db)
    as_role(db, "svc_query", org)
    fails(db, "SELECT count(*) FROM governance.model_route_decision", code=DENIED)


# ─── catalog: certification of consolidated dataset versions (BR-156) ───


def _consolidated_version(db, org, creator, origin="consolidated"):
    cols = {
        "data_space_id": new_id(),
        "origin": origin,
        "schema_name": "silver",
        "table_name": f"t_{new_id().hex[:8]}",
    }
    if origin == "source":
        cols["source_id"] = ins(
            db,
            "catalog.source",
            org,
            data_space_id=new_id(),
            name=f"s-{new_id().hex[:6]}",
            kind="postgresql",
            secret_ref="kv/s",
        )
    dataset = ins(db, "catalog.dataset", org, **cols)
    return ins(
        db,
        "catalog.dataset_version",
        org,
        dataset_id=dataset,
        version=1,
        schema_hash="h",
        valid_from="2026-07-01T00:00:00Z",
        created_by=creator,
    ), dataset


def _certify(db, org, version, actor, action="certify", reason=None):
    return _insert(
        "catalog.dataset_version_certification",
        org,
        {"dataset_version_id": version, "action": action, "actor_id": actor, "reason": reason},
    )[:2]


def test_dataset_version_certification_refusals(db):
    org = new_org(db)
    creator, steward = new_id(), new_id()
    version, dataset = _consolidated_version(db, org, creator)
    refused(
        db,
        "catalog.dataset_version",
        org,
        dataset_id=dataset,
        version=2,
        schema_hash="h",
        valid_from="2026-07-02T00:00:00Z",
    )  # consolidated without created_by
    refused(
        db,
        "catalog.dataset_version",
        org,
        dataset_id=dataset,
        version=3,
        schema_hash="h",
        valid_from="2026-07-02T00:00:00Z",
        created_by=creator,
        certified_by=steward,
        certified_at="2026-07-02T00:00:00Z",
    )
    fails(
        db,
        "UPDATE catalog.dataset_version SET certified_at = now(), certified_by = %s WHERE id = %s",
        (str(steward), str(version)),
        code=CHECK,
    )
    fails(
        db,
        "UPDATE catalog.dataset_version SET created_by = %s WHERE id = %s",
        (str(steward), str(version)),
        code=CHECK,
    )
    as_role(db, "svc_catalog", org)
    fails(db, *_certify(db, org, version, creator), code=CHECK)  # the creator certifies
    fails(
        db, *_certify(db, org, version, steward, "revoke", "lý do"), code=CHECK
    )  # not certified yet
    source_version, _ = _consolidated_version(db, org, creator, origin="source")
    fails(db, *_certify(db, org, source_version, steward), code=CHECK)
    run(db, *_certify(db, org, version, steward))
    fails(db, *_certify(db, org, version, steward), code=CHECK)  # second certification
    fails(db, *_certify(db, org, version, steward, "revoke"), code=CHECK)
    fails(db, *_certify(db, org, version, steward, "revoke", "   "), code=CHECK)


def test_dataset_version_certify_revoke_certify(db):
    org = new_org(db)
    creator, steward = new_id(), new_id()
    version, dataset = _consolidated_version(db, org, creator)
    as_role(db, "svc_catalog", org)
    run(db, *_certify(db, org, version, steward))
    assert (
        one(db, "SELECT certified_by FROM catalog.dataset_version WHERE id = %s", (str(version),))
        == steward
    )
    run(db, *_certify(db, org, version, steward, "revoke", "Nguồn sửa hồi tố"))
    assert (
        one(db, "SELECT certified_at FROM catalog.dataset_version WHERE id = %s", (str(version),))
        is None
    )
    run(db, *_certify(db, org, version, steward))
    as_admin(db)
    assert len(events(db, "catalog", "catalog.dataset_version.certified", version)) == 2
    (revoked,) = events(db, "catalog", "catalog.dataset_version.certification_revoked", version)
    assert revoked["dataset_id"] == str(dataset) and revoked["previous_certified_by"] == str(
        steward
    )
    as_role(db, "svc_catalog", org)
    fails(db, "UPDATE catalog.dataset_version_certification SET note = 'x'", code=DENIED)
    fails(db, "DELETE FROM catalog.dataset_version_certification", code=DENIED)


# ─── agent: depth guard (BR-77), stop reasons and loop detection (BR-170) ───


def _agent(db, org):
    return ins(
        db,
        "agent.agent_def",
        org,
        data_space_id=new_id(),
        code=f"a-{new_id().hex[:8]}",
        name="Agent",
        kind="analysis",
        owner_user_id=new_id(),
        account_user_id=new_id(),
    )


def _run(db, org, agent, **kw):
    return ins(
        db,
        "agent.agent_run",
        org,
        agent_id=agent,
        config_version=1,
        data_space_id=new_id(),
        trigger_kind="manual",
        **kw,
    )


def test_agent_run_depth_is_derived_and_capped(db):
    org, other = new_org(db), new_org(db)
    agent = _agent(db, org)
    root = _run(db, org, agent)
    child = _run(db, org, agent, parent_run_id=root, depth=0)  # the service's value is overwritten
    assert one(db, "SELECT depth FROM agent.agent_run WHERE id = %s", (str(child),)) == 1
    level2 = _run(db, org, agent, parent_run_id=child, depth=1)
    level3 = _run(db, org, agent, parent_run_id=level2, depth=1)
    assert one(db, "SELECT depth FROM agent.agent_run WHERE id = %s", (str(level3),)) == 3
    refused(
        db,
        "agent.agent_run",
        org,
        agent_id=agent,
        config_version=1,
        data_space_id=new_id(),
        trigger_kind="agent_call",
        parent_run_id=level3,
        depth=3,
    )
    foreign = _run(db, other, _agent(db, other))
    refused(
        db,
        "agent.agent_run",
        org,
        agent_id=agent,
        config_version=1,
        data_space_id=new_id(),
        trigger_kind="agent_call",
        parent_run_id=foreign,
        depth=1,
    )


def test_agent_run_stop_reason_and_loop_steps(db):
    org = new_org(db)
    agent = _agent(db, org)
    refused(
        db,
        "agent.agent_run",
        org,
        agent_id=agent,
        config_version=1,
        data_space_id=new_id(),
        trigger_kind="manual",
        status="succeeded",
    )
    refused(
        db,
        "agent.agent_run",
        org,
        agent_id=agent,
        config_version=1,
        data_space_id=new_id(),
        trigger_kind="manual",
        status="failed",
        stop_reason="gave_up",
    )
    run_id = _run(db, org, agent, status="running")
    for seq in (1, 2, 3):
        ins(
            db,
            "agent.agent_step",
            org,
            run_id=run_id,
            seq=seq,
            kind="tool_call",
            name="sql.query",
            status="ok",
            started_at="now",
            call_fingerprint="f" * 64,
            result_hash="e" * 64,
        )
    assert (
        one(
            db,
            "SELECT count(*) FROM agent.agent_step WHERE run_id = %s AND call_fingerprint = %s",
            (str(run_id), "f" * 64),
        )
        == 3
    )
    run(
        db,
        "UPDATE agent.agent_run SET status = 'failed', stop_reason = 'loop_detected', stop_detail = %s "
        "WHERE id = %s",
        (Json({"tool": "sql.query", "call_fingerprint": "f" * 64, "count": 3}), run_id),
    )
    indexes = one(
        db, "SELECT array_agg(indexname::text) FROM pg_indexes WHERE schemaname = 'agent'"
    )
    assert {"agent_run_agent_id_stop_reason_idx", "agent_step_run_id_call_fingerprint_idx"} <= set(
        indexes
    )


def test_agent_steps_land_in_month_partitions(db):
    org = new_org(db)
    run_id = _run(db, org, _agent(db, org))
    now_step = ins(
        db,
        "agent.agent_step",
        org,
        run_id=run_id,
        seq=1,
        kind="plan",
        name="p",
        status="ok",
        started_at="now",
    )
    old_step = ins(
        db,
        "agent.agent_step",
        org,
        run_id=run_id,
        seq=2,
        kind="plan",
        name="p",
        status="ok",
        started_at="2020-01-15T00:00:00Z",
    )
    month = one(db, "SELECT to_char(now() AT TIME ZONE 'UTC', 'YYYYMM')")
    where = "SELECT tableoid::regclass::text FROM agent.agent_step WHERE id = %s"
    assert one(db, where, (str(now_step),)) == f"agent.agent_step_{month}"
    assert one(db, where, (str(old_step),)) == "agent.agent_step_default"


# ─── eval: Error Book (BR-179) ───


def _entry(db, org, **kw):
    return ins(
        db,
        "eval.error_book_entry",
        org,
        data_space_id=new_id(),
        source_kind="forecast_miss",
        accuracy_log_id=new_id(),
        forecast_detail=Json({"period": "2026-07-02"}),
        title="Lệch dự báo",
        **kw,
    )


def _fixed(**kw):
    return dict(
        error_kind="data",
        root_cause="Nguồn trễ",
        assignee_id=new_id(),
        fix_kind="none",
        fixed_by=new_id(),
        **kw,
    )


def test_error_book_numbering_and_opened_event(db):
    org = new_org(db)
    first, second = _entry(db, org), _entry(db, org)
    assert (
        one(db, "SELECT entry_no FROM eval.error_book_entry WHERE id = %s", (str(first),))
        == "EB-0001"
    )
    assert (
        one(db, "SELECT entry_no FROM eval.error_book_entry WHERE id = %s", (str(second),))
        == "EB-0002"
    )
    assert events(db, "eval", "eval.error_book.opened", first)[0]["entry_no"] == "EB-0001"
    refused(
        db,
        "eval.error_book_entry",
        org,
        data_space_id=new_id(),
        source_kind="forecast_miss",
        title="x",
    )
    log = new_id()
    ins(
        db,
        "eval.error_book_entry",
        org,
        data_space_id=new_id(),
        source_kind="forecast_miss",
        accuracy_log_id=log,
        forecast_detail=Json({}),
        title="x",
    )
    refused(
        db,
        "eval.error_book_entry",
        org,
        sqlstate=UNIQUE,
        data_space_id=new_id(),
        source_kind="forecast_miss",
        accuracy_log_id=log,
        forecast_detail=Json({}),
        title="x",
    )


def test_error_book_status_machine(db):
    org = new_org(db)
    entry = _entry(db, org)
    eval_set = ins(
        db, "eval.eval_set", org, subject_type="agent", subject_id=new_id(), name="Hồi quy"
    )
    item = ins(
        db,
        "eval.eval_item",
        org,
        eval_set_id=eval_set,
        input=Json({}),
        expected=Json({}),
        origin="error_book",
        origin_ref=str(entry),
    )
    early = ins(
        db,
        "eval.eval_run",
        org,
        eval_set_id=eval_set,
        subject_version="v1",
        trigger="deploy",
        started_at="2026-07-01T00:00:00Z",
    )
    late = ins(
        db,
        "eval.eval_run",
        org,
        eval_set_id=eval_set,
        subject_version="v2",
        trigger="nightly",
        started_at="2026-07-05T00:00:00Z",
    )
    for eval_run in (early, late):
        ins(db, "eval.eval_result", org, eval_run_id=eval_run, eval_item_id=item, passed=True)
    eid = str(entry)
    fails(
        db,
        "UPDATE eval.error_book_entry SET status = 'regression_added', eval_item_id = %s, "
        "regression_run_id = %s, fixed_at = '2026-07-03', error_kind = 'data', root_cause = 'x', "
        "assignee_id = %s, fix_kind = 'none', fixed_by = %s WHERE id = %s",
        (str(item), str(late), str(new_id()), str(new_id()), eid),
        code=CHECK,
    )
    fails(
        db, "UPDATE eval.error_book_entry SET status = 'fixed' WHERE id = %s", (eid,), code=CHECK
    )  # no cause
    fixed = _fixed()
    run(
        db,
        "UPDATE eval.error_book_entry SET status = 'fixed', error_kind = %s, root_cause = %s, assignee_id = %s, "
        "fix_kind = %s, fixed_by = %s, fixed_at = '2026-07-03' WHERE id = %s",
        (
            fixed["error_kind"],
            fixed["root_cause"],
            fixed["assignee_id"],
            fixed["fix_kind"],
            fixed["fixed_by"],
            entry,
        ),
    )
    fails(db, "UPDATE eval.error_book_entry SET status = 'open' WHERE id = %s", (eid,), code=CHECK)
    fails(
        db,
        "UPDATE eval.error_book_entry SET status = 'regression_added', eval_item_id = %s, "
        "regression_run_id = %s WHERE id = %s",
        (str(item), str(early), eid),
        code=CHECK,
    )
    run(
        db,
        "UPDATE eval.error_book_entry SET status = 'regression_added', eval_item_id = %s, regression_run_id = %s "
        "WHERE id = %s",
        (item, late, entry),
    )
    as_role(db, "svc_eval", org)
    assert one(db, "SELECT eval.error_book_on_regression_failed(%s)", (str(item),)) == 1
    as_admin(db)
    assert (
        one(
            db,
            "SELECT (status, reopened_count)::text FROM eval.error_book_entry WHERE id = %s",
            (eid,),
        )
        == "(open,1)"
    )
    assert len(events(db, "eval", "eval.error_book.reopened", entry)) == 1
    other = _entry(db, org)
    fails(
        db,
        "UPDATE eval.error_book_entry SET status = 'dismissed' WHERE id = %s",
        (str(other),),
        code=CHECK,
    )
    run(
        db,
        "UPDATE eval.error_book_entry SET status = 'dismissed', dismiss_reason = 'Không phải lỗi' WHERE id = %s",
        (other,),
    )


# ─── workspace, monitor, document ───


def test_tile_positions_swap_inside_a_transaction(db):
    org = new_org(db)
    ws = ins(
        db, "workspace.workspace", org, data_space_id=new_id(), owner_id=new_id(), title="Phiên"
    )
    t1 = ins(db, "workspace.tile", org, workspace_id=ws, position=1, kind="text")
    t2 = ins(db, "workspace.tile", org, workspace_id=ws, position=2, kind="text")
    run(db, "UPDATE workspace.tile SET position = 2 WHERE id = %s", (t1,))
    run(db, "UPDATE workspace.tile SET position = 1 WHERE id = %s", (t2,))
    run(
        db, "SET CONSTRAINTS workspace.tile_workspace_id_position_key IMMEDIATE"
    )  # checks the swap now
    run(db, "SET CONSTRAINTS workspace.tile_workspace_id_position_key DEFERRED")
    ins(db, "workspace.tile", org, workspace_id=ws, position=1, kind="text")
    fails(db, "SET CONSTRAINTS workspace.tile_workspace_id_position_key IMMEDIATE", code=UNIQUE)


def test_upload_materializer_accepts_a_column_named_value(db):
    """The upload path must materialize ordinary headers without PL/pgSQL alias collisions."""
    table_name = f"upload_regression_{new_id().hex[:8]}"
    run(
        db,
        "SELECT public.da_materialize_upload(%s, %s::jsonb, %s::jsonb, true)",
        (
            table_name,
            Json([{"name": "value", "type": "numeric"}, {"name": "label", "type": "text"}]),
            Json([["12.5", "ok"], ["7", "retry"]]),
        ),
    )
    with db.cursor() as cur:
        cur.execute(
            f'SELECT "value"::text, "label" FROM workspace."{table_name}" ORDER BY "value"::numeric'
        )
        assert cur.fetchall() == [("7", "retry"), ("12.5", "ok")]


def _calendar(db, org):
    return ins(
        db,
        "monitor.external_calendar",
        org,
        data_space_id=new_id(),
        code=f"c_{new_id().hex[:8]}",
        name="Kỳ điều hành giá xăng dầu",
        owner_id=new_id(),
        recurrence_hint="FREQ=WEEKLY;BYDAY=TH",
    )


def test_external_event_bulletin_time_and_shift(db):
    org = new_org(db)
    event = ins(
        db,
        "monitor.external_event",
        org,
        calendar_id=_calendar(db, org),
        period_label="2026-11-12",
        expected_at="2026-11-12T15:00:00+07:00",
        origin="recurrence",
    )
    assert one(
        db,
        "SELECT pre_bulletin_at = '2026-11-11T20:00:00+07:00' FROM monitor.external_event WHERE id = %s",
        (str(event),),
    )
    as_role(db, "svc_monitor", org)
    run(
        db,
        "UPDATE monitor.external_event SET expected_at = '2026-11-13T15:00:00+07:00', status = 'shifted' "
        "WHERE id = %s",
        (event,),
    )
    as_admin(db)
    shifted_ok, pre_ok = one(
        db,
        "SELECT shifted_from = '2026-11-12T15:00:00+07:00', pre_bulletin_at = '2026-11-12T20:00:00+07:00' "
        "FROM monitor.external_event WHERE id = %s",
        (str(event),),
    )
    assert shifted_ok and pre_ok
    assert len(events(db, "monitor", "monitor.external_event.changed", event)) == 1
    run(
        db,
        "UPDATE monitor.external_event SET status = 'occurred', occurred_at = now() WHERE id = %s",
        (event,),
    )
    fails(
        db,
        "UPDATE monitor.external_event SET status = 'confirmed' WHERE id = %s",
        (str(event),),
        code=CHECK,
    )


def test_event_subscription_and_bulletin_rules(db):
    org = new_org(db)
    calendar = _calendar(db, org)
    refused(
        db,
        "monitor.event_subscription",
        org,
        calendar_id=calendar,
        user_id=new_id(),
        channels=["email"],
    )
    event = ins(
        db,
        "monitor.external_event",
        org,
        calendar_id=calendar,
        period_label="2026-11-12",
        expected_at="2026-11-12T15:00:00+07:00",
        origin="recurrence",
    )
    refused(
        db,
        "monitor.event_bulletin",
        org,
        event_id=event,
        kind="pre",
        status="sent",
        sent_at="2026-11-11T13:00:00Z",
    )


def test_chunk_text_has_a_full_text_index(db):
    org = new_org(db)
    collection = ins(
        db,
        "document.collection",
        org,
        data_space_id=new_id(),
        code="vb",
        name="Văn bản",
        purpose="qa",
        qdrant_collection="vb",
        embed_model="bge-m3",
    )
    document = ins(
        db,
        "document.document",
        org,
        collection_id=collection,
        sha256="d" * 64,
        media="pdf",
        file_name="nd80.pdf",
        object_key="k",
        size_bytes=10,
    )
    ins(
        db,
        "document.chunk",
        org,
        document_id=document,
        collection_id=collection,
        page_from=1,
        page_to=1,
        text="Điều 5. Giá xăng dầu được điều hành",
        tokens=8,
    )
    run(db, "SET LOCAL enable_seqscan = off")
    query = (
        "FROM document.chunk WHERE to_tsvector('simple', text) @@ plainto_tsquery('simple', 'xăng')"
    )
    assert one(db, f"SELECT count(*) {query}") == 1
    with db.cursor() as cur:
        cur.execute(f"EXPLAIN SELECT id {query}")
        assert "chunk_text_idx" in " ".join(r[0] for r in cur.fetchall())


# ─── D5.2 grants and the D5.4 acceptance list of tab 3 ───


def test_svc_period_cannot_change_published_results(db):
    org = new_org(db)
    close = ins(
        db,
        "period.period_close",
        org,
        data_space_id=new_id(),
        period_label="2026-06",
        business_date="2026-06-30",
    )
    result = ins(
        db,
        "period.published_result",
        org,
        period_close_id=close,
        metric_version_id=new_id(),
        value=1,
        report_ref="BC-01",
        sha256="s",
    )
    as_role(db, "svc_period", org)
    fails(
        db,
        "UPDATE period.published_result SET value = 2 WHERE id = %s",
        (str(result),),
        code=DENIED,
    )
    fails(db, "DELETE FROM period.published_result WHERE id = %s", (str(result),), code=DENIED)


def test_cross_schema_reads_of_svc_qa(db):
    org = new_org(db)
    as_role(db, "svc_qa", org)
    assert one(db, "SELECT count(*) FROM context.metric") == 0
    assert one(db, "SELECT count(*) FROM period.published_result") == 0
    fails(
        db,
        "INSERT INTO context.metric (id, org_id, data_space_id, code, owner_id) "
        "VALUES (gen_random_uuid(), %s, gen_random_uuid(), 'm', gen_random_uuid())",
        (str(org),),
        code=DENIED,
    )
    fails(db, "SELECT count(*) FROM pipeline.pipeline", code=DENIED)
    fails(db, "SELECT count(*) FROM context.outbox", code=DENIED)


def test_business_rules_identity_and_policy(db):
    org = new_org(db)
    # BR-147: an agent without a human owner.
    fails(
        db,
        "INSERT INTO agent.agent_def (id, org_id, data_space_id, code, name, kind, account_user_id) "
        "VALUES (gen_random_uuid(), %s, gen_random_uuid(), 'a', 'A', 'analysis', gen_random_uuid())",
        (str(org),),
        code=NOT_NULL,
    )
    refused(db, "org.app_user", org, username="agent-1", display_name="Agent", kind="agent")
    # BR-44: an emergency role without an end.
    user = ins(db, "org.app_user", org, username="u1", display_name="U")
    role = ins(db, "org.role", org, code="analyst", name="Analyst")
    refused(
        db,
        "org.user_role",
        org,
        user_id=user,
        role_id=role,
        source="emergency",
        approved_by=new_id(),
    )
    # A policy switched on before it was simulated.
    refused(
        db,
        "governance.access_policy",
        org,
        data_space_id=new_id(),
        dataset_id=new_id(),
        name="p",
        kind="row",
        condition_expr="true",
        status="active",
    )


def test_business_rules_integration_and_catalog(db):
    org = new_org(db)
    # BR-144: a client valid for more than 12 months.
    refused(
        db,
        "integration.api_client",
        org,
        name="c",
        kind="rest",
        owner_user_id=new_id(),
        keycloak_client_id="kc",
        secret_ref="kv/c",
        expires_at="2099-01-01T00:00:00Z",
    )
    # BR-103: two overlapping scans of one source.
    source = ins(
        db,
        "catalog.source",
        org,
        data_space_id=new_id(),
        name="core",
        kind="postgresql",
        secret_ref="kv/s",
    )
    ins(db, "catalog.scan_run", org, source_id=source, kind="schema", trigger="manual")
    refused(
        db,
        "catalog.scan_run",
        org,
        sqlstate=UNIQUE,
        source_id=source,
        kind="schema",
        trigger="manual",
    )
    # A derived table attached to a source.
    refused(
        db,
        "catalog.dataset",
        org,
        data_space_id=new_id(),
        origin="derived",
        source_id=source,
        schema_name="s",
        table_name="t",
    )


def test_business_rules_context_and_qa(db):
    org = new_org(db)
    metric = ins(db, "context.metric", org, data_space_id=new_id(), code="nim", owner_id=new_id())
    certified = {
        "display_name": "NIM",
        "expression": "x",
        "period_semantics": "end_of_period",
        "status": "certified",
        "certified_by": new_id(),
        "certified_at": "2026-07-01T00:00:00Z",
        "valid_from": "2026-07-01",
    }
    ins(db, "context.metric_version", org, metric_id=metric, version=1, **certified)
    refused(
        db, "context.metric_version", org, sqlstate=UNIQUE, metric_id=metric, version=2, **certified
    )
    conv = ins(db, "qa.conversation", org, data_space_id=new_id(), user_id=new_id())
    refused(db, "qa.question", org, conversation_id=conv, seq=1, text="?", knowledge_mode="at")
    refused(db, "qa.plan", org, chips=Json([]))  # owned by neither a question nor a run
    question = ins(db, "qa.question", org, conversation_id=conv, seq=2, text="?")
    plan = ins(db, "qa.plan", org, question_id=question, chips=Json([]))
    refused(db, "qa.answer", org, question_id=question, plan_id=plan, text="x", kind="forecast")


def test_business_rules_agent_inbox_monitor_notify_workspace(db):
    org = new_org(db)
    # BR-70: a tool that sends outside without a checkpoint.
    config = ins(db, "agent.agent_config", org, agent_id=_agent(db, org), version=1, goal="g")
    refused(
        db,
        "agent.agent_tool",
        org,
        agent_config_id=config,
        tool="email.send",
        permission_level="send_external",
    )
    # Rejecting a proposal without a reason.
    proposal = ins(
        db,
        "inbox.proposal",
        org,
        data_space_id=new_id(),
        kind="synonym",
        target_service="context",
        title="p",
        proposed_by=new_id(),
    )
    refused(
        db,
        "inbox.proposal_review",
        org,
        proposal_id=proposal,
        reviewer_id=new_id(),
        decision="reject",
    )
    # BR-124: closing an alert that fired three times without a note.
    monitor = ins(
        db,
        "monitor.monitor",
        org,
        data_space_id=new_id(),
        owner_id=new_id(),
        metric_id=new_id(),
        rule_kind="threshold",
        rule=Json({}),
        schedule_cron="0 8 * * *",
    )
    refused(
        db,
        "monitor.alert",
        org,
        monitor_id=monitor,
        summary="s",
        dedup_key="k",
        occurrences=3,
        status="closed",
        closed_at="2026-07-01T00:00:00Z",
    )
    # BR-127: approval requests without the in-app channel.
    refused(db, "notify.notification_pref", org, user_id=new_id(), kind="hitl", channels=["email"])
    # Publishing a session report nobody approved.
    ws = ins(db, "workspace.workspace", org, data_space_id=new_id(), owner_id=new_id(), title="w")
    refused(
        db,
        "workspace.session_report",
        org,
        workspace_id=ws,
        title="r",
        body_ref="k",
        status="published",
        published_at="2026-07-01T00:00:00Z",
    )
    # Existing tables keep their new kinds: inbox.hitl_request data_review.
    ins(
        db,
        "inbox.hitl_request",
        org,
        data_space_id=new_id(),
        kind="data_review",
        title="t",
        source_service="acquire",
        assignee_id=new_id(),
    )


def test_business_rules_document_period_pipeline_pack(db):
    org = new_org(db)
    # BR-19: a page OCR'd below 0.85 marked usable.
    collection = ins(
        db,
        "document.collection",
        org,
        data_space_id=new_id(),
        code="c",
        name="c",
        purpose="qa",
        qdrant_collection="c",
        embed_model="e",
    )
    document = ins(
        db,
        "document.document",
        org,
        collection_id=collection,
        sha256="d" * 64,
        media="pdf",
        file_name="f",
        object_key="k",
        size_bytes=1,
    )
    refused(
        db,
        "document.document_page",
        org,
        document_id=document,
        page_no=1,
        page_hash="h",
        ocr_conf=0.8,
        usable=True,
    )
    # A reconciliation "matched" beyond tolerance; a manual figure without approval (BR-133).
    close = ins(
        db,
        "period.period_close",
        org,
        data_space_id=new_id(),
        period_label="2026-06",
        business_date="2026-06-30",
    )
    refused(
        db,
        "period.period_reconciliation",
        org,
        period_close_id=close,
        item_code="i",
        snapshot_value=100,
        ledger_value=90,
        tolerance=1,
        delta=10,
        status="matched",
    )
    refused(
        db,
        "period.published_result",
        org,
        period_close_id=close,
        metric_version_id=new_id(),
        value=1,
        reported_value=2,
        manual_override=True,
        override_reason="r",
        report_ref="BC",
        sha256="s",
    )
    # BR-113: a write-back that deletes rows; BR-24: approved by the requester.
    requester = new_id()
    wb = {
        "data_space_id": new_id(),
        "source_id": new_id(),
        "target_table": "t",
        "from_dataset_id": new_id(),
        "requested_by": requester,
    }
    refused(db, "pipeline.writeback_request", org, deletes=1, **wb)
    refused(db, "pipeline.writeback_request", org, approved_by=requester, **wb)
    # A pack metric without a legal basis; applying an upgrade with open conflicts.
    pack = ins(
        db,
        "pack.industry_pack",
        org,
        code="bank",
        name="Ngân hàng",
        industry_code="K64",
        publisher="p",
        publisher_key_id="k",
    )
    versions = [
        ins(
            db,
            "pack.pack_version",
            org,
            pack_id=pack,
            version=v,
            manifest=Json({}),
            signature="s",
            min_core_version="3.0",
            object_key="k",
        )
        for v in ("1.0", "1.1")
    ]
    refused(
        db,
        "pack.pack_metric",
        org,
        pack_version_id=versions[0],
        code="car",
        name="CAR",
        expression="x",
    )
    install = ins(db, "pack.pack_install", org, data_space_id=new_id(), pack_version_id=versions[0])
    refused(
        db,
        "pack.pack_upgrade",
        org,
        pack_install_id=install,
        from_version_id=versions[0],
        to_version_id=versions[1],
        status="applied",
        conflicts_open=2,
        approved_by=new_id(),
    )


def test_consolidated_dataset_is_an_accepted_origin(db):
    org = new_org(db)
    ins(
        db,
        "catalog.dataset",
        org,
        data_space_id=new_id(),
        origin="consolidated",
        schema_name="s",
        table_name="t",
    )


# AC-03-06-04, BR-97
def test_a_skill_version_that_has_run_is_kept(db):
    org, space, owner = new_id(), new_id(), new_id()
    skill, ran, unused = new_id(), new_id(), new_id()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO skill.skill (id, org_id, data_space_id, code, name, category, owner_id)"
            " VALUES (%s, %s, %s, 'tach-chenh-lech', 'Tách chênh lệch', 'decomposition', %s)",
            (str(skill), str(org), str(space), str(owner)),
        )
        for version, number in ((ran, 1), (unused, 2)):
            cur.execute(
                "INSERT INTO skill.skill_version (id, org_id, skill_id, version, params_schema, steps)"
                " VALUES (%s, %s, %s, %s, '{}', '[]')",
                (str(version), str(org), str(skill), number),
            )
        cur.execute(
            "INSERT INTO skill.skill_run (id, org_id, skill_version_id, data_space_id, caller_kind,"
            " principal_id, params) VALUES (%s, %s, %s, %s, 'user', %s, '{}')",
            (str(new_id()), str(org), str(ran), str(space), str(owner)),
        )
    fails(db, "DELETE FROM skill.skill_version WHERE id = %s", (str(ran),), code="23514")
    with db.cursor() as cur:
        cur.execute("DELETE FROM skill.skill_version WHERE id = %s", (str(unused),))
        assert cur.rowcount == 1
