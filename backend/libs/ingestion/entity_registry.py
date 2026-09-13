from __future__ import annotations

from typing import Any, Awaitable, Callable
import re
import unicodedata
import uuid

from qdrant_client import models
from qdrant_client.async_qdrant_client import AsyncQdrantClient
from sqlalchemy import or_ as sa_or, select as sa_select

from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBEntityLookupORM,
)
from libs.database_connector.model_connector import ModelServiceClient
from libs.database_connector.postgres_connector import PostgresClient
from libs.ingestion import config as cfg

# Stable namespace for deterministic entity UUIDs
_ENTITY_NS = uuid.UUID("6ba7b812-9dad-11d1-80b4-00c04fd430c9")


# ── Canonical normalisation ────────────────────────────────────────────────────

def to_canonical(name: str) -> str:
    """Convert any entity surface form to a lowercase_underscore canonical that
    PRESERVES Unicode letters"""
    s = unicodedata.normalize("NFC", name).casefold().strip()
    s = re.sub(r"[\s\-/\\]+", "_", s)  # whitespace / hyphens / slashes → _
    s = re.sub(r"[^\w]", "", s)  # drop punctuation; \w keeps Unicode letters
    s = re.sub(r"_+", "_", s).strip("_")  # collapse runs, trim edges
    return s


def _entity_point_id(tenant_id: str, canonical: str) -> str:
    return str(uuid.uuid5(_ENTITY_NS, f"{tenant_id}:{canonical}"))


def _embed_text(name: str, description: str) -> str:
    """Text embedded for registry matching: surface form + meaning. The description
    is what lets "Mỹ" and "Hoa Kỳ" land near each other despite sharing no tokens."""
    return f"{name}: {description}" if description else name


# ── Exact alias lookup (Postgres KBEntityLookup) ──────────────────────────────

async def _alias_lookup(
        postgres: PostgresClient,
        tenant_id: str,
        surface_forms_by_entity: list[list[str]],
) -> list[str | None]:
    """Each entity's canonical from the alias table, in ONE query for all of them.

    Alias keys are stored canonicalized, so the lookup is hash-index equality. An entity
    resolves on its first surface form that has an entry; tenant rows beat global ones.
    Raises: a swallowed failure reads as "no alias", which forks a second canonical for
    an entity the registry already knows.
    """
    alias_keys_by_entity = [
        [key for key in (to_canonical(form) for form in surface_forms) if key]
        for surface_forms in surface_forms_by_entity
    ]
    every_alias_key = {key for alias_keys in alias_keys_by_entity for key in alias_keys}
    if not every_alias_key:
        return [None] * len(surface_forms_by_entity)
    try:
        tenant_uuid = uuid.UUID(tenant_id)
    except (ValueError, AttributeError):
        tenant_uuid = None

    async with postgres.get_client() as session:
        rows = (await session.execute(
            sa_select(
                KBEntityLookupORM.alias_name,
                KBEntityLookupORM.canonical_name,
                KBEntityLookupORM.tenant_id,
            )
            .where(KBEntityLookupORM.alias_name.in_(every_alias_key))
            .where(KBEntityLookupORM.is_deleted.is_(False))
        )).all()

    tenant_canonical_by_key: dict[str, str] = {}
    global_canonical_by_key: dict[str, str] = {}
    for alias_name, canonical_name, row_tenant_id in rows:
        if row_tenant_id == tenant_uuid:
            tenant_canonical_by_key.setdefault(alias_name, canonical_name)
        elif row_tenant_id is None:
            global_canonical_by_key.setdefault(alias_name, canonical_name)

    return [
        next((tenant_canonical_by_key[key] for key in alias_keys if key in tenant_canonical_by_key), None)
        or next((global_canonical_by_key[key] for key in alias_keys if key in global_canonical_by_key), None)
        for alias_keys in alias_keys_by_entity
    ]


