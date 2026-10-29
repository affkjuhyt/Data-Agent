"""Run the per-service pytest suites in their images, in parallel.

`make test-unit`, `make test-integration`, `make test` and `make test-changed` call
this. Each suite still runs in its own service image (no single image can import
every service), but:

- the test tooling is baked into a cached image (tests/test-image.Dockerfile)
  instead of being pip-installed into a fresh container on every run;
- the suites run at the same time — they share nothing but the Docker daemon
  (every integration suite starts its own Postgres container on a random port);
- `changed` runs only the suites that the files changed since a base ref can
  affect;
- `related` runs, in each of those suites, only the test files the changes touch:
  the test files changed, the ones that import a changed module or name a changed
  file, and for an api module the files named after it. A change that maps to no
  test file (a conftest, the DDL, a service's Dockerfile) still runs its suites whole;
- test paths in PYTEST_ARGS replace the suite's directory: each suite runs the paths
  under its own tests/<kind>/<suite>/, and a suite none of them is under is skipped.

Host Python only orchestrates `docker`; no test runs on the host interpreter
(3.10, the code targets 3.11+). Standard library only, for the same reason.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Pass-through of the Makefile defaults so `python3 tests/run_suites.py …` works alone.
# `repo` (deploy/, k8s, Ansible, model/, cross-service policy) belongs to no service, so it
# runs on the local dev-tools image (`make dev-tools` builds it) instead of a registry image.
DEFAULT_UNIT = (
    "platform_workers:platform-workers contracts:api "
    "sandbox_executor:sandbox_executor auth_api:auth_api model_service:model_service api:api worker_agent:worker-agent worker_ingest:worker-ingest "
    "worker_document:worker-document crawler:crawler repo:dataagent/dev-tools:local"
)
DEFAULT_INTEGRATION = (
    "sandbox_executor:sandbox_executor contracts:api api:api migrate:migrate "
    "worker_agent:worker-agent worker_ingest:worker-ingest worker_document:worker-document "
    "crawler:crawler"
)

# Which suites a change under backend/services/<service>/ can break: the suite of
# that service, plus every suite whose tests import it (measured with grep over
# tests/ and the V3 service boundaries.
SERVICE_SUITES: dict[str, set[tuple[str, str]]] = {
    "api": {("unit", "api"), ("integration", "api")},
    "worker_agent": {("unit", "worker_agent"), ("integration", "worker_agent")},
    "worker_ingest": {("unit", "worker_ingest")},
    "worker_document": {("unit", "worker_document"), ("integration", "worker_document")},
    "crawler": {("unit", "crawler"), ("integration", "crawler")},
    "auth_api": {("unit", "auth_api")},
    "model_service": {("unit", "model_service")},
    "platform_workers": {("unit", "platform_workers")},
    "sandbox_executor": {
        ("unit", "sandbox_executor"), ("integration", "sandbox_executor"),
    },
}

# The repo suite (tests/unit/repo) reads every service's Dockerfile and scans their
# sources for tenant scoping and authentication, so a change to any service can break it.
REPO_SUITE = ("unit", "repo")

# Files outside the services that some unit suites read directly (manifests, Ansible,
# the frontend's Dockerfiles and dev proxy, model/ configs).
OTHER_PREFIXES: dict[str, set[tuple[str, str]]] = {
    "deploy/": {REPO_SUITE},
    "frontend/": {REPO_SUITE},
    "model/": {REPO_SUITE, ("unit", "model_service")},
}

# Per-suite pytest flags. sandbox_executor starts a real container per test, so it is
# spread over 4 xdist workers (161 s -> 64 s, 2026-10-02); tests sharing a fixed
# Docker resource carry @pytest.mark.xdist_group.
SUITE_PYTEST_ARGS: dict[tuple[str, str], list[str]] = {
    ("integration", "sandbox_executor"): ["-n", "4", "--dist", "loadgroup"],
}

# Paths with no further meaning to this script: frontend code has its own
# `make frontend-test`, docs and CI config run no Python.
IRRELEVANT_PREFIXES = ("docs/", "frontend/", ".github/", ".claude/", "README", "CLAUDE.md")


@dataclass(frozen=True)
class Suite:
    kind: str  # "unit" | "integration"
    name: str
    image: str

    @property
    def label(self) -> str:
        return f"{self.kind}: {self.name}"


def parse_suites(kind: str, spec: str) -> list[Suite]:
    suites = []
    for pair in spec.split():
        name, _, image = pair.partition(":")
        suites.append(Suite(kind, name, image or name))
    return suites


def suites_for_paths(paths: list[str], every: list[Suite]) -> list[Suite]:
    """The suites a set of changed paths can affect. Unknown paths under backend/
    or tests/ select everything: running a suite too many beats missing one."""
    by_key = {(s.kind, s.name): s for s in every}
    selected: set[tuple[str, str]] = set()
    for path in paths:
        parts = path.split("/")
        if path.startswith(("tests/unit/", "tests/integration/")) and len(parts) > 3:
            selected.add((parts[1], parts[2]))
        elif path.startswith("backend/services/") and parts[2] in SERVICE_SUITES:
            selected |= SERVICE_SUITES[parts[2]] | {REPO_SUITE}
        elif any(path.startswith(prefix) for prefix in OTHER_PREFIXES):
            for prefix, suites in OTHER_PREFIXES.items():
                if path.startswith(prefix):
                    selected |= suites
        elif path.startswith(IRRELEVANT_PREFIXES):
            continue
        elif path.startswith(("backend/", "tests/")) or path in {
            "pyproject.toml", "requirements-test.txt", "requirements-integration.txt", "Makefile",
        }:
            # backend/libs, backend/alembic, shared test fixtures, test tooling.
            return every
    return [by_key[key] for key in sorted(selected) if key in by_key]


def changed_paths(base: str) -> list[str]:
    def git(*args: str) -> list[str]:
        out = subprocess.run(["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True)
        return [line for line in out.stdout.splitlines() if line]

    # Committed on this branch since it left `base`, plus staged, unstaged and
    # untracked work: what a feature in progress has touched.
    paths = set(git("diff", "--name-only", f"{base}...HEAD"))
    paths |= set(git("diff", "--name-only", "HEAD"))
    paths |= set(git("ls-files", "--others", "--exclude-standard"))
    return sorted(paths)


def split_targets(pytest_args: str) -> tuple[list[str], list[str]]:
    """(the test paths, the other pytest arguments) of PYTEST_ARGS."""
    words = shlex.split(pytest_args)
    paths = [w for w in words if w.startswith("tests/")]
    return paths, [w for w in words if not w.startswith("tests/")]


def own_targets(suite: Suite, paths: list[str]) -> list[str]:
    root = f"tests/{suite.kind}/{suite.name}"
    return [p for p in paths if p == root or p.startswith(root + "/")]


def _module_of(path: str) -> str | None:
    """backend/services/worker_agent/sqlgen/answer.py -> services.worker_agent.sqlgen.answer."""
    if not path.startswith("backend/") or not path.endswith(".py"):
        return None
    dotted = path[len("backend/") : -len(".py")].replace("/", ".")
    return dotted.removesuffix(".__init__")


def _refers(text: str, module: str) -> bool:
    """`text` imports `module` (by its dotted name, or `from <package> import <leaf>`)."""
    if re.search(rf"\b{re.escape(module)}\b", text):
        return True
    package, _, leaf = module.rpartition(".")
    return bool(
        package
        and re.search(
            rf"from {re.escape(package)} import\s*\(?[^)]*?\b{re.escape(leaf)}\b", text, re.S
        )
    )


def related_tests(paths: list[str], suite: Suite, files: dict[str, str]) -> tuple[list[str], bool]:
    """(the test files of `suite` the changed `paths` touch, whether a change maps to no test
    file of it and so needs the suite whole). `files` is test path -> its text."""
    root = f"tests/{suite.kind}/{suite.name}/"
    mine = {p: t for p, t in files.items() if p.startswith(root)}
    picked: set[str] = set()
    whole = False
    for path in paths:
        if path.startswith(root):
            if Path(path).name.startswith("test_") and path in mine:
                stem = Path(path).stem
                # It, and the test files that borrow its fixtures or helpers.
                picked.add(path)
                picked |= {
                    p
                    for p, t in mine.items()
                    if re.search(rf"from \.{stem} import|from \. import[^\n]*\b{stem}\b", t)
                }
            elif path.endswith(".py"):
                whole = True  # a conftest or a helper every test may lean on
            continue
        if path.startswith("tests/"):
            # Another suite's test runs in that suite; shared tooling (tests/integration/_db.py,
            # tests/conftest.py) runs the suites `changed` picks for it whole.
            if len(path.split("/")) <= 3:
                whole = whole or bool(suites_for_paths([path], [suite]))
            continue
        if path.startswith("backend/services/api/modules/") and suite.name == "api":
            name = path.split("/")[4]
            named = {p for p in mine if Path(p).name.startswith(f"test_{name}")}
            picked |= named
        module = _module_of(path)
        if module is not None:
            picked |= {p for p, t in mine.items() if _refers(t, module)}
            continue
        # No import to follow (the DDL, a Dockerfile, a manifest, test tooling): the suites
        # `changed` picks for it run whole, and a test naming the file (a seed it reads) runs.
        whole = whole or bool(suites_for_paths([path], [suite]))
        picked |= {p for p, t in mine.items() if Path(path).name in t}
    return sorted(picked), whole


def base_image(suite: Suite, registry: str) -> str:
    """A bare name is a service image in the registry; a reference with a `/` (the repo
    suite's local dataagent/dev-tools:local) is used as it is."""
    return suite.image if "/" in suite.image else f"{registry}/dataagent/{suite.image}:latest"


def build_test_image(suite: Suite, registry: str) -> str:
    tag = f"dataagent-test/{suite.name}:latest"
    subprocess.run(
        [
            "docker", "build", "-q",
            "--build-arg", f"BASE={base_image(suite, registry)}",
            "--build-arg", f"SUITE={suite.name}",
            "-f", "tests/test-image.Dockerfile", "-t", tag, ".",
        ],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    )
    return tag


def run_suite(
    suite: Suite, registry: str, pytest_args: str, log_dir: Path, targets: list[str] | None = None
) -> tuple[Suite, int, float, Path]:
    log = log_dir / f"{suite.kind}-{suite.name}.log"
    start = time.monotonic()
    with log.open("w") as out:
        try:
            # Integration and unit suites of one service share the image: build once.
            tag = build_test_image(suite, registry)
        except subprocess.CalledProcessError as e:
            out.write(f"building the test image failed:\n{e.stdout}\n{e.stderr}\n")
            return suite, 1, time.monotonic() - start, log
        # --user 0, no bytecode, no pytest cache: the checkout is mounted, and a run
        # must not leave root-owned files in it.
        python_path = "/src/backend:/src"
        if suite.name == "sandbox_executor":
            # The sandbox image exposes api/auth as service-local top-level modules.
            # Keep the normal repo path first, then add that service root for its tests.
            python_path = "/src/backend:/src/backend/services/sandbox_executor:/src"
        cmd = [
            "docker", "run", "--rm", "--user", "0", "-v", f"{REPO_ROOT}:/src", "-w", "/src",
            "-e", f"PYTHONPATH={python_path}", "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-e", "OTEL_SDK_DISABLED=true",
        ]
        if suite.kind == "integration":
            # Testcontainers talks to the host daemon through the mounted socket and
            # reaches the containers it starts over the host network.
            cmd += [
                "--network", "host", "-v", "/var/run/docker.sock:/var/run/docker.sock",
                "-e", "TESTCONTAINERS_RYUK_DISABLED=true",
                "-e", f"SANDBOX_RUNNER_IMAGE={os.environ.get('SANDBOX_RUNNER_IMAGE', 'dataagent/sandbox-runner:latest')}",
            ]
        cmd += [
            tag, "python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-m", suite.kind,
            *SUITE_PYTEST_ARGS.get((suite.kind, suite.name), []), *shlex.split(pytest_args),
            *(targets or [f"tests/{suite.kind}/{suite.name}"]),
        ]
        rc = subprocess.run(cmd, cwd=REPO_ROOT, stdout=out, stderr=subprocess.STDOUT).returncode
    return suite, rc, time.monotonic() - start, log


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=["unit", "integration", "all", "changed", "related"])
    parser.add_argument("--unit-suites", default=os.environ.get("UNIT_SUITES") or DEFAULT_UNIT)
    parser.add_argument(
        "--integration-suites", default=os.environ.get("INTEGRATION_SUITES") or DEFAULT_INTEGRATION
    )
    parser.add_argument(
        "--base", default="main", help="for `changed` and `related`: compare against this ref"
    )
    parser.add_argument("--jobs", type=int, default=0, help="suites at once; 0 = all of them")
    parser.add_argument("--registry", default=os.environ.get("IMAGE_REGISTRY") or "localhost:5000")
    parser.add_argument("--pytest-args", default=os.environ.get("PYTEST_ARGS", ""))
    args = parser.parse_args()

    unit = parse_suites("unit", args.unit_suites)
    integration = parse_suites("integration", args.integration_suites)
    suites = {"unit": unit, "integration": integration}.get(args.mode, unit + integration)
    targets: dict[Suite, list[str] | None] = {}
    paths_given, flags = split_targets(args.pytest_args)
    if paths_given:
        suites = [s for s in suites if own_targets(s, paths_given)]
        targets = {s: own_targets(s, paths_given) for s in suites}
        args.pytest_args = shlex.join(flags)
    if args.mode in ("changed", "related"):
        paths = changed_paths(args.base)
        suites = suites_for_paths(paths, unit + integration)
        if args.mode == "related":
            # Any suite may hold a test that imports or reads a changed file (a worker test
            # reading a deploy/ seed), so every suite is looked into, not only those above.
            suites = unit + integration
            files = {
                str(p.relative_to(REPO_ROOT)): p.read_text(encoding="utf-8", errors="replace")
                for p in (REPO_ROOT / "tests").glob("*/*/**/test_*.py")
            }
            narrowed = []
            for s in suites:
                picked, whole = related_tests(paths, s, files)
                if whole or picked:
                    narrowed.append(s)
                    targets[s] = None if whole else picked
            suites = narrowed
        print(
            f"{len(paths)} path(s) changed since {args.base}; running: "
            + (
                ", ".join(
                    f"{s.label} ({'all' if targets.get(s) is None else len(targets[s] or [])})"
                    for s in suites
                )
                or "nothing"
            )
        )
    if not suites:
        return 0


    started = time.monotonic()
    failed: list[Suite] = []
    timings: list[tuple[Suite, int, float]] = []
    with tempfile.TemporaryDirectory(prefix="dataagent-tests-") as tmp, ThreadPoolExecutor(
        max_workers=args.jobs or len(suites)
    ) as pool:
        futures = [
            pool.submit(run_suite, s, args.registry, args.pytest_args, Path(tmp), targets.get(s))
            for s in suites
        ]
        # Each suite's output is printed whole when it finishes, never interleaved.
        for future in as_completed(futures):
            suite, rc, seconds, log = future.result()
            timings.append((suite, rc, seconds))
            print(f"== {suite.label} ({base_image(suite, args.registry)}) "
                  f"{'ok' if rc == 0 else 'FAILED'} in {seconds:.0f}s", flush=True)
            print(log.read_text(), end="", flush=True)
            if rc != 0:
                failed.append(suite)

    print("\n== summary")
    for suite, rc, seconds in sorted(timings, key=lambda t: -t[2]):
        print(f"  {'ok    ' if rc == 0 else 'FAILED'} {seconds:6.0f}s  {suite.label}")
    print(f"  wall {time.monotonic() - started:.0f}s")
    for kind in ("unit", "integration"):
        names = [s.name for s in failed if s.kind == kind]
        if names:
            print(f"failed {kind} suites: {' '.join(names)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
