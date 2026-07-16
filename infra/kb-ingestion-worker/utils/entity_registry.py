from __future__ import annotations

import logging
import re
import unicodedata
import uuid

from qdrant_client import models
from qdrant_client.async_qdrant_client import AsyncQdrantClient
from sqlalchemy import select as sa_select

from basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import (
    KBEntityLookupORM,
)
from services.database_connector.model_connector import ModelServiceClient
from services.database_connector.postgres_connector import PostgresClient
from utils import config as cfg

log = logging.getLogger(__name__)

# Stable namespace for deterministic entity UUIDs
_ENTITY_NS = uuid.UUID("6ba7b812-9dad-11d1-80b4-00c04fd430c9")


# ── Canonical normalisation ────────────────────────────────────────────────────

def to_canonical(name: str) -> str:
    """Convert any entity surface form to a lowercase_underscore canonical that
    PRESERVES Unicode letters"""
    s = unicodedata.normalize("NFC", name).casefold().strip()
    s = re.sub(r"[\s\-/\\]+", "_", s)        # whitespace / hyphens / slashes → _
    s = re.sub(r"[^\w]", "", s)              # drop punctuation; \w keeps Unicode letters
    s = re.sub(r"_+", "_", s).strip("_")     # collapse runs, trim edges
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
    surface_forms: list[str],
) -> str | None:
    """Return the canonical name for the first surface form with an exact alias
    entry, or None. Alias keys are stored in canonical form so lookup is a pure
    hash-index equality. Tenant-scoped rows win over global (tenant_id IS NULL)."""
    alias_keys = [key for key in (to_canonical(form) for form in surface_forms) if key]
    if not alias_keys:
        return None
    try:
        tenant_uuid = uuid.UUID(tenant_id)
    except (ValueError, AttributeError):
        tenant_uuid = None
    try:
        async with postgres.get_client() as session:
            rows = (await session.execute(
                sa_select(
                    KBEntityLookupORM.alias_name,
                    KBEntityLookupORM.canonical_name,
                    KBEntityLookupORM.tenant_id,
                )
                .where(KBEntityLookupORM.alias_name.in_(alias_keys))
                .where(KBEntityLookupORM.is_deleted.is_(False))
            )).all()
    except Exception as exc:
        log.warning("entity_registry | alias lookup failed: %s", exc)
        return None

    tenant_match: str | None = None
    global_match: str | None = None
    for alias_name, canonical_name, row_tenant_id in rows:
        if row_tenant_id == tenant_uuid and tenant_match is None:
            tenant_match = canonical_name
        elif row_tenant_id is None and global_match is None:
            global_match = canonical_name
    return tenant_match or global_match


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
            # Existing alias rows for these keys — ANY tenant scope. A key that
            # already maps to a DIFFERENT canonical is a collision: never
            # overwrite or shadow it (first registration wins; LLM association
            # pollution must not rebind an existing entity's name).
            taken_by_key: dict[str, str] = {}
            for alias_name, mapped_canonical in (await session.execute(
                sa_select(KBEntityLookupORM.alias_name, KBEntityLookupORM.canonical_name)
                .where(KBEntityLookupORM.alias_name.in_(list(alias_key_to_surface)))
                .where(KBEntityLookupORM.is_deleted.is_(False))
            )).all():
                taken_by_key.setdefault(alias_name, mapped_canonical)
            # Keys that ARE some other entity's canonical name are equally off-limits
            # ("creative_commons" can never become an alias of the GFDL license).
            other_canonicals = set((await session.execute(
                sa_select(KBEntityLookupORM.canonical_name)
                .where(KBEntityLookupORM.canonical_name.in_(
                    [key for key in alias_key_to_surface if key != canonical]))
                .where(KBEntityLookupORM.is_deleted.is_(False))
            )).scalars())

            new_rows = []
            for alias_key in alias_key_to_surface:
                if alias_key in taken_by_key:
                    if taken_by_key[alias_key] != canonical:
                        log.warning(
                            "entity_registry | alias collision: %r already → %r, "
                            "NOT rebinding to %r", alias_key, taken_by_key[alias_key], canonical,
                        )
                    continue
                if alias_key in other_canonicals:
                    log.warning(
                        "entity_registry | %r is another entity's canonical — "
                        "refusing to alias it to %r", alias_key, canonical,
                    )
                    continue
                new_rows.append(
                    KBEntityLookupORM(alias_name=alias_key, canonical_name=canonical, tenant_id=tenant_uuid)
                )
            if new_rows:
                session.add_all(new_rows)
                await session.commit()
                log.debug(
                    "entity_registry | learned %d alias(es) → %r",
                    len(new_rows), canonical,
                )
    except Exception as exc:
        log.warning("entity_registry | alias registration failed for %r: %s", canonical, exc)


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
) -> tuple[str, float, dict] | None:
    """Best embedding match at or above the verify band: (canonical, score, payload)."""
    try:
        resp = await qdrant.query_points(
            collection_name=cfg.ENTITY_COLLECTION,
            query=embedding,
            limit=1,
            query_filter=_tenant_filter(tenant_id),
            with_payload=True,
        )
        hits = resp.points
    except Exception as exc:
        log.warning("entity_registry | lookup failed: %s", exc)
        return None
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

    incoming_aliases = [a for a in [raw, *aliases] if a and to_canonical(a) != canonical]

    if existing:
        payload = existing[0].payload or {}
        current_aliases: list[str] = payload.get("aliases", [])
        updates: dict = {}
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
                    "tenant_id":          tenant_id,
                    "knowledge_base":     knowledge_base,
                    "canonical":          canonical,
                    "display_name":       raw,
                    "aliases":            incoming_aliases,
                    "description":        description,
                    "entity_type":        entity_type or "concept",
                    "is_deleted":         False,
                    "embedding_model_id": embed_model_id,
                },
            )
        ],
    )
    log.debug("entity_registry | registered canonical=%r raw=%r aliases=%r",
              canonical, raw, incoming_aliases)


