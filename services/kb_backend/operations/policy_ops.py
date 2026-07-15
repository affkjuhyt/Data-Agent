"""Business logic for filter policy and extraction policy CRUD."""
import json
import logging
import uuid
from fastapi import HTTPException
from sqlalchemy import update as sa_update

from basemodel.services_databaseconnector.postgres_model import (
    ReadJoinRequest, OrderBy, OrderDirection,
    KBFilterPolicyInsert, KBFilterPolicyDelete,
    KBExtractionPolicyInsert,
    PolicyFilteringType,
)
from services.kb_backend.operations.pagination import (
    PAGE_SIZE, get_cursor, advance, build_page,
)
from basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBFilterPolicyORM, KBExtractionPolicyORM,
)
from services.kb_backend.schemas import to_string

log = logging.getLogger(__name__)

_BASE_EXTRACTION = (
    "Extract all named entities including:\n"
    "• Organizations (ORG): companies, institutions, agencies, departments\n"
    "• Persons (PER): individuals, roles, job titles, team names\n"
    "• Locations (LOC): geographic references, addresses, regions\n"
    "• Dates & Times (DATE): temporal references, schedules, deadlines\n"
    "• Concepts (CONCEPT): domain-specific abstract terms and definitions"
)

# configformat in DB is the canonical wire value (natural_language|exact_word).
# No translation needed — see src/lib/enums.ts for the shared canonical set.


def _to_filter_policy(row: dict) -> dict:
    ptype = row.get("configformat") or "natural_language"
    cfg = row.get("config") or {}
    rules: list = cfg.get("rules") or []
    content = json.dumps(rules) if ptype == "exact_word" else (rules[0] if rules else "")
    return {
        "id":         to_string(row.get("policy_id")),
        "name":       row.get("policy_name", ""),
        "type":       ptype,
        "content":    content,
        "added_by":   row.get("created_by", ""),
        "added_when": to_string(row.get("created_at", ""))[:10],
        "active":     bool(row.get("is_active", False)),
    }


def _rules_from_body(ptype: str, content: str) -> list[str]:
    if ptype == "exact_word":
        try:
            return json.loads(content)
        except (ValueError, TypeError):
            return [w.strip() for w in content.split(",") if w.strip()]
    return [content] if content else []


async def list_filter_policies(
    postgres, tenant_id: str | None,
    user_id: str, page: int = 1,
) -> dict:
    cursor_key = f"policies:filter:{tenant_id}"
    cursor_ts  = get_cursor(user_id, cursor_key, page)
    resp = await postgres.read(ReadJoinRequest(
        tenant_id=tenant_id,
        joins_table=["KBFilterPolicy"],
        cursor=cursor_ts,
        limit=PAGE_SIZE,
        order_by=OrderBy(table_name="KBFilterPolicy", column="inserted_at", order=OrderDirection.ASC),
    ))
    if resp.code != 200:
        log.error("list_filter_policies: DB error: %s", resp.error)
        raise HTTPException(status_code=500, detail="Failed to load filter policies")
    raw = resp.data or []
    advance(user_id, cursor_key, page, raw)
    return build_page([_to_filter_policy(r) for r in raw], page)


async def create_filter_policy(
    postgres,
    name: str, ptype: str, content: str,
    tenant_id: str, created_by: str,
    active: bool = True,
) -> dict:
    # created_by stores the human-readable username (not the user_id UUID) so the
    # UI shows "added_by: helios-admin" rather than an opaque id.
    resp = await postgres.insert(KBFilterPolicyInsert(
        tenant_id=tenant_id,
        policy_name=name,
        configformat=PolicyFilteringType(ptype),
        config={"rules": _rules_from_body(ptype, content)},
        is_active=active,
        created_by=created_by,
    ))
    if resp.code != 200:
        log.error("create_filter_policy: DB error: %s", resp.error)
        # Surface client-fixable problems (duplicate / bad reference / missing
        # field) with their real status + message instead of a blanket 500, so the
        # UI can show "name already exists" etc.
        if resp.code in (400, 409):
            raise HTTPException(status_code=resp.code, detail=resp.error or "Could not create filter policy")
        raise HTTPException(status_code=500, detail="Failed to create filter policy")
    return {"policy_id": resp.data.get("policy_id")}


async def update_filter_policy(postgres, policy_id: str, body: dict) -> dict:
    updates: dict = {}
    if "name" in body:
        updates["policy_name"] = body["name"]
    if "active" in body:
        updates["is_active"] = bool(body["active"])
    if "type" in body and "content" in body:
        ptype = body["type"]
        content = body["content"]
        updates["configformat"] = ptype if ptype in ("natural_language", "exact_word") else PolicyFilteringType.NATURAL_LANG.value
        updates["config"] = {"rules": _rules_from_body(ptype, content)}

    if not updates:
        return {"status": "no-op"}

    policy_uuid = uuid.UUID(policy_id) if isinstance(policy_id, str) else policy_id
    async with postgres.get_client() as session:
        await session.execute(
            sa_update(KBFilterPolicyORM)
            .where(KBFilterPolicyORM.policy_id == policy_uuid)
            .values(**updates)
        )
        await session.commit()
    return {"status": "ok", "policy_id": policy_id}


async def delete_filter_policy(postgres, policy_id: str, tenant_id: str) -> None:
    resp = await postgres.soft_delete(KBFilterPolicyDelete(
        tenant_id=tenant_id,
        policy_id=policy_id,
    ))
    if resp.code == 404:
        raise HTTPException(status_code=404, detail="Policy not found")
    if resp.code != 200:
        log.error("delete_filter_policy: DB error: %s", resp.error)
        raise HTTPException(status_code=500, detail="Failed to delete filter policy")


async def get_extraction_policy(postgres, tenant_id: str | None) -> dict:
    resp = await postgres.read(ReadJoinRequest(
        tenant_id=tenant_id, joins_table=["KBExtractionPolicy"], limit=1,
    ))
    custom = ""
    if resp.code == 200 and resp.data:
        custom = resp.data[0].get("custom_override") or ""
    return {"base": _BASE_EXTRACTION, "custom": custom}


async def update_extraction_policy(
    postgres, custom: str, tenant_id: str, created_by: str,
) -> dict:
    resp = await postgres.read(ReadJoinRequest(
        tenant_id=tenant_id, joins_table=["KBExtractionPolicy"], limit=1,
    ))
    if resp.code == 200 and resp.data:
        pid = resp.data[0].get("policy_id")
        pid_uuid = uuid.UUID(pid) if isinstance(pid, str) else pid
        async with postgres.get_client() as session:
            await session.execute(
                sa_update(KBExtractionPolicyORM)
                .where(KBExtractionPolicyORM.policy_id == pid_uuid)
                .values(custom_override=custom)
            )
            await session.commit()
    else:
        from basemodel.services_databaseconnector.postgres_model import PolicyExtractionType
        await postgres.insert(KBExtractionPolicyInsert(
            tenant_id=tenant_id,
            policy_name="Default Extraction Policy",
            policy_type=PolicyExtractionType.ENTITY_NODE,
            custom_override=custom,
            created_by=created_by,
        ))
    return {"status": "ok"}
