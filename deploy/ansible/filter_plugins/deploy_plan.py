"""Change detection for deploy.yml: what to rebuild, rerun and restart.

Kept as pure functions over plain data (Dockerfile text, path lists, kubectl
JSON) so tests/unit/repo/test_deploy_plan.py can check the decisions without a
cluster or a Docker daemon. deploy.yml only gathers the inputs and acts on the
answers.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import posixpath
import re
import shlex
from collections.abc import Iterable
from typing import Any

_GLOB = re.compile(r"[*?\[]")


# ── images ───────────────────────────────────────────────────────────────────


def _logical_lines(dockerfile: str) -> list[str]:
    """Instructions with their backslash continuations joined; comment lines
    inside a continuation are dropped, as Docker drops them."""
    lines: list[str] = []
    current = ""
    for raw in dockerfile.splitlines():
        stripped = raw.strip()
        if stripped.startswith("#") or (not stripped and not current):
            continue
        if stripped.endswith("\\"):
            current += stripped[:-1] + " "
            continue
        lines.append(current + stripped)
        current = ""
    if current:
        lines.append(current)
    return lines


def _context_path(context: str, src: str) -> str:
    joined = posixpath.normpath(posixpath.join(context, src.lstrip("/")))
    return "." if joined in ("", ".") else joined


def dockerfile_inputs(
    dockerfile: str, image_dir: str, context: str, dockerfile_name: str = "Dockerfile"
) -> list[str]:
    """Repo-relative paths (or globs) whose change can change the image.

    That is the Dockerfile itself plus every COPY/ADD source resolved against
    the build context. `COPY --from=<stage>` reads another stage, not the
    context, so it adds nothing. Anything this parser cannot resolve (a `$VAR`
    in a path, a heredoc) widens to the whole context: over-building is a wasted
    minute, under-building is a stale image in production.
    """
    inputs = {posixpath.join(image_dir, dockerfile_name)}
    reads_context = False
    for line in _logical_lines(dockerfile):
        match = re.match(r"(?i)(COPY|ADD)\s+(.*)", line)
        if not match:
            continue
        rest = match.group(2).strip()
        try:
            tokens = json.loads(rest) if rest.startswith("[") else shlex.split(rest)
        except ValueError:
            tokens = []
        flags = [t for t in tokens if t.startswith("--")]
        args = [t for t in tokens if not t.startswith("--")]
        if any(f.startswith("--from") for f in flags):
            continue
        reads_context = True
        sources = args[:-1]
        if not sources or any("$" in s or s.startswith("<<") for s in sources):
            inputs.add(_context_path(context, "."))
            continue
        for src in sources:
            if re.match(r"^(https?|git)://|^git@", src):
                continue
            inputs.add(_context_path(context, src.rstrip("/") or "."))
    if reads_context:
        # What the context sends is filtered by its .dockerignore.
        inputs.add(_context_path(context, ".dockerignore"))
    return sorted(inputs)


def _hits(path: str, pattern: str) -> bool:
    if pattern == ".":
        return True
    if path == pattern or path.startswith(pattern + "/"):
        return True
    if _GLOB.search(pattern):
        return fnmatch.fnmatchcase(path, pattern) or fnmatch.fnmatchcase(path, pattern + "/*")
    return False


def paths_touching(changed_paths: Iterable[str], inputs: Iterable[str]) -> list[str]:
    """The changed paths that fall under at least one input."""
    patterns = list(inputs)
    return [p for p in changed_paths if any(_hits(p, i) for i in patterns)]


def images_to_build(
    catalog: list[dict[str, Any]], changed_paths: Iterable[str], full: bool = False
) -> list[dict[str, Any]]:
    """Catalog entries (dir, name, context, dockerfile, inputs) to rebuild,
    each with the changed paths that selected it under `because`."""
    changed = list(changed_paths)
    selected = []
    for image in catalog:
        because = ["(full deploy)"] if full else paths_touching(changed, image["inputs"])
        if because:
            selected.append({**image, "because": because})
    return selected


def parse_build_results(lines: Iterable[str]) -> dict[str, dict[str, str]]:
    """`built <name> <id before|none> <id after> <seconds>` lines from the build
    task, keyed by image name. `changed` is only informational: whether to push
    or restart is decided against the registry and the running pods, which a
    failed earlier run may have left behind the local image."""
    results: dict[str, dict[str, Any]] = {}
    for line in lines:
        parts = line.split()
        if len(parts) == 5 and parts[0] == "built":
            _, name, before, after, took = parts
            results[name] = {
                "before": before,
                "after": after,
                "took": took,
                "changed": before != after,
            }
    return results


def stale_images(
    built: dict[str, dict[str, Any]], pod_images: Iterable[str], deploy_prefix: str, tag: str
) -> list[str]:
    """Built images that some pod is running at a different digest.

    `pod_images` are `<image ref> <imageID>` lines from the pods' container
    statuses; the imageID ends in `@sha256:<digest>`, the same digest the
    registry serves and `docker image inspect .Id` reports locally. An image no
    pod runs (a Job's, the sandbox runner) is never stale: there is nothing to
    restart.
    """
    running: dict[str, set[str]] = {}
    for line in pod_images:
        parts = line.split()
        if not parts:
            continue
        digest = parts[1].rsplit("@", 1)[-1] if len(parts) > 1 else ""
        running.setdefault(parts[0], set()).add(digest)
    stale = []
    for name, result in sorted(built.items()):
        digests = running.get(f"{deploy_prefix}/{name}:{tag}", set())
        if digests and digests != {result["after"]}:
            stale.append(name)
    return stale


def workloads_running(workload_lines: Iterable[str], refs: Iterable[str]) -> list[str]:
    """`<Kind>/<name> <namespace>` for every workload whose containers use one
    of `refs`. Input lines are `<Kind>/<name> <namespace> <image> [<image>…]`."""
    wanted = set(refs)
    hits = []
    for line in workload_lines:
        parts = line.split()
        if len(parts) >= 3 and wanted.intersection(parts[2:]):
            hits.append(f"{parts[0]} {parts[1]}")
    return sorted(set(hits))


# ── Secrets and ConfigMaps ───────────────────────────────────────────────────


def config_hashes(object_list: dict[str, Any]) -> dict[str, str]:
    """`<Kind>/<name>` -> sha256 of its data, from `kubectl get secrets,configmaps
    -o json`. Only the content: metadata (resourceVersion, the last-applied
    annotation) moves on every apply even when nothing a pod reads changed."""
    hashes = {}
    for item in object_list.get("items", []):
        content = {"data": item.get("data") or {}, "binaryData": item.get("binaryData") or {}}
        digest = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
        hashes[f"{item['kind']}/{item['metadata']['name']}"] = digest
    return hashes


def changed_keys(before: dict[str, str], after: dict[str, str]) -> list[str]:
    """Objects created or whose content changed between two snapshots."""
    return sorted(k for k, v in after.items() if before.get(k) != v)


def pod_config_refs(pod_spec: dict[str, Any]) -> set[str]:
    """Every Secret/ConfigMap a pod spec reads, as `<Kind>/<name>`."""
    refs: set[str] = set()
    containers = (pod_spec.get("containers") or []) + (pod_spec.get("initContainers") or [])
    for c in containers:
        for src in c.get("envFrom") or []:
            if "configMapRef" in src:
                refs.add("ConfigMap/" + src["configMapRef"]["name"])
            if "secretRef" in src:
                refs.add("Secret/" + src["secretRef"]["name"])
        for env in c.get("env") or []:
            value_from = env.get("valueFrom") or {}
            if "configMapKeyRef" in value_from:
                refs.add("ConfigMap/" + value_from["configMapKeyRef"]["name"])
            if "secretKeyRef" in value_from:
                refs.add("Secret/" + value_from["secretKeyRef"]["name"])
    for vol in pod_spec.get("volumes") or []:
        if "configMap" in vol:
            refs.add("ConfigMap/" + vol["configMap"]["name"])
        if "secret" in vol:
            refs.add("Secret/" + vol["secret"]["secretName"])
        for proj in (vol.get("projected") or {}).get("sources") or []:
            if "configMap" in proj:
                refs.add("ConfigMap/" + proj["configMap"]["name"])
            if "secret" in proj:
                refs.add("Secret/" + proj["secret"]["name"])
    return refs


def workloads_reading(workload_list: dict[str, Any], keys: Iterable[str]) -> list[str]:
    """`<Kind>/<name>` of every workload in a `kubectl get … -o json` list whose
    pod template reads one of `keys`."""
    wanted = set(keys)
    hits = []
    for item in workload_list.get("items", []):
        pod_spec = item.get("spec", {}).get("template", {}).get("spec", {})
        if pod_config_refs(pod_spec) & wanted:
            hits.append(f"{item['kind']}/{item['metadata']['name']}")
    return sorted(hits)


# ── Jobs ─────────────────────────────────────────────────────────────────────


def jobs_to_rerun(
    jobs: Iterable[str],
    job_inputs: dict[str, list[str]],
    changed_paths: Iterable[str],
    rebuilt_images: Iterable[str],
    job_manifest_dir: str,
    force: bool = False,
) -> list[dict[str, Any]]:
    """The Jobs to delete and rerun, in `jobs` order, each with its reason.

    A Job reruns when its own manifest changed, or anything in its
    `job_inputs` entry did: a path (prefix or glob), `image:<name>` (that image
    was selected for rebuild — decided from the diff, so a failed run retries
    it), or `job:<name>` (that Job reruns too; applied until nothing changes).
    """
    order = list(jobs)
    changed = list(changed_paths)
    rebuilt = set(rebuilt_images)
    reasons: dict[str, list[str]] = {}
    for job in order:
        if force:
            reasons[job] = ["(forced)"]
            continue
        paths = [posixpath.join(job_manifest_dir, f"{job}.yaml")]
        why = []
        for entry in job_inputs.get(job, []):
            if entry.startswith("image:"):
                if entry[len("image:") :] in rebuilt:
                    why.append(entry)
            elif not entry.startswith("job:"):
                paths.append(entry)
        why += paths_touching(changed, paths)
        if why:
            reasons[job] = why
    grew = True
    while grew:
        grew = False
        for job in order:
            if job in reasons:
                continue
            deps = [e for e in job_inputs.get(job, []) if e.startswith("job:") and e[4:] in reasons]
            if deps:
                reasons[job] = deps
                grew = True
    return [{"name": j, "because": reasons[j]} for j in order if j in reasons]


class FilterModule:
    def filters(self) -> dict[str, Any]:
        return {
            "dockerfile_inputs": dockerfile_inputs,
            "paths_touching": paths_touching,
            "images_to_build": images_to_build,
            "parse_build_results": parse_build_results,
            "stale_images": stale_images,
            "workloads_running": workloads_running,
            "config_hashes": config_hashes,
            "changed_keys": changed_keys,
            "workloads_reading": workloads_reading,
            "jobs_to_rerun": jobs_to_rerun,
        }