async def _register_aliases(
        postgres: PostgresClient,
        tenant_id: str,
        canonical: str,
        surface_forms: list[str],
) -> None:
    """Insert alias → canonical rows for any surface forms not yet known, so the
    next document resolves them exactly without an embedding call. Best-effort:
    a failure here never fails the gold commit."""
    alias_key_to_surface: dict[str, str] = {}
    for surface_form in surface_forms:
        canonical_key = to_canonical(surface_form)
        if canonical_key and canonical_key != canonical:
            alias_key_to_surface[canonical_key] = surface_form
    # The canonical form itself also gets a self-row so a future exact mention
    # of the canonical resolves at step 1.
    alias_key_to_surface.setdefault(canonical, canonical)
    if not alias_key_to_surface:
        return
    try:
        tenant_uuid = uuid.UUID(tenant_id)
    except (ValueError, AttributeError):
        tenant_uuid = None
    try:
        async with postgres.get_client() as session:
            # Both guards read the same table, so they share one query:
            #  - a key already mapped to a DIFFERENT canonical is a collision — never
            #    overwrite or shadow it (first registration wins; LLM association
            #    pollution must not rebind an existing entity's name).
            #  - a key that IS some other entity's canonical is equally off-limits
            #    ("creative_commons" can never become an alias of the GFDL license).
            foreign_canonical_keys = [key for key in alias_key_to_surface if key != canonical]
            claimed_rows = (await session.execute(
                sa_select(KBEntityLookupORM.alias_name, KBEntityLookupORM.canonical_name)
                .where(sa_or(
                    KBEntityLookupORM.alias_name.in_(list(alias_key_to_surface)),
                    KBEntityLookupORM.canonical_name.in_(foreign_canonical_keys),
                ))
                .where(KBEntityLookupORM.is_deleted.is_(False))
            )).all()

            taken_by_key: dict[str, str] = {}
            other_canonicals: set[str] = set()
            for alias_name, mapped_canonical in claimed_rows:
                if alias_name in alias_key_to_surface:
                    taken_by_key.setdefault(alias_name, mapped_canonical)
                if mapped_canonical in foreign_canonical_keys:
                    other_canonicals.add(mapped_canonical)

            new_rows = [
                KBEntityLookupORM(alias_name=alias_key, canonical_name=canonical, tenant_id=tenant_uuid)
                for alias_key in alias_key_to_surface
                if alias_key not in taken_by_key and alias_key not in other_canonicals
            ]
            if new_rows:
                try:
                    session.add_all(new_rows)
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise
    except Exception as exc:
        raise RuntimeError(f"alias registration failed for canonical={canonical!r}: {exc}") from exc


# ── Qdrant helpers ─────────────────────────────────────────────────────────────

def _tenant_filter(tenant_id: str) -> models.Filter:
    return models.Filter(must=[
        models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id)),
        models.FieldCondition(key="is_deleted", match=models.MatchValue(value=False)),
    ])


def _types_conflict(type_a: str, type_b: str) -> bool:
    """Two SPECIFIC (non-concept) types that differ block a merge """
    a, b = (type_a or "").strip().lower(), (type_b or "").strip().lower()
    return bool(a and b and a != "concept" and b != "concept" and a != b)


async def _best_candidate(
    qdrant: AsyncQdrantClient,
    tenant_id: str,
    embedding: list[float],
) -> tuple[str, float, dict[str, Any]] | None:
    """Best embedding match at or above the verify band: (canonical, score, payload).

    The DECISION (merge / verify / new) belongs to the caller — multilingual-e5
    cosines overlap between same-entity and different-entity pairs (measured:
    Bangladesh~Ấn Độ 0.949 vs TP.HCM~Sài Gòn 0.950), so a score alone is only a
    candidate signal, never a verdict, except at the very top of the range.
    """
    try:
        resp = await qdrant.query_points(
            collection_name=cfg.ENTITY_COLLECTION,
            query=embedding,
            limit=1,
            query_filter=_tenant_filter(tenant_id),
            with_payload=True,
        )
    except Exception as exc:
        raise RuntimeError(f"entity registry candidate lookup failed: {exc}") from exc
    hits = resp.points
    if not hits or hits[0].score < cfg.ENTITY_VERIFY_BAND:
        return None
    payload = hits[0].payload or {}
    canonical = payload.get("canonical", "")
    return (canonical, hits[0].score, payload) if canonical else None


