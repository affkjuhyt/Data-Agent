"""GEN-05 and GEN-06: the production manifests bake in no pilot address, no localhost and no dev
server; what differs per install comes from the inventory and a missing value stops the deploy."""

from __future__ import annotations

import pathlib
import re

import pytest
import yaml

pytestmark = [pytest.mark.unit]

REPO = pathlib.Path(__file__).parents[3]
PRODUCTION = REPO / "deploy" / "k8s" / "production"
DEFAULTS = REPO / "deploy" / "ansible" / "roles" / "k8s_common" / "defaults" / "main.yml"
# A whole placeholder, not a double underscore inside a name (QDRANT__SERVICE__HTTP_PORT).
PLACEHOLDER = re.compile(r"(?<![A-Za-z0-9_])__([A-Z][A-Z0-9_]*)__(?![A-Za-z0-9_])")


def _manifests() -> dict[pathlib.Path, str]:
    return {p: p.read_text() for p in sorted(PRODUCTION.rglob("*.yaml"))}


def test_no_pilot_address_or_localhost_is_baked_into_production() -> None:
    for path, text in _manifests().items():
        assert "10.10.55.101" not in text, path
        assert not re.search(r"localhost:30\d{3}", text), path


def test_every_placeholder_is_an_inventory_value_and_rendering_leaves_none() -> None:
    values = yaml.safe_load(DEFAULTS.read_text())["manifest_values"]
    used = {name for text in _manifests().values() for name in PLACEHOLDER.findall(text)}
    assert used == set(values), "a placeholder without a value, or a value nothing uses"
    sample = dict.fromkeys(values, "rendered")
    for path, text in _manifests().items():
        rendered = PLACEHOLDER.sub(lambda m: sample[m.group(1)], text)
        assert not PLACEHOLDER.search(rendered), path


def test_a_production_deploy_stops_on_a_missing_address() -> None:
    defaults = yaml.safe_load(DEFAULTS.read_text())
    named = re.findall(r"\{\{ (\w+)", " ".join(defaults["manifest_values"].values()))
    assert all(defaults[name] in ("", []) for name in named), "an address has a default"
    preflight = yaml.safe_load(
        (REPO / "deploy/ansible/roles/k8s_preflight/tasks/main.yml").read_text()
    )
    check = next(t for t in preflight if t["name"] == "Check the addresses of a production install")
    assert check["when"] == "k8s_env == 'production'"
    assert check["loop"] == "{{ manifest_values | dict2items }}"


def test_retrieval_reaches_its_models_in_production() -> None:
    for name in ("api", "worker-agent", "worker-document"):
        assert (
            "DOCUMENT_EMBED_URL: __DOCUMENT_EMBED_URL__"
            in (PRODUCTION / "service" / f"{name}.yaml").read_text()
        )
    assert (
        "DOCUMENT_RERANK_URL: __DOCUMENT_RERANK_URL__"
        in (PRODUCTION / "service" / "worker-agent.yaml").read_text()
    )
    policies = [
        d for d in yaml.safe_load_all((PRODUCTION / "networkpolicy-service.yaml").read_text()) if d
    ]
    (models,) = (p for p in policies if p["metadata"]["name"] == "allow-egress-document-models")
    assert set(models["spec"]["podSelector"]["matchExpressions"][0]["values"]) == {
        "api",
        "worker-agent",
        "worker-document",
    }


def test_production_serves_the_built_frontend_with_its_settings_at_runtime() -> None:
    docs = [
        d for d in yaml.safe_load_all((PRODUCTION / "service" / "frontend.yaml").read_text()) if d
    ]
    deployment = next(d for d in docs if d["kind"] == "Deployment")
    (container,) = deployment["spec"]["template"]["spec"]["containers"]
    command = " ".join(container["command"])
    assert "npm run preview" in command and "write-runtime-config.mjs" in command
    assert "npm run dev" not in command
    mounts = {m["mountPath"] for m in container["volumeMounts"]}
    assert {"/app/dist/runtime", "/app/node_modules/.vite-temp"} <= mounts
    overrides = yaml.safe_load(DEFAULTS.read_text())["image_dockerfile_overrides"]
    assert overrides["frontend"] == "{{ 'Dockerfile.dev' if k8s_env == 'dev' else 'Dockerfile' }}"
    dockerfile = (REPO / "frontend" / "Dockerfile").read_text()
    assert "npm run build" in dockerfile and "write-runtime-config.mjs" in dockerfile
    assert (
        '<script src="/runtime/config.js"></script>' in (REPO / "frontend/index.html").read_text()
    )


def test_the_eval_job_names_no_demo_data_space() -> None:
    assert "EVAL_DATA_SPACE_ID: ''" in (PRODUCTION / "job" / "eval.yaml").read_text()
