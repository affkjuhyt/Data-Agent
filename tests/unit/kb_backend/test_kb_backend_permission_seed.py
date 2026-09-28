"""The seeded matrix and the routes that read it must stay in step.

When the grant table was a registry in dependencies/auth.py, a permission name
and the route asking for it were reviewed in one diff. Now that the matrix is
rows in Postgres, nothing in the type system connects `require_perm("addData")`
to a seeded `action = 'addData'` — a typo in either place fails open into a
route no role can reach, or a grant no route honours. These tests are what is
left holding the two ends together.
"""
from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
SEED = REPO_ROOT / "deploy" / "docker" / "sql" / "kb-seed.sql"
ROUTERS = REPO_ROOT / "backend" / "services" / "kb_backend" / "router"

ROLES = {"PLATFORM_ADMIN", "AI_ENGINEER", "EXECUTIVE", "BUSINESS_OPERATOR"}

# The matrix exactly as the deleted PermissionRegistry granted it. Kept here so
# the move to Postgres is verifiable as faithful rather than approximately
# right — a role that silently gained `deleteData` in translation would
# otherwise look like a seed nobody could argue with.
UNIVERSAL = {
    "viewDocuments", "editTableRow", "viewConflicts", "viewFleetStats",
    "viewKnowledgeBases", "retrieveKnowledge", "viewGraph", "runGraphQuery",
    "viewPolicies", "viewQdrantCollections", "searchQdrant", "viewTrash",
    "viewWarehouses", "viewSources",
}
RESTRICTED = {
    "PLATFORM_ADMIN": {
        "addData", "processLayer", "deleteData", "addChunkVersion", "editConflict",
        "addFilteringPolicy", "editFilteringPolicy", "deleteFilteringPolicy",
        "editExtractionPolicy", "toggleQdrant", "addWarehouse", "addWarehouseConfig",
        "editWarehouseConfig", "addSource", "editSource", "deleteSource", "runSource",
    },
    "AI_ENGINEER": {"toggleQdrant", "editExtractionPolicy"},
    "EXECUTIVE": {"editConflict", "processLayer", "addFilteringPolicy"},
    "BUSINESS_OPERATOR": set(),
}
EXPECTED = {role: UNIVERSAL | extra for role, extra in RESTRICTED.items()}


def _seed_grants() -> dict[str, set[str]]:
    """(role → permission names) as kb-seed.sql inserts them, anchors excluded."""
    sql = SEED.read_text(encoding="utf-8")
    universal_block = sql.split("-- 2a.")[1].split("-- 2b.")[0]
    restricted_block = sql.split("-- 2b.")[1].split(";")[0]

    roles = re.findall(r"\('([A-Z_]+)'\)", universal_block)
    pairs = re.findall(r"\('([a-z_]+)',\s*'(\w+)'\)", universal_block)
    triples = re.findall(r"\('([A-Z_]+)',\s*'([a-z_]+)',\s*'(\w+)'\)", restricted_block)

    grants: dict[str, set[str]] = defaultdict(set)
    for role in roles:
        for _resource, action in pairs:
            grants[role].add(action)
    for role, _resource, action in triples:
        grants[role].add(action)
    return dict(grants)


def _required_permissions() -> dict[str, list[str]]:
    """Every name passed to require_perm(), mapped to the routes asking for it."""
    required: dict[str, list[str]] = defaultdict(list)
    for path in ROUTERS.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id != "require_perm":
                continue
            for arg in node.args:
                assert isinstance(arg, ast.Constant) and isinstance(arg.value, str), (
                    f"{path.relative_to(REPO_ROOT)}:{node.lineno} passes a non-literal to "
                    "require_perm — this test can no longer see what it requires"
                )
                required[arg.value].append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    return dict(required)


