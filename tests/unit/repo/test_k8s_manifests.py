"""The k8s manifests, checked the way the migrations are checked.

`tests/integration/migrate/` exists because a migration that only gets exercised by a deploy is a
migration whose failures land in production. The manifests had the same shape of problem and no
equivalent test:
a misspelled field is not an error to the API server, it is a field that
silently does not exist, and the first symptom is a pod that starts without the
setting you thought you gave it.

Three classes of drift are caught here:

  * **Field names.** Walked against the generated OpenAPI models the API server
    itself validates against, so `terminationGracePeriodSecond` fails here
    instead of being quietly dropped at apply time.
  * **The migration gate.** The runtime tier must not start before the schema is
    at head. That is an initContainer, and an initContainer is easy to forget when
    copying a manifest for a new service.
  * **A destructive job in the directory apply.** `kubectl apply -f deploy/k8s/` is the
    documented routine. Anything that drops a database must therefore not be in
    it must ship suspended with backoffLimit 0, which is what this test
    enforces.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
from typing import ClassVar

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
K8S_DIR = REPO_ROOT / "deploy" / "k8s"
MANIFEST_DIR = K8S_DIR / "production"

# Deployments that reach Postgres through the ORM, and so must not serve traffic
# on a schema older than their code.
MIGRATION_GATED = {"api", "worker-agent", "worker-ingest"}
GATE_CONTAINER = "wait-for-migrations"

# Queue consumers and pollers: no inbound traffic, so no Service and no HTTP probe.
HEADLESS_WORKERS = {
    "kb-promote-silver",
    "kb-promote-gold",
    "kb-conflict-resolution",
    "kb-structured-data",
    "langfuse-worker",
    "platform-workers-audit",
    "platform-workers-memory",
    "platform-workers-schema-crawler",
    "platform-workers-web-crawler",
    "worker-agent",
    "worker-ingest",
}


def _docs(path: pathlib.Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]


def _all_docs(directory: pathlib.Path) -> list[tuple[pathlib.Path, dict]]:
    return [
        (f, d)
        for f in sorted(directory.glob("*.yaml"))
        if f.name != "kustomization.yaml"
        for d in _docs(f)
    ]


# `kubectl apply -R -f deploy/k8s/` covers these three tiers plus the shared
# cluster objects. dr/ is deliberately outside it.
APPLIED_DIRS = [
    MANIFEST_DIR,
    MANIFEST_DIR / "database",
    MANIFEST_DIR / "service",
    MANIFEST_DIR / "job",
]
TOP_LEVEL = [pair for d in APPLIED_DIRS for pair in _all_docs(d)]
DEPLOYMENTS = {d["metadata"]["name"]: d for _, d in TOP_LEVEL if d["kind"] == "Deployment"}
WORKLOADS = {
    d["metadata"]["name"]: d
    for _, d in TOP_LEVEL
    if d["kind"] in ("Deployment", "StatefulSet", "Job", "CronJob")
}


def _containers(deployment: dict) -> list[dict]:
    return deployment["spec"]["template"]["spec"]["containers"]


def _init_containers(deployment: dict) -> list[dict]:
    return deployment["spec"]["template"]["spec"].get("initContainers", [])


# ── field names ────────────────────────────────────────────────────────────


class TestManifestsMatchTheApiSchema:
    """Every key must be a real field of its type.

    Uses the official python client's generated models — the same OpenAPI
    definitions the API server validates against — so this is a schema check,
    not a hand-maintained allowlist that rots.
    """

    @staticmethod
    def _walk(node, type_name: str, path: str, k8s, errors: list[str]) -> None:
        if node is None:
            return

        as_list = re.fullmatch(r"list\[(.+)\]", type_name)
        if as_list:
            if not isinstance(node, list):
                errors.append(f"{path}: expected a list, got {type(node).__name__}")
                return
            for i, item in enumerate(node):
                TestManifestsMatchTheApiSchema._walk(
                    item, as_list.group(1), f"{path}[{i}]", k8s, errors
                )
            return

        as_map = re.fullmatch(r"dict\(str, (.+)\)", type_name)
        if as_map:
            if not isinstance(node, dict):
                errors.append(f"{path}: expected a mapping, got {type(node).__name__}")
                return
            for key, value in node.items():
                TestManifestsMatchTheApiSchema._walk(
                    value, as_map.group(1), f"{path}.{key}", k8s, errors
                )
            return

        if type_name in ("str", "int", "float", "bool", "object", "datetime", "date"):
            return

        model = getattr(k8s, type_name, None)
        if model is None or not hasattr(model, "attribute_map"):
            return  # IntOrString, Quantity — no field list to check against

        if not isinstance(node, dict):
            errors.append(f"{path}: expected an object ({type_name}), got {type(node).__name__}")
            return

        wire_to_py = {wire: py for py, wire in model.attribute_map.items()}
        for key, value in node.items():
            if key not in wire_to_py:
                errors.append(f"{path}.{key} is not a field of {type_name}")
                continue
            TestManifestsMatchTheApiSchema._walk(
                value,
                model.openapi_types[wire_to_py[key]],
                f"{path}.{key}",
                k8s,
                errors,
            )

    def test_every_manifest_validates(self):
        # Optional dependency: where it is absent this check is skipped rather
        # than faked. It is in the `test` extra so CI does run it.
        k8s = pytest.importorskip(
            "kubernetes.client",
            reason="pip install kubernetes — needed to validate manifests offline",
        )
        models = {
            ("v1", "Namespace"): "V1Namespace",
            ("v1", "ServiceAccount"): "V1ServiceAccount",
            ("rbac.authorization.k8s.io/v1", "Role"): "V1Role",
            ("rbac.authorization.k8s.io/v1", "RoleBinding"): "V1RoleBinding",
            ("rbac.authorization.k8s.io/v1", "ClusterRole"): "V1ClusterRole",
            ("rbac.authorization.k8s.io/v1", "ClusterRoleBinding"): "V1ClusterRoleBinding",
            ("v1", "LimitRange"): "V1LimitRange",
            ("apps/v1", "Deployment"): "V1Deployment",
            ("apps/v1", "StatefulSet"): "V1StatefulSet",
            ("v1", "Service"): "V1Service",
            ("v1", "ConfigMap"): "V1ConfigMap",
            ("v1", "PersistentVolumeClaim"): "V1PersistentVolumeClaim",
            ("autoscaling/v2", "HorizontalPodAutoscaler"): "V2HorizontalPodAutoscaler",
            ("batch/v1", "Job"): "V1Job",
            ("batch/v1", "CronJob"): "V1CronJob",
            ("networking.k8s.io/v1", "NetworkPolicy"): "V1NetworkPolicy",
        }

        errors: list[str] = []
        for path, doc in TOP_LEVEL:
            key = (doc.get("apiVersion"), doc.get("kind"))
            model_name = models.get(key)
            if model_name is None:
                errors.append(f"{path.name}: unhandled {key} — add it to this test's map")
                continue
            where = f"{path.name}:{doc['kind']}/{doc['metadata']['name']}"
            self._walk(doc, model_name, where, k8s, errors)

        assert not errors, "\n".join(errors)


# ── names the API server would reject ──────────────────────────────────────

# RFC 1123 label: what the API server enforces for object names, Service names
# and container names. `metadata.name: workflow_runtime` is not a schema error
# (it is a perfectly valid string), it is a create-time rejection — which the
# schema walk above cannot catch, so it gets its own pure-YAML check that runs
# even without the kubernetes package installed.
DNS_1123 = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


class TestNamesAreValidDns1123:
    def _bad_names(self) -> list[str]:
        bad: list[str] = []
        for path, doc in TOP_LEVEL:
            name = doc.get("metadata", {}).get("name", "")
            if not DNS_1123.fullmatch(name):
                bad.append(f"{path.name}: {doc.get('kind')}/{name!r} (metadata.name)")
            spec = doc.get("spec", {})
            for pod in (
                spec.get("template", {}).get("spec"),
                spec.get("jobTemplate", {}).get("spec", {}).get("template", {}).get("spec"),
            ):
                if not pod:
                    continue
                for c in pod.get("containers", []) + pod.get("initContainers", []):
                    if not DNS_1123.fullmatch(c.get("name", "")):
                        bad.append(
                            f"{path.name}: {doc.get('kind')}/{name} container {c.get('name')!r}"
                        )
        return bad

    def test_every_object_and_container_name_is_a_dns_label(self):
        """Underscores in a name (copied from a Python package name) are the classic bug: valid
        YAML, valid schema, rejected by the API server at apply time."""
        bad = self._bad_names()
        assert not bad, "not DNS-1123 (^[a-z0-9]([-a-z0-9]*[a-z0-9])?$):\n" + "\n".join(bad)


# ── the migration gate ─────────────────────────────────────────────────────


class TestNoPodServesOnAnOlderSchema:
    @pytest.mark.parametrize("name", sorted(MIGRATION_GATED))
    def test_the_runtime_tier_waits_for_alembic(self, name):
        init_names = [c["name"] for c in _init_containers(DEPLOYMENTS[name])]
        assert GATE_CONTAINER in init_names, (
            f"{name} starts without waiting for the schema. A pod rescheduled "
            f"during a rolling upgrade would serve on the old schema."
        )

    def test_the_gate_and_the_job_run_the_same_revision_graph(self):
        """Two alembic images means two answers to 'what is head'. The gate
        would then wait for a head the Job never applies, or wave through a
        schema the Job has not finished with."""
        job = next(
            d for _, d in TOP_LEVEL if d["kind"] == "Job" and d["metadata"]["name"] == "migrate"
        )
        job_image = job["spec"]["template"]["spec"]["containers"][0]["image"]

        gates = [
            (name, c["image"])
            for name, dep in DEPLOYMENTS.items()
            for c in _init_containers(dep)
            if c["name"] == GATE_CONTAINER
        ]
        assert gates, "no wait-for-migrations initContainer found at all"
        for name, image in gates:
            assert image == job_image, f"{name}'s gate image {image} != migrate Job's {job_image}"

    def test_the_gate_actually_blocks_rather_than_just_reporting(self):
        """A gate that runs `alembic current` and exits 0 regardless is
        decoration. It has to loop until the database says head."""
        for name, dep in DEPLOYMENTS.items():
            for c in _init_containers(dep):
                if c["name"] != GATE_CONTAINER:
                    continue
                script = " ".join(c["command"])
                assert (
                    "until" in script and "(head)" in script
                ), f"{name}'s gate does not wait for head: {script!r}"


# ── probes ─────────────────────────────────────────────────────────────────


class TestEveryDeploymentIsProbed:
    @pytest.mark.parametrize("name", sorted(set(DEPLOYMENTS) - HEADLESS_WORKERS))
    def test_readiness_and_liveness_are_both_set(self, name):
        """Readiness alone lets a wedged pod stay in the rotation; liveness
        alone sends traffic to a pod that has not finished starting."""
        for container in _containers(DEPLOYMENTS[name]):
            assert container.get(
                "readinessProbe"
            ), f"{name}/{container['name']} has no readinessProbe"
            assert container.get(
                "livenessProbe"
            ), f"{name}/{container['name']} has no livenessProbe"

    @pytest.mark.parametrize("name", sorted(set(DEPLOYMENTS) - HEADLESS_WORKERS))
    def test_a_service_selects_the_deployment(self, name):
        services = [d for _, d in TOP_LEVEL if d["kind"] == "Service"]
        labels = DEPLOYMENTS[name]["spec"]["template"]["metadata"]["labels"]
        assert any(
            all(labels.get(k) == v for k, v in s["spec"]["selector"].items()) for s in services
        ), f"no Service selects {name}'s pods — the manifest would be unreachable"


# ── the shell inside the manifests ─────────────────────────────────────────


class TestInlineScriptsAreValidShell:
    """A manifest carries shell as a string, so nothing checks it until the pod
    runs — and a backup or restore job is the worst place to learn that.

    This caught a real one: a backup sidecar's loop was a single-quoted
    `sh -c '...'` argument, and a comment containing an apostrophe ("pg_dump's")
    closed the quote and turned the rest of the loop into a syntax error.
    """

    @staticmethod
    def _sh():
        shell = shutil.which("sh") or shutil.which("bash")
        if shell is None:
            pytest.skip("no POSIX shell available to syntax-check with")
        return shell

    @staticmethod
    def _pod_specs(doc: dict):
        spec = doc.get("spec", {})
        for candidate in (
            spec.get("template", {}).get("spec"),
            spec.get("jobTemplate", {}).get("spec", {}).get("template", {}).get("spec"),
        ):
            if candidate:
                yield candidate

    def _inline_scripts(self):
        """(label, script) for every `sh -c <script>` in the manifests."""
        for path, doc in TOP_LEVEL:
            for pod in self._pod_specs(doc):
                for c in pod.get("containers", []) + pod.get("initContainers", []):
                    cmd = c.get("command") or []
                    if len(cmd) == 3 and cmd[0] in ("sh", "bash") and cmd[1] == "-c":
                        yield f"{path.name}:{doc['metadata']['name']}/{c['name']}", cmd[2]

    def test_every_inline_script_parses(self, tmp_path):
        scripts = list(self._inline_scripts())
        assert scripts, "no inline scripts found — did the extraction break?"
        for label, script in scripts:
            f = tmp_path / "s.sh"
            f.write_bytes(script.encode("utf-8"))
            done = subprocess.run([self._sh(), "-n", str(f)], capture_output=True, text=True)
            assert done.returncode == 0, f"{label} is not valid shell:{chr(10)}{done.stderr}"


# ── a dump is not a backup until it restores ───────────────────────────────


class TestBackupsAreVerifiedNotAssumed:
    def _script(self, path: pathlib.Path, container: str) -> str:
        for doc in _docs(path):
            spec = doc.get("spec", {})
            pod = spec.get("template", {}).get("spec") or spec.get("jobTemplate", {}).get(
                "spec", {}
            ).get("template", {}).get("spec")
            for c in (pod or {}).get("containers", []):
                if c["name"] == container:
                    return c["command"][2]
        raise AssertionError(f"{container} not found in {path.name}")

    def test_the_dump_is_checked_because_the_exit_code_cannot_be(self):
        """`pg_dump | gzip > f` reports gzip's status, so a dump that dies
        mid-stream exits 0 and leaves a file that looks fine until restore
        day. The artifact has to be verified instead."""
        script = self._script(MANIFEST_DIR / "job" / "postgres-backup.yaml", "postgres-backup")
        assert "gzip -t" in script
        assert "PostgreSQL database dump complete" in script

    def test_the_restore_verifies_before_it_drops(self):
        """Ordering is the whole point: verifying after the DROP means finding
        out the archive is truncated with the database already gone."""
        script = self._script(MANIFEST_DIR / "job" / "postgres-restore.yaml", "pg-restore")
        assert "gzip -t" in script, "restore does not verify the dump at all"
        assert script.index("gzip -t") < script.index("DROP DATABASE"), (
            "restore drops the database before verifying the dump it is about " "to restore from"
        )


# ── the directory apply must stay safe ─────────────────────────────────────


class TestNothingDestructiveInTheRoutineApply:
    def test_a_destructive_job_cannot_start_on_apply(self):
        """The restore job sits in `job/` with everything else, so the directory
        no longer protects anything — `suspend: true` is the only thing between
        `kubectl apply` and a dropped database."""
        for path, doc in TOP_LEVEL:
            if "DROP DATABASE" not in yaml.dump(doc).upper():
                continue
            assert doc["kind"] == "Job", (
                f"{path.name} drops a database and is not a Job — it could run "
                f"on any apply or reschedule"
            )
            assert doc["spec"].get("suspend") is True, (
                f"{path.name} drops a database and is not suspended. "
                f"`kubectl apply -f deploy/k8s/production/job/` would start it."
            )
            assert (
                doc["spec"].get("backoffLimit") == 0
            ), f"{path.name} would retry a half-applied restore"

    def test_the_restore_job_ships_suspended(self):
        restore = next(
            d for d in _docs(MANIFEST_DIR / "job" / "postgres-restore.yaml") if d["kind"] == "Job"
        )
        assert (
            restore["spec"]["suspend"] is True
        ), "the restore Job must not start on apply — it drops the database"
        assert (
            restore["spec"]["backoffLimit"] == 0
        ), "a half-applied restore must not be retried automatically"


# ── pod security ───────────────────────────────────────────────────────────


class TestEveryPodIsHardened:
    """The namespace enforces the `restricted` Pod Security Standard, which is
    an admission check: a pod that violates it is rejected, not degraded. These
    assertions fail here instead, where the message says which field is missing.
    """

    @staticmethod
    def _pods():
        for path, doc in TOP_LEVEL:
            spec = doc.get("spec", {})
            for pod in (
                spec.get("template", {}).get("spec"),
                spec.get("jobTemplate", {}).get("spec", {}).get("template", {}).get("spec"),
            ):
                if pod:
                    yield f"{path.name}:{doc['metadata']['name']}", pod

    def test_no_pod_runs_as_root(self):
        bad = [
            w
            for w, pod in self._pods()
            if pod.get("securityContext", {}).get("runAsNonRoot") is not True
            or not pod.get("securityContext", {}).get("runAsUser")
        ]
        assert not bad, f"pods without an explicit non-root uid: {bad}"

    def test_every_pod_sets_the_default_seccomp_profile(self):
        bad = [
            w
            for w, pod in self._pods()
            if pod.get("securityContext", {}).get("seccompProfile", {}).get("type")
            != "RuntimeDefault"
        ]
        assert not bad, f"pods without seccompProfile RuntimeDefault: {bad}"

    def test_no_pod_mounts_a_service_account_token(self):
        """A mounted token is a credential waiting to be stolen out of a
        compromised container. OpenBao calls the API server (TokenReview for
        the Kubernetes auth method) and sandbox-executor manages its own pool
        of execution pods (backend/services/sandbox_executor/k8s_pool.py) —
        the two deliberate exceptions, and the only two with a RoleBinding."""
        bad = [
            w
            for w, pod in self._pods()
            if pod.get("automountServiceAccountToken") is not False
            and not w.endswith("openbao")
            and not w.endswith("sandbox-executor")
        ]
        assert not bad, f"pods still automounting a SA token: {bad}"

    def test_every_container_drops_privileges(self):
        bad = []
        for w, pod in self._pods():
            for c in pod.get("containers", []) + pod.get("initContainers", []):
                sc = c.get("securityContext", {})
                if (
                    sc.get("allowPrivilegeEscalation") is not False
                    or sc.get("readOnlyRootFilesystem") is not True
                    or sc.get("capabilities", {}).get("drop") != ["ALL"]
                ):
                    bad.append(f"{w}/{c['name']}")
        assert not bad, (
            "containers missing allowPrivilegeEscalation:false, "
            f"readOnlyRootFilesystem:true or capabilities.drop:[ALL]: {bad}"
        )

    def test_no_container_is_privileged_or_uses_the_host(self):
        bad = []
        for w, pod in self._pods():
            for key in ("hostNetwork", "hostPID", "hostIPC"):
                if pod.get(key):
                    bad.append(f"{w}: {key}")
            for v in pod.get("volumes", []):
                if "hostPath" in v:
                    bad.append(f"{w}: hostPath volume {v['name']}")
            for c in pod.get("containers", []) + pod.get("initContainers", []):
                if c.get("securityContext", {}).get("privileged"):
                    bad.append(f"{w}/{c['name']}: privileged")
        assert not bad, f"host access or privileged containers: {bad}"


class TestEveryContainerIsBounded:
    def test_requests_and_limits_are_set(self):
        """A container with no limit is a container that can evict its
        neighbours; one with no request cannot be scheduled predictably."""
        bad = []
        for w, pod in TestEveryPodIsHardened._pods():
            for c in pod.get("containers", []) + pod.get("initContainers", []):
                r = c.get("resources", {})
                if not r.get("limits") or not r.get("requests"):
                    bad.append(f"{w}/{c['name']}")
        assert not bad, f"containers without both requests and limits: {bad}"


# ── the port plan ──────────────────────────────────────────────────────────

LLM_RESERVED = range(8000, 8100)
ALLOWED = range(8100, 8600)


class TestPortsFollowThePlan:
    """`deploy/k8s/README.md` documents one number per workload, in 8xxx, with
    8000-8099 reserved for the LLM tier. These keep the manifests honest to it."""

    @staticmethod
    def _services():
        return [d for _, d in TOP_LEVEL if d["kind"] == "Service"]

    def test_every_port_is_in_the_8xxx_plan(self):
        bad = []
        for _, doc in TOP_LEVEL:
            if doc["kind"] == "Service":
                for p in doc["spec"]["ports"]:
                    for key in ("port", "targetPort"):
                        v = p.get(key)
                        if isinstance(v, int) and v not in ALLOWED:
                            bad.append(f"Service/{doc['metadata']['name']}.{key}={v}")
                continue
            spec = doc.get("spec", {})
            tmpl = spec.get("template") or spec.get("jobTemplate", {}).get("spec", {}).get(
                "template"
            )
            pod = (tmpl or {}).get("spec")
            for c in (pod or {}).get("containers", []):
                for p in c.get("ports", []):
                    if p["containerPort"] not in ALLOWED:
                        bad.append(f"{doc['metadata']['name']}/{c['name']}={p['containerPort']}")
        assert not bad, f"ports outside 8100-8599: {bad}"

    def test_the_llm_band_is_left_free(self):
        used = [
            p["port"]
            for s in self._services()
            for p in s["spec"]["ports"]
            if p["port"] in LLM_RESERVED
        ]
        assert not used, f"8000-8099 is reserved for the LLM tier, but {used} is taken"

    def test_no_two_services_share_a_port(self):
        seen: dict[int, str] = {}
        clashes = []
        for s in self._services():
            for p in s["spec"]["ports"]:
                owner = seen.setdefault(p["port"], s["metadata"]["name"])
                if owner != s["metadata"]["name"]:
                    clashes.append(f"{p['port']}: {owner} and {s['metadata']['name']}")
        assert not clashes, f"port collisions: {clashes}"

    def test_service_port_equals_target_port(self):
        """One number per workload — nothing to translate when reading a URL."""
        bad = [
            f"{s['metadata']['name']}:{p['port']}->{p['targetPort']}"
            for s in self._services()
            for p in s["spec"]["ports"]
            if p["port"] != p["targetPort"]
        ]
        assert not bad, f"Service port != targetPort: {bad}"

    def test_no_env_value_points_at_a_pre_migration_port(self):
        legacy = re.compile(
            r"\b(?:postgres|mongo|redis|qdrant|neo4j|kafka|minio|clickhouse|"
            r"openbao|keycloak|kong|kong-admin|litellm|langfuse|jaeger)"
            r":(?!8[1-5]\d\d\b)\d{4,5}\b"
        )
        bad = []
        for path, doc in TOP_LEVEL:
            spec = doc.get("spec", {})
            tmpl = spec.get("template") or spec.get("jobTemplate", {}).get("spec", {}).get(
                "template"
            )
            pod = (tmpl or {}).get("spec")
            for c in (pod or {}).get("containers", []) + (pod or {}).get("initContainers", []):
                for e in c.get("env", []):
                    v = e.get("value")
                    if isinstance(v, str) and legacy.search(v):
                        bad.append(f"{path.name}:{e['name']}={v}")
            if doc["kind"] == "ConfigMap":
                for k, v in (doc.get("data") or {}).items():
                    if isinstance(v, str) and legacy.search(v):
                        bad.append(f"{path.name}:{k}={v}")
        assert not bad, f"env still on a pre-migration port: {bad}"

    def test_grafana_datasources_point_at_a_real_service_port(self):
        """The datasource files become the grafana-datasources ConfigMap
        (create-configmaps.sh), so nothing in the manifests would flag a wrong
        port: Grafana starts fine and every panel just shows "no data"."""
        ports = {
            (s["metadata"]["name"], s["metadata"]["namespace"]): {
                p["port"] for p in s["spec"]["ports"]
            }
            for s in self._services()
        }
        url = re.compile(r"url:\s*https?://([a-z0-9-]+)\.([a-z0-9-]+)\.svc\.cluster\.local:(\d+)")
        datasources = (
            REPO_ROOT
            / "deploy"
            / "docker"
            / "monitoring"
            / "grafana"
            / "provisioning"
            / "datasources"
        )
        bad, checked = [], 0
        for path in sorted(datasources.glob("*.y*ml")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip().startswith("url:"):
                    continue
                m = url.search(line)
                if not m:
                    bad.append(f"{path.name}: {line.strip()} is not a Service FQDN")
                    continue
                checked += 1
                name, namespace, port = m.group(1), m.group(2), int(m.group(3))
                if port not in ports.get((name, namespace), set()):
                    bad.append(f"{path.name}: {name}.{namespace}:{port} is not a Service port")
        assert checked, "no datasource URL found"
        assert not bad, bad


# ── namespaces, identity and network isolation ─────────────────────────────

SVC_NS, DB_NS, SANDBOX_NS = "dataagent-service", "dataagent-database", "dataagent-sandbox"
ALL_NS = (SVC_NS, DB_NS, SANDBOX_NS)


class TestTheNamespaces:
    def test_both_exist_and_are_part_of_dataagent(self):
        found = {
            d["metadata"]["name"]: d["metadata"]["labels"]
            for _, d in TOP_LEVEL
            if d["kind"] == "Namespace"
        }
        assert set(found) == set(ALL_NS), f"expected {sorted(ALL_NS)}, got {sorted(found)}"
        for name, labels in found.items():
            assert labels.get("app.kubernetes.io/part-of") == "dataagent", name
            assert labels.get("pod-security.kubernetes.io/enforce") == "restricted", name
            # the NetworkPolicies select namespaces by this label
            assert labels.get("name") == name, f"{name} needs a `name` label for netpol selectors"

    def test_every_namespaced_object_lands_in_one_of_them(self):
        bad = [
            f"{p.name}:{d['kind']}/{d['metadata']['name']}={d['metadata'].get('namespace')}"
            for p, d in TOP_LEVEL
            if d["kind"] not in ("Namespace", "ClusterRole", "ClusterRoleBinding")
            and d["metadata"].get("namespace") not in ALL_NS
        ]
        assert not bad, f"objects outside the three namespaces: {bad}"

    def test_the_stores_are_in_the_database_namespace(self):
        stores = {
            "postgres",
            "mongo",
            "redis",
            "qdrant",
            "neo4j",
            "kafka",
            "minio",
            "clickhouse",
            "openbao",
            "pgbouncer",
        }
        bad = [
            n for n, d in WORKLOADS.items() if n in stores and d["metadata"]["namespace"] != DB_NS
        ]
        assert not bad, f"stores outside {DB_NS}: {bad}"


class TestDenyAllIsTheDefault:
    @pytest.mark.parametrize("namespace", [SVC_NS, DB_NS, SANDBOX_NS])
    def test_each_namespace_denies_all_ingress_and_egress(self, namespace):
        """An empty podSelector with no rules is the only shape that denies
        everything — inside the namespace as well as across it."""
        policies = [
            d
            for _, d in TOP_LEVEL
            if d["kind"] == "NetworkPolicy" and d["metadata"]["namespace"] == namespace
        ]
        deny = [d for d in policies if d["metadata"]["name"] == "default-deny-all"]
        assert deny, f"{namespace} has no default-deny-all policy"
        spec = deny[0]["spec"]
        assert spec["podSelector"] == {}, "must select every pod"
        assert set(spec["policyTypes"]) == {"Ingress", "Egress"}
        assert (
            "ingress" not in spec and "egress" not in spec
        ), "a deny-all policy carries no allow rules"

    @pytest.mark.parametrize("namespace", [SVC_NS, DB_NS])
    def test_dns_is_allowed_or_nothing_resolves(self, namespace):
        policies = [
            d
            for _, d in TOP_LEVEL
            if d["kind"] == "NetworkPolicy" and d["metadata"]["namespace"] == namespace
        ]
        assert any(
            d["metadata"]["name"] == "allow-dns" for d in policies
        ), f"{namespace} denies egress with no DNS exception — every lookup fails"

    def test_the_sandbox_namespace_has_no_allow_rules_at_all(self):
        """dataagent-sandbox is the k8s-native equivalent of the Docker
        backend's `network_disabled=True` (no network namespace at all,
        docker_runner.py/k8s_pool.py module docstrings) — not of the other
        two namespaces' "reopen one flow at a time" pattern. Not even DNS:
        a sandboxed exec has no legitimate reason to resolve anything."""
        policies = [
            d
            for _, d in TOP_LEVEL
            if d["kind"] == "NetworkPolicy" and d["metadata"]["namespace"] == SANDBOX_NS
        ]
        assert len(policies) == 1 and policies[0]["metadata"]["name"] == "default-deny-all", (
            f"dataagent-sandbox must carry only default-deny-all, found: "
            f"{sorted(d['metadata']['name'] for d in policies)}"
        )


class TestEveryWorkloadHasItsOwnIdentity:
    def test_each_workload_names_a_service_account_of_its_own(self):
        accounts = {
            (d["metadata"]["namespace"], d["metadata"]["name"])
            for _, d in TOP_LEVEL
            if d["kind"] == "ServiceAccount"
        }
        bad = []
        for name, d in WORKLOADS.items():
            spec = d["spec"]
            tmpl = spec.get("template") or spec["jobTemplate"]["spec"]["template"]
            pod = tmpl["spec"]
            ns = d["metadata"]["namespace"]
            if pod.get("serviceAccountName") != name:
                bad.append(f"{name}: serviceAccountName={pod.get('serviceAccountName')}")
            elif (ns, name) not in accounts:
                bad.append(f"{name}: no ServiceAccount object in {ns}")
        assert not bad, f"workloads without a dedicated ServiceAccount: {bad}"

    def test_only_the_two_deliberate_exceptions_are_granted_anything(self):
        """A ServiceAccount with no binding can call almost nothing. That is
        the default here; every exception has to be deliberate. OpenBao
        (TokenReview for the Kubernetes auth method) and sandbox-executor
        (manages its own pool of execution pods, k8s_pool.py) are the two."""
        granted = {
            s["name"]
            for _, d in TOP_LEVEL
            if d["kind"] in ("RoleBinding", "ClusterRoleBinding")
            for s in d["subjects"]
            if s["kind"] == "ServiceAccount"
        }
        assert granted == {
            "openbao",
            "sandbox-executor",
        }, f"only openbao and sandbox-executor should hold RBAC; these do: {sorted(granted)}"

    def test_openbao_is_granted_only_what_the_auth_method_needs(self):
        rules = [
            r
            for _, d in TOP_LEVEL
            if d["kind"] in ("Role", "ClusterRole") and d["metadata"]["name"].startswith("openbao")
            for r in d["rules"]
        ]
        allowed = {
            ("", "serviceaccounts", "get"),
            ("", "serviceaccounts/token", "create"),
            ("authentication.k8s.io", "tokenreviews", "create"),
        }
        actual = {
            (g, res, v)
            for r in rules
            for g in r["apiGroups"]
            for res in r["resources"]
            for v in r["verbs"]
        }
        assert actual <= allowed, f"OpenBao granted more than it needs: {sorted(actual - allowed)}"
        assert not any(
            "*" in r["verbs"] or "*" in r["resources"] for r in rules
        ), "no wildcard verbs or resources"

    def test_sandbox_executor_is_granted_only_pod_lifecycle_in_its_own_namespace(self):
        """k8s_pool.py needs to create/watch/retire its own pool's pods and
        exec into them — nothing about Secrets, ConfigMaps, or any other
        namespace's resources."""
        role_docs = [
            d
            for _, d in TOP_LEVEL
            if d["kind"] == "Role" and d["metadata"]["name"] == "sandbox-executor-pool"
        ]
        assert role_docs, "expected a sandbox-executor-pool Role"
        for d in role_docs:
            assert d["metadata"]["namespace"] == "dataagent-sandbox", (
                "sandbox-executor's Role must be scoped to dataagent-sandbox, "
                f"not {d['metadata']['namespace']}"
            )
        rules = [r for d in role_docs for r in d["rules"]]
        allowed = {
            ("", "pods", "get"),
            ("", "pods", "list"),
            ("", "pods", "watch"),
            ("", "pods", "create"),
            ("", "pods", "delete"),
            ("", "pods/exec", "get"),
            ("", "pods/exec", "create"),
            ("", "pods/log", "get"),
        }
        actual = {
            (g, res, v)
            for r in rules
            for g in r["apiGroups"]
            for res in r["resources"]
            for v in r["verbs"]
        }
        assert (
            actual <= allowed
        ), f"sandbox-executor granted more than it needs: {sorted(actual - allowed)}"
        assert not any(
            "*" in r["verbs"] or "*" in r["resources"] for r in rules
        ), "no wildcard verbs or resources"


