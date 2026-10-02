"""Entities, series and observations (the paths the NOP-151 mock published)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from libs.contracts.v1 import EntityRef, Observation, SeriesRef
from services.indicator_api.api import NOT_IMPLEMENTED_RESPONSE, not_implemented
from services.indicator_api.auth import current_tenant

router = APIRouter(dependencies=[Depends(current_tenant)], responses=NOT_IMPLEMENTED_RESPONSE)


@router.get("/entities", response_model=list[EntityRef], tags=["entities"])
def list_entities() -> list[EntityRef]:
    not_implemented()


@router.get("/entities/{entity_id}", response_model=EntityRef, tags=["entities"])
def get_entity(entity_id: str) -> EntityRef:
    not_implemented()


@router.get("/series", response_model=list[SeriesRef], tags=["series"])
def list_series() -> list[SeriesRef]:
    not_implemented()


@router.get("/series/{series_key}", response_model=SeriesRef, tags=["series"])
def get_series(series_key: str) -> SeriesRef:
    not_implemented()


@router.get(
    "/series/{series_key}/observations", response_model=list[Observation], tags=["observations"]
)
def list_observations(series_key: str) -> list[Observation]:
    not_implemented()


@router.get("/query", response_model=list[Observation], tags=["query"])
def query(series_key: Annotated[list[str], Query(min_length=1)]) -> list[Observation]:
    not_implemented()
