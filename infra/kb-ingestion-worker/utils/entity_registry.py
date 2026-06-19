"""
Entity Registry — semantic deduplication and normalisation for extracted entities.

Vietnamese-aware design
=======================
Vietnamese tone marks and diacritics are LEXICAL — "bán" (sell), "bàn" (table) and
"bạn" (friend) are different words. Canonicalization therefore NEVER strips
diacritics: it Unicode-normalises (NFC) and casefolds, keeping every Vietnamese
letter intact ("Đường sắt Việt Nam" → "đường_sắt_việt_nam").

Vietnamese is also extremely synonym-rich: the same entity routinely appears as an
official name, an abbreviation and a historical/colloquial name with ZERO shared
tokens ("Thành phố Hồ Chí Minh" / "TP.HCM" / "Sài Gòn"; "Hoa Kỳ" / "Mỹ"). Pure
string normalisation can never join these, and embedding similarity on the bare
surface form is unreliable for short names. Resolution therefore runs in order:

  1. EXACT alias lookup in Postgres "KBEntityLookup" (tenant-scoped, NULL = global
     seed aliases). Aliases come from the doc-level extraction LLM, which is asked
     to emit the other surface forms of each entity — so the synonym table grows
     with the corpus and a known synonym resolves deterministically, no embedding
     guesswork.
  2. Embedding match in the entity_registry Qdrant collection. The embedded text is
     "name: description" — the description carries the semantics that bridges
     synonyms whose surface forms share nothing.
       cosine ≥ ENTITY_SIMILARITY_THRESHOLD                     → definite match
       cosine ≥ ENTITY_SOFT_THRESHOLD + word-overlap Jaccard    → same entity
  3. No match → new entity; register canonical + aliases.

Every successful match LEARNS: surface forms and LLM aliases not yet in
KBEntityLookup are inserted, so the next document resolves them at step 1.

Each unique entity (per tenant) is stored in the entity_registry Qdrant collection.
Point ID is a deterministic UUID v5:  uuid5(NS, "{tenant_id}:{canonical}")
so an upsert is idempotent — re-processing the same entity never creates a duplicate.
The payload keeps `display_name`: the original surface form with case and
diacritics exactly as written, used for Neo4j node display.
"""
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
    PRESERVES Unicode letters. NFC first so the same Vietnamese text always uses
    composed code points regardless of how the source document encoded it
    (decomposed "ậ" = a + circumflex + dot-below must equal composed "ậ").
    casefold() lowercases Vietnamese letters correctly ("Đ" → "đ")."""
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
    keys = [k for k in (to_canonical(s) for s in surface_forms) if k]
    if not keys:
        return None
    try:
        tid = uuid.UUID(tenant_id)
    except (ValueError, AttributeError):
        tid = None
    try:
        async with postgres.get_client() as session:
            rows = (await session.execute(
                sa_select(
                    KBEntityLookupORM.alias_name,
                    KBEntityLookupORM.canonical_name,
                    KBEntityLookupORM.tenant_id,
                )
                .where(KBEntityLookupORM.alias_name.in_(keys))
                .where(KBEntityLookupORM.is_deleted.is_(False))
            )).all()
    except Exception as exc:
        log.warning("entity_registry | alias lookup failed: %s", exc)
        return None

    tenant_hit: str | None = None
    global_hit: str | None = None
    for alias, canonical, row_tid in rows:
        if row_tid == tid and tenant_hit is None:
            tenant_hit = canonical
        elif row_tid is None and global_hit is None:
            global_hit = canonical
    return tenant_hit or global_hit


async def _register_aliases(
    postgres: PostgresClient,
    tenant_id: str,
    canonical: str,
    surface_forms: list[str],
) -> None:
    """Insert alias → canonical rows for any surface forms not yet known, so the
    next document resolves them exactly without an embedding call. Best-effort:
    a failure here never fails the gold commit."""
    keys: dict[str, str] = {}
    for s in surface_forms:
        k = to_canonical(s)
        if k and k != canonical:
            keys[k] = s
    # The canonical form itself also gets a self-row so a future exact mention
    # of the canonical resolves at step 1.
    keys.setdefault(canonical, canonical)
    if not keys:
        return
    try:
        tid = uuid.UUID(tenant_id)
    except (ValueError, AttributeError):
        tid = None
    try:
        async with postgres.get_client() as session:
            # Existing alias rows for these keys — ANY tenant scope. A key that
            # already maps to a DIFFERENT canonical is a collision: never
            # overwrite or shadow it (first registration wins; LLM association
            # pollution must not rebind an existing entity's name).
            taken: dict[str, str] = {}
            for alias, can in (await session.execute(
                sa_select(KBEntityLookupORM.alias_name, KBEntityLookupORM.canonical_name)
                .where(KBEntityLookupORM.alias_name.in_(list(keys)))
                .where(KBEntityLookupORM.is_deleted.is_(False))
            )).all():
                taken.setdefault(alias, can)
            # Keys that ARE some other entity's canonical name are equally off-limits
            # ("creative_commons" can never become an alias of the GFDL license).
            other_canonicals = set((await session.execute(
                sa_select(KBEntityLookupORM.canonical_name)
                .where(KBEntityLookupORM.canonical_name.in_(
                    [k for k in keys if k != canonical]))
                .where(KBEntityLookupORM.is_deleted.is_(False))
            )).scalars())

            new_rows = []
            for k in keys:
                if k in taken:
                    if taken[k] != canonical:
                        log.warning(
                            "entity_registry | alias collision: %r already → %r, "
                            "NOT rebinding to %r", k, taken[k], canonical,
                        )
                    continue
                if k in other_canonicals:
                    log.warning(
                        "entity_registry | %r is another entity's canonical — "
                        "refusing to alias it to %r", k, canonical,
                    )
                    continue
                new_rows.append(
                    KBEntityLookupORM(alias_name=k, canonical_name=canonical, tenant_id=tid)
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
    """Two SPECIFIC (non-concept) types that differ block a merge — LLM typing is
    noisy, but a person and a location are never the same entity."""
    a, b = (type_a or "").strip().lower(), (type_b or "").strip().lower()
    return bool(a and b and a != "concept" and b != "concept" and a != b)


async def _best_candidate(
    qdrant: AsyncQdrantClient,
    tenant_id: str,
    embedding: list[float],
) -> tuple[str, float, dict] | None:
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
                    # Original surface form — case and Vietnamese diacritics exactly
                    # as written. Used as the Neo4j node display name.
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
    clean = [
        {
            "name":        unicodedata.normalize("NFC", e["name"]).strip(),
            "description": (e.get("description") or "").strip(),
            "entity_type": (e.get("type") or "").strip().lower(),
            "aliases":     [
                unicodedata.normalize("NFC", a).strip()
                for a in (e.get("aliases") or [])
                if isinstance(a, str) and a.strip()
            ],
        }
        for e in raw_entities
        if isinstance(e, dict) and e.get("name", "").strip()
    ]
    if not clean:
        return [], []

    result: list[str] = []
    details: list[dict] = []
    # Entities whose embedding lookup is still needed (alias misses).
    needs_embedding: list[tuple[int, dict]] = []

    for idx, entry in enumerate(clean):
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

    if needs_embedding:
        try:
            embeddings = await model.embed(
                [_embed_text(e["name"], e["description"]) for _, e in needs_embedding]
            )
        except Exception as exc:
            log.warning("entity_registry | embed failed (%s) — falling back to canonical-only", exc)
            embeddings = None

        for pos, (idx, entry) in enumerate(needs_embedding):
            raw                 = entry["name"]
            aliases             = entry["aliases"]
            description         = entry["description"]
            canonical_candidate = to_canonical(raw)
            if not canonical_candidate:
                continue
            surface_forms = [raw, *aliases]

            if embeddings is None:
                result[idx] = canonical_candidate
                details[idx] = {
                    "raw": raw, "aliases": aliases, "canonical": canonical_candidate,
                    "action": "new", "similarity": None,
                }
                continue

            embedding = embeddings[pos]

            # Three-band merge decision (e5 cosines overlap between same-entity
            # and different-entity pairs, so the bands are: auto / verify / new):
            #   1. type guard — specific-type mismatch always blocks
            #   2. score >= ENTITY_SIMILARITY_THRESHOLD → merge
            #   3. score >= ENTITY_VERIFY_BAND → merge only if verify_fn confirms
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

            if merged_to:
                result[idx] = merged_to
                details[idx] = {
                    "raw":        raw,
                    "aliases":    aliases,
                    "canonical":  merged_to,
                    "action":     action,
                    "similarity": score_out,
                }
                # Learning is allowed here precisely because the merge was either
                # near-exact or LLM-confirmed — unverified embedding proximity
                # never reaches this branch.
                await _register_aliases(postgres, tenant_id, merged_to, surface_forms)
                await _upsert_entity(
                    qdrant, tenant_id, merged_to, raw, aliases, description, embedding,
                    entity_type=entry["entity_type"],
                    knowledge_base=knowledge_base, embed_model_id=embed_model_id,
                )
            else:
                await _upsert_entity(
                    qdrant, tenant_id, canonical_candidate, raw, aliases, description, embedding,
                    entity_type=entry["entity_type"],
                    knowledge_base=knowledge_base, embed_model_id=embed_model_id,
                )
                await _register_aliases(postgres, tenant_id, canonical_candidate, surface_forms)
                result[idx] = canonical_candidate
                details[idx] = {
                    "raw":        raw,
                    "aliases":    aliases,
                    "canonical":  canonical_candidate,
                    "action":     "new",
                    "similarity": score_out,
                }

    seen: set[str] = set()
    deduped_names: list[str] = []
    deduped_details: list[dict] = []
    for name, detail in zip(result, details):
        if name and name not in seen:
            seen.add(name)
            deduped_names.append(name)
            deduped_details.append(detail)
    return deduped_names, deduped_details
