"""Compatibility gate for the published contract (NOP-152).

    python -m libs.contracts.compat --base <generated dir|openapi file> --head <...>

Compares the committed schema artifacts of two trees — the base branch's
`generated/` (the agreed contract) and this branch's — and blocks a breaking
change that did not raise a version. What counts as breaking is
COMPATIBILITY.md; this module only enforces it. It reads JSON alone (stdlib, no
model import), so it judges the base side by what was published, not by
today's code.

Exit 0: compatible, or every breaking change carries its bump. Exit 1: blocked,
or an input is unreadable. Exit 2: the base has no published contract yet, so
there is nothing to compare against.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PACKAGE_DIR = Path(__file__).resolve().parent
ACCEPTED_PATH = PACKAGE_DIR / "compat-accepted.json"
OPENAPI_NAME = "contracts.openapi.json"

EXIT_OK, EXIT_BLOCKED, EXIT_NO_BASELINE = 0, 1, 2

_LOWER_BOUNDS = ("minimum", "exclusiveMinimum", "minLength", "minItems", "minProperties")
_UPPER_BOUNDS = ("maximum", "exclusiveMaximum", "maxLength", "maxItems", "maxProperties")
# A changed regex or format may be wider or narrower and no stdlib check can
# tell which, so a change is blocked unless compat-accepted.json declares it a
# widening (COMPATIBILITY.md allows widening, forbids narrowing).
_DECLARABLE = ("pattern", "format")


class InputError(Exception):
    """An input file is missing or not the JSON the gate expects."""


@dataclass(frozen=True)
class Change:
    location: str  # "Observation.unit", "Observation.obs_status -> ObsStatus"
    message: str
    keyword: str | None = None
    old: Any = None
    new: Any = None


@dataclass(frozen=True)
class Contract:
    api: dict[str, Any]
    events: dict[str, dict[str, Any]]  # file name -> schema


@dataclass(frozen=True)
class Acceptance:
    location: str
    keyword: str
    old: str
    new: str

    def covers(self, change: Change) -> bool:
        return (
            change.keyword == self.keyword
            and change.location == self.location
            and change.old == self.old
            and change.new == self.new
        )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InputError(f"{path}: {exc}") from None


def load_contract(path: Path) -> Contract:
    if path.is_dir():
        api = _read_json(path / OPENAPI_NAME)
        events = {p.name: _read_json(p) for p in sorted((path / "events").glob("*.json"))}
    else:
        api, events = _read_json(path), {}
    if not isinstance(api, dict) or not isinstance(api.get("components", {}).get("schemas"), dict):
        raise InputError(f"{path}: no components.schemas")
    return Contract(api=api, events=events)


def has_baseline(path: Path) -> bool:
    return path.is_file() or (path / OPENAPI_NAME).is_file()


def load_accepted(path: Path) -> list[Acceptance]:
    if not path.exists():
        return []
    entries = _read_json(path)
    if not isinstance(entries, list):
        raise InputError(f"{path}: expected a JSON list")
    accepted = []
    for i, entry in enumerate(entries):
        fields = ("schema", "field", "keyword", "from", "to", "reason")
        if not isinstance(entry, dict) or not all(
            isinstance(entry.get(k), str) and entry[k] for k in fields
        ):
            raise InputError(f"{path}[{i}]: needs non-empty {', '.join(fields)}")
        if entry["keyword"] not in _DECLARABLE:
            raise InputError(
                f"{path}[{i}]: only {' / '.join(_DECLARABLE)} widenings can be declared"
            )
        accepted.append(
            Acceptance(
                location=f"{entry['schema']}.{entry['field']}",
                keyword=entry["keyword"],
                old=entry["from"],
                new=entry["to"],
            )
        )
    return accepted


class _Differ:
    """Walks a base and a head schema side by side and yields breaking changes.

    `stop_at_versioned`: in the OpenAPI document every versioned schema is
    judged on its own, so a $ref into one is not followed (its own check reports
    it). An event file is one published unit, so everything in it is followed.
    """

    def __init__(self, base_doc: Any, head_doc: Any, *, stop_at_versioned: bool) -> None:
        self.base_doc, self.head_doc = base_doc, head_doc
        self.stop_at_versioned = stop_at_versioned

    def diff(self, base: Any, head: Any, location: str) -> Iterator[Change]:
        if not isinstance(base, dict) or not isinstance(head, dict):
            if base != head:
                yield Change(location, f"type {_brief(base)} -> {_brief(head)}")
            return
        # Only shape keywords are compared. title/description/examples are
        # annotations, and `default` is left to review: changing it changes
        # meaning, not shape (COMPATIBILITY.md, "đổi nghĩa").
        yield from self._ref(base, head, location)
        yield from self._type(base, head, location)
        yield from self._properties(base, head, location)
        yield from self._required(base, head, location)
        yield from self._additional(base, head, location)
        if "items" in base or "items" in head:
            yield from self.diff(base.get("items"), head.get("items"), f"{location}[]")
        yield from self._enum(base, head, location)
        yield from self._const(base, head, location)
        yield from self._bounds(base, head, location)
        yield from self._declarable(base, head, location)
        yield from self._unions(base, head, location)

    def _ref(self, base: dict[str, Any], head: dict[str, Any], location: str) -> Iterator[Change]:
        old, new = base.get("$ref"), head.get("$ref")
        if old is None and new is None:
            return
        if not isinstance(old, str) or old != new:
            yield Change(location, f"type {_brief(base)} -> {_brief(head)}")
            return
        name = old.rsplit("/", 1)[-1]
        if f" -> {name}" in location:
            return  # a recursive def, already being compared on this path
        base_target, head_target = _resolve(self.base_doc, old), _resolve(self.head_doc, new)
        if base_target is None or head_target is None:
            # The fixture documents carry one schema without the defs it points
            # to; a target missing on both sides is compared by name only.
            if (base_target is None) != (head_target is None):
                yield Change(location, f"{name} no longer resolves")
            return
        if self.stop_at_versioned and "x-schema-version" in base_target:
            return
        yield from self.diff(base_target, head_target, f"{location} -> {name}")

    def _type(self, base: dict[str, Any], head: dict[str, Any], location: str) -> Iterator[Change]:
        # "Đổi kiểu field" is breaking in either direction: a consumer of the
        # old shape cannot read a value of a type it was never told about.
        if "$ref" in base or "$ref" in head:
            return  # _ref reports a $ref that appears, disappears or changes
        if _is_union(base) != _is_union(head) or _types(base) != _types(head):
            yield Change(location, f"type {_brief(base)} -> {_brief(head)}")

    def _properties(
        self, base: dict[str, Any], head: dict[str, Any], location: str
    ) -> Iterator[Change]:
        old, new = base.get("properties", {}), head.get("properties", {})
        for name, schema in old.items():
            if name not in new:
                yield Change(f"{location}.{name}", "field removed")
            else:
                yield from self.diff(schema, new[name], f"{location}.{name}")

    def _required(
        self, base: dict[str, Any], head: dict[str, Any], location: str
    ) -> Iterator[Change]:
        old, new = set(base.get("required", [])), set(head.get("required", []))
        old_props = base.get("properties", {})
        for name in sorted(new - old):
            what = "optional -> required" if name in old_props else "new required field"
            yield Change(f"{location}.{name}", what)
        # COMPATIBILITY.md does not list this direction; it breaks a consumer
        # still on the old model, which refuses a payload that omits the field.
        for name in sorted(old - new):
            if name in head.get("properties", {}):
                yield Change(f"{location}.{name}", "required -> optional")

    def _additional(
        self, base: dict[str, Any], head: dict[str, Any], location: str
    ) -> Iterator[Change]:
        old, new = base.get("additionalProperties"), head.get("additionalProperties")
        if isinstance(old, dict) and isinstance(new, dict):
            yield from self.diff(old, new, f"{location}.*")
        elif old is not False and new is False:
            yield Change(location, "additional properties no longer allowed")
        elif old in (None, True) and isinstance(new, dict):
            yield Change(location, f"additional properties restricted to {_brief(new)}")

    def _enum(self, base: dict[str, Any], head: dict[str, Any], location: str) -> Iterator[Change]:
        if "enum" not in base and "enum" not in head:
            return
        old, new = base.get("enum"), head.get("enum")
        if old is None:
            yield Change(location, f"restricted to {new}")
            return
        if new is None:
            return
        for value in [v for v in old if v not in new]:
            yield Change(location, f"enum value {value!r} removed")
        # Openness is the base's: an enum cannot be opened and grown in one change.
        if not base.get("x-open-enum"):
            for value in [v for v in new if v not in old]:
                yield Change(location, f"enum value {value!r} added to a closed enum")

    def _const(self, base: dict[str, Any], head: dict[str, Any], location: str) -> Iterator[Change]:
        if "const" in head and base.get("const", _MISSING) != head["const"]:
            yield Change(location, f"const {base.get('const')!r} -> {head['const']!r}")

    def _bounds(
        self, base: dict[str, Any], head: dict[str, Any], location: str
    ) -> Iterator[Change]:
        for key in _LOWER_BOUNDS:
            if key in head and (key not in base or head[key] > base[key]):
                yield Change(location, f"{key} {base.get(key)} -> {head[key]} (tighter)")
        for key in _UPPER_BOUNDS:
            if key in head and (key not in base or head[key] < base[key]):
                yield Change(location, f"{key} {base.get(key)} -> {head[key]} (tighter)")

    def _declarable(
        self, base: dict[str, Any], head: dict[str, Any], location: str
    ) -> Iterator[Change]:
        for key in _DECLARABLE:
            if key not in head or base.get(key) == head[key]:
                continue
            if key not in base:
                yield Change(location, f"{key} added {head[key]!r}")
            else:
                yield Change(
                    location,
                    f"{key} {base[key]!r} -> {head[key]!r}",
                    keyword=key,
                    old=base[key],
                    new=head[key],
                )

    def _unions(
        self, base: dict[str, Any], head: dict[str, Any], location: str
    ) -> Iterator[Change]:
        for key in ("anyOf", "oneOf", "allOf"):
            old, new = base.get(key), head.get(key)
            if old is None or new is None:
                continue
            old_disc, new_disc = base.get("discriminator"), head.get("discriminator")
            if old_disc or new_disc:
                yield from self._discriminated(old_disc, new_disc, location)
                continue
            yield from self._branches(old, new, location)

    def _discriminated(
        self, old: dict[str, Any] | None, new: dict[str, Any] | None, location: str
    ) -> Iterator[Change]:
        if old is None or new is None or old.get("propertyName") != new.get("propertyName"):
            yield Change(location, f"discriminator {_brief(old)} -> {_brief(new)}")
            return
        old_map, new_map = old.get("mapping", {}), new.get("mapping", {})
        for tag in sorted(set(old_map) - set(new_map)):
            yield Change(location, f"union variant {tag!r} removed")
        for tag in sorted(set(new_map) - set(old_map)):
            yield Change(location, f"union variant {tag!r} added")
        for tag in sorted(set(old_map) & set(new_map)):
            yield from self.diff({"$ref": old_map[tag]}, {"$ref": new_map[tag]}, location)

    def _branches(self, old: list[Any], new: list[Any], location: str) -> Iterator[Change]:
        # Plain unions (Optional[...], A | B) are paired by what each branch is:
        # its $ref, else its type. An unpaired branch on either side is a type
        # change.
        remaining = list(new)
        for branch in old:
            match = next((b for b in remaining if _signature(b) == _signature(branch)), None)
            if match is None:
                yield Change(location, f"union branch {_brief(branch)} removed")
                continue
            remaining.remove(match)
            yield from self.diff(branch, match, location)
        for branch in remaining:
            yield Change(location, f"union branch {_brief(branch)} added")


_MISSING = object()


def _resolve(document: Any, ref: str) -> Any:
    if not ref.startswith("#/"):
        return None
    node = document
    for part in ref[2:].split("/"):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _types(schema: dict[str, Any]) -> frozenset[str] | None:
    value = schema.get("type")
    if value is None:
        return None
    return frozenset([value] if isinstance(value, str) else value)


def _fmt_types(types: frozenset[str] | None) -> str:
    return "none" if types is None else "|".join(sorted(types))


def _is_union(schema: dict[str, Any]) -> bool:
    return any(k in schema for k in ("anyOf", "oneOf", "allOf"))


def _signature(schema: Any) -> str:
    if isinstance(schema, dict):
        if "$ref" in schema:
            return f"ref:{schema['$ref']}"
        if "type" in schema:
            return f"type:{_fmt_types(_types(schema))}"
    return json.dumps(schema, sort_keys=True)


def _brief(schema: Any) -> str:
    if isinstance(schema, dict):
        if "$ref" in schema:
            return str(schema["$ref"]).rsplit("/", 1)[-1]
        if "type" in schema:
            return _fmt_types(_types(schema))
        if _is_union(schema):
            return "union"
    return json.dumps(schema, sort_keys=True)


def _version(schema: dict[str, Any]) -> int:
    value = schema.get("x-schema-version", 1)
    return value if isinstance(value, int) else 1


def _major(document: dict[str, Any]) -> int | None:
    version = document.get("info", {}).get("version")
    try:
        return int(str(version).split(".", 1)[0])
    except ValueError:
        return None


def _removal_allowed(base: Contract, head: Contract) -> bool:
    old, new = _major(base.api), _major(head.api)
    return old is not None and new is not None and new > old


def _contract_version(document: dict[str, Any]) -> str:
    return str(document.get("info", {}).get("version", "?"))


def check(base: Contract, head: Contract, accepted: list[Acceptance]) -> list[str]:
    """Return one report line per change that blocks the merge."""

    def breaking(changes: Iterator[Change]) -> list[Change]:
        return [c for c in changes if not any(a.covers(c) for a in accepted)]

    blocked: list[str] = []
    removal_note = (
        f"info.version {_contract_version(base.api)} -> {_contract_version(head.api)}, "
        "a removal needs a major bump"
    )

    base_schemas = base.api["components"]["schemas"]
    head_schemas = head.api["components"]["schemas"]
    for name, schema in base_schemas.items():
        if "x-schema-version" not in schema:
            continue  # enums and nested defs are judged through the models using them
        if name not in head_schemas:
            if not _removal_allowed(base, head):
                blocked.append(f"BLOCKED API {name}: schema removed ({removal_note})")
            continue
        differ = _Differ(base.api, head.api, stop_at_versioned=True)
        changes = breaking(differ.diff(schema, head_schemas[name], name))
        old, new = _version(schema), _version(head_schemas[name])
        if changes and new <= old:
            blocked += [
                f"BLOCKED API {c.location}: {c.message} (x-schema-version {old} -> {new})"
                for c in changes
            ]

    for file_name, schema in base.events.items():
        label = file_name.removesuffix(".schema.json")
        if file_name not in head.events:
            if not _removal_allowed(base, head):
                blocked.append(f"BLOCKED event {label}: schema file removed ({removal_note})")
            continue
        head_schema = head.events[file_name]
        # Consumers pick the payload model by the envelope's schema_version, the
        # N in the file name, so a published vN never changes shape: a bump inside
        # the file does not help, only a new vN+1 file does (COMPATIBILITY.md).
        fix = f"payload {label} is published: add v{_next_version(label)} instead"
        old_key, new_key = schema.get("x-partition-key"), head_schema.get("x-partition-key")
        if old_key is not None and new_key is not None and old_key != new_key:
            blocked.append(f"BLOCKED event {label}: partition key {old_key} -> {new_key} ({fix})")
        differ = _Differ(schema, head_schema, stop_at_versioned=False)
        for c in breaking(differ.diff(schema, head_schema, label)):
            blocked.append(f"BLOCKED event {c.location}: {c.message} ({fix})")
    return blocked


def _next_version(label: str) -> str:
    _, _, version = label.rpartition(".v")
    return str(int(version) + 1) if version.isdigit() else "N+1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", type=Path, required=True, help="agreed generated/ dir or file")
    parser.add_argument("--head", type=Path, required=True, help="this branch's generated/ or file")
    parser.add_argument(
        "--accepted", type=Path, default=ACCEPTED_PATH, help="declared pattern/format widenings"
    )
    args = parser.parse_args(argv)
    if not has_baseline(args.base):
        print(f"contract compat: no published contract at {args.base}, nothing to compare")
        return EXIT_NO_BASELINE
    try:
        blocked = check(
            load_contract(args.base), load_contract(args.head), load_accepted(args.accepted)
        )
    except InputError as exc:
        print(f"contract compat: {exc}", file=sys.stderr)
        return EXIT_BLOCKED
    for line in blocked:
        print(line)
    if blocked:
        print(
            f"contract compat: {len(blocked)} breaking change(s) without a version bump. "
            "Bump the model's SCHEMA_VERSION, add a new event payload version, or declare a "
            "pattern/format widening in compat-accepted.json (libs/contracts/COMPATIBILITY.md)",
            file=sys.stderr,
        )
        return EXIT_BLOCKED
    print("contract compat: compatible")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
