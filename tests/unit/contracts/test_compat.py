"""The contract compatibility gate (NOP-152).

A breaking change to the published contract without a version bump must fail
the pipeline and name what broke; a compatible change, or a breaking one that
carries its bump, must pass. The rules are COMPATIBILITY.md's.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from libs.contracts.compat import (
    ACCEPTED_PATH,
    EXIT_BLOCKED,
    EXIT_NO_BASELINE,
    EXIT_OK,
    Acceptance,
    Contract,
    check,
    load_accepted,
    load_contract,
    main,
)
from libs.contracts.schema_export import GENERATED_DIR

from .compat_fixtures import COMPAT_DIR

pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.req("NOP-152")]

REF = "#/components/schemas/"


def model(
    properties: dict[str, Any], required: tuple[str, ...] = (), version: int = 1
) -> dict[str, Any]:
    return {
        "additionalProperties": False,
        "properties": properties,
        "required": list(required),
        "type": "object",
        "x-schema-version": version,
    }


def api(schemas: dict[str, Any], version: str = "1.0.0") -> dict[str, Any]:
    return {"openapi": "3.1.0", "info": {"version": version}, "components": {"schemas": schemas}}


def contract(
    schemas: dict[str, Any], version: str = "1.0.0", events: dict[str, Any] | None = None
) -> Contract:
    return Contract(api=api(schemas, version), events=events or {})


def series(**unit: Any) -> dict[str, Any]:
    unit = unit or {"pattern": "^[a-z]+$", "type": "string"}
    return model({"unit": unit, "note": {"type": "string"}}, required=("unit",))


def blocked(base: Contract, head: Contract, accepted: list[Acceptance] | None = None) -> list[str]:
    return check(base, head, accepted or [])


# --- the fixtures NOP-150 left for this gate --------------------------------


@pytest.mark.parametrize(
    "case", json.loads((COMPAT_DIR / "expected.json").read_text(encoding="utf-8")).items()
)
def test_compat_fixtures_get_the_expected_verdict(case: tuple[str, str]) -> None:
    name, verdict = case
    base = load_contract(COMPAT_DIR / "base.openapi.json")
    head = load_contract(COMPAT_DIR / name / "head.openapi.json")
    assert ("blocked" if blocked(base, head) else "pass") == verdict


def test_the_blocked_fixture_names_the_field_and_the_unchanged_version() -> None:
    base = load_contract(COMPAT_DIR / "base.openapi.json")
    head = load_contract(COMPAT_DIR / "breaking_without_bump" / "head.openapi.json")
    assert blocked(base, head) == [
        "BLOCKED API Observation.unit: type string -> object (x-schema-version 1 -> 1)"
    ]


def test_the_published_contract_is_compatible_with_itself() -> None:
    published = load_contract(GENERATED_DIR)
    assert published.events
    assert blocked(published, published) == []


def test_the_committed_acceptance_list_is_valid_and_empty() -> None:
    assert load_accepted(ACCEPTED_PATH) == []


# --- breaking changes: blocked without a bump, passed with one --------------

BREAKING = {
    "field removed": (
        series(),
        model({"unit": {"pattern": "^[a-z]+$", "type": "string"}}, required=("unit",)),
        "Series.note: field removed",
    ),
    "type changed": (series(), series(type="integer"), "Series.unit: type string -> integer"),
    "type widened to nullable": (
        series(),
        series(anyOf=[{"pattern": "^[a-z]+$", "type": "string"}, {"type": "null"}]),
        "Series.unit: type string -> union",
    ),
    "optional to required": (
        series(),
        {**series(), "required": ["unit", "note"]},
        "Series.note: optional -> required",
    ),
    "new required field": (
        series(),
        {
            **series(),
            "properties": {**series()["properties"], "scale": {"type": "integer"}},
            "required": ["unit", "scale"],
        },
        "Series.scale: new required field",
    ),
    "required to optional": (
        series(),
        {**series(), "required": []},
        "Series.unit: required -> optional",
    ),
    "lower bound raised": (
        series(type="string", minLength=1),
        series(type="string", minLength=2),
        "Series.unit: minLength 1 -> 2 (tighter)",
    ),
    "upper bound added": (
        series(type="string"),
        series(type="string", maxLength=8),
        "Series.unit: maxLength None -> 8 (tighter)",
    ),
    "pattern added": (
        series(type="string"),
        series(type="string", pattern="^[a-z]+$"),
        "Series.unit: pattern added '^[a-z]+$'",
    ),
    "pattern changed": (
        series(),
        series(type="string", pattern="^[a-z]{2,}$"),
        "Series.unit: pattern '^[a-z]+$' -> '^[a-z]{2,}$'",
    ),
    "inline enum value removed": (
        series(type="string", enum=["t", "kg"]),
        series(type="string", enum=["t"]),
        "Series.unit: enum value 'kg' removed",
    ),
    "const changed": (
        series(const="t", type="string"),
        series(const="kg", type="string"),
        "Series.unit: const 't' -> 'kg'",
    ),
    "extra fields refused": (
        {**series(), "additionalProperties": True},
        series(),
        "Series: additional properties no longer allowed",
    ),
}


@pytest.mark.parametrize("name", BREAKING)
def test_a_breaking_change_without_a_bump_is_blocked_and_named(name: str) -> None:
    old, new, line = BREAKING[name]
    assert blocked(contract({"Series": old}), contract({"Series": new})) == [
        f"BLOCKED API {line} (x-schema-version 1 -> 1)"
    ]


@pytest.mark.parametrize("name", BREAKING)
def test_the_same_breaking_change_with_a_bump_passes(name: str) -> None:
    old, new, _ = BREAKING[name]
    head = {**copy.deepcopy(new), "x-schema-version": 2}
    assert blocked(contract({"Series": old}), contract({"Series": head})) == []


COMPATIBLE = {
    "optional field added": (
        series(),
        {**series(), "properties": {**series()["properties"], "scale": {"type": "integer"}}},
    ),
    "lower bound lowered": (
        series(type="string", minLength=2),
        series(type="string", minLength=1),
    ),
    "upper bound removed": (series(type="string", maxLength=8), series(type="string")),
    "pattern removed": (series(), series(type="string")),
    "annotations and default changed": (
        series(),
        {**series(), "description": "new", "title": "S", "examples": [{}]}
        | {"properties": {**series()["properties"], "note": {"type": "string", "default": "x"}}},
    ),
}


@pytest.mark.parametrize("name", COMPATIBLE)
def test_a_compatible_change_passes_without_a_bump(name: str) -> None:
    old, new = COMPATIBLE[name]
    assert blocked(contract({"Series": old}), contract({"Series": new})) == []


def test_a_new_schema_is_compatible() -> None:
    assert (
        blocked(contract({"Series": series()}), contract({"Series": series(), "New": series()}))
        == []
    )


# --- enums and nested definitions --------------------------------------------


def status_model() -> dict[str, Any]:
    return model({"status": {"$ref": f"{REF}Status"}}, required=("status",))


def status_enum(*values: str, open_: bool = False) -> dict[str, Any]:
    schema: dict[str, Any] = {"enum": list(values), "title": "Status", "type": "string"}
    if open_:
        schema["x-open-enum"] = True
    return schema


def test_a_value_added_to_a_closed_enum_is_blocked_on_every_model_using_it() -> None:
    base = contract({"Obs": status_model(), "Case": status_model(), "Status": status_enum("a")})
    head = contract(
        {"Obs": status_model(), "Case": status_model(), "Status": status_enum("a", "b")}
    )
    assert blocked(base, head) == [
        "BLOCKED API Obs.status -> Status: enum value 'b' added to a closed enum "
        "(x-schema-version 1 -> 1)",
        "BLOCKED API Case.status -> Status: enum value 'b' added to a closed enum "
        "(x-schema-version 1 -> 1)",
    ]


def test_a_value_added_to_an_open_enum_passes() -> None:
    base = contract({"Obs": status_model(), "Status": status_enum("a", open_=True)})
    head = contract({"Obs": status_model(), "Status": status_enum("a", "b", open_=True)})
    assert blocked(base, head) == []


def test_an_enum_cannot_be_opened_and_grown_in_one_change() -> None:
    base = contract({"Obs": status_model(), "Status": status_enum("a")})
    head = contract({"Obs": status_model(), "Status": status_enum("a", "b", open_=True)})
    assert len(blocked(base, head)) == 1


def test_a_value_removed_from_an_open_enum_is_blocked() -> None:
    base = contract({"Obs": status_model(), "Status": status_enum("a", "b", open_=True)})
    head = contract({"Obs": status_model(), "Status": status_enum("a", open_=True)})
    assert blocked(base, head) == [
        "BLOCKED API Obs.status -> Status: enum value 'b' removed (x-schema-version 1 -> 1)"
    ]


def test_a_change_inside_a_versioned_child_is_reported_on_the_child_only() -> None:
    parent = model({"series": {"$ref": f"{REF}Series"}}, required=("series",))
    base = contract({"Obs": parent, "Series": series()})
    head = contract({"Obs": parent, "Series": series(type="integer")})
    assert blocked(base, head) == [
        "BLOCKED API Series.unit: type string -> integer (x-schema-version 1 -> 1)"
    ]


def test_a_ref_pointing_elsewhere_is_a_type_change() -> None:
    base = contract({"Obs": status_model(), "Status": status_enum("a"), "Other": status_enum("a")})
    head_model = model({"status": {"$ref": f"{REF}Other"}}, required=("status",))
    head = contract({"Obs": head_model, "Status": status_enum("a"), "Other": status_enum("a")})
    assert blocked(base, head) == [
        "BLOCKED API Obs.status: type Status -> Other (x-schema-version 1 -> 1)"
    ]


# --- discriminated unions ------------------------------------------------------


def located(*kinds: str) -> dict[str, Any]:
    union = {
        "discriminator": {"mapping": {k: f"{REF}{k}" for k in kinds}, "propertyName": "kind"},
        "oneOf": [{"$ref": f"{REF}{k}"} for k in kinds],
    }
    return model({"location": union}, required=("location",))


def variant(kind: str) -> dict[str, Any]:
    return model({"kind": {"const": kind, "type": "string"}}, required=("kind",))


def test_a_union_variant_added_is_blocked() -> None:
    base = contract({"Ref": located("page"), "page": variant("page"), "cell": variant("cell")})
    head = contract(
        {"Ref": located("page", "cell"), "page": variant("page"), "cell": variant("cell")}
    )
    assert blocked(base, head) == [
        "BLOCKED API Ref.location: union variant 'cell' added (x-schema-version 1 -> 1)"
    ]


def test_a_union_variant_removed_is_blocked() -> None:
    base = contract(
        {"Ref": located("page", "cell"), "page": variant("page"), "cell": variant("cell")}
    )
    head = contract({"Ref": located("page"), "page": variant("page"), "cell": variant("cell")})
    assert blocked(base, head) == [
        "BLOCKED API Ref.location: union variant 'cell' removed (x-schema-version 1 -> 1)"
    ]


# --- removals ------------------------------------------------------------------


def test_a_removed_schema_needs_a_major_contract_bump() -> None:
    base = contract({"Series": series(), "Old": series()}, "1.2.0")
    assert blocked(base, contract({"Series": series()}, "1.3.0")) == [
        "BLOCKED API Old: schema removed (info.version 1.2.0 -> 1.3.0, a removal needs a major bump)"
    ]
    assert blocked(base, contract({"Series": series()}, "2.0.0")) == []


# --- declared pattern widenings --------------------------------------------------

WIDER = series(type="string", pattern="^[a-z0-9]+$")


def acceptance(**overrides: str) -> Acceptance:
    fields = {
        "location": "Series.unit",
        "keyword": "pattern",
        "old": "^[a-z]+$",
        "new": "^[a-z0-9]+$",
    } | overrides
    return Acceptance(**fields)


def test_a_declared_pattern_widening_passes_without_a_bump() -> None:
    assert (
        blocked(contract({"Series": series()}), contract({"Series": WIDER}), [acceptance()]) == []
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"old": "^[a-z]*$"},
        {"new": "^[a-z]{2}$"},
        {"location": "Series.note"},
        {"keyword": "format"},
    ],
)
def test_a_declaration_covers_only_the_exact_change(overrides: dict[str, str]) -> None:
    assert (
        len(
            blocked(
                contract({"Series": series()}),
                contract({"Series": WIDER}),
                [acceptance(**overrides)],
            )
        )
        == 1
    )


def test_a_declaration_never_lowers_another_rule() -> None:
    head = series(type="integer", pattern="^[a-z0-9]+$")
    assert blocked(contract({"Series": series()}), contract({"Series": head}), [acceptance()]) == [
        "BLOCKED API Series.unit: type string -> integer (x-schema-version 1 -> 1)"
    ]


def test_the_acceptance_file_is_parsed_into_locations(tmp_path: Path) -> None:
    path = tmp_path / "accepted.json"
    entry = {
        "schema": "Series",
        "field": "unit",
        "keyword": "pattern",
        "from": "^[a-z]+$",
        "to": "^[a-z0-9]+$",
        "reason": "units may carry digits (m3)",
    }
    path.write_text(json.dumps([entry]), encoding="utf-8")
    assert load_accepted(path) == [acceptance()]


# --- events ------------------------------------------------------------------------


def payload(version: int = 1, key: str = "source_id", **extra: Any) -> dict[str, Any]:
    schema = model(
        {"source_id": {"type": "string"}, **extra}, required=("source_id",), version=version
    )
    schema["x-partition-key"] = key
    return schema


def test_a_breaking_payload_change_is_blocked_even_with_a_bump_inside_the_file() -> None:
    base = contract({}, events={"artifact.stored.v1.schema.json": payload(note={"type": "string"})})
    head = contract({}, events={"artifact.stored.v1.schema.json": payload(version=2)})
    assert blocked(base, head) == [
        "BLOCKED event artifact.stored.v1.note: field removed "
        "(payload artifact.stored.v1 is published: add v2 instead)"
    ]


def test_a_new_payload_version_beside_the_old_one_passes() -> None:
    old = {"artifact.stored.v1.schema.json": payload(note={"type": "string"})}
    head = contract({}, events={**old, "artifact.stored.v2.schema.json": payload(version=2)})
    assert blocked(contract({}, events=old), head) == []


def test_a_changed_partition_key_is_blocked() -> None:
    base = contract({}, events={"mapping.proposed.v1.schema.json": payload()})
    head = contract({}, events={"mapping.proposed.v1.schema.json": payload(key="series_key")})
    assert blocked(base, head) == [
        "BLOCKED event mapping.proposed.v1: partition key source_id -> series_key "
        "(payload mapping.proposed.v1 is published: add v2 instead)"
    ]


def test_stamps_appearing_for_the_first_time_are_not_a_change() -> None:
    bare = payload()
    del bare["x-partition-key"]
    base = contract(
        {"Obs": status_model(), "Status": status_enum("a")}, events={"a.v1.schema.json": bare}
    )
    head = contract(
        {"Obs": status_model(), "Status": status_enum("a", open_=True)},
        events={"a.v1.schema.json": payload()},
    )
    assert blocked(base, head) == []


def test_a_removed_payload_file_needs_a_major_contract_bump() -> None:
    files = {"artifact.stored.v1.schema.json": payload()}
    assert blocked(contract({}, events=files), contract({})) == [
        "BLOCKED event artifact.stored.v1: schema file removed "
        "(info.version 1.0.0 -> 1.0.0, a removal needs a major bump)"
    ]
    assert blocked(contract({}, events=files), contract({}, "2.0.0")) == []


# --- command line --------------------------------------------------------------------


def write_tree(root: Path, schemas: dict[str, Any], **events: Any) -> Path:
    (root / "events").mkdir(parents=True)
    (root / "contracts.openapi.json").write_text(json.dumps(api(schemas)), encoding="utf-8")
    for name, schema in events.items():
        (root / "events" / name).write_text(json.dumps(schema), encoding="utf-8")
    return root


def run(tmp_path: Path, base: Path, head: Path, accepted: Path | None = None) -> int:
    accepted = accepted or tmp_path / "no-accepted.json"
    return main(["--base", str(base), "--head", str(head), "--accepted", str(accepted)])


def test_cli_passes_a_compatible_tree(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    tree = write_tree(tmp_path / "t", {"Series": series()})
    assert run(tmp_path, tree, tree) == EXIT_OK
    assert "compatible" in capsys.readouterr().out


def test_cli_blocks_and_prints_one_line_per_change(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = write_tree(tmp_path / "base", {"Series": series()})
    head = write_tree(tmp_path / "head", {"Series": series(type="integer")})
    assert run(tmp_path, base, head) == EXIT_BLOCKED
    out = capsys.readouterr()
    assert out.out.splitlines() == [
        "BLOCKED API Series.unit: type string -> integer (x-schema-version 1 -> 1)"
    ]
    assert "SCHEMA_VERSION" in out.err


def test_cli_without_a_published_baseline_exits_2(tmp_path: Path) -> None:
    head = write_tree(tmp_path / "head", {"Series": series()})
    assert run(tmp_path, tmp_path / "missing", head) == EXIT_NO_BASELINE


def test_cli_refuses_an_unreadable_head(tmp_path: Path) -> None:
    base = write_tree(tmp_path / "base", {"Series": series()})
    head = write_tree(tmp_path / "head", {"Series": series()})
    (head / "contracts.openapi.json").write_text("{not json", encoding="utf-8")
    assert run(tmp_path, base, head) == EXIT_BLOCKED


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        json.dumps({"schema": "Series"}),
        json.dumps(
            [{"schema": "Series", "field": "unit", "keyword": "pattern", "from": "a", "to": "b"}]
        ),
        json.dumps(
            [
                {
                    "schema": "Series",
                    "field": "unit",
                    "keyword": "type",
                    "from": "a",
                    "to": "b",
                    "reason": "r",
                }
            ]
        ),
    ],
    ids=["bad-json", "not-a-list", "no-reason", "not-declarable"],
)
def test_cli_refuses_a_malformed_acceptance_file(tmp_path: Path, content: str) -> None:
    tree = write_tree(tmp_path / "t", {"Series": series()})
    accepted = tmp_path / "accepted.json"
    accepted.write_text(content, encoding="utf-8")
    assert run(tmp_path, tree, tree, accepted) == EXIT_BLOCKED
