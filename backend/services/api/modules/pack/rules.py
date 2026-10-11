"""Pure rules of the pack module: the pack file, its signature, versions, the three-way merge.

No database here, so the rules are unit-tested without Postgres.

The pack file (UC-09-01) is one JSON document:

    {"manifest": {...}, "signature": "<base64 Ed25519 over the canonical manifest>",
     "resources": {"<name>": "<base64 bytes>"}}

The manifest names the pack (code, name, industry_code, publisher, publisher_key_id), the
version, min_core_version, the content (entities with their attributes, metrics, rules, skills)
and, in `resources`, the SHA-256 of every resource the file carries. The canonical manifest is
its JSON with sorted keys and no spaces, so the signature covers every field and every
resource hash: a resource changed after signing fails the hash check (E1, AC-09-01-03).
"""

from __future__ import annotations

import base64
import binascii
import datetime
import hashlib
import json
import os
import re
from typing import Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, ValidationError

# The manifest layouts this core reads (UC-09-01 step 3).
SUPPORTED_SCHEMA_VERSIONS = frozenset({1})
# The running core's version, against min_core_version (UC-09-01 E2). The deploy sets it.
CORE_VERSION_ENV = "DATAAGENT_CORE_VERSION"
DEFAULT_CORE_VERSION = "3.0.0"
# Publisher keys this installation trusts (BR-128): JSON {key_id: base64 raw Ed25519 public key}.
# There is no table for them yet, so the deploy provides them; see the module report.
TRUSTED_KEYS_ENV = "PACK_TRUSTED_KEYS"
# BR-131: a breaking change is announced 30 days before a customer can apply it.
BREAKING_NOTICE = datetime.timedelta(days=30)
# UC-09-04 6a: the previous version is kept 30 days for a rollback.
ROLLBACK_WINDOW = datetime.timedelta(days=30)


