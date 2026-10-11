"""Schema exec (P16, the executive vault, D4.24): owner-only RLS, the worker's column grants, the
admin paths and the triggers of routines/exec.sql, checked against the D5.4 row "Lượt 4 · lớp riêng
lãnh đạo (BR-188 đến BR-195)"."""

import pytest

from tests.integration.migrate._ddl import as_admin, as_role, fails, new_id, new_org, one

pytestmark = pytest.mark.integration

# A whole UTC hour two days ahead (R-35) and the byte lengths the CHECKs demand.
DUE = "date_trunc('hour', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' + interval '2 days'"


def _bytes(n: int, fill: str = "01") -> str:
    return f"decode(repeat('{fill}', {n}), 'hex')"


def _vault(db, org, owner, *, wraps=("passkey_prf", "recovery_code")):
    vault = new_id()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO exec.vault (id, org_id, owner_id, box_public_key, box_private_key_ct, transit_key_name) "
            f"VALUES (%s, %s, %s, {_bytes(32)}, {_bytes(72, '02')}, %s)",
            (str(vault), str(org), str(owner), f"exec-cond-{org}-{owner}"),
        )
    for kind in wraps:
        _wrap(db, org, vault, owner, kind)
    return vault


def _wrap(db, org, vault, owner, kind):
    wrap = new_id()
    credential = "NULL" if kind == "recovery_code" else f"decode('{wrap.hex}', 'hex')"
    kdf = "argon2id" if kind == "recovery_code" else "hkdf_sha256"
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO exec.vault_key_wrap (id, org_id, vault_id, owner_id, kind, credential_id, label, kdf, "
            f"kdf_salt, wrapped_vmk) VALUES (%s, %s, %s, %s, %s, {credential}, 'k', %s, {_bytes(32)}, {_bytes(72)})",
            (str(wrap), str(org), str(vault), str(owner), kind, kdf),
        )
    return wrap


THESIS_SQL = (
    "INSERT INTO exec.thesis (id, org_id, vault_id, owner_id, content_ct, wrapped_dek, cond_ct, "
    f"cond_key_version, due_at) VALUES (%s, %s, %s, %s, {_bytes(41)}, {_bytes(72)}, {_bytes(512)}, 1, {{due}})"
)


def _thesis(db, org, vault, owner, due=DUE):
    thesis = new_id()
    with db.cursor() as cur:
        cur.execute(THESIS_SQL.format(due=due), (str(thesis), str(org), str(vault), str(owner)))
    return thesis


def _setup(db):
    """An organisation with one owner, an unlockable vault and an open thesis, all as admin."""
    org, owner = new_org(db), new_id()
    vault = _vault(db, org, owner)
    return org, owner, vault, _thesis(db, org, vault, owner)


def _execute(db, sql, params=()):
    with db.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def _evidence_sql(thesis, org, owner, batch, sides):
    rows = ", ".join(
        f"(gen_random_uuid(), '{org}', '{thesis}', '{owner}', {batch}, '{side}', '{kind}', "
        f"{'NULL' if kind != 'evidence' else chr(39) + 'strong' + chr(39)}, true, {_bytes(49)}, gen_random_uuid())"
        for side, kind in sides
    )
    return (
        "INSERT INTO exec.thesis_evidence (id, org_id, thesis_id, owner_id, batch_no, side, item_kind, strength, "
        f"freshness_ok, sealed_ct, agent_run_id) VALUES {rows}"
    )


# ─── exec.vault, exec.vault_key_wrap ───