class TestCrossNamespaceHostnamesAreQualified:
    def test_no_short_service_name_survives_the_split(self):
        """`postgres:8300` resolves only inside the namespace that owns it.
        Across the two namespaces every hostname has to be an FQDN."""
        hosts = [
            "postgres",
            "mongo",
            "redis",
            "qdrant",
            "neo4j",
            "kafka",
            "minio",
            "clickhouse",
            "openbao",
            "pgbouncer",
            "kong-admin",
            "kong",
            "keycloak",
            "litellm",
            "langfuse",
            "jaeger",
            "prometheus",
            "grafana",
            "auth-api",
            "kb-backend",
            "model-service",
            "workflow-runtime",
            "api",
            "sandbox-executor",
            "llm-exporter",
            "frontend",
        ]
        pattern = re.compile(
            r"(?<![\w.-])(" + "|".join(sorted(hosts, key=len, reverse=True)) + r"):\d{4}"
        )
        # An image reference is `repo/name:tag`, not a hostname: `bitnamilegacy/minio:2024…`
        # matches the pattern and is checked by TestImageReferencesAreIntact instead.
        image_line = re.compile(r"^\s*(?:-\s+)?image:\s")
        bad = []
        for path in sorted(f for d in APPLIED_DIRS for f in d.glob("*.yaml")):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if image_line.match(line):
                    continue
                if pattern.search(line) and ".svc.cluster.local" not in line:
                    bad.append(f"{path.name}:{i} {line.strip()[:80]}")
        assert not bad, f"unqualified cross-namespace hostnames: {bad}"


