"""
Silver stage: load from MinIO → parse/describe → filter → store chunks to MongoDB.
Doc/web: InlineDoclingParser.  Image: VLM single-shot.  Video: VLM per sampled frame.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import uuid
from typing import Any

from sqlalchemy import update as sa_update

from utils import llm_client

from libs.basemodel.services_databaseconnector.postgres_model import (
    KBLifecycleHistoryInsert,
    PolicyFilteringType,
    ReadJoinRequest,
    SourceType,
    Tier,
    WhereFilter,
)
from libs.basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import KBDataORM
from libs.database_connector.minio_connector import MinIOClient
from libs.database_connector.mongo_connector import MongoClient
from libs.database_connector.postgres_connector import PostgresClient

from utils import config as cfg
from utils.log_writer import set_chunk_progress, write_log
from utils.pipeline_events import NULL_EMITTER, PipelineEventEmitter
from utils.prompts import SILVER_SYSTEM_PROMPTS as SYSTEM_PROMPTS

log = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
_JSON_OBJ   = re.compile(r'\{[\s\S]*\}', re.DOTALL)


# ── LLM helpers ───────────────────────────────────────────────────────────────

async def _llm_generate_json(system_type: str, user: str, max_tokens: int = 512) -> dict:
    raw = await llm_client.generate_json(
        user,
        system_prompt=SYSTEM_PROMPTS[system_type],
        system_type=system_type,
        max_tokens=max_tokens,
        temperature=0.1,
    )
    m = _JSON_FENCE.search(raw)
    cleaned = m.group(1).strip() if m else raw.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        obj_m = _JSON_OBJ.search(cleaned)
        if obj_m:
            try:
                return json.loads(obj_m.group(0))
            except json.JSONDecodeError:
                pass
        log.warning("_llm_generate_json: malformed JSON from LLM (system_type=%s) — returning {}", system_type)
        return {}


async def _vlm_describe(image_bytes: bytes, content_type: str = "image/jpeg") -> str:
    # When USE_VLM=false (default), the image/video paths are never reached.
    raise NotImplementedError(
        "VLM describe requires USE_VLM=true and a dedicated VLM endpoint. "
        "Set USE_VLM=false to skip image/video processing."
    )


# ── Parsing helpers ────────────────────────────────────────────────────────────

def _docling_chunks_to_dicts(docling_result) -> list[dict]:
    """Map a DoclingResult into the silver chunk dicts (shared by doc + web)."""
    chunks: list[dict] = []
    for parsed_chunk in docling_result.chunks:
        chunk: dict = {
            "block_index": parsed_chunk.block_index,
            "content": parsed_chunk.content,
            "table_involved": parsed_chunk.table_involved,
            "table": None,
        }
        if parsed_chunk.table is not None:
            chunk["table"] = {
                "table_name": parsed_chunk.table.table_name,
                "description": parsed_chunk.table.description,
                "data": parsed_chunk.table.data,
            }
        chunks.append(chunk)
    return chunks


async def _parse_doc(docling, bucket: str, object_key: str, extension: str) -> list[dict]:
    """docling: any object with .parse(bucket, object_key, extension) -> DoclingResult"""
    result = await docling.parse(bucket=bucket, object_key=object_key, extension=extension)
    return _docling_chunks_to_dicts(result)


# ── Web portal: fetch → strip boilerplate → markdown → Docling ────────────────

_WEB_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


async def _fetch_url_static(url: str) -> str:
    """Plain HTTP fetch — no JS. Fine for server-rendered pages; an SPA returns an
    almost-empty shell here, which is why the rendered path is tried first."""
    import httpx
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(cfg.WEB_FETCH_TIMEOUT),
        headers={"User-Agent": _WEB_USER_AGENT, "Accept-Language": "vi,en;q=0.8"},
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


async def _fetch_url_rendered(url: str) -> str:
    """Render with headless Chromium so client-side (React/Vue/SPA) content is in
    the DOM before extraction. Waits for the network to settle so a late-loading
    template finishes painting; if it never settles (analytics/polling sites) we
    proceed with whatever has rendered within the wait budget."""
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        try:
            page = await browser.new_page(user_agent=_WEB_USER_AGENT, locale="vi-VN")
            await page.goto(url, wait_until="domcontentloaded", timeout=int(cfg.WEB_FETCH_TIMEOUT * 1000))
            try:
                await page.wait_for_load_state("networkidle", timeout=int(cfg.WEB_RENDER_WAIT * 1000))
            except PWTimeout:
                log.info("Web render: networkidle not reached for %s — using current DOM", url)
            return await page.content()
        finally:
            await browser.close()


async def _fetch_url(url: str) -> str:
    """Fetch a web portal's HTML. Headless-render first (most portals are SPAs that
    need JS to paint content); fall back to a static fetch if rendering fails."""
    if cfg.WEB_USE_PLAYWRIGHT:
        try:
            html = await _fetch_url_rendered(url)
            log.info("Web fetch: rendered %s → %d bytes (headless Chromium)", url, len(html))
            return html
        except Exception as exc:
            log.warning("Web fetch: headless render failed for %s (%s) — falling back to static", url, exc)
    return await _fetch_url_static(url)


def _extract_main_content(html: str, url: str) -> tuple[str, str]:
    """Isolate ONLY the main content and return (content, extension).

    Docling on its own keeps the entire DOM — nav bars, ads, sidebars, cookie
    banners, footers. So we run a readability-style extractor (trafilatura) FIRST
    to drop that boilerplate and emit clean markdown; Docling then chunks the
    markdown exactly like a document. Falls back to raw HTML (parsed as .html) if
    trafilatura can't isolate content, so a page never fails outright.
    """
    try:
        import trafilatura
    except ImportError:
        log.warning("trafilatura missing — feeding raw HTML to Docling (boilerplate NOT stripped)")
        return html, "html"

    for fmt in ("markdown", "txt"):
        try:
            extracted = trafilatura.extract(
                html, url=url, output_format=fmt,
                include_tables=True, include_comments=False, include_images=False,
                favor_precision=True,
            )
        except Exception as exc:
            log.warning("trafilatura extract(%s) failed: %s", fmt, exc)
            extracted = None
        if extracted and extracted.strip():
            return extracted, "md"

    log.warning("trafilatura found no main content for %s — falling back to raw HTML", url)
    return html, "html"


async def _parse_web(docling, url: str) -> list[dict]:
    """bronze→silver web path: fetch the portal, strip boilerplate to markdown,
    then Docling-chunk it like a document."""
    if not url:
        raise ValueError("Web document has no URL in its metadata")
    html = await _fetch_url(url)
    loop = asyncio.get_event_loop()
    content, extension = await loop.run_in_executor(None, _extract_main_content, html, url)
    log.info("Silver web: %s → %d chars main content (%s)", url, len(content), extension)
    result = await docling.parse_bytes(content.encode("utf-8"), extension)
    return _docling_chunks_to_dicts(result)


async def _parse_image(file_bytes: bytes, extension: str) -> list[dict]:
    content_type = f"image/{extension.lstrip('.')}"
    description = await _vlm_describe(file_bytes, content_type)
    return [{"block_index": 0, "content": description, "table_involved": False, "table": None}]


async def _parse_video(file_bytes: bytes) -> list[dict]:
    try:
        import av  # type: ignore[import]
    except ImportError:
        log.error("PyAV not installed — cannot extract video frames.")
        raise

    container = av.open(io.BytesIO(file_bytes))
    video_stream = next((s for s in container.streams if s.type == "video"), None)
    if video_stream is None:
        raise ValueError("No video stream found in file")

    chunks: list[dict] = []
    for i, frame in enumerate(container.decode(video_stream)):
        if i % cfg.VLM_FRAME_INTERVAL != 0:
            continue
        buf = io.BytesIO()
        frame.to_image().save(buf, format="JPEG")
        description = await _vlm_describe(buf.getvalue(), "image/jpeg")
        chunks.append({
            "block_index": len(chunks),
            "content": f"[Frame {i}] {description}",
            "table_involved": False,
            "table": None,
        })

    container.close()
    return chunks


# ── Filtering ─────────────────────────────────────────────────────────────────

def _exact_match_blocked(content: str, banned_phrases: list[str]) -> str | None:
    """Return the first banned phrase found in content (the block reason), else None."""
    lower = content.lower()
    for phrase in banned_phrases:
        if phrase.lower() in lower:
            return phrase
    return None


async def _llm_policy_blocked(content: str, nlp_rules: list[str]) -> bool:
    if not nlp_rules:
        return False
    rules_text = "\n".join(f"- {r}" for r in nlp_rules)
    result = await _llm_generate_json(
        system_type="moderate",
        user=f"Rules:\n{rules_text}\n\nChunk:\n{content[:2000]}",
        max_tokens=128,
    )
    return bool(result.get("blocked", False))


async def _filter_chunks(
    postgres: PostgresClient,
    tenant_id: str,
    chunks: list[dict],
    emitter: PipelineEventEmitter = NULL_EMITTER,
) -> list[dict]:
    policies_response = await postgres.read(ReadJoinRequest(
        tenant_id=tenant_id,
        joins_table=["KBFilterPolicy"],
        filters=[WhereFilter(table_name="KBFilterPolicy", column_name="is_active", value=True)],
        limit=100,
    ))

    exact_phrases: list[str] = []
    if policies_response.code == 200:
        for policy in (policies_response.data or []):
            policy_config = policy.get("config") or {}
            raw_rules = policy_config.get("rules") or []
            # Rules may be plain strings OR structured dicts {"type": ..., "value": ...}
            rule_values = [
                rule["value"] if isinstance(rule, dict) and "value" in rule else str(rule)
                for rule in raw_rules
            ]
            if policy.get("configformat") == PolicyFilteringType.EXACT_MATCH.value:
                exact_phrases.extend(rule_values)
            # NLP/LLM-based policies skipped — exact-match only for reliability

    accepted_chunks: list[dict] = []
    for chunk in chunks:
        content = chunk["content"]
        block_index = chunk["block_index"]

        matched_phrase = _exact_match_blocked(content, exact_phrases) if exact_phrases else None
        if matched_phrase:
            log.info("Silver filter: exact-match blocked block_index=%d phrase=%r",
                     block_index, matched_phrase)
            await emitter.emit("silver.filter.blocked", {
                "index": block_index,
                "method": "exact_match",
                "reason": f"Matched banned phrase: '{matched_phrase}'",
                "phrase": matched_phrase,
                "preview": content[:200],
                "length": len(content),
                "table_involved": chunk.get("table_involved", False),
            })
            continue

        await emitter.emit("silver.filter.accepted", {"index": block_index})
        accepted_chunks.append(chunk)

    return accepted_chunks


# ── Per-chunk summary + intents ───────────────────────────────────────────────

def _strip_surrogates(text: str) -> str:
    """Drop lone UTF-16 surrogates (\\udcxx) the LLM emits when a multibyte (e.g.
    Chinese/Vietnamese) character is truncated mid-generation — strip them before
    persisting so corrupted text never reaches Postgres/Qdrant."""
    if not text:
        return text
    return "".join(char for char in text if not 0xD800 <= ord(char) <= 0xDFFF)


def _clean_chunk_entities(raw_entities: Any) -> list[dict]:
    """Parse + sanitize per-chunk entities from the LLM. Names/descriptions are
    surrogate-stripped; names stay in the chunk's own language. Gold applies the
    generic-word blacklist, dedup, and canonicalization."""
    cleaned_entities: list[dict] = []
    for entity in raw_entities or []:
        if not isinstance(entity, dict):
            continue
        name = _strip_surrogates(str(entity.get("name") or "").strip())
        if len(name) < 2:
            continue
        cleaned_entities.append({
            "name":        name,
            "type":        (str(entity.get("type") or "concept").strip() or "concept"),
            "description": _strip_surrogates(str(entity.get("description") or "").strip()),
            "aliases":     [],
            "score":       entity.get("score", 0.5),
        })
    return cleaned_entities


def _clean_chunk_relationships(raw_relationships: Any, entity_names: list[str]) -> list[dict]:
    """Keep only relationships whose from/to are among THIS chunk's entities (drops
    hallucinated endpoints). Names stay in the chunk's language — gold remaps them
    to canonical entities."""
    allowed_names = {_strip_surrogates(name).strip().lower() for name in entity_names}
    cleaned_relationships: list[dict] = []
    for relationship in raw_relationships or []:
        if not isinstance(relationship, dict):
            continue
        source = _strip_surrogates(str(relationship.get("from") or "").strip())
        target = _strip_surrogates(str(relationship.get("to") or "").strip())
        rel_type = str(relationship.get("type") or "").strip()
        if not (source and target and rel_type) or source == target:
            continue
        if source.lower() not in allowed_names or target.lower() not in allowed_names:
            continue
        cleaned_relationships.append(
            {"from": source, "to": target, "type": rel_type, "score": relationship.get("score", 0.5)}
        )
    return cleaned_relationships


def _fallback_chunk_summary(content: str) -> dict:
    """Heuristic summary when the LLM can't return valid JSON: first sentence,
    truncated. Splits on Latin sentence punctuation, which Vietnamese also uses."""
    first_sentence = re.split(r"[.!?…]", content, maxsplit=1)[0].strip()
    summary = " ".join((first_sentence or content).split()[:40])
    return {"summary": summary, "intents": ["document_content"], "entities": [],
            "relationships": [], "skip": False}


async def summarize_chunk(content: str) -> dict:
    """One LLM call → {summary, entities, intents, skip}. Entities are extracted
    PER CHUNK here (in the chunk's own language). Never raises — falls back to the
    heuristic summary so a flaky LLM can't fail the whole silver stage."""
    for attempt in range(2):
        try:
            summary_result = await _llm_generate_json(
                system_type="chunk-summary",
                user=content[:3000],
                max_tokens=512,
            )
            summary = _strip_surrogates(str(summary_result.get("summary") or "").strip())
            intents = [
                intent for intent in summary_result.get("intents") or []
                if isinstance(intent, str) and intent.strip()
            ]
            if summary:
                entities = _clean_chunk_entities(summary_result.get("entities"))
                return {
                    "summary":  summary,
                    "intents":  intents or ["document_content"],
                    "entities": entities,
                    "relationships": _clean_chunk_relationships(
                        summary_result.get("relationships"), [entity["name"] for entity in entities]),
                    "skip":     bool(summary_result.get("skip", False)),
                }
        except Exception as exc:
            log.warning("Silver summary: attempt %d failed: %s", attempt + 1, exc)
    return _fallback_chunk_summary(content)


async def _summarize_chunks(
    chunks: list[dict],
    emitter: PipelineEventEmitter = NULL_EMITTER,
    database=None,
    data_id: str | None = None,
) -> list[dict]:
    """Annotate every chunk with summary + intents (bounded-concurrency LLM calls)
    and drop chunks the LLM flags as boilerplate. Table chunks are never dropped —
    the table itself is valuable structured data even if the prose around it isn't."""
    if database is not None and data_id is not None:
        await set_chunk_progress(database, data_id, total=len(chunks), processed=0)

    summary_semaphore = asyncio.Semaphore(cfg.SILVER_SUMMARY_CONCURRENCY)
    completed_count = 0

    async def _summarize_one(chunk: dict) -> dict:
        nonlocal completed_count
        async with summary_semaphore:
            result = await summarize_chunk(chunk["content"])
        completed_count += 1
        if database is not None and data_id is not None:
            await set_chunk_progress(database, data_id, processed=completed_count)
        return result

    summary_results = await asyncio.gather(*(_summarize_one(chunk) for chunk in chunks))

    kept_chunks: list[dict] = []
    for chunk, summary_result in zip(chunks, summary_results):
        if summary_result["skip"] and not chunk.get("table_involved"):
            log.info("Silver summary: block_index=%d flagged boilerplate — dropping",
                     chunk["block_index"])
            await emitter.emit("silver.summary.skipped", {
                "index":  chunk["block_index"],
                "reason": "boilerplate_no_descriptive_content",
            })
            continue
        chunk["summary"]  = summary_result["summary"]
        chunk["intents"]  = summary_result["intents"]
        chunk["entities"] = summary_result.get("entities", [])
        chunk["relationships"] = summary_result.get("relationships", [])
        await emitter.emit("silver.summary.item", {
            "index":    chunk["block_index"],
            "summary":  summary_result["summary"],
            "intents":  summary_result["intents"],
            "entities": [entity["name"] for entity in summary_result.get("entities", [])],
        })
        kept_chunks.append(chunk)
    return kept_chunks


# ── Main stage function ────────────────────────────────────────────────────────

async def _load_kb_data(postgres: PostgresClient, tenant_id: str, data_id: str) -> dict:
    data_response = await postgres.read(ReadJoinRequest(
        tenant_id=tenant_id,
        joins_table=["KBData"],
        filters=[WhereFilter(table_name="KBData", column_name="data_id", value=uuid.UUID(data_id))],
        limit=1,
    ))
    if data_response.code != 200 or not data_response.data:
        raise ValueError(f"KBData not found: data_id={data_id}")
    return data_response.data[0]


async def _parse_source(
    minio: MinIOClient, docling, kb_data: dict, data_id: str,
    source_type: SourceType, path: str, extension: str,
    emitter: PipelineEventEmitter,
) -> list[dict]:
    """Parse the bronze source into raw chunks. The emit envelope is handled by the
    caller's `silver.parse` step; this only adds the parser name to that scope."""
    if source_type == SourceType.DOC:
        return await _parse_doc(docling, cfg.MINIO_BUCKET, path, extension)
    if source_type == SourceType.WEB:
        # read() keys the row by the DB column name ("metadata"); the JSONB value
        # may arrive as a dict or a JSON string depending on the driver codec.
        document_metadata = kb_data.get("metadata") or kb_data.get("doc_metadata") or {}
        if isinstance(document_metadata, str):
            try:
                document_metadata = json.loads(document_metadata)
            except Exception:
                document_metadata = {}
        web_url = (document_metadata or {}).get("url", "")
        return await _parse_web(docling, web_url)
    if source_type in (SourceType.IMAGE, SourceType.VIDEO):
        file_bytes = await minio.download(cfg.MINIO_BUCKET, path)
        log.info("Silver: downloaded %d bytes data_id=%s", len(file_bytes), data_id)
        if source_type == SourceType.IMAGE:
            return await _parse_image(file_bytes, extension)
        return await _parse_video(file_bytes)
    raise ValueError(f"Unsupported source_type for silver promotion: {source_type}")


async def _persist_silver(
    postgres: PostgresClient, mongo: MongoClient, data_id: str, tenant_id: str,
    source_type: SourceType, accepted_chunks: list[dict], raw_chunk_count: int,
    approved_by: str,
) -> None:
    """Write the silver staging doc + flip the tier, each guarded so a failure rolls
    back and is recorded in the document history before propagating."""
    database = mongo.get_client()
    rejected_count = raw_chunk_count - len(accepted_chunks)
    tables_found = sum(1 for chunk in accepted_chunks if chunk.get("table_involved"))

    try:
        await database["kb_silver_staging"].replace_one(
            {"data_id": data_id},
            {"data_id": data_id, "tenant_id": tenant_id,
             "source_type": source_type.value, "chunks": accepted_chunks},
            upsert=True,
        )
    except Exception as exc:
        await write_log(database, data_id, "FAILED", f"Silver staging write failed: {exc}", level="ERROR")
        raise

    try:
        async with postgres.get_client() as session:
            try:
                await session.execute(
                    sa_update(KBDataORM)
                    .where(KBDataORM.data_id == uuid.UUID(data_id))
                    .values(current_tier=Tier.SILVER.value)
                )
                await session.commit()
            except Exception:
                await session.rollback()
                raise
    except Exception as exc:
        await write_log(database, data_id, "FAILED", f"Silver tier update failed: {exc}", level="ERROR")
        raise

    # Lifecycle history is an audit row — best-effort, never fails the stage.
    try:
        await postgres.insert(KBLifecycleHistoryInsert(
            data_id=data_id,
            to_tier=Tier.SILVER,
            from_tier=Tier.BRONZE,
            approved_by=approved_by,
            notes=f"Promoted to silver: {len(accepted_chunks)} chunks accepted, {rejected_count} rejected",
            pipeline_metadata={
                "stage":           "silver",
                "source_type":     source_type.value,
                "chunks_accepted": len(accepted_chunks),
                "chunks_rejected": rejected_count,
                "chunks_total":    raw_chunk_count,
                "tables_found":    tables_found,
            },
        ))
    except Exception as exc:
        log.warning("Silver: lifecycle history insert failed data_id=%s: %s", data_id, exc)
        await write_log(database, data_id, "RUNNING",
                        f"Silver lifecycle history not recorded: {exc}", level="WARNING")


async def promote_to_silver(
    minio: MinIOClient,
    postgres: PostgresClient,
    docling,
    mongo: MongoClient,
    data_id: str,
    tenant_id: str,
    approved_by: str,
    emitter: PipelineEventEmitter = NULL_EMITTER,
) -> dict:
    """
    Load bronze file → parse → filter → summarize → store chunks to MongoDB silver
    staging, then flip KBData tier to SILVER. Gold promotion is NOT triggered here —
    it waits for an explicit user request (POST /promote/{data_id}/gold).
    Returns: {data_id, chunks_count, layer}
    """
    kb_data = await _load_kb_data(postgres, tenant_id, data_id)
    source_type = SourceType(kb_data["source_type"])
    path: str = kb_data["path"]
    extension: str = kb_data["extension"]
    parser_name = "docling" if source_type in (SourceType.DOC, SourceType.WEB) else "vlm"

    async with emitter.step("silver", source_type=source_type.value, parser=parser_name) as silver_scope:
        async with emitter.step("silver.parse", parser=parser_name) as parse_scope:
            raw_chunks = await _parse_source(
                minio, docling, kb_data, data_id, source_type, path, extension, emitter,
            )
            parse_scope.set(chunks=len(raw_chunks))
        log.info("Silver: parsed %d chunks data_id=%s", len(raw_chunks), data_id)

        for chunk in raw_chunks:
            content = chunk["content"]
            await emitter.emit("silver.chunk.found", {
                "index":          chunk["block_index"],
                "preview":        content[:200],
                "length":         len(content),
                "table_involved": chunk.get("table_involved", False),
                "table_name":     (chunk.get("table") or {}).get("table_name"),
            })

        async with emitter.step("silver.filter") as filter_scope:
            accepted_chunks = await _filter_chunks(postgres, tenant_id, raw_chunks, emitter)
            filter_scope.set(total=len(raw_chunks), accepted=len(accepted_chunks))
        log.info("Silver: %d/%d chunks passed filters data_id=%s",
                 len(accepted_chunks), len(raw_chunks), data_id)

        async with emitter.step("silver.summary", total=len(accepted_chunks)) as summary_scope:
            accepted_chunks = await _summarize_chunks(
                accepted_chunks, emitter, database=mongo.get_client(), data_id=data_id,
            )
            summary_scope.set(kept=len(accepted_chunks))
        log.info("Silver: %d chunks after summary/boilerplate pass data_id=%s",
                 len(accepted_chunks), data_id)

        for new_index, chunk in enumerate(accepted_chunks):
            chunk["block_index"] = new_index

        await _persist_silver(
            postgres, mongo, data_id, tenant_id, source_type,
            accepted_chunks, len(raw_chunks), approved_by,
        )
        silver_scope.set(
            accepted=len(accepted_chunks),
            rejected=len(raw_chunks) - len(accepted_chunks),
            total=len(raw_chunks),
        )

    log.info("Silver complete data_id=%s chunks=%d — waiting for gold promotion request",
             data_id, len(accepted_chunks))
    return {"data_id": data_id, "chunks_count": len(accepted_chunks), "layer": Tier.SILVER.value}