def test_vault_is_owner_only_and_the_admin_overview_has_five_columns(db):
    org, owner, vault, _ = _setup(db)
    other = new_id()
    as_role(db, "svc_exec", org, other)
    assert one(db, "SELECT count(*) FROM exec.vault") == 0
    as_role(db, "svc_exec", org, owner)
    assert one(db, "SELECT count(*) FROM exec.vault") == 1
    fails(db, "DELETE FROM exec.vault", code="42501")
    # AC05/AC06 (UC-16-06): only through the function, and only with app.exec_admin = 'on'.
    as_admin(db)
    as_role(db, "svc_exec", org, other, exec_admin="on")
    assert one(db, "SELECT count(*) FROM exec.vault") == 0
    with db.cursor() as cur:
        cur.execute("SELECT * FROM exec.vault_admin_overview()")
        assert [c.name for c in cur.description] == [
            "id",
            "owner_id",
            "status",
            "exit_policy",
            "sealed_read_until",
        ]
        assert str(cur.fetchone()[0]) == str(vault)
    as_admin(db)
    as_role(db, "svc_exec", org, other, exec_admin="")
    assert one(db, "SELECT count(*) FROM exec.vault_admin_overview()") == 0
    as_admin(db)
    as_role(db, "svc_context", org, other)
    fails(db, "SELECT * FROM exec.vault_admin_overview()", code="42501")
    fails(db, "SELECT count(*) FROM exec.vault", code="42501")


def test_worker_reads_only_the_vault_operational_columns_with_its_flag(db):
    org, owner, _, _ = _setup(db)
    as_role(db, "svc_exec_worker", org)
    assert one(db, "SELECT count(*) FROM (SELECT owner_id FROM exec.vault) v") == 0
    as_admin(db)
    as_role(db, "svc_exec_worker", org, exec_workflow="on")
    assert one(db, "SELECT octet_length(box_public_key) FROM exec.vault") == 32
    fails(db, "SELECT box_private_key_ct FROM exec.vault", code="42501")
    fails(db, "SELECT count(*) FROM exec.vault_key_wrap", code="42501")


def test_vault_status_moves_forward_and_a_shredded_vault_never_reopens(db):
    org, owner, vault, _ = _setup(db)
    as_role(db, "svc_exec", org, owner)
    _execute(db, "UPDATE exec.vault SET status = 'sealed'")
    assert one(db, "SELECT sealed_at IS NOT NULL FROM exec.vault") is True
    fails(db, "UPDATE exec.vault SET status = 'active'", code="23514")
    fails(db, f"UPDATE exec.vault SET box_public_key = {_bytes(32, '09')}", code="23514")
    _execute(db, "UPDATE exec.vault SET status = 'shredded'")
    assert one(db, "SELECT octet_length(box_private_key_ct) FROM exec.vault") == 0
    assert one(db, "SELECT count(*) FROM exec.vault_key_wrap") == 0
    fails(db, "UPDATE exec.vault SET status = 'active'", code="23514")
    fails(db, f"UPDATE exec.vault SET box_private_key_ct = {_bytes(72)}", code="23514")


def test_revoking_a_key_wrap_keeps_two_live_wraps_one_offline(db):
    org, owner, vault, _ = _setup(db)
    passkey = one(db, "SELECT id FROM exec.vault_key_wrap WHERE kind = 'passkey_prf'")
    as_role(db, "svc_exec", org, owner)
    fails(
        db,
        "UPDATE exec.vault_key_wrap SET revoked_at = now() WHERE id = %s",
        (passkey,),
        code="23514",
    )
    fails(db, "UPDATE exec.vault_key_wrap SET label = 'x'", code="42501")
    fails(db, "DELETE FROM exec.vault_key_wrap", code="42501")
    as_admin(db)
    _wrap(db, org, vault, owner, "hardware_key")
    as_role(db, "svc_exec", org, owner)
    assert (
        _execute(db, "UPDATE exec.vault_key_wrap SET revoked_at = now() WHERE id = %s", (passkey,))
        == 1
    )
    fails(
        db,
        "UPDATE exec.vault_key_wrap SET revoked_at = NULL WHERE id = %s",
        (passkey,),
        code="23514",
    )
    assert (
        _execute(db, "UPDATE exec.vault_key_wrap SET last_used_at = now() WHERE revoked_at IS NULL")
        == 2
    )


# ─── exec.thesis ───