# ── configuration and secrets ──────────────────────────────────────────────


class TestConfigComesFromConfigMapsAndSecrets:
    """Nothing is set inline: literal values live in `<workload>-env`, secrets in
    `<workload>-secrets`. That keeps the pod spec free of configuration and, more
    importantly, stops every pod receiving every credential."""

    @staticmethod
    def _containers():
        for _path, doc in TOP_LEVEL:
            spec = doc.get("spec", {})
            tmpl = spec.get("template") or spec.get("jobTemplate", {}).get("spec", {}).get(
                "template"
            )
            pod = (tmpl or {}).get("spec")
            if not pod:
                continue
            for c in pod.get("containers", []) + pod.get("initContainers", []):
                yield doc["metadata"]["name"], doc["metadata"]["namespace"], c

    def test_no_literal_env_is_set_inline(self):
        bad = [
            f"{w}/{c['name']}:{e['name']}"
            for w, _, c in self._containers()
            for e in c.get("env", [])
            if "value" in e
        ]
        assert not bad, f"literal env still inline instead of in a ConfigMap: {bad}"

    def test_no_workload_receives_a_shared_secret_wholesale(self):
        """`envFrom: secretRef: <shared>` hands a pod every key in that Secret.
        Each workload gets its own, holding only what it reads."""
        bad = []
        for w, _, c in self._containers():
            for src in c.get("envFrom", []):
                name = (src.get("secretRef") or {}).get("name")
                if name and not name.endswith("-secrets"):
                    bad.append(f"{w}/{c['name']} -> {name}")
                elif name and not name.startswith(w):
                    bad.append(f"{w}/{c['name']} reads another workload's secret {name}")
        assert not bad, f"unscoped secret references: {bad}"

    def test_every_env_configmap_referenced_is_committed(self):
        defined = {
            (d["metadata"]["namespace"], d["metadata"]["name"])
            for _, d in TOP_LEVEL
            if d["kind"] == "ConfigMap"
        }
        bad = [
            f"{w}: {name}"
            for w, ns, c in self._containers()
            for src in c.get("envFrom", [])
            if (name := (src.get("configMapRef") or {}).get("name")) and (ns, name) not in defined
        ]
        assert not bad, f"envFrom references a ConfigMap no manifest creates: {bad}"

    def test_shared_config_is_read_before_the_workload_overrides_it(self):
        """envFrom sources are merged in order, so `dataagent-config` has to come
        first or a workload cannot override a shared default."""
        bad = []
        for w, _, c in self._containers():
            names = [
                (s.get("configMapRef") or s.get("secretRef") or {}).get("name")
                for s in c.get("envFrom", [])
            ]
            if (
                "dataagent-config" in names
                and f"{w}-env" in names
                and names.index("dataagent-config") > names.index(f"{w}-env")
            ):
                bad.append(w)
        assert not bad, f"workload ConfigMap listed before the shared one: {bad}"

    def test_the_auth_chain_keys_reach_exactly_the_services_that_verify_them(self):
        """GATEWAY_KEY and SERVICE_AUTH_SECRET fail silently when missing, so the
        set of holders is pinned to the services importing libs.shared.auth, plus,
        for SERVICE_AUTH_SECRET, sandbox-executor, which verifies X-Service-Assertion
        through libs.shared.service_identity (sandbox_executor/auth.py)."""
        script = (K8S_DIR / "create-secrets.sh").read_text(encoding="utf-8")
        for key in ("GATEWAY_KEY", "SERVICE_AUTH_SECRET"):
            holders = {
                m.split("create secret generic ")[1].split("-secrets")[0]
                for m in script.split("kubectl -n ")[1:]
                if key in m
            }
            expected = {"auth-api", "kb-backend"}
            if key == "GATEWAY_KEY":
                expected |= {"setup"}  # kong-setup.sh writes it into the plugin
                # Not the eval Job: model/eval's agent_client calls the public
                # V3 API with a Keycloak token (EVAL_ACCESS_TOKEN), not as Kong.
            else:
                expected |= {"sandbox-executor"}
            assert (
                holders == expected
            ), f"{key} goes to {sorted(holders)}, expected {sorted(expected)}"


