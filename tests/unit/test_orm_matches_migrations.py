"""The ORM must not claim foreign-key behaviour the real schema lacks.

Alembic owns the schema; the ORM is only a mapping over it. `ondelete=` in a
mapped_column emits DDL, and nothing emits DDL any more (kb-backend stopped
running create_all at startup), so a cascade declared here and absent from the
migrations is a promise to the next reader that the database will not keep —
which is how trash_ops ended up hand-deleting children in dependency order.
"""
from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ORM_DIR = REPO_ROOT / "backend" / "libs" / "basemodel" / "services_databaseconnector" / "postgres_orm"
MIGRATIONS = REPO_ROOT / "backend" / "alembic" / "versions"


def _orm_cascades() -> set[tuple[str, str]]:
    """(child table, parent reference) for every ondelete=CASCADE in the ORM."""
    cascades = set()
    for orm_file in ORM_DIR.glob("*.py"):
        for chunk in orm_file.read_text(encoding="utf-8").split("\nclass ")[1:]:
            table = re.search(r'__tablename__ = "([^"]+)"', chunk)
            if not table:
                continue
            for parent in re.findall(r'ForeignKey\("([^"]+)",\s*ondelete="CASCADE"\)', chunk):
                cascades.add((table.group(1), parent))
    return cascades


def _migration_cascades() -> set[tuple[str, str]]:
    """The same pairs, as the migrations actually create them."""
    cascades = set()
    for migration in MIGRATIONS.glob("*.py"):
        text = migration.read_text(encoding="utf-8")

        # op.create_table("Child", ..., ForeignKeyConstraint(["col"], ["Parent.col"], ondelete="CASCADE"))
        for block in re.split(r"op\.create_table\(", text)[1:]:
            child = re.match(r"""\s*['"]([A-Za-z0-9_]+)['"]""", block)
            if not child:
                continue
            body = block.split("op.create_")[0]
            for parent in re.findall(
                r"""ForeignKeyConstraint\(\s*\[[^]]*\],\s*\[\s*['"]([^'"]+)['"]\s*\][^)]*ondelete=['"]CASCADE['"]""",
                body,
            ):
                cascades.add((child.group(1), parent))

        # Raw SQL: CREATE TABLE "Child" ( ... REFERENCES "Parent"(col) ON DELETE CASCADE
        for statement in re.split(r'CREATE TABLE(?: IF NOT EXISTS)?\s+"', text)[1:]:
            child = re.match(r"([A-Za-z0-9_]+)", statement)
            if not child:
                continue
            body = statement.split(";")[0]
            for parent_table, parent_column in re.findall(
                r'REFERENCES\s+"([A-Za-z0-9_]+)"\(([A-Za-z0-9_]+)\)\s+ON DELETE CASCADE', body
            ):
                cascades.add((child.group(1), f"{parent_table}.{parent_column}"))
    return cascades


class TestDeclaredCascadesExist:
    def test_the_orm_declares_no_cascade_the_schema_lacks(self):
        missing = sorted(_orm_cascades() - _migration_cascades())
        assert not missing, (
            "ORM declares ON DELETE CASCADE that no migration creates: "
            f"{missing} - either add a migration or drop the ondelete="
        )

    def test_some_cascades_are_genuinely_declared(self):
        """Guards the parsers above: if either stopped matching, the test over
        it would pass by finding nothing at all."""
        assert _orm_cascades(), "parsed no ORM cascades - the ORM parser broke"
        assert _migration_cascades(), "parsed no migration cascades - the migration parser broke"


class TestSchemaOwnership:
    def test_kb_backend_does_not_create_its_own_schema_at_startup(self):
        """Alembic owns the schema. create_all at startup needs a superuser,
        races across replicas, and produces tables with neither RLS nor grants."""
        main = (REPO_ROOT / "backend" / "services" / "kb_backend" / "main.py").read_text(encoding="utf-8")
        assert "create_all" not in main
        assert "CREATE DATABASE" not in main
