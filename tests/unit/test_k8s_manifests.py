"""The k8s manifests, checked the way the migrations are checked.

`tests/integration/test_alembic_migrations.py` exists because a migration that
only gets exercised by `docker compose up` is a migration whose failures land in
production. The manifests had the same shape of problem and no equivalent test:
a misspelled field is not an error to the API server, it is a field that
silently does not exist, and the first symptom is a pod that starts without the
setting you thought you gave it.

Three classes of drift are caught here:

  * **Field names.** Walked against the generated OpenAPI models the API server
    itself validates against, so `terminationGracePeriodSecond` fails here
    instead of being quietly dropped at apply time.
  * **The migration gate.** The runtime tier must not start before the schema is
    at head. Compose gets that from `depends_on: service_completed_successfully`;
    here it is an initContainer, and an initContainer is easy to forget when
    copying a manifest for a new service.
  * **A destructive job in the directory apply.** `kubectl apply -f deploy/k8s/` is the
    documented routine. Anything that drops a database must therefore not be in
    `deploy/k8s/*.yaml` — the restore template lives in `deploy/k8s/dr/` for exactly that
    reason, and this test is what keeps it there.
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
K8S_DIR = REPO_ROOT / "deploy" / "k8s"
DR_DIR = K8S_DIR / "dr"

# Deployments that reach Postgres through the ORM, and so must not serve traffic
# on a schema older than their code.
MIGRATION_GATED = {"workflow-runtime"}
GATE_CONTAINER = "wait-for-migrations"


def _docs(path: pathlib.Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text(encoding="utf-8")) if d]


def _all_docs(directory: pathlib.Path) -> list[tuple[pathlib.Path, dict]]:
    return [(f, d) for f in sorted(directory.glob("*.yaml")) for d in _docs(f)]


TOP_LEVEL = _all_docs(K8S_DIR)
DEPLOYMENTS = {d["metadata"]["name"]: d for _, d in TOP_LEVEL if d["kind"] == "Deployment"}


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
                TestManifestsMatchTheApiSchema._walk(item, as_list.group(1), f"{path}[{i}]", k8s, errors)
            return

        as_map = re.fullmatch(r"dict\(str, (.+)\)", type_name)
        if as_map:
            if not isinstance(node, dict):
                errors.append(f"{path}: expected a mapping, got {type(node).__name__}")
                return
            for key, value in node.items():
                TestManifestsMatchTheApiSchema._walk(value, as_map.group(1), f"{path}.{key}", k8s, errors)
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
                value, model.openapi_types[wire_to_py[key]], f"{path}.{key}", k8s, errors,
            )

    def test_every_manifest_validates(self):
        # Optional dependency: where it is absent this check is skipped rather
        # than faked. It is in the `test` extra so CI does run it.
        k8s = pytest.importorskip(
            "kubernetes.client",
            reason="pip install kubernetes — needed to validate manifests offline",
        )
        models = {
            ("apps/v1", "Deployment"): "V1Deployment",
            ("v1", "Service"): "V1Service",
            ("v1", "ConfigMap"): "V1ConfigMap",
            ("v1", "PersistentVolumeClaim"): "V1PersistentVolumeClaim",
            ("autoscaling/v2", "HorizontalPodAutoscaler"): "V2HorizontalPodAutoscaler",
            ("batch/v1", "Job"): "V1Job",
            ("batch/v1", "CronJob"): "V1CronJob",
            ("networking.k8s.io/v1", "NetworkPolicy"): "V1NetworkPolicy",
        }

        errors: list[str] = []
        for path, doc in TOP_LEVEL + _all_docs(DR_DIR):
            key = (doc.get("apiVersion"), doc.get("kind"))
            model_name = models.get(key)
            if model_name is None:
                errors.append(f"{path.name}: unhandled {key} — add it to this test's map")
                continue
            where = f"{path.name}:{doc['kind']}/{doc['metadata']['name']}"
            self._walk(doc, model_name, where, k8s, errors)

        assert not errors, "\n".join(errors)


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
        job = next(d for _, d in TOP_LEVEL if d["kind"] == "Job" and d["metadata"]["name"] == "dataagent-migrate")
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
                assert "until" in script and "(head)" in script, (
                    f"{name}'s gate does not wait for head: {script!r}"
                )


# ── probes ─────────────────────────────────────────────────────────────────

class TestEveryDeploymentIsProbed:
    @pytest.mark.parametrize("name", sorted(DEPLOYMENTS))
    def test_readiness_and_liveness_are_both_set(self, name):
        """Readiness alone lets a wedged pod stay in the rotation; liveness
        alone sends traffic to a pod that has not finished starting."""
        for container in _containers(DEPLOYMENTS[name]):
            assert container.get("readinessProbe"), f"{name}/{container['name']} has no readinessProbe"
            assert container.get("livenessProbe"), f"{name}/{container['name']} has no livenessProbe"

    @pytest.mark.parametrize("name", sorted(DEPLOYMENTS))
    def test_a_service_selects_the_deployment(self, name):
        services = [d for _, d in TOP_LEVEL if d["kind"] == "Service"]
        labels = DEPLOYMENTS[name]["spec"]["template"]["metadata"]["labels"]
        assert any(
            all(labels.get(k) == v for k, v in s["spec"]["selector"].items())
            for s in services
        ), f"no Service selects {name}'s pods — the manifest would be unreachable"


# ── the shell inside the manifests ─────────────────────────────────────────

class TestInlineScriptsAreValidShell:
    """A manifest carries shell as a string, so nothing checks it until the pod
    runs — and a backup or restore job is the worst place to learn that.

    This caught a real one: the compose backup sidecar is a single-quoted
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
        for path, doc in TOP_LEVEL + _all_docs(DR_DIR):
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

    def test_the_compose_backup_sidecar_parses_once_rendered(self, tmp_path):
        """Same check for the sidecar these jobs were ported from. `$$` is
        compose's escape for a literal `$`, so render that before parsing."""
        compose = yaml.safe_load(
            (REPO_ROOT / "deploy" / "docker" / "docker-compose.prod.yml").read_text(encoding="utf-8")
        )
        command = compose["services"]["postgres-backup"]["command"]
        f = tmp_path / "sidecar.sh"
        f.write_bytes(command.replace("$$", "$").encode("utf-8"))
        done = subprocess.run([self._sh(), "-n", str(f)], capture_output=True, text=True)
        assert done.returncode == 0, f"compose backup sidecar is not valid shell:{chr(10)}{done.stderr}"