# ── Resolution helpers (extracted from normalize_entities_detailed for SRP) ────

def _clean_raw_entities(raw_entities: list[dict]) -> list[dict]:
    """NFC-normalise + strip each raw entity's name/description/aliases/type and drop
    the unnamed ones. Returns the cleaned list the resolution loop iterates over."""
    return [
        {
            "name":        unicodedata.normalize("NFC", entity["name"]).strip(),
            "description": (entity.get("description") or "").strip(),
            "entity_type": (entity.get("type") or "").strip().lower(),
            "aliases":     [
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
    entry: dict,
    embedding: list[float],
    verify_fn,
    knowledge_base: str,
    embed_model_id: str,
) -> dict:
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
        if _types_conflict(entry["entity_type"], cand_payload.get("entity_type", "")):
            log.info(
                "entity_registry | type guard: %r (%s) vs %r (%s) score=%.3f — no merge",
                raw, entry["entity_type"],
                cand_payload.get("display_name", cand_canonical),
                cand_payload.get("entity_type"), score,
            )
        elif score >= cfg.ENTITY_SIMILARITY_THRESHOLD:
            merged_to, action = cand_canonical, "matched_hard"
        elif verify_fn is not None:
            confirmed = await verify_fn(
                raw, description,
                cand_payload.get("display_name", cand_canonical),
                cand_payload.get("description", ""),
            )
            if confirmed:
                merged_to, action = cand_canonical, "matched_verified"
            else:
                log.info(
                    "entity_registry | merge-verify rejected: %r vs %r score=%.3f",
                    raw, cand_payload.get("display_name", cand_canonical), score,
                )

    canonical = merged_to or canonical_candidate
    await _upsert_entity(
        qdrant, tenant_id, canonical, raw, aliases, description, embedding,
        entity_type=entry["entity_type"],
        knowledge_base=knowledge_base, embed_model_id=embed_model_id,
    )
    await _register_aliases(postgres, tenant_id, canonical, surface_forms)
    return {
        "raw":        raw,
        "aliases":    aliases,
        "canonical":  canonical,
        "action":     action,
        "similarity": score_out,
    }


def _dedupe_by_canonical(
    names: list[str], details: list[dict],
) -> tuple[list[str], list[dict]]:
    """Collapse the positional (name, detail) lists to unique canonicals, first
    occurrence winning, dropping empty placeholders."""
    seen: set[str] = set()
    out_names: list[str] = []
    out_details: list[dict] = []
    for name, detail in zip(names, details):
        if name and name not in seen:
            seen.add(name)
            out_names.append(name)
            out_details.append(detail)
    return out_names, out_details


async def resolve_existing_entities(
    qdrant: AsyncQdrantClient,
    postgres: PostgresClient,
    model: ModelServiceClient,
    tenant_id: str,
    names: list[str],
) -> list[str]:
    """Read-only canonical lookup for surface-form names: exact alias match, then
    a hard-band embedding match. Never registers a new entity — a name with no
    existing match is dropped. For scoping a retrieval filter to entities that
    already exist in the KB, not for ingesting new ones."""
    cleaned_names = [
        unicodedata.normalize("NFC", name).strip() for name in names if isinstance(name, str) and name.strip()
    ]
    if not cleaned_names:
        return []

    resolved: list[str] = []
    names_needing_embedding: list[str] = []
    for name in cleaned_names:
        canonical = await _alias_lookup(postgres, tenant_id, [name])
        if canonical:
            resolved.append(canonical)
        else:
            names_needing_embedding.append(name)

    if names_needing_embedding:
        try:
            embeddings = await model.embed(names_needing_embedding)
        except Exception as exc:
            log.warning("entity_registry | resolve_existing_entities embed failed: %s", exc)
            embeddings = []
        for name, embedding in zip(names_needing_embedding, embeddings):
            candidate = await _best_candidate(qdrant, tenant_id, embedding)
            if candidate and candidate[1] >= cfg.ENTITY_SIMILARITY_THRESHOLD:
                resolved.append(candidate[0])

    return list(dict.fromkeys(resolved))


# ── Public API ─────────────────────────────────────────────────────────────────

async def normalize_entities(
    qdrant: AsyncQdrantClient,
    postgres: PostgresClient,
    model: ModelServiceClient,
    tenant_id: str,
    raw_entities: list[dict],
    verify_fn=None,
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
    raw_entities: list[dict],
    verify_fn=None,
    knowledge_base: str = "",
    embed_model_id: str = "",
) -> tuple[list[str], list[dict]]:
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
    details: list[dict] = []
    # Entities whose embedding lookup is still needed (alias misses).
    needs_embedding: list[tuple[int, dict]] = []

    # Pass 1 — exact alias lookup (deterministic, no embedding).
    for idx, entry in enumerate(cleaned_entities):
        result.append("")   # placeholder, filled below
        details.append({})
        surface_forms = [entry["name"], *entry["aliases"]]
        canonical = await _alias_lookup(postgres, tenant_id, surface_forms)
        if canonical:
            result[idx] = canonical
            details[idx] = {
                "raw":        entry["name"],
                "aliases":    entry["aliases"],
                "canonical":  canonical,
                "action":     "matched_alias",
                "similarity": None,
            }
            # Learn any surface form that wasn't in the table yet.
            await _register_aliases(postgres, tenant_id, canonical, surface_forms)
        else:
            needs_embedding.append((idx, entry))

    # Pass 2 — embedding-based three-band resolution for the alias misses.
    if needs_embedding:
        try:
            embeddings = await model.embed(
                [_embed_text(e["name"], e["description"]) for _, e in needs_embedding]
            )
        except Exception as exc:
            log.warning("entity_registry | embed failed (%s) — falling back to canonical-only", exc)
            embeddings = None

        for pos, (idx, entry) in enumerate(needs_embedding):
            canonical_candidate = to_canonical(entry["name"])
            if not canonical_candidate:
                continue
            if embeddings is None:
                # No embeddings → register nothing, just use the canonical form.
                result[idx] = canonical_candidate
                details[idx] = {
                    "raw": entry["name"], "aliases": entry["aliases"],
                    "canonical": canonical_candidate, "action": "new", "similarity": None,
                }
                continue
            detail = await _resolve_embedded_entity(
                qdrant, postgres, tenant_id, entry, embeddings[pos], verify_fn,
                knowledge_base, embed_model_id,
            )
            result[idx] = detail["canonical"]
            details[idx] = detail

    return _dedupe_by_canonical(result, details)
