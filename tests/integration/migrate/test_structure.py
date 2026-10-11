"""The generated V3 database matches the counts of the design (tab 3 D5.1) and isolates organisations."""

import pytest

from tests.integration.migrate._ddl import as_role, fails, new_org, one

pytestmark = pytest.mark.integration


def test_object_counts_match_the_design(db):
    tables = one(
        db,
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relkind IN ('r','p') AND NOT c.relispartition "
        "AND n.nspname NOT IN ('pg_catalog','information_schema','public')",
    )
    # 194 tables of 23 schemas plus one outbox per schema (D5.1).
    assert tables == 194 + 23
    fks = one(
        db,
        "SELECT count(*) FROM pg_constraint c JOIN pg_namespace n ON n.oid = c.connamespace "
        "WHERE c.contype = 'f' AND c.conparentid = 0 "
        "AND n.nspname NOT IN ('pg_catalog','information_schema','public')",
    )
    assert fks == 203


def test_rows_of_another_organisation_are_invisible(db):
    mine, theirs = new_org(db), new_org(db)
    as_role(db, "svc_org", mine)
    assert one(db, "SELECT count(*) FROM org.organization") == 1
    fails(
        db,
        "INSERT INTO org.role (id, org_id, code, name) VALUES (gen_random_uuid(), %s, 'x', 'x')",
        (str(theirs),),
        code="42501",
    )