class TestTheCatalogueIsTheVocabulary:
    def test_a_route_cannot_name_a_permission_the_catalogue_lacks(self):
        """The typo guard. A misspelt name is an ImportError while the routes
        are being declared, not a route every role is silently refused."""
        from services.kb_backend.dependencies.auth import require_perm
        from services.kb_backend.dependencies.permissions import UnknownPermission

        with pytest.raises(UnknownPermission) as exc:
            require_perm("viewDocument")

        assert "viewDocument" in str(exc.value)

    def test_a_correctly_spelt_route_declares_without_complaint(self):
        from services.kb_backend.dependencies.auth import require_perm

        assert callable(require_perm("viewDocuments", "deleteData"))

    def test_the_catalogue_covers_every_route(self):
        from services.kb_backend.dependencies.permissions import PERMISSIONS

        assert set(_required_permissions()) <= PERMISSIONS

    def test_the_catalogue_has_no_entry_no_route_uses(self):
        """An entry nothing asks for is either a route that got deleted without
        its permission, or a permission added in anticipation of one."""
        from services.kb_backend.dependencies.permissions import PERMISSIONS

        assert PERMISSIONS == set(_required_permissions())

    def test_the_seed_uses_the_catalogue_resource_for_every_permission(self):
        """`resource` is not part of the lookup, so a mismatch is invisible at
        runtime — and would quietly make the two groupings disagree."""
        from services.kb_backend.dependencies.permissions import CATALOG

        sql = SEED.read_text(encoding="utf-8")
        block = sql.split("-- 2a.")[1].split("-- KBModel")[0]
        pairs = re.findall(r"\('([a-z_]+)',\s*'([a-zA-Z]+)'\)", block)
        triples = re.findall(r"\('[A-Z_]+',\s*'([a-z_]+)',\s*'([a-zA-Z]+)'\)", block)

        mismatched = {
            action: (resource, CATALOG[action])
            for resource, action in pairs + triples
            if action in CATALOG and CATALOG[action] != resource
        }
        assert not mismatched, f"seed resource disagrees with the catalogue: {mismatched}"


class TestTheSeedMatchesTheRoutes:
    def test_every_required_permission_is_seeded_for_someone(self):
        """A route requiring a name no row grants is unreachable by every role —
        a 403 that looks like policy and is really a typo."""
        seeded = set().union(*_seed_grants().values())
        required = _required_permissions()
        orphans = {name: sites for name, sites in required.items() if name not in seeded}

        assert not orphans, (
            "require_perm() names that no seeded row grants to any role — these "
            f"routes are dead: {orphans}"
        )

    def test_every_seeded_permission_is_required_by_some_route(self):
        """The reverse typo: a grant spelled `viewDocument` sits in the table
        looking authoritative and authorising nothing."""
        seeded = set().union(*_seed_grants().values())
        required = set(_required_permissions())
        unused = seeded - required

        assert not unused, (
            f"seeded permissions no route asks for: {sorted(unused)} — a renamed "
            "route decorator leaves exactly this trace"
        )

    def test_the_routes_have_not_grown_a_permission_nobody_reviewed(self):
        """26 names existed when the matrix moved out of code. A new one is
        fine — it just has to arrive with its seed rows."""
        assert set(_required_permissions()) == UNIVERSAL | RESTRICTED["PLATFORM_ADMIN"]


class TestTheSeedMatchesTheRegistryItReplaced:
    def test_each_role_holds_exactly_what_the_code_granted_it(self):
        assert _seed_grants() == EXPECTED

    def test_every_role_is_seeded(self):
        assert set(_seed_grants()) == ROLES

    def test_business_operator_gained_nothing_in_translation(self):
        """The read-only role is where an over-broad seed would do most damage,
        and a CROSS JOIN over all four roles is an easy way to cause one."""
        assert _seed_grants()["BUSINESS_OPERATOR"] == UNIVERSAL

    def test_platform_admin_holds_every_permission(self):
        seeded = _seed_grants()
        assert seeded["PLATFORM_ADMIN"] == set().union(*seeded.values())


class TestAnchorRows:
    def test_anchors_keep_the_uuids_kbdata_rows_point_at(self):
        """These are FK targets for KBData.role_id and are mirrored in
        kb_backend/config.py. Changing one orphans live documents."""
        from services.kb_backend.config import ROLE_PERMISSION_IDS

        sql = SEED.read_text(encoding="utf-8")
        anchors = dict(re.findall(
            r"\('([0-9a-f-]{36})', '([A-Z_]+)',\s*'_role_anchor', 'label'\)", sql,
        ))

        assert {role: uuid for uuid, role in anchors.items()} == ROLE_PERMISSION_IDS

    def test_anchors_are_not_grants(self):
        """`label` must never reach the permission universe — it would be
        a grant for a permission no route defines."""
        seeded = set().union(*_seed_grants().values())
        assert "label" not in seeded
