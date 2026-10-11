"""Schema context: the knowledge layer (BR-161 to BR-167, BR-194) and the data products (BR-175,
BR-178) of routines/context.sql, checked against the D5.4 rows "Lớp tri thức …" and "Lượt 4 · data
product, biên nhận" of tab 3: CHECKs written as prose, the append-only grants of D5.2, the reader
row-level security (app.knowledge_reader, app.data_space_ids, app.clearance), triggers, functions."""

import pytest

from tests.integration.migrate._ddl import as_admin, as_role, fails, new_id, new_org, one

pytestmark = pytest.mark.integration

SHA = "a" * 64
TOKEN = "{{metric:lai_suat_12t|period=2026-W41}}"
BODY = "Lãi suất huy động {{claim:c1}} ở mức " + TOKEN + "."
AUTHOR, REVIEWER, CERTIFIER = new_id(), new_id(), new_id()


def _x(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(
            sql,
            tuple(
                str(p) if p is not None and not isinstance(p, str | int | bool) else p
                for p in params
            ),
        )
        return cur.rowcount


def _page(db, org, ds, slug, kind="metric", subject_kind="metric", subject_ref=""):
    page = new_id()
    subject_ref = slug if subject_ref == "" else subject_ref
    _x(
        db,
        "INSERT INTO context.knowledge_page (id, org_id, data_space_id, kind, subject_kind, subject_ref, slug, "
        "title, owner_id) VALUES (%s, %s, %s, %s, %s, %s, %s, 'Trang', %s)",
        (page, org, ds, kind, subject_kind, subject_ref, slug, AUTHOR),
    )
    return page


def _version(db, org, page, body=BODY, origin="manual", run=None, answer=None, summary=""):
    version = new_id()
    _x(
        db,
        "INSERT INTO context.knowledge_page_version (id, org_id, page_id, origin, body, summary, knowledge_run_id, "
        "source_answer_id, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (version, org, page, origin, body, summary, run, answer, AUTHOR),
    )
    return version


def _source(
    db,
    org,
    version,
    ds,
    kind="metric_version",
    ref=None,
    classification="internal",
    external=False,
    dataset_version=None,
):
    source = new_id()
    _x(
        db,
        "INSERT INTO context.knowledge_source_link (id, org_id, page_version_id, source_kind, source_ref, "
        "source_label, dataset_version_id, external, content_sha256, data_space_id, classification) "
        "VALUES (%s, %s, %s, %s, %s, 'Nguồn', %s, %s, %s, %s, %s)",
        (
            source,
            org,
            version,
            kind,
            ref or new_id(),
            dataset_version,
            external,
            SHA,
            ds,
            classification,
        ),
    )
    return source


def _claim(db, org, version, source, anchor="c1", statement="Lãi suất huy động tăng."):
    _x(
        db,
        "INSERT INTO context.knowledge_claim (id, org_id, page_version_id, anchor, statement, source_link_id, "
        "valid_from) VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, '2026-10-01')",
        (org, version, anchor, statement, source),
    )


def _figure(db, org, version, source, token=TOKEN):
    _x(
        db,
        "INSERT INTO context.knowledge_figure (id, org_id, page_version_id, token, binding_kind, metric_code, "
        "source_link_id) VALUES (gen_random_uuid(), %s, %s, %s, 'metric', 'lai_suat_12t', %s)",
        (org, version, token, source),
    )


def _status(db, version, status, **cols):
    sets = ", ".join(["status = %s"] + [f"{c} = %s" for c in cols])
    _x(
        db,
        f"UPDATE context.knowledge_page_version SET {sets} WHERE id = %s",
        (status, *cols.values(), version),
    )


def _review(db, version):
    _status(db, version, "pending_review")
    _status(db, version, "reviewed", reviewed_by=REVIEWER, reviewed_at="2026-10-11")


def _draft(db, org, page, ds, sources=(("internal", None),), **kw):
    """A complete draft: one source per (classification, data_space), claim c1 and the figure."""
    version = _version(db, org, page, **kw)
    first = None
    for classification, space in sources:
        s = _source(db, org, version, space or ds, classification=classification)
        first = first or s
    _claim(db, org, version, first)
    _figure(db, org, version, first)
    return version, first


def _publish(db, org, ds, slug="lai-suat", sources=(("internal", None),)):
    page = _page(db, org, ds, slug)
    version, source = _draft(db, org, page, ds, sources)
    _review(db, version)
    return page, version, source


def _events(db, aggregate):
    with db.cursor() as cur:
        cur.execute(
            "SELECT event_type FROM context.outbox WHERE aggregate_id = %s ORDER BY created_at, event_type",
            (str(aggregate),),
        )
        return [r[0] for r in cur.fetchall()]


def _seed(db, org, sql, params=()):
    """Write a row of another schema as admin with its triggers off (replica), then act as svc_context
    again: the rows are fixtures, the rules of their own schema are tested elsewhere."""
    as_admin(db)
    _x(db, "SET LOCAL session_replication_role = replica")
    _x(db, sql, params)
    _x(db, "SET LOCAL session_replication_role = origin")
    as_role(db, "svc_context", org, AUTHOR)


@pytest.fixture
def ctx(db):
    org, ds = new_org(db), new_id()
    as_role(db, "svc_context", org, AUTHOR)
    return db, org, ds


# ─── CHECKs written as prose ────────────────────────────────────────────────────────────────────


def test_page_kind_pairs_with_subject_and_never_a_person(ctx):
    db, org, ds = ctx
    fails(
        db,
        "INSERT INTO context.knowledge_page (id, org_id, data_space_id, kind, subject_kind, subject_ref, slug, "
        "title, owner_id) VALUES (gen_random_uuid(), %s, %s, 'entity', 'customer', 'KH01', 'khach-hang', 't', %s)",
        (str(org), str(ds), str(AUTHOR)),
        code="23514",
    )
    fails(
        db,
        "INSERT INTO context.knowledge_page (id, org_id, data_space_id, kind, subject_kind, subject_ref, slug, "
        "title, owner_id) VALUES (gen_random_uuid(), %s, %s, 'metric', 'product', 'x', 'sai-cap', 't', %s)",
        (str(org), str(ds), str(AUTHOR)),
        code="23514",
    )
    _page(db, org, ds, "ngan-hang-b", "entity", "organization", "NHB")
    _page(db, org, ds, "chu-de", "topic", "topic", None)
    # BR-194: hypothesis only with exec_publication, whose id is the subject_ref.
    _page(db, org, ds, "gia-thuyet", "hypothesis", "exec_publication", str(new_id()))
    fails(
        db,
        "INSERT INTO context.knowledge_page (id, org_id, data_space_id, kind, subject_kind, subject_ref, slug, "
        "title, owner_id) VALUES (gen_random_uuid(), %s, %s, 'hypothesis', 'metric', 'ls', 'gt-sai', 't', %s)",
        (str(org), str(ds), str(AUTHOR)),
        code="23514",
    )


def test_figure_binding_and_finding_checks(ctx):
    db, org, ds = ctx
    page = _page(db, org, ds, "lai-suat")
    version = _version(db, org, page)
    source = _source(db, org, version, ds)
    fails(
        db,
        "INSERT INTO context.knowledge_figure (id, org_id, page_version_id, token, binding_kind, metric_code, "
        "source_link_id) VALUES (gen_random_uuid(), %s, %s, '{{agg:tong|k=v}}', 'metric', 'm', %s)",
        (str(org), str(version), str(source)),
        code="23514",
    )
    fails(
        db,
        "INSERT INTO context.knowledge_figure (id, org_id, page_version_id, token, binding_kind, aggregate_code, "
        "source_link_id) VALUES (gen_random_uuid(), %s, %s, '{{agg:tong}}', 'aggregate', 'tong', %s)",
        (str(org), str(version), str(source)),
        code="23514",
    )  # aggregate without dataset_id
    fails(
        db,
        "UPDATE context.knowledge_source_link SET classification = 'secret' WHERE id = %s",
        (str(source),),
        code="23514",
    )
    run = _run(db, org, ds)
    base = (
        "INSERT INTO context.knowledge_lint_finding (id, org_id, run_id, data_space_id, page_id, kind, severity, "
        "fingerprint, status, action, action_reason, suppressed_until, resolved_by, resolved_at, page_version_id) "
        "VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, 'low', %s, %s, %s, %s, %s, %s, %s, %s)"
    )

    def finding(
        kind="orphan_page",
        status="open",
        action=None,
        reason=None,
        until=None,
        by=None,
        at=None,
        pv=None,
    ):
        return (
            str(org),
            str(run),
            str(ds),
            str(page),
            kind,
            new_id().hex,
            status,
            action,
            reason,
            until,
            by and str(by),
            at,
            pv and str(pv),
        )

    fails(db, base, finding(kind="typo"), code="23514")
    fails(db, base, finding(status="actioned", action="rewrite_freely"), code="23514")
    # Dismissing without a reason (BR-166), resolving without who and when, propose_edit without a draft.
    fails(
        db,
        base,
        finding(
            status="dismissed", action="dismiss", until="2027-01-01", by=REVIEWER, at="2026-10-11"
        ),
        code="23514",
    )
    fails(
        db,
        base,
        finding(status="dismissed", action="dismiss", reason="trùng", until="2027-01-01"),
        code="23514",
    )
    fails(
        db,
        base,
        finding(status="actioned", action="dismiss", reason="trùng", until="2027-01-01"),
        code="23514",
    )
    fails(db, base, finding(status="actioned", action="propose_edit"), code="23514")
    _x(
        db,
        base,
        finding(
            status="dismissed",
            action="dismiss",
            reason="trùng",
            until="2027-01-01",
            by=REVIEWER,
            at="2026-10-11",
        ),
    )
    _x(db, base, finding(status="actioned", action="propose_edit", pv=version))


# ─── versions: immutable, numbered, reviewed (BR-161, BR-162, BR-164, BR-167) ──────────────────────


def test_handwritten_numbers_and_malformed_placeholders_are_refused(ctx):
    db, org, ds = ctx
    page = _page(db, org, ds, "lai-suat")
    for body, summary in (
        ("Lãi suất là 4,85%.", ""),
        ("{{claim:c1}}", "sửa 3 câu"),
        ("Lãi suất {{metric:Lai Suat}}.", ""),
        ("Xem [[Trang Sai]] {", ""),
    ):
        msg = fails(
            db,
            "INSERT INTO context.knowledge_page_version (id, org_id, page_id, origin, body, summary) "
            "VALUES (gen_random_uuid(), %s, %s, 'manual', %s, %s)",
            (str(org), str(page), body, summary),
            code="23514",
        )
        assert "BR-162" in msg
    version = _version(db, org, page)
    source = _source(db, org, version, ds)
    assert "BR-162" in fails(
        db,
        "INSERT INTO context.knowledge_claim (id, org_id, page_version_id, anchor, statement, "
        "source_link_id, valid_from) VALUES (gen_random_uuid(), %s, %s, 'c1', "
        "'Tăng 0,12 điểm', %s, '2026-10-01')",
        (str(org), str(version), str(source)),
        code="23514",
    )
    _claim(db, org, version, source, statement="Tăng " + TOKEN + " điểm.")
    # The {{hyp:condition}} tag of a hypothesis page is exempt like a placeholder (BR-194).
    hyp = _page(db, org, ds, "gia-thuyet", "hypothesis", "exec_publication", str(new_id()))
    _version(db, org, hyp, body="Giả thuyết: {{hyp:condition}} {{claim:c1}}", origin="exec_publish")


def test_versions_are_numbered_hashed_and_immutable(ctx):
    db, org, ds = ctx
    page, v1, _ = _publish(db, org, ds)
    v2 = _version(db, org, page)
    assert (
        one(db, "SELECT version FROM context.knowledge_page_version WHERE id = %s", (str(v2),)) == 2
    )
    assert (
        one(
            db,
            "SELECT parent_version_id FROM context.knowledge_page_version WHERE id = %s",
            (str(v2),),
        )
        == v1
    )
    assert (
        one(
            db,
            "SELECT body_sha256 = encode(sha256(convert_to(body, 'UTF8')), 'hex') "
            "FROM context.knowledge_page_version WHERE id = %s",
            (str(v2),),
        )
        is True
    )
    assert (
        one(db, "SELECT latest_version_no FROM context.knowledge_page WHERE id = %s", (str(page),))
        == 2
    )
    fails(
        db,
        "UPDATE context.knowledge_page_version SET body = 'Khác {{claim:c1}}' WHERE id = %s",
        (str(v2),),
        code="23514",
    )
    # Two open drafts on one page.
    fails(
        db,
        "INSERT INTO context.knowledge_page_version (id, org_id, page_id, origin, body) "
        "VALUES (gen_random_uuid(), %s, %s, 'manual', 'x')",
        (str(org), str(page)),
        code="23505",
    )
    # A version is created as draft, never reviewed.
    other = _page(db, org, ds, "trang-khac")
    fails(
        db,
        "INSERT INTO context.knowledge_page_version (id, org_id, page_id, origin, body, status, reviewed_by, "
        "reviewed_at) VALUES (gen_random_uuid(), %s, %s, 'manual', 'x', 'reviewed', %s, now())",
        (str(org), str(other), str(REVIEWER)),
        code="23514",
    )
    # No DELETE for svc_context on the knowledge_ tables (BR-164).
    fails(db, "DELETE FROM context.knowledge_page_version WHERE id = %s", (str(v2),), code="42501")
    fails(db, "DELETE FROM context.knowledge_page WHERE id = %s", (str(page),), code="42501")


def test_review_transitions_and_four_eyes(ctx):
    db, org, ds = ctx
    page = _page(db, org, ds, "lai-suat")
    version, _ = _draft(db, org, page, ds)
    fails(
        db,
        "UPDATE context.knowledge_page_version SET status = 'reviewed', reviewed_by = %s, reviewed_at = now() "
        "WHERE id = %s",
        (str(REVIEWER), str(version)),
        code="23514",
    )  # draft straight to reviewed
    _status(db, version, "pending_review")
    fails(
        db,
        "UPDATE context.knowledge_page_version SET status = 'reviewed', reviewed_by = %s, reviewed_at = now() "
        "WHERE id = %s",
        (str(AUTHOR), str(version)),
        code="23514",
    )  # author reviews own version
    _status(db, version, "reviewed", reviewed_by=REVIEWER, reviewed_at="2026-10-11")
    fails(
        db,
        "UPDATE context.knowledge_page_version SET status = 'certified', certified_by = %s, certified_at = now() "
        "WHERE id = %s",
        (str(REVIEWER), str(version)),
        code="23514",
    )  # reviewer certifies
    fails(
        db,
        "UPDATE context.knowledge_page_version SET status = 'certified' WHERE id = %s",
        (str(version),),
        code="23514",
    )  # certified without a certifier
    fails(
        db,
        "UPDATE context.knowledge_page_version SET status = 'draft' WHERE id = %s",
        (str(version),),
        code="23514",
    )
    _status(db, version, "certified", certified_by=CERTIFIER, certified_at="2026-10-11")
    assert "context.knowledge_page.certified" in _events(db, version)


def test_submit_checks_claims_figures_links_and_saveback(ctx):
    db, org, ds = ctx
    page = _page(db, org, ds, "lai-suat")
    submit = "UPDATE context.knowledge_page_version SET status = 'pending_review' WHERE id = %s"
    v = _version(db, org, page)
    s = _source(db, org, v, ds)
    assert "BR-161" in fails(db, submit, (str(v),), code="23514")  # no claim
    _claim(db, org, v, s, anchor="c2")
    assert "c1" in fails(db, submit, (str(v),), code="23514")  # {{claim:c1}} without its claim
    _claim(db, org, v, s, anchor="c1")
    assert TOKEN in fails(db, submit, (str(v),), code="23514")  # placeholder not bound
    _figure(db, org, v, s)
    _status(db, v, "rejected")

    # [[slug]] must point at a page in use with a knowledge_link row (BR-167).
    v = _version(db, org, page, body=BODY + " Xem [[ngan-hang-b]].")
    s = _source(db, org, v, ds)
    _claim(db, org, v, s)
    _figure(db, org, v, s)
    assert "ngan-hang-b" in fails(db, submit, (str(v),), code="23514")
    target = _page(db, org, ds, "ngan-hang-b", "entity", "organization", "NHB")
    assert "ngan-hang-b" in fails(db, submit, (str(v),), code="23514")
    _x(
        db,
        "INSERT INTO context.knowledge_link (id, org_id, from_version_id, from_page_id, to_page_id) "
        "VALUES (gen_random_uuid(), %s, %s, %s, %s)",
        (org, v, page, target),
    )
    _status(db, v, "pending_review")
    _status(db, v, "rejected")

    # A save-back cites the answer it came from (BR-167); the answer id itself is a generated CHECK.
    answer = new_id()
    fails(
        db,
        "INSERT INTO context.knowledge_page_version (id, org_id, page_id, origin, body) "
        "VALUES (gen_random_uuid(), %s, %s, 'saveback', 'x')",
        (str(org), str(page)),
        code="23514",
    )
    v = _version(db, org, page, origin="saveback", answer=answer)
    s = _source(db, org, v, ds)
    _claim(db, org, v, s)
    _figure(db, org, v, s)
    assert "BR-167" in fails(db, submit, (str(v),), code="23514")
    a = _source(db, org, v, ds, kind="answer", ref=answer)
    assert a
    _status(db, v, "pending_review")


def test_rows_of_a_version_belong_to_it_and_to_its_draft(ctx):
    db, org, ds = ctx
    page, served, served_source = _publish(db, org, ds)
    draft = _version(db, org, page)
    s = _source(db, org, draft, ds)
    # Claim and figure citing the source of another version (BR-161).
    assert "BR-161" in fails(
        db,
        "INSERT INTO context.knowledge_claim (id, org_id, page_version_id, anchor, statement, "
        "source_link_id, valid_from) VALUES (gen_random_uuid(), %s, %s, 'c1', 'x', %s, "
        "'2026-10-01')",
        (str(org), str(draft), str(served_source)),
        code="23514",
    )
    fails(
        db,
        "INSERT INTO context.knowledge_figure (id, org_id, page_version_id, token, binding_kind, metric_code, "
        "source_link_id) VALUES (gen_random_uuid(), %s, %s, %s, 'metric', 'm', %s)",
        (str(org), str(draft), TOKEN, str(served_source)),
        code="23514",
    )
    fails(
        db,
        "INSERT INTO context.knowledge_claim (id, org_id, page_version_id, anchor, statement, source_link_id, "
        "valid_from, valid_to) VALUES (gen_random_uuid(), %s, %s, 'c1', 'x', %s, '2026-10-02', '2026-10-01')",
        (str(org), str(draft), str(s)),
        code="23514",
    )
    # Nothing is added to a version that left draft.
    for sql, params in (
        (
            "INSERT INTO context.knowledge_claim (id, org_id, page_version_id, anchor, statement, source_link_id, "
            "valid_from) VALUES (gen_random_uuid(), %s, %s, 'c9', 'x', %s, '2026-10-01')",
            (org, served, served_source),
        ),
        (
            "INSERT INTO context.knowledge_figure (id, org_id, page_version_id, token, binding_kind, metric_code, "
            "source_link_id) VALUES (gen_random_uuid(), %s, %s, '{{metric:khac}}', 'metric', 'khac', %s)",
            (org, served, served_source),
        ),
        (
            "INSERT INTO context.knowledge_source_link (id, org_id, page_version_id, source_kind, source_ref, "
            "source_label, content_sha256, data_space_id) VALUES (gen_random_uuid(), %s, %s, 'answer', "
            "gen_random_uuid(), 'x', repeat('a', 64), %s)",
            (org, served, ds),
        ),
    ):
        assert "draft" in fails(db, sql, tuple(str(p) for p in params), code="23514")
    other = _page(db, org, ds, "trang-khac")
    fails(
        db,
        "INSERT INTO context.knowledge_link (id, org_id, from_version_id, from_page_id, to_page_id) "
        "VALUES (gen_random_uuid(), %s, %s, %s, %s)",
        (str(org), str(served), str(page), str(other)),
        code="23514",
    )
    fails(
        db,
        "INSERT INTO context.knowledge_link (id, org_id, from_version_id, from_page_id, to_page_id) "
        "VALUES (gen_random_uuid(), %s, %s, %s, %s)",
        (str(org), str(draft), str(other), str(page)),
        code="23514",
    )
    _x(
        db,
        "INSERT INTO context.knowledge_link (id, org_id, from_version_id, from_page_id, to_page_id) "
        "VALUES (gen_random_uuid(), %s, %s, %s, %s)",
        (org, draft, page, other),
    )
    # exec_publication sources belong to hypothesis pages only (BR-194).
    fails(
        db,
        "INSERT INTO context.knowledge_source_link (id, org_id, page_version_id, source_kind, source_ref, "
        "source_label, content_sha256, data_space_id) VALUES (gen_random_uuid(), %s, %s, 'exec_publication', "
        "gen_random_uuid(), 'x', repeat('a', 64), %s)",
        (str(org), str(draft), str(ds)),
        code="23514",
    )
    hyp = _page(db, org, ds, "gia-thuyet", "hypothesis", "exec_publication", str(new_id()))
    hv = _version(db, org, hyp, body="{{hyp:condition}} {{claim:c1}}", origin="exec_publish")
    _source(db, org, hv, ds, kind="exec_publication")
    # Append-only: no UPDATE on claims, figures, links; sources only on the four permission columns.
    fails(
        db,
        "UPDATE context.knowledge_claim SET statement = 'khác' WHERE page_version_id = %s",
        (str(served),),
        code="42501",
    )
    fails(
        db,
        "UPDATE context.knowledge_figure SET params = '{}' WHERE page_version_id = %s",
        (str(served),),
        code="42501",
    )
    fails(db, "UPDATE context.knowledge_link SET link_kind = 'defines'", code="42501")
    fails(
        db,
        "UPDATE context.knowledge_source_link SET source_label = 'khác' WHERE id = %s",
        (str(s),),
        code="42501",
    )
    _x(
        db,
        "UPDATE context.knowledge_source_link SET classification = 'confidential', policy_updated_at = now() "
        "WHERE id = %s",
        (s,),
    )


def test_a_retired_page_takes_no_version(ctx):
    db, org, ds = ctx
    page = _page(db, org, ds, "lai-suat")
    _x(db, "UPDATE context.knowledge_page SET status = 'retired' WHERE id = %s", (page,))
    assert "retired" in fails(
        db,
        "INSERT INTO context.knowledge_page_version (id, org_id, page_id, origin, body) "
        "VALUES (gen_random_uuid(), %s, %s, 'manual', 'x')",
        (str(org), str(page)),
        code="23514",
    )


# ─── publishing and the page policy (BR-163) ───────────────────────────────────────────────────────


def test_review_serves_the_version_and_computes_the_policy(ctx):
    db, org, ds = ctx
    treasury = new_id()
    page, v1, _ = _publish(db, org, ds)
    row = one(
        db,
        "SELECT status, current_version_id, policy_state, required_data_space_ids = ARRAY[%s]::uuid[], "
        "classification FROM context.knowledge_page WHERE id = %s",
        (str(ds), str(page)),
    )
    assert row == ("active", v1, "current", True, "internal")
    assert "context.knowledge_page.published" in _events(db, v1)
    assert _events(db, page) == ["context.knowledge_page.policy_changed"]

    # A new version with a Treasury source narrows the page; the old one is superseded.
    v2, _ = _draft(db, org, page, ds, sources=(("internal", None), ("confidential", treasury)))
    _review(db, v2)
    assert (
        one(db, "SELECT status FROM context.knowledge_page_version WHERE id = %s", (str(v1),))
        == "superseded"
    )
    row = one(
        db,
        "SELECT current_version_id, classification, required_data_space_ids @> ARRAY[%s, %s]::uuid[] "
        "FROM context.knowledge_page WHERE id = %s",
        (str(ds), str(treasury), str(page)),
    )
    assert row == (v2, "confidential", True)


def test_source_permission_change_recomputes_and_invalidate_hides(ctx):
    db, org, ds = ctx
    page, _, source = _publish(db, org, ds)
    _x(
        db,
        "UPDATE context.knowledge_source_link SET classification = 'restricted', pii_column_refs = '{kh.cccd}' "
        "WHERE id = %s",
        (source,),
    )
    assert one(
        db,
        "SELECT classification, pii_column_refs FROM context.knowledge_page WHERE id = %s",
        (str(page),),
    ) == ("restricted", ["kh.cccd"])
    ref = one(
        db, "SELECT source_ref FROM context.knowledge_source_link WHERE id = %s", (str(source),)
    )
    assert one(db, "SELECT context.knowledge_policy_invalidate('metric_version', %s)", (ref,)) == 1
    assert (
        one(db, "SELECT policy_state FROM context.knowledge_page WHERE id = %s", (str(page),))
        == "pending"
    )
    assert one(db, "SELECT context.knowledge_page_policy_refresh(%s)", (str(page),)) is False
    assert (
        one(db, "SELECT policy_state FROM context.knowledge_page WHERE id = %s", (str(page),))
        == "current"
    )


def test_reader_sees_only_what_its_data_spaces_and_clearance_allow(db):
    org, funding, treasury = new_org(db), new_id(), new_id()
    as_role(db, "svc_context", org, AUTHOR)
    narrow, v_old, _ = _publish(db, org, funding, "nguon-von")
    wide, _, _ = _publish(
        db, org, funding, "treasury", sources=(("internal", None), ("internal", treasury))
    )
    draft = _version(db, org, narrow)
    _source(db, org, draft, funding)
    _status(db, draft, "rejected")
    v_new, _ = _draft(
        db, org, narrow, funding, sources=(("internal", None), ("internal", treasury))
    )
    _review(db, v_new)  # narrow now needs Treasury too; v_old is superseded with only Nguồn vốn

    def reader(spaces, clearance, role="svc_qa", knowledge_reader=""):
        # Every GUC is (re)set: set_config(..., true) lasts until the end of the transaction.
        as_admin(db)
        as_role(
            db,
            role,
            org,
            REVIEWER,
            data_space_ids="" if spaces is None else "{" + ",".join(str(s) for s in spaces) + "}",
            clearance="" if clearance is None else str(clearance),
            knowledge_reader=knowledge_reader,
        )

    count = "SELECT count(*) FROM context.knowledge_page"
    reader([funding], 1)
    assert one(db, count) == 0  # both pages now need Treasury
    # The superseded version with only Nguồn vốn sources stays readable only if its page is visible.
    assert one(db, "SELECT count(*) FROM context.knowledge_page_version") == 0
    reader([funding, treasury], 1)
    assert one(db, count) == 2
    assert (
        one(db, "SELECT count(*) FROM context.knowledge_page_version WHERE id = %s", (str(v_old),))
        == 1
    )
    assert (
        one(
            db,
            "SELECT count(*) FROM context.knowledge_page_version WHERE status = 'draft' "
            "OR status = 'rejected'",
        )
        == 0
    )  # drafts and rejected versions are never read
    assert (
        one(
            db,
            "SELECT count(*) FROM context.knowledge_claim WHERE page_version_id = %s",
            (str(draft),),
        )
        == 0
    )
    assert (
        one(
            db,
            "SELECT count(*) FROM context.knowledge_source_link WHERE page_version_id = %s",
            (str(draft),),
        )
        == 0
    )
    reader([funding, treasury], None)
    assert one(db, count) == 0  # no clearance set
    reader(None, 3)
    assert one(db, count) == 0  # no Data Spaces set
    # A source turned confidential hides the page from clearance 1, not from clearance 2.
    as_admin(db)
    as_role(db, "svc_context", org, AUTHOR)
    _x(
        db,
        "UPDATE context.knowledge_source_link SET classification = 'confidential' WHERE page_version_id = "
        "(SELECT current_version_id FROM context.knowledge_page WHERE id = %s) AND data_space_id = %s",
        (wide, treasury),
    )
    reader([funding, treasury], 1)
    assert one(db, count) == 1
    reader([funding, treasury], 2)
    assert one(db, count) == 2
    # A page waiting for its policy is hidden.
    as_admin(db)
    as_role(db, "svc_context", org, AUTHOR)
    _x(db, "UPDATE context.knowledge_page SET policy_state = 'pending' WHERE id = %s", (wide,))
    reader([funding, treasury], 3)
    assert one(db, count) == 1
    # Readers do not write and do not see the agent's runs.
    fails(
        db,
        "INSERT INTO context.knowledge_page (id, org_id, data_space_id, kind, subject_kind, slug, title, "
        "owner_id) VALUES (gen_random_uuid(), %s, %s, 'topic', 'topic', 'x-y', 'x', %s)",
        (str(org), str(funding), str(AUTHOR)),
        code="42501",
    )
    fails(db, "SELECT count(*) FROM context.knowledge_run", code="42501")
    fails(db, "SELECT count(*) FROM context.knowledge_lint_finding", code="42501")
    # svc_context reads as a reader when the api says so, and sees everything as the writer otherwise.
    reader(None, None, role="svc_context", knowledge_reader="on")
    assert one(db, count) == 0
    assert one(db, "SELECT count(*) FROM context.knowledge_run") == 0
    reader([funding, treasury], 3, role="svc_context", knowledge_reader="on")
    assert one(db, count) == 1
    reader(None, None, role="svc_context")
    assert one(db, count) == 2


# ─── runs, lint, decertification (BR-165, BR-166) ──────────────────────────────────────────────────


def _run(db, org, ds, limit=20, status="writing"):
    run = new_id()
    _x(
        db,
        "INSERT INTO context.knowledge_run (id, org_id, data_space_id, kind, trigger, status, page_limit) "
        "VALUES (%s, %s, %s, 'ingest', 'event', %s, %s)",
        (run, org, ds, status, limit),
    )
    return run


def test_run_counts_pages_within_its_limit_and_while_running(ctx):
    db, org, ds = ctx
    run = _run(db, org, ds, limit=1)
    _version(db, org, _page(db, org, ds, "trang-mot"), run=run)
    assert (
        one(db, "SELECT pages_changed FROM context.knowledge_run WHERE id = %s", (str(run),)) == 1
    )
    second = _page(db, org, ds, "trang-hai")
    assert "page_limit" in fails(
        db,
        "INSERT INTO context.knowledge_page_version (id, org_id, page_id, origin, body, "
        "knowledge_run_id) VALUES (gen_random_uuid(), %s, %s, 'ingest', 'x', %s)",
        (str(org), str(second), str(run)),
        code="23514",
    )
    _x(
        db,
        "UPDATE context.knowledge_run SET status = 'completed', finished_at = now() WHERE id = %s",
        (run,),
    )
    fails(
        db,
        "INSERT INTO context.knowledge_page_version (id, org_id, page_id, origin, body, knowledge_run_id) "
        "VALUES (gen_random_uuid(), %s, %s, 'ingest', 'x', %s)",
        (str(org), str(second), str(run)),
        code="23514",
    )


def test_lint_findings_act_on_pages_and_stay_quiet_once_dismissed(ctx):
    db, org, ds = ctx
    page, _, _ = _publish(db, org, ds)
    run = _run(db, org, ds)
    ins = (
        "INSERT INTO context.knowledge_lint_finding (id, org_id, run_id, data_space_id, page_id, kind, severity, "
        "fingerprint) VALUES (%s, %s, %s, %s, %s, 'expired_claim', 'medium', %s)"
    )
    f1 = new_id()
    _x(db, ins, (f1, org, run, ds, page, "fp-1"))
    assert _events(db, f1) == ["context.knowledge_finding.opened"]
    _x(
        db,
        "UPDATE context.knowledge_lint_finding SET status = 'actioned', action = 'mark_stale' WHERE id = %s",
        (f1,),
    )
    assert one(
        db, "SELECT status, stale_reason FROM context.knowledge_page WHERE id = %s", (str(page),)
    ) == ("stale", "expired_claim")
    fails(
        db,
        "UPDATE context.knowledge_lint_finding SET action = 'retire_page' WHERE id = %s",
        (str(f1),),
        code="23514",
    )
    _x(
        db,
        "UPDATE context.knowledge_lint_finding SET status = 'resolved', resolved_by = %s, resolved_at = now() "
        "WHERE id = %s",
        (REVIEWER, f1),
    )
    assert "context.knowledge_finding.resolved" in _events(db, f1)

    f2 = new_id()
    _x(db, ins, (f2, org, run, ds, page, "fp-2"))
    _x(
        db,
        "UPDATE context.knowledge_lint_finding SET status = 'dismissed', action = 'dismiss', action_reason = "
        "'Đã xác nhận', suppressed_until = now() + interval '90 days', resolved_by = %s, resolved_at = now() "
        "WHERE id = %s",
        (REVIEWER, f2),
    )
    assert "BR-166" in fails(
        db, ins, (str(new_id()), str(org), str(run), str(ds), str(page), "fp-2"), code="23514"
    )
    # Once the hiding period is over the same fingerprint may be reported again.
    _seed(
        db,
        org,
        "UPDATE context.knowledge_lint_finding SET suppressed_until = now() - interval '1 day' "
        "WHERE id = %s",
        (f2,),
    )
    _x(db, ins, (new_id(), org, run, ds, page, "fp-2"))


def test_certification_needs_certified_external_dataset_and_revocation_suspends(ctx):
    db, org, ds = ctx
    dataset, dv = new_id(), new_id()
    _seed(
        db,
        org,
        "INSERT INTO catalog.dataset (id, org_id, data_space_id, origin, schema_name, table_name, "
        "created_by) VALUES (%s, %s, %s, 'consolidated', 'dw', 'lai_suat', %s)",
        (dataset, org, ds, AUTHOR),
    )
    _seed(
        db,
        org,
        "INSERT INTO catalog.dataset_version (id, org_id, dataset_id, version, schema_hash, valid_from, "
        "created_by) VALUES (%s, %s, %s, 1, 'h', now(), %s)",
        (dv, org, dataset, AUTHOR),
    )
    page = _page(db, org, ds, "lai-suat")
    version = _version(db, org, page)
    source = _source(
        db, org, version, ds, kind="dataset_version", ref=dv, external=True, dataset_version=dv
    )
    _claim(db, org, version, source)
    _figure(db, org, version, source)
    _review(db, version)
    assert (
        one(
            db, "SELECT has_external_source FROM context.knowledge_page WHERE id = %s", (str(page),)
        )
        is True
    )
    certify = (
        "UPDATE context.knowledge_page_version SET status = 'certified', certified_by = %s, "
        "certified_at = now() WHERE id = %s"
    )
    assert "BR-165" in fails(db, certify, (str(CERTIFIER), str(version)), code="23514")
    _seed(
        db,
        org,
        "UPDATE catalog.dataset_version SET certified_at = now(), certified_by = %s WHERE id = %s",
        (CERTIFIER, dv),
    )
    _x(db, certify, (CERTIFIER, version))

    assert one(db, "SELECT context.knowledge_on_dataset_decertified(%s)", (str(dv),)) == 1
    assert (
        one(
            db,
            "SELECT certification_suspended FROM context.knowledge_page WHERE id = %s",
            (str(page),),
        )
        is True
    )
    assert (
        one(
            db,
            "SELECT kind || ':' || severity FROM context.knowledge_lint_finding WHERE page_id = %s",
            (str(page),),
        )
        == "source_decertified:high"
    )
    assert (
        one(
            db, "SELECT findings_count FROM context.knowledge_run WHERE source_ref = %s", (str(dv),)
        )
        == 1
    )
    # A second revocation does not report the same finding twice.
    assert one(db, "SELECT context.knowledge_on_dataset_decertified(%s)", (str(dv),)) == 1
    assert (
        one(
            db,
            "SELECT count(*) FROM context.knowledge_lint_finding WHERE page_id = %s",
            (str(page),),
        )
        == 1
    )


# ─── data products (BR-175, BR-178, BR-180) ────────────────────────────────────────────────────────


def _product(db, org, ds, code="gia_xang_dau"):
    product = new_id()
    _x(
        db,
        "INSERT INTO context.data_product (id, org_id, data_space_id, code, name, description, sample_questions, "
        "period_grain, period_from, owner_id, steward_id, freshness_statement, eval_set_id, created_by) VALUES "
        "(%s, %s, %s, %s, 'Giá xăng dầu', 'Giá bán lẻ', (SELECT jsonb_agg(i) FROM generate_series(1, 10) i), "
        "'event', '2024-01-01', %s, %s, 'Cập nhật trước 16:30', gen_random_uuid(), %s)",
        (product, org, ds, code, AUTHOR, REVIEWER, AUTHOR),
    )
    return product


def _member(db, org, product, kind, ref, role="primary", **cols):
    member = new_id()
    names = "".join(f", {c}" for c in cols)
    marks = "".join(", %s" for _ in cols)
    _x(
        db,
        f"INSERT INTO context.data_product_member (id, org_id, data_product_id, member_kind, member_ref, role{names}) "
        f"VALUES (%s, %s, %s, %s, %s, %s{marks})",
        (member, org, product, kind, ref, role, *cols.values()),
    )
    return member


def _metric(db, org, ds, status="certified"):
    metric = new_id()
    _x(
        db,
        "INSERT INTO context.metric (id, org_id, data_space_id, code, owner_id, current_version) "
        "VALUES (%s, %s, %s, %s, %s, 1)",
        (metric, org, ds, "m_" + metric.hex[:8], AUTHOR),
    )
    _x(
        db,
        "INSERT INTO context.metric_version (id, org_id, metric_id, version, display_name, expression, "
        "period_semantics, status, certified_by, certified_at, valid_from) VALUES (gen_random_uuid(), %s, %s, 1, "
        "'Giá', 'avg(gia)', 'end_of_period', %s, %s, now(), now())",
        (org, metric, status, CERTIFIER if status == "certified" else None),
    )
    return metric


def _dataset(db, org, ds, history="snapshot"):
    dataset = new_id()
    _seed(
        db,
        org,
        "INSERT INTO catalog.dataset (id, org_id, data_space_id, origin, schema_name, table_name, "
        "history_kind) VALUES (%s, %s, %s, 'derived', 'dw', %s, %s)",
        (dataset, org, ds, "t_" + dataset.hex[:8], history),
    )
    return dataset


ACTIVATE = (
    "UPDATE context.data_product SET status = 'active', activated_by = %s, activated_at = now() "
    "WHERE id = %s"
)


def test_activation_needs_certified_metric_and_traceable_primary_dataset(ctx):
    db, org, ds = ctx
    product = _product(db, org, ds)
    _member(db, org, product, "metric", _metric(db, org, ds, status="draft"))
    assert "BR-175" in fails(db, ACTIVATE, (str(REVIEWER), str(product)), code="23514")
    _member(db, org, product, "metric", _metric(db, org, ds))
    assert "BR-175" in fails(
        db, ACTIVATE, (str(REVIEWER), str(product)), code="23514"
    )  # no primary dataset
    flat = _member(
        db,
        org,
        product,
        "dataset",
        _dataset(db, org, ds, history="none"),
        sla_kind="max_age",
        sla_max_age="1 day",
    )
    assert "thời điểm" in fails(db, ACTIVATE, (str(REVIEWER), str(product)), code="23514")
    _x(db, "DELETE FROM context.data_product_member WHERE id = %s", (flat,))
    _member(
        db, org, product, "dataset", _dataset(db, org, ds), sla_kind="max_age", sla_max_age="1 day"
    )
    fails(
        db, ACTIVATE, (str(AUTHOR), str(product)), code="23514"
    )  # the author activates own product
    _x(db, ACTIVATE, (REVIEWER, product))
    assert "context.data_product.changed" in _events(db, product)

    fails(
        db,
        "UPDATE context.data_product SET code = 'ma_moi' WHERE id = %s",
        (str(product),),
        code="23514",
    )
    _x(
        db,
        "UPDATE context.data_product SET freshness_statement = 'Cập nhật trước 17:00' WHERE id = %s",
        (product,),
    )
    assert one(db, "SELECT version FROM context.data_product WHERE id = %s", (str(product),)) == 2
    _x(db, "UPDATE context.data_product SET name = 'Tên khác' WHERE id = %s", (product,))
    assert one(db, "SELECT version FROM context.data_product WHERE id = %s", (str(product),)) == 2


def test_members_drive_classification_and_guard_the_commitment(ctx):
    db, org, ds = ctx
    product = _product(db, org, ds)
    metric = _metric(db, org, ds)
    _member(db, org, product, "metric", metric)
    primary = _member(
        db,
        org,
        product,
        "dataset",
        _dataset(db, org, ds),
        sla_kind="max_age",
        sla_max_age="1 day",
        classification="public",
        classification_confirmed_by=CERTIFIER,
    )
    reference = _member(
        db,
        org,
        product,
        "knowledge_page",
        new_id(),
        role="reference",
        classification="confidential",
    )
    assert (
        one(db, "SELECT classification FROM context.data_product WHERE id = %s", (str(product),))
        == "confidential"
    )
    _x(db, ACTIVATE, (REVIEWER, product))
    fails(
        db, "DELETE FROM context.data_product_member WHERE id = %s", (str(primary),), code="23514"
    )
    _x(db, "DELETE FROM context.data_product_member WHERE id = %s", (reference,))
    assert (
        one(db, "SELECT classification FROM context.data_product WHERE id = %s", (str(product),))
        == "internal"
    )

    assert (
        one(db, "SELECT context.data_product_on_member_decertified('metric', %s)", (str(metric),))
        == 1
    )
    assert (
        one(
            db,
            "SELECT status || ':' || suspended_reason FROM context.data_product WHERE id = %s",
            (str(product),),
        )
        == "suspended:metric_decertified"
    )
    _x(db, "UPDATE context.data_product SET status = 'retired' WHERE id = %s", (product,))
    assert "retired" in fails(
        db,
        "INSERT INTO context.data_product_member (id, org_id, data_product_id, member_kind, "
        "member_ref, role) VALUES (gen_random_uuid(), %s, %s, 'metric', gen_random_uuid(), "
        "'reference')",
        (str(org), str(product)),
        code="23514",
    )


def test_freshness_follows_the_schedule_and_the_work_calendar(ctx):
    db, org, ds = ctx
    product = _product(db, org, ds)
    # Thursdays 16:30 Hà Nội time, 30 minutes of grace (the fuel price example).
    member = _member(
        db,
        org,
        product,
        "dataset",
        new_id(),
        sla_kind="schedule",
        sla_cron="30 16 * * 4",
        sla_grace="30 minutes",
    )
    refresh = "SELECT context.data_product_freshness_refresh(%s, %s::timestamptz)"
    assert (
        one(db, refresh, (str(member), "2026-10-08 16:45+07")) == "missing"
    )  # within grace: last Thursday counts
    assert (
        one(
            db,
            "SELECT expected_as_of = '2026-10-01 00:00+07' AND next_due_at = '2026-10-08 16:30+07' "
            "FROM context.data_product_member WHERE id = %s",
            (str(member),),
        )
        is True
    )
    _x(
        db,
        "UPDATE context.data_product_member SET last_as_of = '2026-10-01 00:00+07' WHERE id = %s",
        (member,),
    )
    assert one(db, refresh, (str(member), "2026-10-08 16:45+07")) == "fresh"
    assert one(db, refresh, (str(member), "2026-10-08 17:05+07")) == "overdue"
    assert "context.data_product.freshness_changed" in _events(db, member)

    # A holiday on Thursday moves the deadline to Friday.
    calendar = new_id()
    _seed(
        db,
        org,
        "INSERT INTO org.work_calendar (id, org_id, code, name) VALUES (%s, %s, 'vn', 'Lịch VN')",
        (calendar, org),
    )
    _seed(
        db,
        org,
        "INSERT INTO org.work_calendar_day (id, org_id, calendar_id, day, kind) "
        "VALUES (gen_random_uuid(), %s, %s, '2026-10-08', 'holiday')",
        (org, calendar),
    )
    _x(
        db,
        "UPDATE context.data_product_member SET sla_work_calendar_id = %s WHERE id = %s",
        (calendar, member),
    )
    assert one(db, refresh, (str(member), "2026-10-08 17:05+07")) == "fresh"
    assert (
        one(
            db,
            "SELECT next_due_at = '2026-10-09 16:30+07' FROM context.data_product_member WHERE id = %s",
            (str(member),),
        )
        is True
    )

    # max_age: fresh while the data is not older than max age plus grace.
    aged = _member(
        db,
        org,
        product,
        "dataset",
        new_id(),
        sla_kind="max_age",
        sla_max_age="1 day",
        last_as_of="2026-10-07 00:00+07",
    )
    assert one(db, refresh, (str(aged), "2026-10-07 23:00+07")) == "fresh"
    assert one(db, refresh, (str(aged), "2026-10-08 01:00+07")) == "overdue"


def test_freshness_can_follow_an_external_event_calendar(ctx):
    db, org, ds = ctx
    calendar = new_id()
    _seed(
        db,
        org,
        "INSERT INTO monitor.external_calendar (id, org_id, data_space_id, code, name, owner_id, "
        "recurrence_hint) VALUES (%s, %s, %s, 'fuel_price_regulation', 'Kỳ điều hành', %s, 'thursday')",
        (calendar, org, ds, AUTHOR),
    )
    for day in ("2026-09-25 15:00+07", "2026-10-02 15:00+07", "2026-10-16 15:00+07"):
        _seed(
            db,
            org,
            "INSERT INTO monitor.external_event (id, org_id, calendar_id, period_label, expected_at, "
            "origin) VALUES (gen_random_uuid(), %s, %s, %s, %s, 'recurrence')",
            (org, calendar, day[:10], day),
        )
    product = _product(db, org, ds)
    member = _member(
        db,
        org,
        product,
        "dataset",
        new_id(),
        sla_kind="schedule",
        sla_cron="30 16 * * *",
        sla_event_calendar_code="fuel_price_regulation",
        last_as_of="2026-10-02 00:00+07",
    )
    assert (
        one(
            db,
            "SELECT context.data_product_freshness_refresh(%s, '2026-10-10 12:00+07')",
            (str(member),),
        )
        == "fresh"
    )
    assert (
        one(
            db,
            "SELECT expected_as_of = '2026-10-02 00:00+07' AND next_due_at = '2026-10-16 16:30+07' "
            "FROM context.data_product_member WHERE id = %s",
            (str(member),),
        )
        is True
    )
    # The definer helper never reads another organisation's calendar.
    as_role(db, "svc_context", new_id(), AUTHOR)
    assert (
        one(
            db,
            "SELECT count(*) FROM context.data_product_calendar_events(%s, 'fuel_price_regulation')",
            (str(ds),),
        )
        == 0
    )
