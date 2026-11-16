"""Third-party licences (PL-15): what each image ships, the ones the product may not ship, the notices.

    python -I tools/licenses.py scan --component api            in an image: its installed packages
    python tools/licenses.py npm --lock package-lock.json --component frontend
    python tools/licenses.py report SCAN.json… [--notices THIRD-PARTY-NOTICES.md]

`make licenses` scans every service image and the frontend's lockfile, writes
THIRD-PARTY-NOTICES.md and fails when a package carries a forbidden licence; CI runs the same
check per service in a fresh environment of that service's requirements. Forbidden is what an
enterprise product sold on premises and as a service cannot carry: the network-copyleft and
source-available licences (AGPL, SSPL, Elastic, BUSL, Commons Clause, Confluent Community) and the
non-commercial ones. The policy, with the exceptions a person vetted, is tools/licenses.json.

Standard library only: it reads installed distributions (importlib.metadata) and the npm lock.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from importlib import metadata
from pathlib import Path

POLICY = Path(__file__).resolve().parent / "licenses.json"
# The installer's own packages, in every image whatever the service needs.
TOOLING = {"pip", "setuptools", "wheel"}


def _from_classifiers(classifiers: list[str]) -> str:
    names = [
        c.split("::")[-1].strip()
        for c in classifiers
        if c.startswith("License ::") and c.strip() != "License :: OSI Approved"
    ]
    return " OR ".join(dict.fromkeys(names))


def licence_of(meta: metadata.PackageMetadata) -> str:
    """The licence a distribution declares: the SPDX expression (PEP 639) first, then the
    classifiers, then the License field when it is a name and not the whole text."""
    expression = meta.get("License-Expression")
    if expression:
        return expression.strip()
    from_classifiers = _from_classifiers(meta.get_all("Classifier") or [])
    if from_classifiers:
        return from_classifiers
    field = (meta.get("License") or "").strip()
    if field and len(field) <= 80 and "\n" not in field and field.upper() != "UNKNOWN":
        return field
    return ""


def _licence_file(dist: metadata.Distribution) -> str:
    """The title line of the licence file a wheel carries, for one whose metadata declares
    nothing (tiktoken 0.14: "MIT License"). Only a short title counts, never the text."""
    for f in dist.files or ():
        if f.name.upper().startswith(("LICENSE", "LICENCE", "COPYING")):
            try:
                first = next((ln.strip() for ln in f.read_text().splitlines() if ln.strip()), "")
            except (OSError, UnicodeDecodeError):
                continue
            if first and len(first) <= 60 and "license" in first.lower():
                return first
    return ""


def scan(component: str) -> list[dict[str, str]]:
    seen: dict[str, dict[str, str]] = {}
    for dist in metadata.distributions():
        name = (dist.metadata.get("Name") or "").strip()
        if not name or name.lower() in TOOLING:
            continue
        seen[name.lower()] = {
            "name": name,
            "version": dist.version,
            "license": licence_of(dist.metadata) or _licence_file(dist),
            "component": component,
            "ecosystem": "pypi",
        }
    return sorted(seen.values(), key=lambda p: p["name"].lower())


def npm(lock: Path, component: str) -> list[dict[str, str]]:
    """The packages the frontend ships: every non-dev entry of the lock."""
    data = json.loads(lock.read_text(encoding="utf-8"))
    out: dict[str, dict[str, str]] = {}
    for path, entry in data.get("packages", {}).items():
        # The workspace packages ("", "frontend") are ours; only what node_modules holds is not.
        if "node_modules/" not in path or entry.get("dev") or entry.get("link"):
            continue
        name = entry.get("name") or path.rsplit("node_modules/", 1)[-1]
        licence = entry.get("license") or ""
        if isinstance(licence, dict):
            licence = licence.get("type", "")
        out[f"{name}@{entry.get('version', '')}"] = {
            "name": name,
            "version": entry.get("version", ""),
            "license": str(licence),
            "component": component,
            "ecosystem": "npm",
        }
    return sorted(out.values(), key=lambda p: p["name"].lower())


RANK = {"allowed": 0, "review": 1, "forbidden": 2}


def _term(term: str, policy: dict) -> str:
    if any(re.search(p, term, re.I) for p in policy["forbidden"]):
        return "forbidden"
    if any(re.search(p, term, re.I) for p in policy["review"]):
        return "review"
    return "allowed"


def classify(package: dict[str, str], policy: dict) -> str:
    """forbidden, review, unknown or allowed; a vetted exception decides first. An expression is
    read the SPDX way: the product picks the best alternative of an OR ("MIT OR AGPL-3.0" is
    MIT) and carries every part of an AND ("MIT AND AGPL-3.0" is AGPL)."""
    key = f"{package['ecosystem']}:{package['name'].lower()}"
    if key in policy["exceptions"]:
        return "allowed"
    licence = package["license"]
    if not licence:
        return "unknown"
    alternatives = []
    for alternative in re.split(r"\s+OR\s+", licence):
        parts = [part.strip(" ()") for part in re.split(r"\s+AND\s+", alternative)]
        alternatives.append(max((_term(part, policy) for part in parts if part), key=RANK.get))
    return min(alternatives, key=RANK.get)


def report(packages: list[dict[str, str]], policy: dict) -> dict[str, list[dict[str, str]]]:
    merged: dict[str, dict[str, str]] = {}
    for p in packages:
        key = f"{p['ecosystem']}:{p['name'].lower()}@{p['version']}"
        if key in merged:
            parts = set(merged[key]["component"].split(", ")) | {p["component"]}
            merged[key]["component"] = ", ".join(sorted(parts))
        else:
            merged[key] = dict(p)
    out: dict[str, list[dict[str, str]]] = {
        "forbidden": [],
        "review": [],
        "unknown": [],
        "allowed": [],
    }
    for p in sorted(merged.values(), key=lambda p: (p["ecosystem"], p["name"].lower())):
        out[classify(p, policy)].append(p)
    return out


def notices(classes: dict[str, list[dict[str, str]]], policy: dict) -> str:
    rows = [p for c in ("forbidden", "review", "unknown", "allowed") for p in classes[c]]
    lines = [
        "# Third-party notices",
        "",
        "Generated by `make licenses` (tools/licenses.py); do not edit by hand. Every package the",
        "service images and the frontend ship, with the licence it declares. Policy and vetted",
        "exceptions: tools/licenses.json.",
        "",
        f"{len(rows)} packages: {len(classes['allowed'])} allowed, {len(classes['review'])} to"
        f" review, {len(classes['unknown'])} without a declared licence,"
        f" {len(classes['forbidden'])} forbidden.",
        "",
        "| Package | Version | Licence | Used by | Ecosystem |",
        "| --- | --- | --- | --- | --- |",
    ]
    for p in rows:
        key = f"{p['ecosystem']}:{p['name'].lower()}"
        licence = p["license"] or "not declared"
        if key in policy["exceptions"]:
            licence += f" (vetted: {policy['exceptions'][key]})"
        cell = licence.replace("|", "\\|")
        lines.append(
            f"| {p['name']} | {p['version']} | {cell} | {p['component']} | {p['ecosystem']} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("scan")
    p.add_argument("--component", required=True)
    p = sub.add_parser("npm")
    p.add_argument("--lock", type=Path, required=True)
    p.add_argument("--component", required=True)
    p = sub.add_parser("report")
    p.add_argument("scans", nargs="+", type=Path)
    p.add_argument("--notices", type=Path)
    args = parser.parse_args(argv)

    if args.command == "scan":
        print(json.dumps(scan(args.component), indent=1))
        return 0
    if args.command == "npm":
        print(json.dumps(npm(args.lock, args.component), indent=1))
        return 0
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    packages = [p for path in args.scans for p in json.loads(path.read_text(encoding="utf-8"))]
    classes = report(packages, policy)
    if args.notices:
        args.notices.write_text(notices(classes, policy), encoding="utf-8")
    for kind in ("forbidden", "review", "unknown"):
        for p in classes[kind]:
            print(
                f"{kind}: {p['ecosystem']} {p['name']} {p['version']}: {p['license'] or '-'}"
                f" ({p['component']})",
                file=sys.stderr,
            )
    print(
        f"{sum(map(len, classes.values()))} packages; {len(classes['forbidden'])} forbidden,"
        f" {len(classes['review'])} to review, {len(classes['unknown'])} undeclared",
        file=sys.stderr,
    )
    return 1 if classes["forbidden"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
