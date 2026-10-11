"""A2 group Chính sách và Quản trị (mô hình, nhà cung cấp, cài đặt AI): the governance schema's routes."""

from __future__ import annotations

import asyncio
import datetime
import decimal
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterable
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import select, tuple_

from libs.dataagent_db import uuid7
from libs.dataagent_db.models import governance
from services.api.catalogue import endpoint
from services.api.kernel.auth import CurrentPrincipal
from services.api.kernel.db import transaction
from services.api.kernel.pagination import (
    Page,
    PageRequest,
    encode_page_token,
    invalid_page_token,
    page_request,
)
from services.api.kernel.problems import Problem
from services.api.modules.governance.schemas import (
    ModelProviderApprove,
    ModelProviderCreate,
    ModelProviderPage,
    ModelProviderPatch,
    ModelProviderTest,
    ModelProviderTestResult,
    ModelProviderView,
    ModelRouteDecisionPage,
    ModelRouteDecisionView,
)

router = APIRouter()
ROLE = "svc_governance"
TEST_PROMPT = "Reply with exactly the word OK."


class _ProviderUnavailable(Exception):
    pass


def _secret_value(secret_ref: str | None) -> str | None:
    if not secret_ref:
        return None
    token = os.environ.get("OPENBAO_TOKEN")
    if not token:
        raise _ProviderUnavailable("The API has no OpenBao resolver for this provider.")
    ref = secret_ref.strip().strip("/")
    parts = ref.split("/", 2)
    if len(parts) == 3 and parts[1] == "data":
        mount, path = parts[0], parts[2]
    elif len(parts) >= 2:
        mount, path = parts[0], "/".join(parts[1:])
    else:
        raise _ProviderUnavailable("The provider secret_ref is not a KV path.")
    base = os.environ.get("OPENBAO_ADDR", "http://openbao:8200").rstrip("/")
    request = urllib.request.Request(
        f"{base}/v1/{mount}/data/{path}",
        headers={"X-Vault-Token": token, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read())
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise _ProviderUnavailable("The provider secret could not be read from OpenBao.") from exc
    data = payload.get("data", {}).get("data", {})
    if not isinstance(data, dict):
        raise _ProviderUnavailable("The provider secret has an invalid KV payload.")
    value = data.get("api_key") or data.get("token") or data.get("value")
    if not isinstance(value, str) or not value:
        raise _ProviderUnavailable("The provider secret has no supported credential field.")
    return value


def _probe_provider(
    provider: governance.ModelProvider, model_name: str
) -> tuple[int, int | None, int | None]:
    secret = _secret_value(provider.secret_ref)
    endpoint = provider.endpoint.rstrip("/")
    if provider.api_style == "openai_compatible":
        url = endpoint if endpoint.endswith("/chat/completions") else f"{endpoint}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if secret:
            headers["Authorization"] = f"Bearer {secret}"
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": TEST_PROMPT}],
            "temperature": 0,
            "max_tokens": 8,
        }
    elif provider.api_style == "anthropic_messages":
        url = endpoint if endpoint.endswith("/messages") else f"{endpoint}/messages"
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if secret:
            headers["x-api-key"] = secret
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": TEST_PROMPT}],
            "temperature": 0,
            "max_tokens": 8,
        }
    else:
        raise _ProviderUnavailable(f"Provider API style {provider.api_style} is not probeable yet.")
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = json.loads(response.read())
    except (OSError, ValueError, urllib.error.URLError) as exc:
        raise _ProviderUnavailable("The provider did not return a usable response.") from exc
    usage = body.get("usage", {}) if isinstance(body, dict) else {}
    input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
    output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
    return (
        max(1, round((time.perf_counter() - started) * 1000)),
        input_tokens if isinstance(input_tokens, int) else None,
        output_tokens if isinstance(output_tokens, int) else None,
    )