async def _upsert_entity(
        qdrant: AsyncQdrantClient,
        tenant_id: str,
        canonical: str,
        raw: str,
        aliases: list[str],
        description: str,
        embedding: list[float],
        entity_type: str = "",
        knowledge_base: str = "",
        embed_model_id: str = "",
) -> None:
    """Insert a new entity or extend an existing canonical's aliases/description."""
    point_id = _entity_point_id(tenant_id, canonical)

    try:
        existing = await qdrant.retrieve(
            collection_name=cfg.ENTITY_COLLECTION,
            ids=[point_id],
            with_payload=True,
        )
    except Exception:
        existing = []

    incoming_aliases = [
        surface_form for surface_form in [raw, *aliases]
        if surface_form and to_canonical(surface_form) != canonical
    ]

    if existing:
        payload = existing[0].payload or {}
        current_aliases: list[str] = payload.get("aliases", [])
        updates: dict[str, Any] = {}
        merged = list(dict.fromkeys([*current_aliases, *incoming_aliases]))
        if merged != current_aliases:
            updates["aliases"] = merged
        # Backfill type once; an existing specific type is never overwritten.
        if entity_type and payload.get("entity_type") in (None, "", "concept") and entity_type != "concept":
            updates["entity_type"] = entity_type
        # Backfill the KB scope / embed model on legacy points that predate them.
        if knowledge_base and not payload.get("knowledge_base"):
            updates["knowledge_base"] = knowledge_base
        if embed_model_id and not payload.get("embedding_model_id"):
            updates["embedding_model_id"] = embed_model_id
        if description and not payload.get("description"):
            updates["description"] = description
            await qdrant.upsert(
                collection_name=cfg.ENTITY_COLLECTION,
                points=[models.PointStruct(
                    id=point_id, vector=embedding,
                    payload={**payload, **updates},
                )],
            )
            return
        if updates:
            await qdrant.set_payload(
                collection_name=cfg.ENTITY_COLLECTION,
                payload=updates,
                points=[point_id],
            )
        return

    await qdrant.upsert(
        collection_name=cfg.ENTITY_COLLECTION,
        points=[
            models.PointStruct(
                id=point_id,
                vector=embedding,
                payload={
                    "tenant_id": tenant_id,
                    "knowledge_base": knowledge_base,
                    "canonical": canonical,
                    "display_name": raw,
                    "aliases": incoming_aliases,
                    "description": description,
                    "entity_type": entity_type or "concept",
                    "is_deleted": False,
                    "embedding_model_id": embed_model_id,
                },
            )
        ],
    )


# ── Resolution helpers (extracted from normalize_entities_detailed for SRP) ────

