"""GEN-07 in the deploy: every install sets up its organisation (roles/k8s_org) from values the
inventory must give, and Keycloak keeps the tenant_id that puts a user in it."""

from __future__ import annotations

import json
import pathlib

import pytest
import yaml

pytestmark = [pytest.mark.unit]

REPO = pathlib.Path(__file__).parents[3]
ANSIBLE = REPO / "deploy" / "ansible"
ORG_VARS = (
    "org_id",
    "org_code",
    "org_name",
    "org_locale",
    "org_time_zone",
    "org_currency",
    "org_admin_username",
    "org_admin_email",
)


def test_the_organisation_is_set_up_after_the_realm_and_the_api_are_up() -> None:
    play = yaml.safe_load((ANSIBLE / "deploy.yml").read_text())[0]
    roles = [
        t["ansible.builtin.import_role"]["name"]
        for t in play["tasks"]
        if "ansible.builtin.import_role" in t
    ]
    assert roles.index("k8s_org") == roles.index("k8s_setup") + 1


def test_nothing_about_the_organisation_is_defaulted() -> None:
    defaults = yaml.safe_load(
        (ANSIBLE / "roles" / "k8s_common" / "defaults" / "main.yml").read_text()
    )
    assert {name: defaults[name] for name in ORG_VARS} == dict.fromkeys(ORG_VARS, "")
    tasks = (ANSIBLE / "roles" / "k8s_org" / "tasks" / "main.yml").read_text()
    assert all(f"{name} |" in tasks for name in ORG_VARS)


def test_the_local_inventory_names_the_demo_organisation_and_its_admin() -> None:
    local = yaml.safe_load(
        (ANSIBLE / "inventories" / "k8s-local" / "group_vars" / "all" / "main.yml").read_text()
    )
    assert all(local[name] for name in ORG_VARS)
    demo = json.loads(
        (REPO / "deploy" / "docker" / "keycloak" / "demo" / "aeroflow-users-0.json").read_text()
    )
    admin = next(u for u in demo["users"] if u["username"] == local["org_admin_username"])
    defaults = yaml.safe_load(
        (ANSIBLE / "roles" / "k8s_common" / "defaults" / "main.yml").read_text()
    )
    # org_id is the demo tenant the realm's demo users carry.
    assert local["org_id"] == "{{ seed_demo_tenant }}"
    assert admin["attributes"]["tenant_id"] == [defaults["seed_demo_tenant"]]


def test_keycloak_keeps_user_attributes_and_only_admins_change_them() -> None:
    # Keycloak 24 drops undeclared attributes of a user made through the admin API; a user able to
    # edit their own tenant_id would move into another organisation.
    realm = json.loads((REPO / "deploy" / "docker" / "keycloak" / "realm-export.json").read_text())
    (profile,) = realm["components"]["org.keycloak.userprofile.UserProfileProvider"]
    config = json.loads(profile["config"]["kc.user.profile.config"][0])
    assert config["unmanagedAttributePolicy"] == "ADMIN_EDIT"
