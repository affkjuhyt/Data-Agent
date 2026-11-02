"""The "two different people" rule of the approval tables: org.two_people() refuses the same person
on both sides of an approval while org.organization.allow_self_approval is off, and lets it through
when on. audit.export_job cannot read the flag, so its CHECK reads its own self_approved column."""

from __future__ import annotations

import pytest

from tests.integration.migrate._ddl import as_admin, as_role, fails, new_id, new_org, one
from tests.integration.migrate.test_core import _consolidated_version

pytestmark = pytest.mark.integration

CHECK = "23514"

# table, the two person columns of one guarded pair (the trigger holds every pair of a table).
GUARDED = [
    ("catalog.dataset_version", "certified_by", "created_by"),
    ("context.knowledge_page_version", "reviewed_by", "created_by"),
    ("context.knowledge_page_version", "certified_by", "created_by"),
    ("context.knowledge_page_version", "certified_by", "reviewed_by"),
    ("context.data_product", "owner_id", "steward_id"),
    ("context.data_product", "activated_by", "created_by"),
    ("pipeline.writeback_request", "approved_by", "requested_by"),
    ("acquire.crawl_source", "legal_reviewed_by", "owner_id"),
    ("forecast.model_version", "approved_by", "created_by"),
    ("forecast.formula_component", "certified_by", "created_by"),
    ("governance.model_provider", "approved_by", "enabled_by"),
]


def _flag(db, org, on: bool) -> None:
    with db.cursor() as cur:
        cur.execute(
            "UPDATE org.organization SET allow_self_approval = %s WHERE id = %s", (on, str(org))
        )


def _version(db, org, creator) -> str:
    """A consolidated dataset version created by `creator`, not yet certified."""
    dataset, version = str(new_id()), str(new_id())
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO catalog.dataset (id, org_id, data_space_id, origin, schema_name, table_name) "
            "VALUES (%s, %s, %s, 'consolidated', 'silver', %s)",
            (dataset, str(org), str(new_id()), f"t_{new_id().hex[:8]}"),
        )
        cur.execute(
            "INSERT INTO catalog.dataset_version (id, org_id, dataset_id, version, schema_hash, "
            "valid_from, created_by) VALUES (%s, %s, %s, 1, 'h', '2026-07-01T00:00:00Z', %s)",
            (version, str(org), dataset, str(creator)),
        )
    return version


def _certify_sql(org, version, actor) -> tuple[str, tuple]:
    return (
        "INSERT INTO catalog.dataset_version_certification (id, org_id, dataset_version_id, action, "
        "actor_id) VALUES (%s, %s, %s, 'certify', %s)",
        (str(new_id()), str(org), version, str(actor)),
    )


def _certify(db, org, version, actor) -> None:
    as_role(db, "svc_catalog", org)
    with db.cursor() as cur:
        cur.execute(*_certify_sql(org, version, actor))
    as_admin(db)


def test_two_people_refused_when_off(db):
    org, creator = new_org(db), new_id()
    version = _version(db, org, creator)
    as_role(db, "svc_catalog", org)
    message = fails(db, *_certify_sql(org, version, creator), code=CHECK)
    assert "hai người" in message


def test_two_people_allowed_when_on(db):
    org, creator = new_org(db), new_id()
    _flag(db, org, True)
    version = _version(db, org, creator)
    _certify(db, org, version, creator)
    assert (
        one(db, "SELECT certified_by FROM catalog.dataset_version WHERE id = %s", (version,))
        == creator
    )


def test_turning_it_off_keeps_existing_rows(db):
    org, creator = new_org(db), new_id()
    _flag(db, org, True)
    v1 = _version(db, org, creator)
    _certify(db, org, v1, creator)
    _flag(db, org, False)
    with db.cursor() as cur:  # an UPDATE of an unrelated column still succeeds
        cur.execute("UPDATE catalog.dataset_version SET diff = '{}' WHERE id = %s", (v1,))
    v2 = _version(db, org, creator)
    as_role(db, "svc_catalog", org)
    fails(db, *_certify_sql(org, v2, creator), code=CHECK)