def test_thesis_needs_an_unlockable_vault_of_its_own_owner(db):
    org, owner = new_org(db), new_id()
    lonely = _vault(db, org, owner, wraps=("passkey_prf",))
    as_role(db, "svc_exec", org, owner)
    params = (str(new_id()), str(org), str(lonely), str(owner))
    fails(db, THESIS_SQL.format(due=DUE), params, code="23514")
    as_admin(db)
    _wrap(db, org, lonely, owner, "recovery_code")
    as_role(db, "svc_exec", org, owner)
    _execute(db, THESIS_SQL.format(due=DUE), params)
    # Writing into another owner's vault: the policy refuses a foreign owner_id, the trigger a foreign vault.
    as_admin(db)
    other = new_id()
    theirs = _vault(db, org, other)
    as_role(db, "svc_exec", org, owner)
    # The BEFORE trigger refuses a foreign vault or owner before the policy's WITH CHECK is reached.
    fails(
        db,
        THESIS_SQL.format(due=DUE),
        (str(new_id()), str(org), str(theirs), str(other)),
        code="23514",
    )
    fails(
        db,
        THESIS_SQL.format(due=DUE),
        (str(new_id()), str(org), str(theirs), str(owner)),
        code="23514",
    )
    # Where no trigger stands in front, owner_only's WITH CHECK refuses a vault for someone else.
    fails(
        db,
        "INSERT INTO exec.vault (id, org_id, owner_id, box_public_key, box_private_key_ct, transit_key_name) "
        f"VALUES (gen_random_uuid(), %s, %s, {_bytes(32)}, {_bytes(72)}, 'k-other')",
        (str(org), str(new_id())),
        code="42501",
    )


def test_thesis_due_at_is_a_whole_hour_at_least_one_day_ahead(db):
    org, owner, vault, _ = _setup(db)
    as_role(db, "svc_exec", org, owner)
    params = (str(new_id()), str(org), str(vault), str(owner))
    fails(db, THESIS_SQL.format(due=DUE + " + interval '30 minutes'"), params, code="23514")
    fails(
        db,
        THESIS_SQL.format(
            due="date_trunc('hour', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' + interval '2 hours'"
        ),
        params,
        code="23514",
    )


def test_thesis_content_is_immutable_and_status_follows_the_lifecycle(db):
    org, owner, _, thesis = _setup(db)
    as_role(db, "svc_exec", org, owner)
    fails(db, f"UPDATE exec.thesis SET content_ct = {_bytes(41, '07')}", code="42501")
    fails(db, "DELETE FROM exec.thesis", code="42501")
    fails(db, "UPDATE exec.thesis SET status = 'scored'", code="23514")
    _execute(db, "UPDATE exec.thesis SET status = 'withdrawn'")
    assert one(db, "SELECT withdrawn_at IS NOT NULL FROM exec.thesis") is True
    # The trigger binds the paths the column grants do not (the table owner, the DBA).
    as_admin(db)
    fails(
        db,
        f"UPDATE exec.thesis SET cond_ct = {_bytes(512, '07')} WHERE id = %s",
        (str(thesis),),
        code="23514",
    )


def test_thesis_cannot_be_withdrawn_once_due(db):
    org, owner, vault, _ = _setup(db)
    late = new_id()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO exec.thesis (id, org_id, vault_id, owner_id, content_ct, wrapped_dek, cond_ct, "
            f"cond_key_version, committed_at, due_at) VALUES (%s, %s, %s, %s, {_bytes(41)}, {_bytes(72)}, "
            f"{_bytes(512)}, 1, now() - interval '3 days', "
            "date_trunc('hour', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' - interval '1 hour')",
            (str(late), str(org), str(vault), str(owner)),
        )
    as_role(db, "svc_exec", org, owner)
    fails(
        db, "UPDATE exec.thesis SET status = 'withdrawn' WHERE id = %s", (str(late),), code="23514"
    )