def _month_start() -> datetime.datetime:
    now = datetime.datetime.now(datetime.UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _provider_cost(
    provider: governance.ModelProvider, usage: Iterable[governance.ModelUsage]
) -> decimal.Decimal:
    """Price usage without exposing provider secrets or making the client derive totals."""
    total = decimal.Decimal("0")
    price = provider.price if isinstance(provider.price, dict) else {}
    for row in usage:
        rates = price.get(row.model_name, {})
        if not isinstance(rates, dict):
            continue
        total += decimal.Decimal(str(row.input_tokens or 0)) * decimal.Decimal(
            str(rates.get("input", 0))
        )
        total += decimal.Decimal(str(row.output_tokens or 0)) * decimal.Decimal(
            str(rates.get("output", 0))
        )
    return total


@router.get(
    "/v1/admin/model-providers",
    response_model=ModelProviderPage,
    **endpoint("GET", "/v1/admin/model-providers", operation_id="listModelProviders"),
)
async def list_model_providers(
    principal: CurrentPrincipal,
    page: Annotated[PageRequest, Depends(page_request)],
    kind: Annotated[str | None, Query(max_length=30)] = None,
    enabled: bool | None = None,
) -> ModelProviderPage:
    provider = governance.ModelProvider
    query = select(provider).where(provider.org_id == principal.org_id)
    if kind:
        query = query.where(provider.kind == kind)
    if enabled is not None:
        query = query.where(provider.enabled.is_(enabled))
    if page.cursor:
        try:
            after = (str(page.cursor["code"]), uuid.UUID(str(page.cursor["id"])))
        except (KeyError, ValueError) as exc:
            raise invalid_page_token() from exc
        query = query.where(tuple_(provider.code, provider.id) > after)
    query = query.order_by(provider.code, provider.id).limit(page.page_size + 1)

    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(query)).all())
        more = len(rows) > page.page_size
        rows = rows[: page.page_size]
        usage_by_provider: dict[uuid.UUID, list[governance.ModelUsage]] = {
            row.id: [] for row in rows
        }
        if rows:
            usage_rows = await session.scalars(
                select(governance.ModelUsage).where(
                    governance.ModelUsage.org_id == principal.org_id,
                    governance.ModelUsage.provider_id.in_(usage_by_provider),
                    governance.ModelUsage.called_at >= _month_start(),
                )
            )
            for usage in usage_rows:
                if usage.provider_id in usage_by_provider:
                    usage_by_provider[usage.provider_id].append(usage)

    last = rows[-1] if more else None
    return Page[ModelProviderView](
        items=[
            ModelProviderView.model_validate(row).model_copy(
                update={"monthly_cost_vnd": _provider_cost(row, usage_by_provider[row.id])}
            )
            for row in rows
        ],
        next_page_token=encode_page_token(
            {"code": last.code, "id": str(last.id)} if last else None
        ),
    )


@router.post(
    "/v1/admin/model-providers",
    response_model=ModelProviderView,
    status_code=201,
    **endpoint("POST", "/v1/admin/model-providers", operation_id="createModelProvider"),
)
async def create_model_provider(
    body: ModelProviderCreate, principal: CurrentPrincipal
) -> ModelProviderView:
    if body.kind == "frontier" and (
        body.secret_ref is None
        or body.data_region is None
        or not body.endpoint.startswith("https://")
    ):
        raise Problem(
            422,
            "validation_failed",
            "A frontier provider needs an https endpoint, secret_ref and data_region.",
        )
    async with transaction(ROLE, principal) as session:
        row = governance.ModelProvider(
            id=uuid7(),
            org_id=principal.org_id,
            code=body.code,
            display_name=body.display_name,
            kind=body.kind,
            api_style=body.api_style,
            endpoint=body.endpoint,
            secret_ref=body.secret_ref,
            data_region=body.data_region,
            zero_retention=body.zero_retention,
            contract_ref=body.contract_ref,
            max_classification="public" if body.kind == "frontier" else "restricted",
            enabled=False,
            price=body.price,
            monthly_budget_vnd=body.monthly_budget_vnd,
            created_by=principal.user_id,
            updated_by=principal.user_id,
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)
    return ModelProviderView.model_validate(row)


