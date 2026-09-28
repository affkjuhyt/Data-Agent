"""deploy.yml's change detection (deploy/ansible/filter_plugins/deploy_plan.py).

These decide what a deploy rebuilds, pushes, restarts and reruns. Getting one
wrong in the cheap direction ships a stale image or skips a migration without
any error, so the edges are pinned here rather than discovered on the cluster.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]
ANSIBLE = REPO_ROOT / "deploy" / "ansible"

_spec = importlib.util.spec_from_file_location(
    "deploy_plan", ANSIBLE / "filter_plugins" / "deploy_plan.py"
)
assert _spec and _spec.loader
plan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(plan)

DEFAULTS = yaml.safe_load((ANSIBLE / "roles" / "k8s_common" / "defaults" / "main.yml").read_text())


def _inputs(image_dir: str, dockerfile: str = "Dockerfile") -> list[str]:
    context = image_dir if image_dir in DEFAULTS["image_self_context"] else "."
    text = (REPO_ROOT / image_dir / dockerfile).read_text()
    return plan.dockerfile_inputs(text, image_dir, context, dockerfile)


# ── Dockerfile parsing ───────────────────────────────────────────────────────


class TestDockerfileInputs:
    def test_copy_sources_resolve_against_the_repo_root_context(self):
        df = "FROM x\nCOPY backend/libs/ ./libs/\nCOPY a/b.txt c/ /dst/\n"
        assert plan.dockerfile_inputs(df, "svc", ".") == [
            ".dockerignore",
            "a/b.txt",
            "backend/libs",
            "c",
            "svc/Dockerfile",
        ]

    def test_copy_sources_resolve_against_a_self_context(self):
        df = "FROM x\nCOPY requirements.txt .\nCOPY *.py ./\nCOPY . .\n"
        assert plan.dockerfile_inputs(df, "svc/x", "svc/x") == [
            "svc/x",
            "svc/x/*.py",
            "svc/x/.dockerignore",
            "svc/x/Dockerfile",
            "svc/x/requirements.txt",
        ]

    def test_flags_continuations_and_json_form(self):
        df = (
            "FROM x\n"
            "COPY --chown=app:app \\\n"
            "    # a comment inside the continuation\n"
            "    one/ /app/one/\n"
            'ADD ["two", "/app/two"]\n'
        )
        assert plan.dockerfile_inputs(df, "d", ".") == [
            ".dockerignore",
            "d/Dockerfile",
            "one",
            "two",
        ]

    def test_copy_from_a_stage_reads_nothing_from_the_context(self):
        df = "FROM x AS b\nFROM y\nCOPY --from=b /install /usr/local\n"
        assert plan.dockerfile_inputs(df, "d", ".") == ["d/Dockerfile"]

    def test_an_unresolvable_source_widens_to_the_whole_context(self):
        df = "FROM x\nARG SRC\nCOPY $SRC /app\n"
        assert "." in plan.dockerfile_inputs(df, "d", ".")

    def test_urls_are_not_paths(self):
        df = "FROM x\nADD https://example.com/f.tgz /tmp/\n"
        assert plan.dockerfile_inputs(df, "d", ".") == [".dockerignore", "d/Dockerfile"]


class TestRealImages:
    """The gaps the old per-directory detection had, pinned on the real files."""

    def test_services_that_copy_all_of_backend_services_see_every_service(self):
        for image_dir in (
            "backend/services/auth_api",
            "backend/services/kb_backend",
            "backend/services/workflow_runtime",
        ):
            assert plan.paths_touching(
                ["backend/services/release_worker/main.py"], _inputs(image_dir)
            )

    def test_images_that_never_copy_libs_ignore_a_libs_change(self):
        for image_dir, dockerfile in (
            ("deploy/docker/keycloak", "Dockerfile"),
            ("deploy/docker/kong", "Dockerfile"),
            ("deploy/docker/setup", "Dockerfile"),
            ("frontend", "Dockerfile.dev"),
            ("backend/services/llm_exporter", "Dockerfile"),
            ("backend/services/model_service", "Dockerfile"),
            ("backend/services/sandbox_executor", "Dockerfile"),
            ("backend/services/sandbox_executor/sandbox_runner", "Dockerfile"),
        ):
            assert not plan.paths_touching(
                ["backend/libs/shared/x.py", "pyproject.toml", "uv.lock"],
                _inputs(image_dir, dockerfile),
            ), image_dir

    def test_the_sandbox_runner_is_not_part_of_the_executor_image(self):
        executor = _inputs("backend/services/sandbox_executor")
        assert not plan.paths_touching(
            ["backend/services/sandbox_executor/sandbox_runner/Dockerfile"], executor
        )
        assert plan.paths_touching(["backend/services/sandbox_executor/main.py"], executor)
        assert plan.paths_touching(["backend/services/sandbox_executor/core/x.py"], executor)

    def test_the_frontend_dev_image_sees_its_whole_directory(self):
        assert plan.paths_touching(["frontend/src/App.tsx"], _inputs("frontend", "Dockerfile.dev"))


class TestPathsTouching:
    def test_a_prefix_only_matches_on_a_path_boundary(self):
        assert plan.paths_touching(["backend/libs/x.py"], ["backend/libs"]) == ["backend/libs/x.py"]
        assert plan.paths_touching(["backend/libsx/x.py"], ["backend/libs"]) == []

    def test_globs(self):
        assert plan.paths_touching(["frontend/package.json"], ["frontend/package*.json"])
        assert not plan.paths_touching(["frontend/vite.config.ts"], ["frontend/package*.json"])

    def test_full_deploy_selects_everything(self):
        catalog = [{"name": "a", "inputs": ["x"]}, {"name": "b", "inputs": ["y"]}]
        assert [i["name"] for i in plan.images_to_build(catalog, [], full=True)] == ["a", "b"]
        assert plan.images_to_build(catalog, [], full=False) == []


# ── build results, pushes, restarts ──────────────────────────────────────────


class TestBuildResults:
    def test_parse(self):
        got = plan.parse_build_results(
            [
                "built a none sha256:1 30",
                "built b sha256:2 sha256:2 4",
                "noise",
                "failed c 3",
            ]
        )
        assert got == {
            "a": {"before": "none", "after": "sha256:1", "took": "30", "changed": True},
            "b": {"before": "sha256:2", "after": "sha256:2", "took": "4", "changed": False},
        }

    def test_stale_is_decided_by_the_running_pods_not_the_previous_local_id(self):
        # b came out identical locally, but its pods still run an older digest
        # (a previous run built it and then failed): it must still restart.
        built = plan.parse_build_results(
            [
                "built a sha256:1 sha256:1 1",
                "built b sha256:2 sha256:2 1",
                "built job-only none sha256:3 1",
            ]
        )
        pods = [
            "localhost:5000/dataagent/a:latest localhost:5000/dataagent/a@sha256:1",
            "localhost:5000/dataagent/b:latest localhost:5000/dataagent/b@sha256:0",
            "localhost:5000/dataagent/b:latest localhost:5000/dataagent/b@sha256:2",
        ]
        assert plan.stale_images(built, pods, "localhost:5000/dataagent", "latest") == ["b"]

    def test_workloads_running(self):
        lines = [
            "Deployment/x dataagent-service reg/dataagent/a:latest sidecar:1",
            "StatefulSet/y dataagent-database reg/dataagent/b:latest",
            "Deployment/z dataagent-service other:1",
        ]
        assert plan.workloads_running(
            lines, ["reg/dataagent/a:latest", "reg/dataagent/b:latest"]
        ) == [
            "Deployment/x dataagent-service",
            "StatefulSet/y dataagent-database",
        ]


# ── Secrets and ConfigMaps ───────────────────────────────────────────────────


def _obj(kind, name, data, rv="1"):
    return {"kind": kind, "metadata": {"name": name, "resourceVersion": rv}, "data": data}


class TestConfigChanges:
    def test_only_content_counts(self):
        before = plan.config_hashes(
            {"items": [_obj("Secret", "s", {"k": "YQ=="}), _obj("ConfigMap", "c", {"k": "v"})]}
        )
        after = plan.config_hashes(
            {
                "items": [
                    _obj("Secret", "s", {"k": "YQ=="}, rv="9"),
                    _obj("ConfigMap", "c", {"k": "w"}),
                    _obj("ConfigMap", "new", {}),
                ]
            }
        )
        assert plan.changed_keys(before, after) == ["ConfigMap/c", "ConfigMap/new"]

    def test_every_way_a_pod_reads_config_is_seen(self):
        deployments = {
            "items": [
                {
                    "kind": "Deployment",
                    "metadata": {"name": "env-from"},
                    "spec": {
                        "template": {
                            "spec": {
                                "containers": [
                                    {
                                        "envFrom": [
                                            {"configMapRef": {"name": "c1"}},
                                            {"secretRef": {"name": "s1"}},
                                        ]
                                    }
                                ]
                            }
                        }
                    },
                },
                {
                    "kind": "Deployment",
                    "metadata": {"name": "key-ref"},
                    "spec": {
                        "template": {
                            "spec": {
                                "initContainers": [
                                    {
                                        "env": [
                                            {
                                                "name": "X",
                                                "valueFrom": {
                                                    "secretKeyRef": {"name": "s2", "key": "k"}
                                                },
                                            }
                                        ]
                                    }
                                ],
                                "containers": [{}],
                            }
                        }
                    },
                },
                {
                    "kind": "Deployment",
                    "metadata": {"name": "volume"},
                    "spec": {
                        "template": {
                            "spec": {"containers": [{}], "volumes": [{"configMap": {"name": "c3"}}]}
                        }
                    },
                },
                {
                    "kind": "Deployment",
                    "metadata": {"name": "projected"},
                    "spec": {
                        "template": {
                            "spec": {
                                "containers": [{}],
                                "volumes": [
                                    {"projected": {"sources": [{"secret": {"name": "s4"}}]}}
                                ],
                            }
                        }
                    },
                },
                {
                    "kind": "Deployment",
                    "metadata": {"name": "untouched"},
                    "spec": {
                        "template": {
                            "spec": {
                                "containers": [{"envFrom": [{"configMapRef": {"name": "other"}}]}]
                            }
                        }
                    },
                },
            ]
        }
        changed = ["ConfigMap/c1", "Secret/s2", "ConfigMap/c3", "Secret/s4"]
        assert plan.workloads_reading(deployments, changed) == [
            "Deployment/env-from",
            "Deployment/key-ref",
            "Deployment/projected",
            "Deployment/volume",
        ]

    def test_a_secret_and_a_configmap_with_the_same_name_are_different_objects(self):
        deployments = {
            "items": [
                {
                    "kind": "Deployment",
                    "metadata": {"name": "d"},
                    "spec": {
                        "template": {
                            "spec": {"containers": [{"envFrom": [{"configMapRef": {"name": "x"}}]}]}
                        }
                    },
                }
            ]
        }
        assert plan.workloads_reading(deployments, ["Secret/x"]) == []


# ── Jobs ─────────────────────────────────────────────────────────────────────

JOBS = DEFAULTS["migration_jobs"] + DEFAULTS["job_workloads"]


def _rerun(changed, rebuilt=(), force=False):
    got = plan.jobs_to_rerun(
        JOBS, DEFAULTS["job_inputs"], changed, rebuilt, "deploy/k8s/dev/job", force
    )
    return [j["name"] for j in got]


class TestJobsToRerun:
    def test_nothing_changed_nothing_reruns(self):
        assert _rerun(["frontend/src/App.tsx"]) == []

    def test_a_new_revision_reruns_migrate_and_then_the_grants(self):
        assert _rerun(["backend/alembic/versions/0042_x.py"]) == ["migrate", "db-init"]

    def test_a_job_manifest_reruns_only_that_job(self):
        assert _rerun(["deploy/k8s/dev/job/kafka-topics.yaml"]) == ["kafka-topics"]
        assert _rerun(["deploy/k8s/production/job/kafka-topics.yaml"]) == []

    def test_a_seed_file_reruns_the_seed_job(self):
        assert _rerun(["tests/e2e/seed-flow.js"]) == ["seed"]

    def test_a_rebuilt_kong_reruns_its_migration(self):
        assert _rerun([], rebuilt=["kong"]) == ["kong-migration"]

    def test_force_reruns_all_in_order(self):
        assert _rerun([], force=True) == JOBS

    def test_every_job_has_inputs_and_every_input_path_exists(self):
        assert set(DEFAULTS["job_inputs"]) == set(JOBS)
        for job, entries in DEFAULTS["job_inputs"].items():
            for entry in entries:
                if entry.startswith("job:"):
                    assert entry[4:] in JOBS, (job, entry)
                elif not entry.startswith("image:"):
                    assert (REPO_ROOT / entry).exists(), (job, entry)


# ── the Dockerfiles keep their pip downloads in the build cache ─────────────

PY_DOCKERFILES = sorted(
    p
    for p in REPO_ROOT.glob("**/Dockerfile")
    if not {"node_modules", ".claude"} & set(p.relative_to(REPO_ROOT).parts)
    and "pip install" in p.read_text(encoding="utf-8")
)


@pytest.mark.parametrize("path", PY_DOCKERFILES, ids=lambda p: str(p.parent.relative_to(REPO_ROOT)))
def test_pip_installs_use_the_cache_mount(path):
    """--no-cache-dir or PIP_NO_CACHE_DIR would leave the mount empty and every
    cold layer re-downloading torch."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# syntax=docker/dockerfile:1")
    assert "--no-cache-dir" not in text and "PIP_NO_CACHE_DIR" not in text
    for run in re.findall(r"^RUN .*pip install.*$", text, flags=re.M):
        assert run.startswith("RUN --mount=type=cache,target=/root/.cache/pip "), run