def test_private_content_is_invisible_to_everyone_but_the_owner(db):
    org, owner, _, _ = _setup(db)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO exec.memory_item (id, org_id, vault_id, owner_id, kind, content_ct, wrapped_dek) "
            f"SELECT gen_random_uuid(), org_id, id, owner_id, 'note', {_bytes(41)}, {_bytes(72)} FROM exec.vault"
        )
    tables = ("exec.thesis", "exec.memory_item", "exec.vault_key_wrap")
    as_role(db, "svc_exec", org, owner)
    assert [one(db, f"SELECT count(*) FROM {t}") for t in tables] == [1, 1, 2]
    # Another user, and an administrator request (app.exec_admin) made by someone else.
    for gucs in ({}, {"exec_admin": "on"}):
        as_admin(db)
        as_role(db, "svc_exec", org, new_id(), **gucs)
        assert [one(db, f"SELECT count(*) FROM {t}") for t in tables] == [0, 0, 0]
    # The table owner is held by FORCE RLS; svc_context only has exec.publication.
    as_admin(db)
    as_role(db, "da_owner", None)
    _execute(db, "SELECT set_config('app.org_id', '', true), set_config('app.user_id', '', true)")
    assert [one(db, f"SELECT count(*) FROM {t}") for t in tables] == [0, 0, 0]
    for role in ("svc_context", "svc_exec_worker"):
        as_admin(db)
        as_role(db, role, org, owner)
        for t in ("exec.memory_item", "exec.vault_key_wrap"):
            fails(db, f"SELECT count(*) FROM {t}", code="42501")
    as_admin(db)
    as_role(db, "svc_context", org, owner)
    fails(db, "SELECT count(*) FROM exec.thesis", code="42501")


def test_worker_reads_the_condition_but_never_the_content(db):
    org, _, _, thesis = _setup(db)
    as_role(db, "svc_exec_worker", org)
    assert one(db, "SELECT count(*) FROM (SELECT id FROM exec.thesis) t") == 0
    as_admin(db)
    as_role(db, "svc_exec_worker", org, exec_workflow="on")
    assert one(db, "SELECT octet_length(cond_ct) FROM exec.thesis") == 512
    fails(db, "SELECT content_ct FROM exec.thesis", code="42501")
    fails(db, "SELECT wrapped_dek FROM exec.thesis", code="42501")
    fails(db, "UPDATE exec.thesis SET workflow_id = 'x'", code="42501")
    assert (
        _execute(db, "UPDATE exec.thesis SET status = 'scoring' WHERE id = %s", (str(thesis),)) == 1
    )


# ─── exec.thesis_evidence ───


def test_evidence_batches_are_two_sided_and_numbered_in_order(db):
    org, owner, _, thesis = _setup(db)
    with db.cursor() as cur:
        cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
    one_sided = _evidence_sql(thesis, org, owner, 1, [("support", "evidence")])
    as_role(db, "svc_exec_worker", org)
    fails(
        db,
        _evidence_sql(thesis, org, owner, 1, [("support", "evidence"), ("against", "none_found")]),
        code="42501",
    )
    as_admin(db)
    as_role(db, "svc_exec_worker", org, exec_workflow="on")
    fails(db, one_sided, code="23514")
    fails(
        db,
        _evidence_sql(thesis, org, owner, 2, [("support", "evidence"), ("against", "evidence")]),
        code="23514",
    )
    _execute(
        db, _evidence_sql(thesis, org, owner, 1, [("support", "evidence"), ("against", "evidence")])
    )
    as_admin(db)
    assert (
        one(db, "SELECT evidence_state FROM exec.thesis WHERE id = %s", (str(thesis),))
        == "two_sided"
    )
    fails(db, "UPDATE exec.thesis_evidence SET side = 'support'", code="42501")
    fails(db, "DELETE FROM exec.thesis_evidence", code="42501")
    as_role(db, "svc_exec", org, owner)
    assert one(db, "SELECT count(*) FROM exec.thesis_evidence") == 2
    fails(
        db,
        _evidence_sql(thesis, org, owner, 2, [("support", "evidence"), ("against", "evidence")]),
        code="42501",
    )