@router.patch(
    "/v1/admin/model-providers/{id}",
    response_model=ModelProviderView,
    **endpoint("PATCH", "/v1/admin/model-providers/{id}", operation_id="patchModelProvider"),
)
async def patch_model_provider(
    body: ModelProviderPatch,
    provider_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
) -> ModelProviderView:
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(
            select(governance.ModelProvider)
            .where(
                governance.ModelProvider.id == provider_id,
                governance.ModelProvider.org_id == principal.org_id,
            )
            .with_for_update()
        )
        if row is None:
            raise Problem(404, "not_found", "Model provider not found.")

        changed_contract = bool(
            {"endpoint", "secret_ref", "data_region", "zero_retention"}
            & body.model_fields_set
        )
        for field in (
            "display_name",
            "endpoint",
            "secret_ref",
            "data_region",
            "zero_retention",
            "contract_ref",
            "price",
            "monthly_budget_vnd",
            "status",
        ):
            if field in body.model_fields_set:
                setattr(row, field, getattr(body, field))

        if row.kind == "frontier" and row.endpoint is not None and not row.endpoint.startswith("https://"):
            raise Problem(422, "validation_failed", "A frontier provider needs an https endpoint.")
        if row.kind == "frontier" and (
            row.secret_ref is None or row.data_region is None
        ):
            raise Problem(
                422,
                "validation_failed",
                "A frontier provider needs secret_ref and data_region.",
            )
        if row.status == "retired" and body.enabled is True:
            raise Problem(422, "business_rule_violated", "A retired provider cannot be enabled.")
        if body.enabled is True and row.kind == "frontier" and (
            row.approved_by is None or row.approved_at is None or not row.zero_retention
        ):
            raise Problem(
                422,
                "business_rule_violated",
                "A frontier provider needs compliance approval and zero retention before enabling.",
            )
        if body.enabled is True and row.approved_by == principal.user_id:
            raise Problem(
                403,
                "policy_denied",
                "The person who approved a frontier provider cannot enable it.",
            )

        if changed_contract:
            row.enabled = False
            row.approved_by = None
            row.approved_at = None
        elif body.enabled is not None:
            row.enabled = body.enabled
            if body.enabled:
                row.enabled_by = principal.user_id
        row.updated_by = principal.user_id
        row.updated_at = datetime.datetime.now(datetime.UTC)
        await session.flush()
        await session.refresh(row)
    return ModelProviderView.model_validate(row)


@router.post(
    "/v1/admin/model-providers/{id}:approve",
    response_model=ModelProviderView,
    **endpoint(
        "POST",
        "/v1/admin/model-providers/{id}:approve",
        operation_id="approveModelProvider",
    ),
)
async def approve_model_provider(
    body: ModelProviderApprove,
    provider_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
) -> ModelProviderView:
    if "compliance" not in principal.capabilities:
        raise Problem(403, "policy_denied", "Only compliance can approve a model provider.")
    async with transaction(ROLE, principal) as session:
        row = await session.scalar(
            select(governance.ModelProvider)
            .where(
                governance.ModelProvider.id == provider_id,
                governance.ModelProvider.org_id == principal.org_id,
            )
            .with_for_update()
        )
        if row is None:
            raise Problem(404, "not_found", "Model provider not found.")
        if row.kind != "frontier":
            raise Problem(422, "business_rule_violated", "Only frontier providers need approval.")
        if row.status == "retired":
            raise Problem(409, "invalid_state", "A retired provider cannot be approved.")
        if row.enabled and row.enabled_by == principal.user_id:
            raise Problem(
                403,
                "policy_denied",
                "The person who enabled a frontier provider cannot approve it.",
            )
        if (
            not row.endpoint.startswith("https://")
            or row.secret_ref is None
            or row.data_region is None
            or not row.zero_retention
        ):
            raise Problem(
                422,
                "business_rule_violated",
                "A frontier provider needs a safe external contract before approval.",
            )
        row.approved_by = principal.user_id
        row.approved_at = datetime.datetime.now(datetime.UTC)
        row.updated_by = principal.user_id
        row.updated_at = datetime.datetime.now(datetime.UTC)
        await session.flush()
        await session.refresh(row)
    return ModelProviderView.model_validate(row)