# ── a dump is not a backup until it restores ───────────────────────────────

class TestBackupsAreVerifiedNotAssumed:
    def _script(self, path: pathlib.Path, container: str) -> str:
        for doc in _docs(path):
            spec = doc.get("spec", {})
            pod = (spec.get("template", {}).get("spec")
                   or spec.get("jobTemplate", {}).get("spec", {}).get("template", {}).get("spec"))
            for c in (pod or {}).get("containers", []):
                if c["name"] == container:
                    return c["command"][2]
        raise AssertionError(f"{container} not found in {path.name}")

    def test_the_dump_is_checked_because_the_exit_code_cannot_be(self):
        """`pg_dump | gzip > f` reports gzip's status, so a dump that dies
        mid-stream exits 0 and leaves a file that looks fine until restore
        day. The artifact has to be verified instead."""
        script = self._script(K8S_DIR / "postgres-backup.yaml", "pg-dump")
        assert "gzip -t" in script
        assert "PostgreSQL database dump complete" in script

    def test_the_restore_verifies_before_it_drops(self):
        """Ordering is the whole point: verifying after the DROP means finding
        out the archive is truncated with the database already gone."""
        script = self._script(DR_DIR / "postgres-restore-job.yaml", "pg-restore")
        assert "gzip -t" in script, "restore does not verify the dump at all"
        assert script.index("gzip -t") < script.index("DROP DATABASE"), (
            "restore drops the database before verifying the dump it is about "
            "to restore from"
        )


# ── the directory apply must stay safe ─────────────────────────────────────

class TestNothingDestructiveInTheRoutineApply:
    def test_no_top_level_manifest_drops_a_database(self):
        """`kubectl apply -f deploy/k8s/` is the documented routine and it is not
        recursive, which is the whole reason the restore template lives in
        deploy/k8s/dr/. Keep it there."""
        for path in sorted(K8S_DIR.glob("*.yaml")):
            body = path.read_text(encoding="utf-8").upper()
            assert "DROP DATABASE" not in body, (
                f"{path.name} would be applied by `kubectl apply -f deploy/k8s/`. "
                f"Destructive jobs belong in deploy/k8s/dr/."
            )

    def test_the_restore_job_ships_suspended(self):
        restore = _docs(DR_DIR / "postgres-restore-job.yaml")[0]
        assert restore["spec"]["suspend"] is True, (
            "the restore Job must not start on apply — it drops the database"
        )
        assert restore["spec"]["backoffLimit"] == 0, (
            "a half-applied restore must not be retried automatically"
        )