def test_a_new_batch_follows_the_last_committed_one(db):
    org, owner, _, thesis = _setup(db)
    with db.cursor() as cur:
        # Batch 1 from an earlier transaction: created_at differs from this transaction's timestamp.
        # Replica mode skips the triggers, as if that batch had been checked when it committed.
        cur.execute("SET LOCAL session_replication_role = replica")
        cur.execute(
            _evidence_sql(
                thesis, org, owner, 1, [("support", "evidence"), ("against", "insufficient")]
            )
            .replace("gen_random_uuid())", "gen_random_uuid(), now() - interval '1 day')")
            .replace("agent_run_id)", "agent_run_id, created_at)")
        )
        cur.execute("SET LOCAL session_replication_role = origin")
        cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
    as_role(db, "svc_exec_worker", org, exec_workflow="on")
    fails(
        db,
        _evidence_sql(thesis, org, owner, 1, [("support", "evidence"), ("against", "evidence")]),
        code="23514",
    )
    _execute(
        db,
        _evidence_sql(thesis, org, owner, 2, [("support", "evidence"), ("against", "none_found")]),
    )
    as_admin(db)
    assert one(db, "SELECT evidence_state FROM exec.thesis") == "one_sided"


# ─── exec.thesis_score ───

SCORE_SQL = (
    "INSERT INTO exec.thesis_score (id, org_id, thesis_id, owner_id, kind, outcome, as_of, sealed_ct, "
    f"workflow_run_id) SELECT gen_random_uuid(), current_setting('app.org_id')::uuid, id, owner_id, %s, %s, due_at, {_bytes(49)}, 'wf' "
    "FROM exec.thesis"
)


def test_final_score_needs_scoring_and_closes_the_thesis_once(db):
    org, owner, _, _ = _setup(db)
    as_role(db, "svc_exec_worker", org, exec_workflow="on")
    fails(db, SCORE_SQL, ("final", "correct"), code="23514")
    fails(db, SCORE_SQL, ("restatement", "correct"), code="23514")
    _execute(db, "UPDATE exec.thesis SET status = 'scoring'")
    _execute(db, SCORE_SQL, ("final", "correct"))
    assert one(db, "SELECT status FROM exec.thesis") == "scored"
    fails(db, SCORE_SQL, ("final", "incorrect"), code="23514")
    _execute(db, SCORE_SQL, ("restatement", "incorrect"))
    fails(db, "SELECT outcome FROM exec.thesis_score", code="42501")
    as_admin(db)
    as_role(db, "svc_exec", org, owner)
    assert one(db, "SELECT count(*) FROM exec.thesis_score") == 2
    fails(db, SCORE_SQL, ("restatement", "correct"), code="42501")
    fails(db, "UPDATE exec.thesis_score SET outcome = 'incorrect'", code="42501")
    # Append-only for the DBA too (BR-191).
    as_admin(db)
    fails(db, "UPDATE exec.thesis_score SET outcome = 'incorrect'", code="42501")
    fails(db, "DELETE FROM exec.thesis_score", code="42501")


def test_unresolvable_final_score_marks_the_thesis_unresolvable(db):
    org, _, _, _ = _setup(db)
    _execute(db, "UPDATE exec.thesis SET status = 'scoring'")
    as_role(db, "svc_exec_worker", org, exec_workflow="on")
    _execute(db, SCORE_SQL, ("final", "unresolvable"))
    as_admin(db)
    assert one(db, "SELECT status FROM exec.thesis") == "unresolvable"


# ─── exec.memory_item ───

MEMORY_SQL = (
    "INSERT INTO exec.memory_item (id, org_id, vault_id, owner_id, kind, content_ct, wrapped_dek) "
    f"VALUES (%s, %s, %s, %s, 'priority', {_bytes(41)}, {_bytes(72)})"
)