class TestImageReferencesAreIntact:
    """A rewrite pass that requalifies `host:port` will happily eat `postgres:16`
    as well. Nothing else validates an image string, so this does."""

    KNOWN: ClassVar[set[str]] = {
        "postgres:16-alpine",
        "mongo:7.0",
        "redis:7-alpine",
        "qdrant/qdrant:v1.13.6",
        "neo4j:5.20",
        "apache/kafka:3.7.0",
        "bitnamilegacy/minio:2024.12.18-debian-12-r0",
        "docker.io/clickhouse/clickhouse-server",
        # Pinned: 2.7 dropped the file storage backend this deployment uses.
        "openbao/openbao:2.6.3",
        "edoburu/pgbouncer:latest",
        "quay.io/keycloak/keycloak:24.0",
        "ghcr.io/berriai/litellm:main-stable",
        "docker.io/langfuse/langfuse:3",
        "docker.io/langfuse/langfuse-worker:3",
        "jaegertracing/all-in-one:1.55",
        "prom/prometheus:v2.54.1",
        "grafana/grafana:11.2.0",
        "python:3.12-alpine",
    }

    @staticmethod
    def _images():
        for _path, doc in TOP_LEVEL:
            spec = doc.get("spec", {})
            tmpl = spec.get("template") or spec.get("jobTemplate", {}).get("spec", {}).get(
                "template"
            )
            pod = (tmpl or {}).get("spec")
            if not pod:
                continue
            for c in pod.get("containers", []) + pod.get("initContainers", []):
                yield f"{doc['metadata']['name']}/{c['name']}", c["image"]

    def test_no_image_was_rewritten_into_a_service_hostname(self):
        bad = [f"{w}: {i}" for w, i in self._images() if "svc.cluster.local" in i]
        assert not bad, f"image reference mangled into a hostname: {bad}"

    def test_every_image_is_one_we_recognise(self):
        bad = [
            f"{w}: {i}"
            for w, i in self._images()
            if not i.startswith("dataagent/") and i not in self.KNOWN
        ]
        assert not bad, f"unexpected image — add it to KNOWN if deliberate: {bad}"

    def test_our_images_all_carry_the_dataagent_prefix(self):
        bad = [
            f"{w}: {i}"
            for w, i in self._images()
            if "/" in i and i not in self.KNOWN and not i.startswith("dataagent/")
        ]
        assert not bad, f"built image outside the dataagent/ namespace: {bad}"


