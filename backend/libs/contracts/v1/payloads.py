"""Typed payloads of the input-flow topics (bổ sung 30/09 to NOP-150, 14 section 9.1).

The other topics travel with a plain dict until the task that introduces them
adds a model here; the envelope still enforces their partition key.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import AwareDatetime

from libs.contracts.v1.base import ContractModel, Sha256


class ArtifactStored(ContractModel):
    source_id: UUID
    release_id: UUID
    artifact_sha256: Sha256


class ExtractionCompleted(ContractModel):
    source_id: UUID
    release_id: UUID


class MappingProposed(ContractModel):
    source_id: UUID
    release_id: UUID
    mapping_version_id: UUID


class SourceSlaBreached(ContractModel):
    source_id: UUID
    overdue_since: AwareDatetime