def test_a_null_side_is_never_refused(db):
    org, someone = new_org(db), str(new_id())
    with db.cursor() as cur:
        cur.execute("CREATE TEMP TABLE pair (org_id uuid, a uuid, b uuid, note text)")
        cur.execute(
            "CREATE TRIGGER pair_two_people BEFORE INSERT OR UPDATE ON pair "
            "FOR EACH ROW EXECUTE FUNCTION org.two_people('a', 'b')"
        )
        cur.execute("INSERT INTO pair VALUES (%s, NULL, %s)", (str(org), someone))
        cur.execute("INSERT INTO pair VALUES (%s, %s, NULL)", (str(org), someone))
        cur.execute("INSERT INTO pair VALUES (%s, NULL, NULL)", (str(org),))
    fails(db, "INSERT INTO pair VALUES (%s, %s, %s)", (str(org), someone, someone), code=CHECK)


def test_an_unknown_organisation_does_not_allow_it(db):
    assert one(db, "SELECT org.self_approval_allowed(%s)", (str(new_id()),)) is False


@pytest.mark.parametrize("table, a, b", GUARDED)
def test_every_guarded_pair_has_the_trigger(db, table, a, b):
    with db.cursor() as cur:
        cur.execute(
            "SELECT tgargs FROM pg_trigger WHERE tgrelid = %s::regclass AND NOT tgisinternal "
            "AND tgfoid = 'org.two_people'::regproc",
            (table,),
        )
        rows = cur.fetchall()
    assert len(rows) == 1, f"{table} must carry exactly one two_people trigger"
    args = bytes(rows[0][0]).decode().rstrip("\x00").split("\x00")
    pairs = list(zip(args[0::2], args[1::2], strict=True))
    assert (a, b) in pairs, f"{table}: {pairs}"


def test_the_exports_check_reads_its_own_flag(audit_db):
    org, user = new_id(), new_id()
    insert = (
        "INSERT INTO audit.export_job (id, org_id, requested_by, approved_by, self_approved) "
        "VALUES (%s, %s, %s, %s, %s)"
    )
    fails(audit_db, insert, (str(new_id()), str(org), str(user), str(user), False), code=CHECK)
    with audit_db.cursor() as cur:
        cur.execute(insert, (str(new_id()), str(org), str(user), str(user), True))


def test_a_version_without_a_creator_is_never_certified(db):
    # Only the equality depends on the switch: a certified version still needs a creator. The
    # dataset_version guard refuses first on every real path, so it is switched off here to reach
    # the CHECK itself, which is what holds if that guard ever changes.
    org = new_org(db)
    with db.cursor() as cur:
        cur.execute("ALTER TABLE catalog.dataset_version DISABLE TRIGGER dataset_version_guard")
    for on in (False, True):
        _flag(db, org, on)
        message = fails(
            db,
            "UPDATE catalog.dataset_version SET certified_at = now(), certified_by = %s "
            "WHERE id = %s",
            (str(new_id()), _no_creator_version(db, org)),
            code=CHECK,
        )
        assert "dataset_version_certifier_created_check" in message


def _no_creator_version(db, org) -> str:
    return _consolidated_version(db, org, None, origin="source")[0]


def test_a_service_role_cannot_read_the_flag_but_its_writes_are_guarded(db):
    org, creator = new_org(db), new_id()
    version = _version(db, org, creator)
    as_role(db, "svc_catalog", org)
    fails(db, "SELECT org.self_approval_allowed(%s)", (str(org),), code="42501")
    fails(db, *_certify_sql(org, version, creator), code=CHECK)
    as_admin(db)
    _flag(db, org, True)
    _certify(db, org, version, creator)


def test_a_misspelled_column_raises_instead_of_guarding_nothing(db):
    org = new_org(db)
    with db.cursor() as cur:
        cur.execute("CREATE TEMP TABLE bad (org_id uuid, a uuid, b uuid)")
        cur.execute(
            "CREATE TRIGGER t BEFORE INSERT ON bad FOR EACH ROW "
            "EXECUTE FUNCTION org.two_people('a', 'typo')"
        )
        cur.execute("CREATE TEMP TABLE odd (org_id uuid, a uuid, b uuid)")
        cur.execute(
            "CREATE TRIGGER t BEFORE INSERT ON odd FOR EACH ROW EXECUTE FUNCTION org.two_people('a')"
        )
    fails(db, "INSERT INTO bad VALUES (%s, NULL, NULL)", (str(org),))
    fails(db, "INSERT INTO odd VALUES (%s, NULL, NULL)", (str(org),))
