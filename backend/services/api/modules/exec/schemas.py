"""Request and response shapes of the group "Lớp riêng lãnh đạo" (tab 4 A3): body fields as A3 names
them, types from the tab 3b D4.24 column of the same name; responses are the exec.* rows minus
org_id, created_by, updated_by.

Every byte column travels as standard base64. The content columns are ciphertext the owner's device
made (BR-189); the server stores and returns them, it never opens them. thesis.cond_ct is never
returned: only the workflow decrypts it, and the device keeps its own copy inside content_ct.
"""

from __future__ import annotations

import base64
import binascii
import datetime
import decimal
import uuid
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    WithJsonSchema,
)


def _from_base64(value: Any) -> Any:
    # ORM rows hand over bytes; JSON bodies carry base64 text.
    if isinstance(value, str):
        try:
            return base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("not standard base64") from exc
    return value


Bytes = Annotated[
    bytes,
    BeforeValidator(_from_base64),
    PlainSerializer(lambda b: base64.b64encode(b).decode("ascii"), return_type=str),
    WithJsonSchema({"type": "string", "format": "byte"}),
]


def _uuid7(value: uuid.UUID) -> uuid.UUID:
    if value.version != 7:
        raise ValueError("must be a UUIDv7")
    return value


# The device generates the id of what it encrypts: the id is in the ciphertext's AAD, so a row
# the api numbered itself could never be decrypted (A3, 13/10/2026).
DeviceId = Annotated[uuid.UUID, AfterValidator(_uuid7)]

KeyWrapKind = Literal["passkey_prf", "hardware_key", "recovery_code"]
Kdf = Literal["hkdf_sha256", "argon2id"]
ExitPolicy = Literal["seal", "selective_handover", "shred"]
ThesisStatus = Literal[
    "open", "invalid_condition", "scoring", "scored", "unresolvable", "withdrawn"
]
MemoryKind = Literal["priority", "decision_reason", "question", "answer_snapshot", "note"]
KeyAction = Literal[
    "decrypt_condition",
    "unlock",
    "seal_evidence",
    "seal_score",
    "rewrap_handover",
    "rotate",
    "shred",
]
HandoverKind = Literal["seal", "selective_handover", "shred"]
HandoverReason = Literal["leave_position", "contract_end", "owner_request", "sealed_expired"]
Comparator = Literal["gte", "lte", "gt", "lt", "between"]


