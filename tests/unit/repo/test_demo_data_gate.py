"""GEN-04: an install without seed_demo gets no demo data. create-configmaps.sh is run with a
kubectl that records what it is asked to create, the way the deploy runs it."""

from __future__ import annotations

import json
import os
import pathlib
import stat
import subprocess

import pytest

pytestmark = [pytest.mark.unit]

REPO = pathlib.Path(__file__).parents[3]
SCRIPT = REPO / "deploy" / "k8s" / "create-configmaps.sh"
DEMO_SQL = (
    "v3-demo-seed.sql",
    "v3-adventureworks-seed.sql",
    "v3-adventureworks-context.sql",
    "v3-adventureworks-documents.sql",
    "v3-agent-seed.sql",
)
# Writes one line per call; `apply -f -` reads the manifest create piped to it.
KUBECTL = """#!/bin/sh
printf '%s\\n' "$*" >> "$KUBECTL_LOG"
case "$*" in *"apply -f -"*) cat > /dev/null ;; esac
"""


def _render(tmp_path: pathlib.Path, seed_demo: str) -> list[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    kubectl = bin_dir / "kubectl"
    kubectl.write_text(KUBECTL)
    kubectl.chmod(kubectl.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / f"kubectl-{seed_demo}.log"
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "KUBECTL_LOG": str(log),
        "SEED_DEMO": seed_demo,
    }
    subprocess.run(["bash", str(SCRIPT)], cwd=REPO, env=env, check=True, capture_output=True)
    return log.read_text().splitlines()


def _configmaps(calls: list[str]) -> dict[str, str]:
    made = {}
    for call in calls:
        words = call.split()
        if "create" in words and "configmap" in words:
            made[words[words.index("configmap") + 1]] = call
    return made


def test_without_seed_demo_no_demo_data_is_rendered(tmp_path: pathlib.Path) -> None:
    calls = _render(tmp_path, "0")
    made = _configmaps(calls)
    assert all(name not in " ".join(made.values()) for name in DEMO_SQL)
    assert "adventureworks-docs" not in " ".join(made.values())
    assert made["db-init-sql"].count("--from-file=") == 1
    assert "aeroflow-users-0.json" not in made["keycloak-realm"]
    # An install that once had them stops loading them.
    (delete,) = (c for c in calls if " delete configmap " in f" {c} ")
    for name in ("db-init-demo-sql", "adventureworks-business-documents"):
        assert name in delete


def test_with_seed_demo_the_demo_data_is_rendered_apart(tmp_path: pathlib.Path) -> None:
    made = _configmaps(_render(tmp_path, "1"))
    assert all(name in made["db-init-demo-sql"] for name in DEMO_SQL)
    assert not any(name in made["db-init-sql"] for name in DEMO_SQL)
    assert "aeroflow-users-0.json" in made["keycloak-realm"]
    assert {"adventureworks-business-documents", "adventureworks-product-documents"} <= set(made)


def test_the_realm_itself_carries_no_user() -> None:
    realm = json.loads((REPO / "deploy/docker/keycloak/realm-export.json").read_text())
    assert "users" not in realm
    demo = json.loads((REPO / "deploy/docker/keycloak/demo/aeroflow-users-0.json").read_text())
    assert demo["realm"] == realm["realm"] and len(demo["users"]) == 6


@pytest.mark.parametrize("env", ["dev", "production"])
def test_db_init_applies_demo_sql_only_from_the_optional_configmap(env: str) -> None:
    job = (REPO / "deploy" / "k8s" / env / "job" / "db-init.yaml").read_text()
    assert "name: db-init-demo-sql\n          optional: true" in job
    assert "for f in /demo-sql/*.sql" in job