class TestPostgresClientsUseTheRightPort:
    """Postgres moved to 8300; psql defaults to 5432 when PGPORT is unset, so a
    client that only sets PGHOST silently dials the wrong port."""

    def test_every_pghost_has_a_matching_pgport(self):
        bad = []
        for _, doc in TOP_LEVEL:
            if doc["kind"] != "ConfigMap":
                continue
            data = doc.get("data") or {}
            if "PGHOST" in data and data.get("PGPORT") != "8300":
                bad.append(f"{doc['metadata']['name']}: PGPORT={data.get('PGPORT')}")
        assert not bad, f"Postgres clients on the wrong port: {bad}"


class TestFileBackedConfigMapsAreCreatable:
    """These ConfigMaps hold files that live in deploy/docker/, so they are made
    imperatively by create-configmaps.sh rather than committed as manifests.
    That script and the manifests have to agree, or a pod waits forever on a
    volume that never appears."""

    SCRIPT = K8S_DIR / "create-configmaps.sh"

    ALIASES: ClassVar[dict[str, str]] = {
        '"$DB"': "dataagent-database",
        '"$SVC"': "dataagent-service",
    }

    @classmethod
    def _lines(cls) -> list[str]:
        """The script joined on backslash-continuations, comments dropped."""
        body = cls.SCRIPT.read_text(encoding="utf-8").replace("\\\n", " ")
        return [ln for ln in body.splitlines() if not ln.lstrip().startswith("#")]

    @classmethod
    def _created(cls) -> set[tuple[str, str]]:
        found = set()
        for line in cls._lines():
            m = re.match(r"cm (\S+) (\S+)", line.strip())
            if m:
                found.add((cls.ALIASES.get(m.group(1), m.group(1)), m.group(2)))
        return found

    @classmethod
    def _sources(cls) -> list[str]:
        return [
            s.split("=")[-1].replace("$K", "deploy/docker")
            for line in cls._lines()
            for s in re.findall(r"--from-file=(\S+)", line)
        ]

    @staticmethod
    def _mounted() -> set[tuple[str, str]]:
        out = set()
        for _, doc in TOP_LEVEL:
            spec = doc.get("spec", {})
            tmpl = spec.get("template") or spec.get("jobTemplate", {}).get("spec", {}).get(
                "template"
            )
            pod = (tmpl or {}).get("spec")
            if not pod:
                continue
            for v in pod.get("volumes", []):
                if "configMap" in v:
                    out.add((doc["metadata"]["namespace"], v["configMap"]["name"]))
        return out

    @staticmethod
    def _inline() -> set[tuple[str, str]]:
        return {
            (d["metadata"]["namespace"], d["metadata"]["name"])
            for _, d in TOP_LEVEL
            if d["kind"] == "ConfigMap"
        }

    def test_every_mounted_configmap_is_inline_or_created_by_the_script(self):
        missing = self._mounted() - self._inline() - self._created()
        assert not missing, (
            f"mounted but nothing creates it: {sorted(missing)} — the pod waits "
            f"forever for that volume"
        )

    def test_the_script_creates_nothing_unused(self):
        extra = self._created() - self._mounted()
        assert not extra, f"create-configmaps.sh creates ConfigMaps nothing mounts: {sorted(extra)}"

    def test_each_configmap_is_created_in_the_namespace_that_mounts_it(self):
        """ConfigMaps are namespaced. `db-init-sql` is the trap: the SQL targets
        the database, but the Job that mounts it runs in the service namespace."""
        by_name: dict[str, set[str]] = {}
        for ns, name in self._mounted():
            by_name.setdefault(name, set()).add(ns)
        wrong = [
            f"{name}: created in {ns}, mounted in {sorted(by_name[name])}"
            for ns, name in self._created()
            if name in by_name and ns not in by_name[name]
        ]
        assert not wrong, f"created in the wrong namespace: {wrong}"

    def test_every_source_file_exists(self):
        missing = [s for s in self._sources() if not (REPO_ROOT / s).exists()]
        assert not missing, f"create-configmaps.sh reads files that do not exist: {missing}"


