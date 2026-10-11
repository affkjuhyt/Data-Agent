"""The interim outbox relay sees every organisation's outbox rows and can only mark them published."""

import pytest

from tests.integration.migrate._ddl import as_admin, as_role, fails, new_org, one

pytestmark = pytest.mark.integration


def _event(db, org) -> None:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO qa.outbox (id, org_id, aggregate_type, aggregate_id, event_type) "
            "VALUES (gen_random_uuid(), %s, 'question', gen_random_uuid(), 'question.received')",
            (str(org),),
        )


def test_the_relay_publishes_across_organisations_and_touches_nothing_else(db):
    first, second = new_org(db), new_org(db)
    _event(db, first)
    _event(db, second)
    as_role(db, "svc_outbox_relay", None)
    rows = one(
        db,
        "SELECT count(*) FROM (SELECT id FROM qa.outbox WHERE published_at IS NULL "
        "FOR UPDATE SKIP LOCKED) claimed",
    )
    assert rows == 2
    with db.cursor() as cur:
        cur.execute("UPDATE qa.outbox SET published_at = now() WHERE published_at IS NULL")
        assert cur.rowcount == 2
    # Only published_at, once; no insert, no delete, no other table.
    fails(db, "UPDATE qa.outbox SET payload = '{}'", code="42501")
    # A published row is out of the relay's reach: un-publishing it changes nothing.
    with db.cursor() as cur:
        cur.execute("UPDATE qa.outbox SET published_at = NULL")
        assert cur.rowcount == 0
    fails(
        db,
        "INSERT INTO qa.outbox (id, org_id, aggregate_type, aggregate_id, event_type) "
        "VALUES (gen_random_uuid(), gen_random_uuid(), 'x', gen_random_uuid(), 'x')",
        code="42501",
    )
    fails(db, "DELETE FROM qa.outbox", code="42501")
    fails(db, "SELECT count(*) FROM qa.question", code="42501")
    as_admin(db)
    assert one(db, "SELECT count(*) FROM qa.outbox WHERE published_at IS NULL") == 0


def test_every_schema_outbox_is_open_to_the_relay(db):
    assert (
        one(
            db,
            "SELECT count(*) FROM pg_policies WHERE tablename = 'outbox' "
            "AND policyname IN ('outbox_relay_read', 'outbox_relay_publish')",
        )
        == 23 * 2
    )
