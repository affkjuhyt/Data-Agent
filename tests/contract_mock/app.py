"""The mock routes. Each returns a fixture as a Contract v1 model; nothing is stored.

The paths are the mock's own until NOP-162 publishes indicator-api's routes —
Contract v1 defines shapes, not paths. There is no prefix: a consumer points its
base URL at the mock or at the real API, nothing else changes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any, TypeVar
from uuid import UUID

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, TypeAdapter

from libs.contracts.v1 import (
    HTTP_STATUS,
    CapabilityRef,
    EntityRef,
    ErrorBody,
    ErrorCode,
    ErrorDetail,
    Observation,
    ReviewCaseRef,
    SeriesRef,
    VersionedWrite,
)

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "contracts" / "mock"

_M = TypeVar("_M", bound=BaseModel)


class ContractError(Exception):
    """A business error answered as ErrorBody with the status HTTP_STATUS fixes for its code."""

    def __init__(self, body: ErrorBody) -> None:
        super().__init__(body.error.message)
        self.body = body


def _load(path: Path, model: type[_M]) -> list[_M]:
    # Validated on load, so a fixture that drifted from the contract fails at
    # startup rather than reaching a consumer as a shape the real API never sends.
    data: Any = json.loads(path.read_text(encoding="utf-8"))
    return TypeAdapter(list[model]).validate_python(data)  # type: ignore[valid-type]


def _load_errors(errors_dir: Path) -> dict[ErrorCode, ErrorBody]:
    return {
        code: ErrorBody.model_validate_json(
            (errors_dir / f"{code.value}.json").read_text(encoding="utf-8")
        )
        for code in ErrorCode
    }


def _not_found(what: str, key: str) -> ContractError:
    # Contract v1 has no "not found" code, and every error must be an ErrorBody.
    # UNRESOLVED_ENTITY is the 404 it has: resolving a series is the same step as
    # resolving its entity (12-numeric-core NC-T6).
    return ContractError(
        ErrorBody(
            error=ErrorDetail(
                code=ErrorCode.UNRESOLVED_ENTITY,
                message=f"no {what} {key!r}",
                details={"id": key},
            )
        )
    )


def create_app(fixtures_dir: Path = FIXTURES_DIR) -> FastAPI:
    happy = fixtures_dir / "happy"
    entities = {e.entity_id: e for e in _load(happy / "entities.json", EntityRef)}
    series = {s.series_key: s for s in _load(happy / "series.json", SeriesRef)}
    observations = _load(happy / "observations.json", Observation)
    review_cases = {c.case_id: c for c in _load(happy / "review_cases.json", ReviewCaseRef)}
    capabilities = _load(happy / "capabilities.json", CapabilityRef)
    errors = _load_errors(fixtures_dir / "errors")

    def forced_error(
        x_mock_error: Annotated[
            ErrorCode | None,
            Header(description="Answer with errors/<code>.json instead of the fixture"),
        ] = None,
    ) -> None:
        # Six of the eight codes only arise from backend logic the mock does not
        # simulate (staleness, permissions, evidence, …); this header is how a
        # consumer gets each of them from a real HTTP call. An unknown value is a
        # malformed request, so FastAPI answers 422 with the valid codes.
        if x_mock_error is not None:
            raise ContractError(errors[x_mock_error])

    app = FastAPI(
        title="Data Agent V2 contract mock",
        description="Contract v1 fixtures over HTTP (NOP-151). Not indicator-api.",
        dependencies=[Depends(forced_error)],
    )
    # The frontend dev server runs on another port and calls the mock from the browser.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ContractError)
    async def _contract_error(_: Request, exc: ContractError) -> JSONResponse:
        return JSONResponse(
            status_code=HTTP_STATUS[exc.body.error.code], content=exc.body.model_dump(mode="json")
        )

    def one_series(series_key: str) -> SeriesRef:
        if series_key not in series:
            raise _not_found("series", series_key)
        return series[series_key]

    def observations_of(series_key: str) -> list[Observation]:
        one_series(series_key)
        # Every stored version of every period, as ClickHouse obs.observation holds them.
        return [o for o in observations if o.series_key == series_key]

    def decide(case_id: UUID, body: VersionedWrite) -> ReviewCaseRef:
        if case_id not in review_cases:
            raise _not_found("review case", str(case_id))
        case = review_cases[case_id]
        if body.expected_version != case.version:
            raise ContractError(
                ErrorBody(
                    error=ErrorDetail(
                        code=ErrorCode.VERSION_CONFLICT,
                        message=f"review case {case_id} is at version {case.version}, "
                        f"not {body.expected_version}",
                        current_version=case.version,
                    )
                )
            )
        # Answered, not stored: the same request always gets the same answer, so
        # tests running in parallel never see each other's decisions.
        return case.model_copy(update={"version": case.version + 1})

    @app.get("/entities", response_model=list[EntityRef], tags=["entities"])
    def list_entities() -> list[EntityRef]:
        return list(entities.values())

    @app.get("/entities/{entity_id}", response_model=EntityRef, tags=["entities"])
    def get_entity(entity_id: str) -> EntityRef:
        if entity_id not in entities:
            raise _not_found("entity", entity_id)
        return entities[entity_id]

    @app.get("/series", response_model=list[SeriesRef], tags=["series"])
    def list_series() -> list[SeriesRef]:
        return list(series.values())

    @app.get("/series/{series_key}", response_model=SeriesRef, tags=["series"])
    def get_series(series_key: str) -> SeriesRef:
        return one_series(series_key)

    @app.get(
        "/series/{series_key}/observations",
        response_model=list[Observation],
        tags=["observations"],
    )
    def list_observations(series_key: str) -> list[Observation]:
        return observations_of(series_key)

    @app.get("/query", response_model=list[Observation], tags=["query"])
    def query(series_key: Annotated[list[str], Query(min_length=1)]) -> list[Observation]:
        return [o for key in series_key for o in observations_of(key)]

    @app.get("/review/cases", response_model=list[ReviewCaseRef], tags=["review"])
    def list_review_cases() -> list[ReviewCaseRef]:
        return list(review_cases.values())

    @app.get("/review/cases/{case_id}", response_model=ReviewCaseRef, tags=["review"])
    def get_review_case(case_id: UUID) -> ReviewCaseRef:
        if case_id not in review_cases:
            raise _not_found("review case", str(case_id))
        return review_cases[case_id]

    @app.post("/review/cases/{case_id}/approve", response_model=ReviewCaseRef, tags=["review"])
    def approve_review_case(case_id: UUID, body: VersionedWrite) -> ReviewCaseRef:
        return decide(case_id, body)

    @app.post("/review/cases/{case_id}/reject", response_model=ReviewCaseRef, tags=["review"])
    def reject_review_case(case_id: UUID, body: VersionedWrite) -> ReviewCaseRef:
        return decide(case_id, body)

    @app.get("/capabilities", response_model=list[CapabilityRef], tags=["capabilities"])
    def list_capabilities() -> list[CapabilityRef]:
        return capabilities

    return app
