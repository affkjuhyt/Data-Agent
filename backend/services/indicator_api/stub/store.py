"""The stub store: seed data plus the same version checks the real stores will make."""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any
from uuid import UUID

from services.indicator_api.domains.capability import Capability
from services.indicator_api.domains.errors import DomainError, ErrorCode
from services.indicator_api.domains.governance import (
    ColumnMapping,
    MappingProposal,
    ReviewCase,
    ReviewCaseKind,
)
from services.indicator_api.domains.observation import Observation
from services.indicator_api.domains.query import AsOfMode
from services.indicator_api.domains.registry import Entity, EntityRelation, RelationType, Series

APPROVED = "approved"
PENDING = "pending"

# Fixed ids so screens and other branches can hard-code them while they build.
MAPPING_PROPOSAL_ID = UUID("5f0c0000-0000-4000-8000-000000000003")
REVIEW_CASE_ID = UUID("5f0c0000-0000-4000-8000-0000000000c1")
BRENT_RELEASE_16_09 = UUID("5f0c0000-0000-4000-8000-000000016009")
BRENT_RELEASE_23_09 = UUID("5f0c0000-0000-4000-8000-000000023009")
EIA_ARTIFACT_SHA = "3f" * 32


def _brent(day: str, released: str, value: str, release_id: UUID) -> Observation:
    # EIA publishes the week's daily spot prices on Wednesday (06 section 1).
    return Observation.model_validate(
        {
            "series_key": "EIA:RBRTE:D",
            "event_time_start": f"{day}T00:00:00-04:00",
            "event_time_end": f"{day}T23:59:59-04:00",
            "market_timezone": "America/New_York",
            "publication_time": f"{released}T10:30:00-04:00",
            "available_at": f"{released}T10:30:00-04:00",
            "system_time": f"{released}T15:00:00+00:00",
            "value": value,
            "unit": "usd_per_barrel",
            "obs_status": "ok",
            "release_id": str(release_id),
            "artifact_sha256": EIA_ARTIFACT_SHA,
            "version_kind": "machine",
            "effective_version": 1,
            "source_ref": {
                "artifact": f"raw/eia/{EIA_ARTIFACT_SHA}",
                "sheet": "Data 1",
                "row": 9000,
                "col": "B",
            },
        }
    )


def _series(key: str, entity_id: str, source_id: str, status: str) -> Series:
    return Series(
        series_key=key,
        entity_id=entity_id,
        measure="price",
        unit="usd_per_barrel" if source_id == "eia" else "vnd_per_litre",
        currency="USD" if source_id == "eia" else "VND",
        grain="spot",
        frequency="D" if source_id == "eia" else "P",
        decimal_precision=2 if source_id == "eia" else 0,
        locale="en-US" if source_id == "eia" else "vi-VN",
        source_id=source_id,
        status=status,
        version=1,
    )


class StubStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.entities: dict[str, Entity] = {
            e.entity_id: e
            for e in (
                Entity(entity_id="BRENT_SPOT", kind="commodity_price", name_vi="Dầu Brent giao ngay",
                       name_en="Europe Brent Spot", aliases=["Brent", "dầu Brent"]),
                Entity(entity_id="BRENT_FUTURES_M1", kind="commodity_price",
                       name_vi="Dầu Brent kỳ hạn tháng gần", name_en="Brent Futures M1"),
                Entity(entity_id="WTI_SPOT", kind="commodity_price", name_vi="Dầu WTI giao ngay",
                       name_en="WTI Cushing Spot", aliases=["WTI"]),
                Entity(entity_id="VN_FUEL_E10RON95_III", kind="regulated_price",
                       name_vi="Xăng E10RON95-III", name_en="E10 RON95-III gasoline"),
            )
        }  # fmt: skip
        self.relations: list[EntityRelation] = [
            EntityRelation(from_id="BRENT_SPOT", predicate=RelationType.NOT_EQUIVALENT,
                           to_id="BRENT_FUTURES_M1"),
        ]  # fmt: skip
        self.series: dict[str, Series] = {
            s.series_key: s
            for s in (
                _series("EIA:RBRTE:D", "BRENT_SPOT", "eia", APPROVED),
                _series("EIA:RWTC:D", "WTI_SPOT", "eia", APPROVED),
                # Pending on purpose: a question about it answers SERIES_NOT_APPROVED (AC-6).
                _series("BCT:E10RON95III:P", "VN_FUEL_E10RON95_III", "bct", PENDING),
            )
        }
        self.observations: list[Observation] = [
            _brent("2026-09-15", "2026-09-16", "130.80", BRENT_RELEASE_16_09),
            _brent("2026-09-22", "2026-09-23", "114.89", BRENT_RELEASE_23_09),
        ]
        self.mapping_proposals: dict[UUID, MappingProposal] = {
            MAPPING_PROPOSAL_ID: MappingProposal(
                proposal_id=MAPPING_PROPOSAL_ID,
                source_id="eia",
                fingerprint="eia-pet-pri-spt-s1-d:v2",
                mappings=[ColumnMapping(source_column="Europe Brent Spot Price FOB",
                                        series_key="EIA:RBRTE:D", unit="usd_per_barrel")],
                status=PENDING,
                version=3,
            )
        }  # fmt: skip
        self.review_cases: dict[UUID, ReviewCase] = {
            REVIEW_CASE_ID: ReviewCase(
                case_id=REVIEW_CASE_ID,
                kind=ReviewCaseKind.SCHEMA_DRIFT,
                subject_ref=f"mapping_proposal:{MAPPING_PROPOSAL_ID}",
                status="open",
                authority_role="DATA_STEWARD",
                created_at=datetime.fromisoformat("2026-09-29T09:00:00+07:00"),
                version=1,
            )
        }
        self.capabilities: list[Capability] = [
            Capability(capability_id="FETCH_SERIES", requires=["series"], returns=["observations"],
                       implementation={"type": "deterministic", "service": "indicator-api",
                                       "operator": "fetch_series"}),
            Capability(capability_id="COMPARE_PERIOD", requires=["series", "period"],
                       returns=["current_value", "baseline_value", "absolute_change", "pct_change"],
                       implementation={"type": "deterministic", "service": "indicator-api",
                                       "operator": "compare_period"},
                       allowed_roles=["ANALYST", "EXECUTIVE", "DATA_STEWARD"], slo_ms=800),
        ]  # fmt: skip

    # ── registry ─────────────────────────────────────────────────────────────

    def entity(self, entity_id: str) -> Entity:
        if entity_id not in self.entities:
            raise DomainError(ErrorCode.UNRESOLVED_ENTITY, f"no entity {entity_id!r}")
        return self.entities[entity_id]

    def relations_of(self, entity_id: str) -> list[EntityRelation]:
        self.entity(entity_id)
        return [r for r in self.relations if entity_id in (r.from_id, r.to_id)]

    def one_series(self, series_key: str) -> Series:
        if series_key not in self.series:
            raise DomainError(ErrorCode.UNRESOLVED_ENTITY, f"no series {series_key!r}")
        return self.series[series_key]

    # ── observations ─────────────────────────────────────────────────────────

    def observations_of(
        self,
        series_key: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        as_of: datetime | None = None,
        as_of_mode: AsOfMode = AsOfMode.WORLD,
    ) -> list[Observation]:
        """Latest known version per period, as of `as_of` (03 section 3.2, argMax).

        `world` hides what was not yet published at as_of; `system` hides what
        the system had not yet recorded.
        """
        self.one_series(series_key)
        best: dict[datetime, Observation] = {}
        for obs in self.observations:
            if obs.series_key != series_key:
                continue
            if start and obs.event_time_end < start or end and obs.event_time_start > end:
                continue
            known_at = obs.available_at if as_of_mode is AsOfMode.WORLD else obs.system_time
            if as_of and known_at > as_of:
                continue
            current = best.get(obs.event_time_start)
            if current is None or (obs.available_at, obs.effective_version) > (
                current.available_at,
                current.effective_version,
            ):
                best[obs.event_time_start] = obs
        if not best:
            raise DomainError(ErrorCode.NO_DATA_FOR_PERIOD, f"no observation of {series_key!r}")
        return [best[k] for k in sorted(best)]

    def add_observation(self, obs: Observation) -> Observation:
        self.one_series(obs.series_key)
        with self._lock:
            self.observations.append(obs)
        return obs

    # ── governance: every write is checked against the version it was based on ──

    def mapping_proposal(self, proposal_id: UUID) -> MappingProposal:
        if proposal_id not in self.mapping_proposals:
            raise DomainError(ErrorCode.UNRESOLVED_ENTITY, f"no mapping proposal {proposal_id}")
        return self.mapping_proposals[proposal_id]

    def update_mapping_proposal(
        self, proposal_id: UUID, expected_version: int, changes: dict[str, Any]
    ) -> MappingProposal:
        with self._lock:
            current = self.mapping_proposal(proposal_id)
            _check_version(current.version, expected_version)
            updated = MappingProposal.model_validate(
                {**current.model_dump(), **changes, "version": current.version + 1}
            )
            self.mapping_proposals[proposal_id] = updated
            return updated

    def review_case(self, case_id: UUID) -> ReviewCase:
        if case_id not in self.review_cases:
            raise DomainError(ErrorCode.UNRESOLVED_ENTITY, f"no review case {case_id}")
        return self.review_cases[case_id]

    def decide_review_case(self, case_id: UUID, expected_version: int, decision: str) -> ReviewCase:
        with self._lock:
            current = self.review_case(case_id)
            _check_version(current.version, expected_version)
            updated = current.model_copy(
                update={"status": decision, "version": current.version + 1}
            )
            self.review_cases[case_id] = updated
            return updated


def _check_version(current: int, expected: int) -> None:
    # No last-write-wins (invariant 10): a write based on an older version is
    # refused and the stored record stays as the first writer left it.
    if expected != current:
        raise DomainError(
            ErrorCode.VERSION_CONFLICT,
            f"expected version {expected}, current is {current}",
            current_version=current,
        )
