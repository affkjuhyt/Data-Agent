"""The stub routers of P0-09: observations, series, entities, query, governance, review,
capabilities. They answer from the in-memory stub; S1-01/S1-02 swap in the adapters.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import AwareDatetime

from services.indicator_api.api.deps import get_store
from services.indicator_api.api.errors import ERROR_RESPONSES
from services.indicator_api.api.schemas import MappingProposalUpdate, ReviewDecision
from services.indicator_api.auth import current_tenant
from services.indicator_api.domains.capability import Capability
from services.indicator_api.domains.errors import DomainError, ErrorCode
from services.indicator_api.domains.governance import MappingProposal, ReviewCase
from services.indicator_api.domains.observation import Observation
from services.indicator_api.domains.query import AsOfMode, QueryResult, QuerySpec
from services.indicator_api.domains.registry import Entity, EntityRelation, Series
from services.indicator_api.stub.store import APPROVED, StubStore

PREFIX = "/api/indicator"


def _router(tag: str) -> APIRouter:
    return APIRouter(
        prefix=PREFIX,
        tags=[tag],
        dependencies=[Depends(current_tenant)],
        responses=ERROR_RESPONSES,
    )


entities = _router("entities")
series = _router("series")
observations = _router("observations")
query = _router("query")
governance = _router("governance")
review = _router("review")
capabilities = _router("capabilities")


@entities.get("/entities")
def list_entities(store: StubStore = Depends(get_store)) -> list[Entity]:
    return list(store.entities.values())


@entities.get("/entities/{entity_id}")
def get_entity(entity_id: str, store: StubStore = Depends(get_store)) -> Entity:
    return store.entity(entity_id)


@entities.get("/entities/{entity_id}/relations")
def get_entity_relations(
    entity_id: str, store: StubStore = Depends(get_store)
) -> list[EntityRelation]:
    return store.relations_of(entity_id)


@series.get("/series")
def list_series(store: StubStore = Depends(get_store)) -> list[Series]:
    return list(store.series.values())


@series.get("/series/{series_key}")
def get_series(series_key: str, store: StubStore = Depends(get_store)) -> Series:
    return store.one_series(series_key)


@observations.get("/series/{series_key}/observations")
def list_observations(
    series_key: str,
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
    as_of: AwareDatetime | None = None,
    as_of_mode: AsOfMode = AsOfMode.WORLD,
    store: StubStore = Depends(get_store),
) -> list[Observation]:
    return store.observations_of(
        series_key, start=start, end=end, as_of=as_of, as_of_mode=as_of_mode
    )


@observations.post("/observations", status_code=status.HTTP_201_CREATED)
def create_observation(obs: Observation, store: StubStore = Depends(get_store)) -> Observation:
    return store.add_observation(obs)


@query.post("/query")
def run_query(spec: QuerySpec, store: StubStore = Depends(get_store)) -> QueryResult:
    found: list[Observation] = []
    for key in spec.series:
        if store.one_series(key).status != APPROVED:
            raise DomainError(ErrorCode.SERIES_NOT_APPROVED, f"series {key!r} is not approved yet")
        found += store.observations_of(key, as_of=spec.as_of, as_of_mode=spec.as_of_mode)
    return QueryResult(spec=spec, observations=found)


@governance.get("/governance/mapping-proposals/{proposal_id}")
def get_mapping_proposal(
    proposal_id: UUID, store: StubStore = Depends(get_store)
) -> MappingProposal:
    return store.mapping_proposal(proposal_id)


@governance.patch("/governance/mapping-proposals/{proposal_id}")
def update_mapping_proposal(
    proposal_id: UUID, body: MappingProposalUpdate, store: StubStore = Depends(get_store)
) -> MappingProposal:
    changes = body.model_dump(exclude={"expected_version"}, exclude_none=True)
    return store.update_mapping_proposal(proposal_id, body.expected_version, changes)


@review.get("/review/cases")
def list_review_cases(store: StubStore = Depends(get_store)) -> list[ReviewCase]:
    return list(store.review_cases.values())


@review.get("/review/cases/{case_id}")
def get_review_case(case_id: UUID, store: StubStore = Depends(get_store)) -> ReviewCase:
    return store.review_case(case_id)


@review.post("/review/cases/{case_id}/decision")
def decide_review_case(
    case_id: UUID, body: ReviewDecision, store: StubStore = Depends(get_store)
) -> ReviewCase:
    return store.decide_review_case(case_id, body.expected_version, body.decision)


@capabilities.get("/capabilities")
def list_capabilities(store: StubStore = Depends(get_store)) -> list[Capability]:
    return store.capabilities


ALL_ROUTERS = (entities, series, observations, query, governance, review, capabilities)