class _Row(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Accepted(BaseModel):
    """A1.6: the body of a 202, next to the Location header."""

    id: uuid.UUID
    status: str
    poll_after_ms: int


# ─── vault, key wraps, transit key (UC-16-01) ───


class KeyWrapCreate(BaseModel):
    kind: KeyWrapKind
    credential_id: Bytes | None = None
    label: str = Field(min_length=1, max_length=200)
    kdf: Kdf
    kdf_salt: Bytes
    wrapped_vmk: Bytes


class KeyWrapView(_Row):
    id: uuid.UUID
    vault_id: uuid.UUID
    kind: str
    credential_id: Bytes | None
    label: str
    kdf: str
    kdf_salt: Bytes
    wrapped_vmk: Bytes
    last_used_at: datetime.datetime | None
    revoked_at: datetime.datetime | None
    created_at: datetime.datetime


class VaultCreate(BaseModel):
    box_public_key: Bytes
    box_private_key_ct: Bytes
    key_wraps: list[KeyWrapCreate] = Field(min_length=1, max_length=20)
    exit_policy: ExitPolicy = "seal"
    contract_ref: str | None = Field(default=None, max_length=200)


class VaultView(_Row):
    id: uuid.UUID
    owner_id: uuid.UUID
    status: str
    aead_alg: str
    box_public_key: Bytes
    box_private_key_ct: Bytes
    transit_key_name: str
    transit_key_version: int
    exit_policy: str
    contract_ref: str | None
    sealed_read_until: datetime.date | None
    recovery_checked_at: datetime.datetime | None
    sealed_at: datetime.datetime | None
    shredded_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class VaultDetail(VaultView):
    # The live wraps only: a revoked wrap no longer opens the vault, so its wrapped VMK stays home.
    key_wraps: list[KeyWrapView] = Field(default_factory=list)
    # Set on POST /v1/exec/vault, where the vault's Transit key is created (BR-189).
    transit_public_key: str | None = None


class TransitKeyView(BaseModel):
    public_key_pem: str
    key_version: int
    algorithm: Literal["RSA-OAEP-SHA256"] = "RSA-OAEP-SHA256"


# ─── theses, evidence, scores (UC-16-01, UC-16-03, UC-16-04) ───


class ThesisCreate(BaseModel):
    id: DeviceId
    content_ct: Bytes
    wrapped_dek: Bytes
    content_version: int = Field(ge=1, le=32_767)
    cond_ct: Bytes
    cond_key_version: int = Field(ge=1)
    due_at: datetime.datetime
    supersedes_id: uuid.UUID | None = None


class ThesisView(_Row):
    id: uuid.UUID
    vault_id: uuid.UUID
    owner_id: uuid.UUID
    content_ct: Bytes
    wrapped_dek: Bytes
    content_version: int
    cond_key_version: int
    due_at: datetime.datetime
    committed_at: datetime.datetime
    status: str
    evidence_state: str
    evidence_refreshed_at: datetime.datetime | None
    supersedes_id: uuid.UUID | None
    withdrawn_at: datetime.datetime | None
    workflow_id: str | None
    created_at: datetime.datetime


class ThesisDetail(ThesisView):
    evidence_batches: int = 0
    publication_id: uuid.UUID | None = None


class EvidenceView(_Row):
    id: uuid.UUID
    thesis_id: uuid.UUID
    batch_no: int
    side: str
    item_kind: str
    strength: str | None
    freshness_ok: bool
    sealed_ct: Bytes
    agent_run_id: uuid.UUID
    recorded_at: datetime.datetime


class ScoreView(_Row):
    id: uuid.UUID
    thesis_id: uuid.UUID
    kind: str
    outcome: str
    as_of: datetime.datetime
    attempt_no: int
    sealed_ct: Bytes
    workflow_run_id: str
    scored_at: datetime.datetime


class ScoreWithThesis(ScoreView):
    committed_at: datetime.datetime


class KeyAccessView(_Row):
    id: uuid.UUID
    vault_id: uuid.UUID
    thesis_id: uuid.UUID | None
    key_kind: str
    action: str
    purpose: str
    actor_kind: str
    actor_ref: str
    correlation_id: uuid.UUID
    at: datetime.datetime


# ─── private memory and private questions (UC-16-02) ───


class MemoryItemCreate(BaseModel):
    id: DeviceId
    kind: MemoryKind
    content_ct: Bytes
    wrapped_dek: Bytes
    # Part of the ciphertext's AAD, so the client states the version it encrypted (A3).
    content_version: int = Field(ge=1, le=32_767)
    related_thesis_id: uuid.UUID | None = None


class MemoryItemView(_Row):
    id: uuid.UUID
    vault_id: uuid.UUID
    kind: str
    content_ct: Bytes | None
    wrapped_dek: Bytes | None
    content_version: int
    related_thesis_id: uuid.UUID | None
    handover_item_id: uuid.UUID | None
    shredded_at: datetime.datetime | None
    created_at: datetime.datetime


class PrivateContextItem(BaseModel):
    """One memory item the device decrypted and chose to attach (UC-16-02 step 2)."""

    memory_item_id: uuid.UUID | None = None
    text: str = Field(min_length=1, max_length=16_000)


class PrivateQuestionCreate(BaseModel):
    text: str = Field(min_length=1, max_length=4_000)
    context_items: list[PrivateContextItem] = Field(default_factory=list, max_length=8)
    as_of: datetime.datetime | None = None


# ─── publications (UC-16-05) ───


class PublicationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    statement_public: str = Field(min_length=1, max_length=2_000)
    target_data_space_id: uuid.UUID
    attribution: Literal["named", "role_only"]
    attribution_label: str = Field(min_length=1, max_length=200)
    confidence_public: decimal.Decimal | None = Field(default=None, max_digits=3, decimal_places=2)
    rationale_public: str | None = Field(default=None, max_length=4_000)
    ref_kind: Literal["metric", "data_product"]
    metric_ref: str = Field(min_length=1, max_length=200)
    scope: dict[str, Any] = Field(default_factory=dict)
    period_label: str = Field(min_length=1, max_length=50)
    comparator: Comparator
    threshold: decimal.Decimal
    threshold_hi: decimal.Decimal | None = None
    unit: str = Field(min_length=1, max_length=50)


class PublicationView(_Row):
    id: uuid.UUID
    thesis_id: uuid.UUID
    owner_id: uuid.UUID
    title: str
    statement_public: str
    ref_kind: str
    metric_ref: str
    scope: Any
    period_label: str
    comparator: str
    threshold: decimal.Decimal
    threshold_hi: decimal.Decimal | None
    unit: str
    due_at: datetime.datetime
    attribution: str
    attribution_label: str
    confidence_public: decimal.Decimal | None
    rationale_public: str | None
    target_data_space_id: uuid.UUID
    knowledge_page_id: uuid.UUID | None
    status: str
    published_at: datetime.datetime | None
    withdrawn_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class PublicationWithdraw(BaseModel):
    reason: str | None = Field(default=None, max_length=2_000)


# ─── handovers (UC-16-06) ───


class HandoverCreate(BaseModel):
    kind: HandoverKind
    reason: HandoverReason
    successor_id: uuid.UUID | None = None
    contract_ref: str | None = Field(default=None, max_length=200)


class HandoverView(_Row):
    id: uuid.UUID
    vault_id: uuid.UUID
    owner_id: uuid.UUID
    kind: str
    reason: str
    requested_by: uuid.UUID
    owner_participates: bool
    approved_by: uuid.UUID | None
    approved_at: datetime.datetime | None
    successor_id: uuid.UUID | None
    status: str
    items_total: int
    contract_ref: str | None
    certificate_sha256: str | None
    completed_at: datetime.datetime | None
    created_at: datetime.datetime
    updated_at: datetime.datetime


class HandoverItemIn(BaseModel):
    source_kind: Literal["memory_item", "thesis_note"]
    source_id: uuid.UUID
    payload_sealed: Bytes


class HandoverItemsCreate(BaseModel):
    items: list[HandoverItemIn] = Field(min_length=1, max_length=500)


class HandoverItemView(_Row):
    id: uuid.UUID
    handover_id: uuid.UUID
    owner_id: uuid.UUID
    successor_id: uuid.UUID
    source_kind: str
    source_id: uuid.UUID
    payload_sealed: Bytes
    imported_at: datetime.datetime | None
    created_at: datetime.datetime


class HandoverApprove(BaseModel):
    note: str | None = Field(default=None, max_length=2_000)


class HandoverAccepted(BaseModel):
    """What the successor's :accept can report: the successor cannot read exec.handover (owner
    only), so the handover's status comes from exec.handover_accept, not the row itself."""

    handover_id: uuid.UUID
    items_imported: int
    items_pending: int
    # completed once every item is imported (A3); otherwise the handover's state as it stands.
    status: str