@router.post(
    "/v1/admin/model-providers/{id}:test",
    response_model=ModelProviderTestResult,
    **endpoint(
        "POST",
        "/v1/admin/model-providers/{id}:test",
        operation_id="testModelProvider",
    ),
)
async def test_model_provider(
    body: ModelProviderTest,
    provider_id: Annotated[uuid.UUID, Path(alias="id")],
    principal: CurrentPrincipal,
) -> ModelProviderTestResult:
    async with transaction(ROLE, principal) as session:
        provider = await session.scalar(
            select(governance.ModelProvider).where(
                governance.ModelProvider.id == provider_id,
                governance.ModelProvider.org_id == principal.org_id,
            )
        )
        if provider is None:
            raise Problem(404, "not_found", "Model provider not found.")
        prompt_hash = hashlib.sha256(TEST_PROMPT.encode()).hexdigest()
        row = governance.ModelRouteDecision(
            id=uuid7(),
            org_id=principal.org_id,
            data_space_id=None,
            task="provider_test",
            caller_kind="eval",
            caller_id=None,
            data_product_id=None,
            product_scope="not_applicable",
            context_labels=["public"],
            max_classification="public",
            contains_internal_data=False,
            pii_redactions=0,
            candidate_provider_id=provider.id if provider.kind == "frontier" else None,
            decision="frontier_allowed" if provider.kind == "frontier" else "on_prem_default",
            reasons=["allowed_public_only"] if provider.kind == "frontier" else [],
            chosen_provider_id=provider.id,
            model_name=body.model_name,
            prompt_sha256=prompt_hash,
        )
        session.add(row)
        await session.flush()
        await session.refresh(row)
        decision_id = row.id

    try:
        latency_ms, input_tokens, output_tokens = await asyncio.to_thread(
            _probe_provider, provider, body.model_name
        )
    except _ProviderUnavailable as exc:
        async with transaction(ROLE, principal) as session:
            row = await session.scalar(
                select(governance.ModelRouteDecision).where(
                    governance.ModelRouteDecision.id == decision_id,
                    governance.ModelRouteDecision.org_id == principal.org_id,
                )
            )
            if row is not None:
                row.outcome = "error"
                await session.flush()
        raise Problem(503, "model_unavailable", str(exc)) from exc

    async with transaction(ROLE, principal) as session:
        row = await session.scalar(
            select(governance.ModelRouteDecision).where(
                governance.ModelRouteDecision.id == decision_id,
                governance.ModelRouteDecision.org_id == principal.org_id,
            )
        )
        if row is not None:
            row.input_tokens = input_tokens
            row.output_tokens = output_tokens
            row.outcome = "ok"
            await session.flush()
    return ModelProviderTestResult(
        provider_id=provider.id,
        model_name=body.model_name,
        outcome="ok",
        latency_ms=latency_ms,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


@router.get(
    "/v1/admin/model-route-decisions",
    response_model=ModelRouteDecisionPage,
    **endpoint(
        "GET",
        "/v1/admin/model-route-decisions",
        operation_id="listModelRouteDecisions",
    ),
)
async def list_model_route_decisions(
    principal: CurrentPrincipal,
    page: Annotated[PageRequest, Depends(page_request)],
    decision: str | None = Query(default=None, max_length=40),
    reason: str | None = Query(default=None, max_length=80),
    provider_id: uuid.UUID | None = None,
    caller_kind: str | None = Query(default=None, max_length=30),
    caller_id: uuid.UUID | None = None,
    from_: Annotated[datetime.datetime | None, Query(alias="from")] = None,
    to: datetime.datetime | None = None,
) -> ModelRouteDecisionPage:
    row_type = governance.ModelRouteDecision
    query = select(row_type).where(row_type.org_id == principal.org_id)
    if decision:
        query = query.where(row_type.decision == decision)
    if reason:
        query = query.where(row_type.reasons.any(reason))
    if provider_id:
        query = query.where(row_type.chosen_provider_id == provider_id)
    if caller_kind:
        query = query.where(row_type.caller_kind == caller_kind)
    if caller_id:
        query = query.where(row_type.caller_id == caller_id)
    if from_:
        query = query.where(row_type.decided_at >= from_)
    if to:
        query = query.where(row_type.decided_at < to)
    if page.cursor:
        try:
            after = (
                datetime.datetime.fromisoformat(str(page.cursor["k"])),
                uuid.UUID(str(page.cursor["id"])),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise invalid_page_token() from exc
        query = query.where(tuple_(row_type.decided_at, row_type.id) < after)
    query = query.order_by(row_type.decided_at.desc(), row_type.id.desc()).limit(
        page.page_size + 1
    )

    async with transaction(ROLE, principal) as session:
        rows = list((await session.scalars(query)).all())
    more = len(rows) > page.page_size
    rows = rows[: page.page_size]
    last = rows[-1] if more else None
    return ModelRouteDecisionPage(
        items=[ModelRouteDecisionView.model_validate(row) for row in rows],
        next_page_token=encode_page_token(
            {"k": last.decided_at.isoformat(), "id": str(last.id)} if last else None
        ),
    )