# ── the Dockerfiles carry no deployment config ─────────────────────────────


class TestDockerfilesCarryNoDeploymentConfig:
    """The start command, the port and every environment-dependent value live in
    deploy/k8s. A Dockerfile that also carries them is a second place to change,
    and the two drift — which is how the images ended up pinned to pre-migration
    ports and a hardcoded Postgres password."""

    DOCKERFILES = sorted(
        [
            *list((REPO_ROOT / "backend" / "services").glob("*/Dockerfile")),
            REPO_ROOT / "deploy" / "docker" / "kb-worker" / "Dockerfile",
            REPO_ROOT / "backend" / "alembic" / "Dockerfile",
            REPO_ROOT / "model" / "eval" / "Dockerfile",
            REPO_ROOT / "frontend" / "Dockerfile.dev",
        ]
    )

    # facts about the image layout, not about any environment
    BUILD_TIME_ENV: ClassVar[set[str]] = {
        "PYTHONPATH",
        "PYTHONUNBUFFERED",
        "PYTHONDONTWRITEBYTECODE",
        "PIP_NO_CACHE_DIR",
        "PLAYWRIGHT_BROWSERS_PATH",
    }

    def test_the_set_is_not_empty(self):
        assert len(self.DOCKERFILES) >= 10, "Dockerfile discovery broke"

    @pytest.mark.parametrize("path", DOCKERFILES, ids=lambda p: p.parent.name)
    def test_no_expose_or_start_command(self, path):
        offenders = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if re.match(r"^\s*(EXPOSE|CMD|ENTRYPOINT)\b", line)
        ]
        assert not offenders, (
            f"{path.parent.name}/{path.name} still declares {offenders} — the port "
            f"and start command belong to the manifest"
        )

    @pytest.mark.parametrize("path", DOCKERFILES, ids=lambda p: p.parent.name)
    def test_no_environment_dependent_env(self, path):
        body = path.read_text(encoding="utf-8")
        # ENV blocks continue across backslash-terminated lines
        names, in_env = [], False
        for line in body.splitlines():
            s = line.strip()
            if re.match(r"^ENV\b", s):
                in_env = True
                s = s[3:].strip()
            elif not in_env:
                continue
            for m in re.finditer(r"([A-Z][A-Z0-9_]*)=", s):
                names.append(m.group(1))
            in_env = s.endswith("\\")
        leaked = sorted(set(names) - self.BUILD_TIME_ENV)
        assert not leaked, (
            f"{path.parent.name}/{path.name} bakes in {leaked} — put it in the "
            f"workload's ConfigMap or Secret instead"
        )

    @pytest.mark.parametrize("path", DOCKERFILES, ids=lambda p: p.parent.name)
    def test_no_dangling_line_continuation(self, path):
        """Removing a directive that was the continuation of the line above it
        leaves a trailing backslash and an unbuildable image — docker only finds
        out at build time, so find it here."""
        lines = path.read_text(encoding="utf-8").splitlines()
        dangling = []
        for i, line in enumerate(lines):
            if not line.rstrip().endswith("\\"):
                continue
            nxt = next((ln for ln in lines[i + 1 :] if ln.strip()), "")
            if not nxt or nxt.lstrip().startswith("#"):
                dangling.append(f"line {i + 1}: {line.strip()[:60]}")
        assert not dangling, f"{path.parent.name}/{path.name} {dangling}"