def test_memory_items_need_an_unlockable_vault_and_can_only_be_shredded(db):
    org, owner = new_org(db), new_id()
    vault = _vault(db, org, owner, wraps=("passkey_prf", "passkey_prf"))
    item = new_id()
    as_role(db, "svc_exec", org, owner)
    fails(db, MEMORY_SQL, (str(item), str(org), str(vault), str(owner)), code="23514")
    as_admin(db)
    _wrap(db, org, vault, owner, "hardware_key")
    as_role(db, "svc_exec", org, owner)
    _execute(db, MEMORY_SQL, (str(item), str(org), str(vault), str(owner)))
    fails(db, f"UPDATE exec.memory_item SET content_ct = {_bytes(41, '05')}", code="23514")
    fails(db, "UPDATE exec.memory_item SET kind = 'note'", code="23514")
    fails(db, "DELETE FROM exec.memory_item", code="42501")
    _execute(db, "UPDATE exec.memory_item SET content_ct = NULL, wrapped_dek = NULL")
    assert one(db, "SELECT shredded_at IS NOT NULL FROM exec.memory_item") is True
    fails(db, "UPDATE exec.memory_item SET content_ct = NULL, wrapped_dek = NULL", code="23514")


# ─── exec.key_access_log ───

LOG_SQL = (
    "INSERT INTO exec.key_access_log (id, org_id, vault_id, owner_id, thesis_id, key_kind, action, purpose, "
    "actor_kind, actor_ref, correlation_id) SELECT gen_random_uuid(), current_setting('app.org_id')::uuid, vault_id, owner_id, id, "
    "'condition_transit', 'decrypt_condition', 'scoring', 'workflow', 'wf/decrypt', gen_random_uuid() "
    "FROM exec.thesis RETURNING id"
)


def test_the_fourth_condition_decrypt_in_a_day_is_not_written(db):
    org, owner, _, thesis = _setup(db)
    as_role(db, "svc_exec_worker", org, exec_workflow="on")
    for _ in range(3):
        assert one(db, LOG_SQL) is not None
    assert one(db, LOG_SQL) is None
    fails(db, "SELECT action FROM exec.key_access_log", code="42501")
    as_admin(db)
    assert one(db, "SELECT count(*) FROM exec.key_access_log") == 3
    assert (
        one(
            db,
            "SELECT count(*) FROM exec.outbox WHERE event_type = 'exec.key.decrypt_limit_exceeded' AND aggregate_id = %s",
            (str(thesis),),
        )
        == 1
    )
    fails(db, "UPDATE exec.key_access_log SET actor_ref = 'x'", code="42501")
    fails(db, "DELETE FROM exec.key_access_log", code="42501")
    as_role(db, "svc_exec", org, owner)
    assert one(db, "SELECT count(*) FROM exec.key_access_log") == 3
    fails(db, "UPDATE exec.key_access_log SET actor_ref = 'x'", code="42501")
    as_admin(db)
    as_role(db, "svc_exec", org, new_id())
    assert one(db, "SELECT count(*) FROM exec.key_access_log") == 0


# ─── exec.handover, exec.handover_item ───