def _clean_raw_entities(raw_entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """NFC-normalise + strip each raw entity's name/description/aliases/type and drop
    the unnamed ones. Returns the list the resolution loop iterates over."""
    return [
        {
            "name": unicodedata.normalize("NFC", entity["name"]).strip(),
            "description": (entity.get("description") or "").strip(),
            "entity_type": (entity.get("type") or "").strip().lower(),
            "aliases": [
                unicodedata.normalize("NFC", alias).strip()
                for alias in (entity.get("aliases") or [])
                if isinstance(alias, str) and alias.strip()
            ],
        }
        for entity in raw_entities
        if isinstance(entity, dict) and entity.get("name", "").strip()
    ]


async def _resolve_embedded_entity(
    qdrant: AsyncQdrantClient,
    postgres: PostgresClient,
    tenant_id: str,
    entry: dict[str, Any],
    embedding: list[float],
    verify_fn: Callable[[str, str, str, str], Awaitable[bool]] | None,
    knowledge_base: str,
    embed_model_id: str,
) -> dict[str, Any]:
    """Three-band merge decision for ONE alias-missed entity that now has an
    embedding, then persist (upsert + alias learning) the resolved canonical.

    Bands (e5 cosines overlap between same/different-entity pairs):
      1. type guard — a specific-type mismatch always blocks the merge
      2. score >= ENTITY_SIMILARITY_THRESHOLD → merge
      3. score >= ENTITY_VERIFY_BAND → merge only if verify_fn confirms
    Returns the detail dict {raw, aliases, canonical, action, similarity}. Learning
    is safe here: this branch is reached only for a near-exact or LLM-confirmed
    merge, or for a brand-new canonical — never for unverified embedding proximity.
    """
    raw, aliases, description = entry["name"], entry["aliases"], entry["description"]
    canonical_candidate = to_canonical(raw)
    surface_forms = [raw, *aliases]

    merged_to: str | None = None
    action = "new"
    score_out: float | None = None
    candidate = await _best_candidate(qdrant, tenant_id, embedding)
    if candidate:
        cand_canonical, score, cand_payload = candidate
        score_out = round(score, 4)
        type_conflict = _types_conflict(entry["entity_type"], cand_payload.get("entity_type", ""))
        if not type_conflict and score >= cfg.ENTITY_SIMILARITY_THRESHOLD:
            merged_to, action = cand_canonical, "matched_hard"
        elif not type_conflict and verify_fn is not None:
            confirmed = await verify_fn(
                raw, description,
                cand_payload.get("display_name", cand_canonical),
                cand_payload.get("description", ""),
            )
            if confirmed:
                merged_to, action = cand_canonical, "matched_verified"

    canonical = merged_to or canonical_candidate
    await _upsert_entity(
        qdrant, tenant_id, canonical, raw, aliases, description, embedding,
        entity_type=entry["entity_type"],
        knowledge_base=knowledge_base, embed_model_id=embed_model_id,
    )
    await _register_aliases(postgres, tenant_id, canonical, surface_forms)
    return {
        "raw": raw,
        "aliases": aliases,
        "canonical": canonical,
        "action": action,
        "similarity": score_out,
    }


def _dedupe_by_canonical(
    names: list[str], details: list[dict[str, Any]],
) -> tuple[list[str], list[dict[str, Any]]]:
    """Collapse the positional (name, detail) lists to unique canonicals, first
    occurrence winning, dropping empty placeholders."""
    seen: set[str] = set()
    out_names: list[str] = []
    out_details: list[dict[str, Any]] = []
    for name, detail in zip(names, details):
        if name and name not in seen:
            seen.add(name)
            out_names.append(name)
            out_details.append(detail)
    return out_names, out_details


# ── Public API ─────────────────────────────────────────────────────────────────

async def normalize_entities(
    qdrant: AsyncQdrantClient,
    postgres: PostgresClient,
    model: ModelServiceClient,
    tenant_id: str,
    raw_entities: list[dict[str, Any]],
    verify_fn: Callable[[str, str, str, str], Awaitable[bool]] | None = None,
    knowledge_base: str = "",
    embed_model_id: str = "",
) -> list[str]:
    """Normalise raw entities — returns deduplicated canonical name list."""
    canonicals, _ = await normalize_entities_detailed(
        qdrant, postgres, model, tenant_id, raw_entities, verify_fn=verify_fn,
        knowledge_base=knowledge_base, embed_model_id=embed_model_id)
    return canonicals


async def normalize_entities_detailed(
    qdrant: AsyncQdrantClient,
    postgres: PostgresClient,
    model: ModelServiceClient,
    tenant_id: str,
    raw_entities: list[dict[str, Any]],
    verify_fn: Callable[[str, str, str, str], Awaitable[bool]] | None = None,
    knowledge_base: str = "",
    embed_model_id: str = "",
) -> tuple[list[str], list[dict[str, Any]]]:
    """Normalise raw entities and return canonicalization details for each.

    Each raw entity: {"name": str, "description": str, "aliases": [str], ...} —
    aliases are other surface forms of the SAME entity proposed by the extraction
    LLM (the Vietnamese synonym channel).

    Returns:
        (canonical_names, details) where each detail dict contains:
            raw        — original surface form (diacritics intact)
            aliases    — alias surface forms carried with the mention
            canonical  — resolved canonical name
            action     — "matched_alias" | "matched_hard" | "matched_soft" | "new"
            similarity — cosine score if embedding-matched, None otherwise
    """
    cleaned_entities = _clean_raw_entities(raw_entities)
    if not cleaned_entities:
        return [], []

    result: list[str] = []
    details: list[dict[str, Any]] = []
    # Entities whose embedding lookup is still needed (alias misses).
    needs_embedding: list[tuple[int, dict[str, Any]]] = []

    # Pass 1 — exact alias lookup (deterministic, no embedding), one query for all.
    surface_forms_by_entity = [[entry["name"], *entry["aliases"]] for entry in cleaned_entities]
    canonical_by_entity = await _alias_lookup(postgres, tenant_id, surface_forms_by_entity)

    for idx, (entry, canonical) in enumerate(zip(cleaned_entities, canonical_by_entity)):
        result.append("")  # placeholder, filled below
        details.append({})
        if not canonical:
            needs_embedding.append((idx, entry))
            continue
        result[idx] = canonical
        details[idx] = {
            "raw": entry["name"],
            "aliases": entry["aliases"],
            "canonical": canonical,
            "action": "matched_alias",
            "similarity": None,
        }
        # Learn any surface form that wasn't in the table yet.
        await _register_aliases(postgres, tenant_id, canonical, surface_forms_by_entity[idx])

    # Pass 2 — embedding-based three-band resolution for the alias misses.
    if needs_embedding:
        try:
            embeddings = await model.embed(
                [_embed_text(entry["name"], entry["description"]) for _, entry in needs_embedding]
            )
        except Exception as exc:
            raise RuntimeError(f"entity registry embed failed for {len(needs_embedding)} entities: {exc}") from exc

        for pos, (idx, entry) in enumerate(needs_embedding):
            canonical_candidate = to_canonical(entry["name"])
            if not canonical_candidate:
                continue
            detail = await _resolve_embedded_entity(
                qdrant, postgres, tenant_id, entry, embeddings[pos], verify_fn,
                knowledge_base, embed_model_id,
            )
            result[idx] = detail["canonical"]
            details[idx] = detail

    return _dedupe_by_canonical(result, details)