class TestEveryBuiltImageIsStartedByItsManifest:
    """With no CMD in the image, a manifest that forgets `command` gives a pod
    that starts and immediately exits."""

    # dataagent/kong is a thin layer over kong:3.6-ubuntu — it adds our Lua
    # plugin and inherits the upstream entrypoint, which is the right start
    # command. Everything else builds from a bare base and has no CMD left.
    INHERITS_UPSTREAM_ENTRYPOINT: ClassVar[set[str]] = {"dataagent/kong:latest"}

    def test_our_containers_all_declare_a_command(self):
        missing = []
        for _, doc in TOP_LEVEL:
            spec = doc.get("spec", {})
            tmpl = spec.get("template") or spec.get("jobTemplate", {}).get("spec", {}).get(
                "template"
            )
            pod = (tmpl or {}).get("spec")
            if not pod:
                continue
            for c in pod.get("containers", []) + pod.get("initContainers", []):
                if not c["image"].startswith("dataagent/"):
                    continue
                if c["image"] in self.INHERITS_UPSTREAM_ENTRYPOINT:
                    continue
                if not c.get("command") and not c.get("args"):
                    missing.append(f"{doc['metadata']['name']}/{c['name']}")
        assert not missing, f"built image with no command and no CMD to fall back on: {missing}"


class TestCreateSecretsHasNoDuplicateKey:
    """`kubectl create secret --from-literal` refuses a key given twice, and the deploy
    stops at its Secrets step. Two commits each adding the same role to one Secret is
    an easy merge to miss by eye."""

    def test_each_secret_names_a_key_once(self):
        script = (K8S_DIR / "create-secrets.sh").read_text()
        duplicated = {}
        for block in re.split(r"(?=kubectl -n \S+ create secret generic )", script):
            name = re.match(r"kubectl -n \S+ create secret generic (\S+)", block)
            if not name:
                continue
            keys = re.findall(r"--from-(?:literal|file)=([A-Za-z0-9_.-]+)=", block)
            repeated = sorted({k for k in keys if keys.count(k) > 1})
            if repeated:
                duplicated[name.group(1)] = repeated
        assert not duplicated, f"keys given twice: {duplicated}"