class PackFileError(ValueError):
    """The file is not a pack this core accepts; `code` is the A1.7 code to answer with."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _Strict(BaseModel):
    model_config = ConfigDict(extra="ignore")


class AttributeSpec(_Strict):
    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    data_type: Literal["string", "integer", "decimal", "boolean", "date", "timestamp", "code"]
    required: bool = False
    code_list: list[Any] | dict[str, Any] | None = None
    pii: bool = False


class EntitySpec(_Strict):
    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None
    key_attributes: list[str] = Field(default_factory=list)
    attributes: list[AttributeSpec] = Field(default_factory=list)


class MetricSpec(_Strict):
    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    expression: str = Field(min_length=1)
    unit: str | None = None
    additivity: Literal["full", "semi", "none"] = "none"
    legal_basis: str | None = None
    is_internal: bool = False
    in_regulatory_report: bool = False


class RuleSpec(_Strict):
    code: str = Field(min_length=1)
    description: str = Field(min_length=1)
    expression: str = Field(min_length=1)
    tolerance: float | None = None
    severity: Literal["blocking", "warning"] = "blocking"


class SkillSpec(_Strict):
    kind: Literal["skill", "agent_template", "eval_set"]
    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    definition: dict[str, Any] | list[Any]


class ContentSpec(_Strict):
    """What a version holds: the canonical model, metrics, rules and sample skills."""

    entities: list[EntitySpec] = Field(default_factory=list)
    metrics: list[MetricSpec] = Field(default_factory=list)
    rules: list[RuleSpec] = Field(default_factory=list)
    skills: list[SkillSpec] = Field(default_factory=list)


class PackSpec(_Strict):
    code: str = Field(min_length=1)
    name: str = Field(min_length=1)
    industry_code: str = Field(min_length=1)
    publisher: str = Field(min_length=1)
    publisher_key_id: str = Field(min_length=1)
    description: str | None = None


class ManifestSpec(ContentSpec):
    schema_version: int = 1
    pack: PackSpec
    version: str = Field(min_length=1)
    min_core_version: str = Field(min_length=1)
    release_notes: str | None = None
    breaking_changes: list[dict[str, Any]] = Field(default_factory=list)
    released_at: datetime.datetime | None = None
    resources: dict[str, str] = Field(default_factory=dict)


class PackFile(BaseModel):
    manifest: ManifestSpec
    raw_manifest: dict[str, Any]
    signature: str


def canonical(manifest: dict[str, Any]) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def trusted_keys() -> dict[str, str]:
    raw = os.environ.get(TRUSTED_KEYS_ENV, "").strip()
    if not raw:
        return {}
    try:
        keys = json.loads(raw)
    except ValueError:
        return {}
    return {str(k): str(v) for k, v in keys.items()} if isinstance(keys, dict) else {}


def core_version() -> str:
    return os.environ.get(CORE_VERSION_ENV, "").strip() or DEFAULT_CORE_VERSION


def version_key(version: str) -> tuple[tuple[int, str], ...]:
    """Orders "2026.3" < "2026.10" < "2027.1": numeric parts as numbers, others as text."""
    return tuple(
        (int(part), "") if part.isdigit() else (-1, part)
        for part in re.split(r"[.\-+]", version.strip())
        if part
    )


def read_pack_file(data: bytes, keys: dict[str, str], core: str) -> PackFile:
    """Parses and checks a pack file (UC-09-01 steps 2-3): signature, resource hashes, versions."""
    try:
        document = json.loads(data)
    except (UnicodeDecodeError, ValueError) as exc:
        raise PackFileError("validation_failed", "The file is not a pack file.") from exc
    if not isinstance(document, dict) or not isinstance(document.get("manifest"), dict):
        raise PackFileError("validation_failed", "The pack file has no manifest.")
    raw = document["manifest"]
    try:
        manifest = ManifestSpec.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first.get("loc", ()))
        raise PackFileError(
            "validation_failed", f"The manifest is invalid at {where}: {first.get('msg')}"
        ) from exc
    signature = document.get("signature")
    if not isinstance(signature, str) or not signature:
        raise PackFileError("business_rule_violated", "The pack is not signed (BR-128).")
    key = keys.get(manifest.pack.publisher_key_id)
    if key is None:
        raise PackFileError(
            "business_rule_violated",
            "The pack is signed with a key this installation does not trust (BR-128).",
        )
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(key, validate=True)).verify(
            base64.b64decode(signature, validate=True), canonical(raw)
        )
    except (InvalidSignature, ValueError, binascii.Error) as exc:
        raise PackFileError(
            "business_rule_violated", "The pack signature is not valid (BR-128)."
        ) from exc
    resources = document.get("resources") or {}
    if not isinstance(resources, dict) or set(resources) != set(manifest.resources):
        raise PackFileError(
            "business_rule_violated", "The pack's resources do not match its manifest (BR-128)."
        )
    for name, expected in manifest.resources.items():
        try:
            content = base64.b64decode(str(resources[name]), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise PackFileError(
                "business_rule_violated", f"Resource {name} is unreadable (BR-128)."
            ) from exc
        if hashlib.sha256(content).hexdigest() != expected.lower():
            raise PackFileError(
                "business_rule_violated",
                f"Resource {name} was changed after signing (BR-128).",
            )
    if manifest.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise PackFileError(
            "business_rule_violated",
            f"Manifest schema version {manifest.schema_version} is not supported by this core.",
        )
    if version_key(manifest.min_core_version) > version_key(core):
        raise PackFileError(
            "business_rule_violated",
            f"The pack needs core version {manifest.min_core_version} or later; "
            f"this core is {core}.",
        )
    return PackFile(manifest=manifest, raw_manifest=raw, signature=signature)


# --- authoring checks (UC-09-05) ---------------------------------------------------------------


def missing_legal_basis(metrics: list[tuple[str, str | None, bool]]) -> list[str]:
    """Metric codes with neither a legal basis nor the internal label (UC-09-05 step 2)."""
    return [code for code, basis, internal in metrics if not internal and not (basis or "").strip()]


def reused_codes(
    mine: dict[str, tuple[str, str | None]], released: dict[str, tuple[str, str | None]]
) -> list[str]:
    """BR-131: a released metric code that this version gives another definition.

    The definition is the formula and the unit; a code keeps its meaning across versions."""
    return sorted(
        code for code, definition in mine.items() if released.get(code, definition) != definition
    )


def breaking_without_guide(breaking_changes: list[Any]) -> int:
    """UC-09-05 4a: how many breaking changes lack a migration guide."""
    return sum(
        1
        for change in breaking_changes
        if not (isinstance(change, dict) and str(change.get("migration") or "").strip())
    )


# --- three-way merge (UC-09-04, SD-21) ---------------------------------------------------------

Choice = Literal["keep_override", "take_new", "defer"]
CHOICES: tuple[str, ...] = ("keep_override", "take_new", "defer")


def merge_metrics(
    base: dict[str, str], new: dict[str, str], overrides: dict[str, str]
) -> list[dict[str, Any]]:
    """One entry per metric code: base = old pack formula, new = new pack formula, overrides =
    the organisation's active override formula (BR-129).

    Not overridden: the new pack's version. Overridden and unchanged in the pack: the override.
    Overridden and changed (or removed) in the pack: a conflict a person decides (step 3)."""
    items: list[dict[str, Any]] = []
    for code in sorted(set(base) | set(new)):
        old, upcoming, own = base.get(code), new.get(code), overrides.get(code)
        if own is None:
            outcome = "removed" if upcoming is None else ("added" if old is None else "take_new")
        elif upcoming is not None and upcoming == old:
            outcome = "keep_override"
        else:
            outcome = "conflict"
        item: dict[str, Any] = {"kind": "metric", "code": code, "outcome": outcome}
        if outcome == "conflict":
            item["options"] = list(CHOICES)
            item["decision"] = None
        items.append(item)
    return items


def merge_mappings(base: set[str], new: set[str], mapped: set[str]) -> list[dict[str, Any]]:
    """Attribute codes the organisation mapped and the new version drops: conflicts to decide."""
    items: list[dict[str, Any]] = []
    for code in sorted(mapped & (base - new)):
        items.append(
            {
                "kind": "mapping",
                "code": code,
                "outcome": "conflict",
                "options": list(CHOICES),
                "decision": None,
            }
        )
    return items


def open_conflicts(items: list[dict[str, Any]]) -> int:
    return sum(1 for i in items if i.get("outcome") == "conflict" and not i.get("decision"))