HANDOVER_SQL = (
    "INSERT INTO exec.handover (id, org_id, vault_id, owner_id, kind, reason, requested_by, owner_participates, "
    "successor_id, status, approved_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


def test_admin_completes_an_approved_shred_and_the_vault_keys_are_destroyed(db):
    org, owner, vault, _ = _setup(db)
    ac05, ac06, handover = new_id(), new_id(), new_id()
    as_role(db, "svc_exec", org, ac05, exec_admin="on")
    base = (
        str(handover),
        str(org),
        str(vault),
        str(owner),
        "shred",
        "leave_position",
        str(ac05),
        False,
        None,
    )
    # A shred the owner does not confirm needs AC06's approval (BR-195).
    fails(db, HANDOVER_SQL, (*base, "approved", None), code="23514")
    _execute(db, HANDOVER_SQL, (*base, "requested", None))
    as_admin(db)
    as_role(db, "svc_exec", org, ac06, exec_admin="on")
    fails(db, "UPDATE exec.handover SET reason = 'contract_end'", code="42501")
    fails(db, "DELETE FROM exec.handover", code="42501")
    _execute(
        db,
        "UPDATE exec.handover SET status = 'approved', approved_by = %s, approved_at = now()",
        (str(ac06),),
    )
    _execute(
        db,
        "UPDATE exec.handover SET status = 'completed', completed_at = now(), certificate_sha256 = repeat('a', 64)",
    )
    as_admin(db)
    assert one(db, "SELECT status FROM exec.vault") == "shredded"
    assert one(db, "SELECT count(*) FROM exec.vault_key_wrap") == 0
    assert (
        one(db, "SELECT count(*) FROM exec.outbox WHERE event_type = 'exec.vault.shred_requested'")
        == 1
    )
    # Without the admin flag the same person sees nothing.
    as_role(db, "svc_exec", org, ac06, exec_admin="")
    assert one(db, "SELECT count(*) FROM exec.handover") == 0


def test_seal_handover_seals_the_vault(db):
    org, owner, vault, _ = _setup(db)
    as_role(db, "svc_exec", org, owner)
    handover = new_id()
    _execute(
        db,
        HANDOVER_SQL,
        (
            str(handover),
            str(org),
            str(vault),
            str(owner),
            "seal",
            "owner_request",
            str(owner),
            True,
            None,
            "in_progress",
            None,
        ),
    )
    _execute(db, "UPDATE exec.handover SET status = 'completed', completed_at = now()")
    assert one(db, "SELECT status FROM exec.vault") == "sealed"


def test_a_handover_cannot_point_at_another_leaders_vault(db):
    org, owner, _vault_of_owner, _ = _setup(db)
    other = new_id()
    other_vault = _vault(db, org, other)
    as_role(db, "svc_exec", org, owner)
    # The owner names their own id but the other leader's vault: an approved shred of this row
    # would otherwise run as definer against that vault.
    row = (
        str(new_id()),
        str(org),
        str(other_vault),
        str(owner),
        "shred",
        "owner_request",
        str(owner),
        True,
        None,
        "requested",
        None,
    )
    fails(db, HANDOVER_SQL, row, code="23514")
    as_admin(db)
    assert one(db, "SELECT status FROM exec.vault WHERE id = %s", (str(other_vault),)) == "active"


def test_handover_items_go_from_the_owner_to_the_successor_only(db):
    org, owner, vault, _ = _setup(db)
    successor, handover = new_id(), new_id()
    item_sql = (
        "INSERT INTO exec.handover_item (id, org_id, handover_id, owner_id, successor_id, source_kind, source_id, "
        f"payload_sealed) VALUES (gen_random_uuid(), %s, %s, %s, %s, 'memory_item', gen_random_uuid(), {_bytes(49)})"
    )
    params = (str(org), str(handover), str(owner), str(successor))
    as_role(db, "svc_exec", org, owner)
    _execute(
        db,
        HANDOVER_SQL,
        (
            str(handover),
            str(org),
            str(vault),
            str(owner),
            "selective_handover",
            "leave_position",
            str(owner),
            True,
            str(successor),
            "requested",
            None,
        ),
    )
    fails(db, item_sql, params, code="23514")
    as_admin(db)
    _execute(db, "UPDATE exec.handover SET status = 'approved'")
    as_role(db, "svc_exec", org, owner)
    _execute(db, item_sql, params)
    _execute(db, item_sql, params)
    assert one(db, "SELECT items_total FROM exec.handover") == 2
    assert one(db, "SELECT count(*) FROM exec.handover_item") == 0
    fails(db, item_sql, (str(org), str(handover), str(owner), str(new_id())), code="23514")
    as_admin(db)
    as_role(db, "svc_exec", org, successor)
    assert one(db, "SELECT count(*) FROM exec.handover_item") == 2
    # The successor cannot see the handover, so the BEFORE trigger refuses before the policy does.
    fails(db, item_sql, params, code="23514")
    fails(db, "UPDATE exec.handover_item SET source_kind = 'thesis_note'", code="42501")
    fails(db, "DELETE FROM exec.handover_item", code="42501")
    _execute(
        db,
        "UPDATE exec.handover_item SET imported_at = now() WHERE id = (SELECT min(id::text)::uuid FROM exec.handover_item)",
    )
    # The daily cleanup is the one DELETE of the schema.
    assert one(db, "SELECT exec.handover_item_cleanup()") == 1
    assert one(db, "SELECT count(*) FROM exec.handover_item") == 1
    as_admin(db)
    as_role(db, "svc_exec_worker", org, exec_workflow="on")
    fails(db, "SELECT exec.handover_item_cleanup()", code="42501")


# ─── exec.publication ───

PUBLICATION_SQL = (
    "INSERT INTO exec.publication (id, org_id, thesis_id, owner_id, title, statement_public, ref_kind, metric_ref, "
    "period_label, comparator, threshold, unit, due_at, attribution_label, target_data_space_id) "
    "SELECT %s, org_id, id, owner_id, 't', %s, 'metric', 'm', 'p', 'gte', 1, 'VND/l', due_at + %s::interval, "
    "'Thành viên Ban điều hành', gen_random_uuid() FROM exec.thesis"
)


def test_publication_rules_and_what_svc_context_reads(db):
    org, owner, _, _ = _setup(db)
    publication = new_id()
    as_role(db, "svc_exec", org, owner)
    fails(db, PUBLICATION_SQL, (str(publication), "Giá xăng vượt 25 nghìn", "0"), code="23514")
    fails(db, PUBLICATION_SQL, (str(publication), "Giá xăng tăng", "1 hour"), code="23514")
    _execute(db, PUBLICATION_SQL, (str(publication), "Giá xăng tăng", "0"))
    fails(db, "DELETE FROM exec.publication", code="42501")
    as_admin(db)
    as_role(db, "svc_context", org)
    assert one(db, "SELECT count(*) FROM exec.publication") == 1
    as_admin(db)
    as_role(db, "svc_exec", org, owner)
    _execute(
        db,
        "UPDATE exec.publication SET status = 'published', published_at = now(), knowledge_page_id = gen_random_uuid()",
    )
    assert (
        one(db, "SELECT count(*) FROM exec.outbox WHERE event_type = 'exec.publication.published'")
        == 1
    )
    _execute(db, "UPDATE exec.publication SET status = 'withdrawn', withdrawn_at = now()")
    assert (
        one(db, "SELECT count(*) FROM exec.outbox WHERE event_type = 'exec.publication.withdrawn'")
        == 1
    )
    as_admin(db)
    as_role(db, "svc_context", org)
    assert one(db, "SELECT count(*) FROM exec.publication") == 0


def test_publication_needs_an_open_or_scored_thesis(db):
    org, owner, _, _ = _setup(db)
    _execute(db, "UPDATE exec.thesis SET status = 'scoring'")
    as_role(db, "svc_exec", org, owner)
    fails(db, PUBLICATION_SQL, (str(new_id()), "Giá xăng tăng", "0"), code="23514")


# ─── isolation ───


def test_exec_rows_of_another_organisation_are_invisible(db):
    _, owner, _, _ = _setup(db)
    elsewhere = new_org(db)
    as_role(db, "svc_exec", elsewhere, owner)
    assert one(db, "SELECT count(*) FROM exec.thesis") == 0
    assert one(db, "SELECT count(*) FROM exec.vault") == 0
    as_admin(db)
    assert (
        one(
            db,
            "SELECT bool_and(c.relforcerowsecurity) AND count(*) = 11 FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'exec' AND c.relkind = 'r'",
        )
        is True
    )
