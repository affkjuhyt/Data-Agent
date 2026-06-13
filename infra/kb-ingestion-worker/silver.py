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

import llm_client
from sqlalchemy import update as sa_update

from basemodel.services_databaseconnector.postgres_model import (
    KBLifecycleHistoryInsert,
    PolicyFilteringType,
    ReadJoinRequest,
    SourceType,
    Tier,
    WhereFilter,
)
from basemodel.services_databaseconnector.postgres_orm.knowledge_base_orm import KBDataORM
from services.database_connector.kafka_connector import KafkaProducerClient
from services.database_connector.minio_connector import MinIOClient
from services.database_connector.mongo_connector import MongoClient
from services.database_connector.postgres_connector import PostgresClient

import config as cfg  # local worker config
from pipeline_events import PipelineEventEmitter

log = logging.getLogger(__name__)

_JSON_FENCE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)
_JSON_OBJ   = re.compile(r'\{[\s\S]*\}', re.DOTALL)

_MODERATE_SYSTEM = (
    "You are a content moderation assistant. "
    "Given a list of policy rules and a text chunk, decide if the chunk violates any rule. "
    'Reply ONLY with valid JSON: {"blocked": true/false, "reason": "..."}.'
)

# Per-chunk summary + intent. Runs once per chunk in silver so gold never makes
# per-chunk LLM calls — gold only reduces these summaries into a document summary
# and extracts entities/relationships ONCE per document.
_CHUNK_SUMMARY_SYSTEM = """\
You summarize text chunks for a knowledge base. Documents may be in Vietnamese, \
English, or mixed. Return JSON with exactly three keys: summary, intents, skip.

── summary ─────────────────────────────────────────────────────────────────────
1–2 sentences (≤ 40 words) capturing the most important facts of the chunk.
LANGUAGE RULE: write the summary in the SAME language as the chunk — a Vietnamese
chunk gets a Vietnamese summary. NEVER translate.
NAME RULE: copy proper names (people, organisations, places, systems, laws,
projects) CHARACTER-FOR-CHARACTER, keeping ALL Vietnamese diacritics and tone
marks exactly as written ("Đà Nẵng" stays "Đà Nẵng", never "Da Nang"). Tone marks
change meaning in Vietnamese — altering them corrupts the entity.
Prefer keeping concrete names, numbers, and dates over generic phrasing: these
summaries are later merged into a document summary used for entity extraction.

── intents (2–4, snake_case English labels) ────────────────────────────────────
Machine-readable purpose labels (ALWAYS English snake_case, even for Vietnamese
text — they are filter keys, not display text). Choose from:
  information_retrieval, regulation_compliance, technical_specification,
  financial_reporting, process_description, organisational_info,
  question_answering, data_definition, historical_record, announcement
Add a custom snake_case label only if none fits.

── skip (boolean) ──────────────────────────────────────────────────────────────
true ONLY for chunks with NO indexable content: pure reference lists, isolated
headers or page numbers, acknowledgements, legal boilerplate. Otherwise false.

Output JSON only — no markdown, no extra text:
{"summary":"…","intents":["…"],"skip":false}"""

# Vietnamese variant (PROMPT_LANG=vi, default) — native instructions for a
# Vietnamese corpus; intents stay English snake_case (machine-readable keys).
_CHUNK_SUMMARY_SYSTEM_VI = """\
Bạn tóm tắt các đoạn văn bản cho một cơ sở tri thức. Văn bản có thể là tiếng Việt,
tiếng Anh hoặc lẫn cả hai. Chỉ trả về JSON với đúng ba khóa: summary, intents, skip.

── summary ─────────────────────────────────────────────────────────────────────
1–2 câu (tối đa 40 từ) nêu các thông tin quan trọng nhất của đoạn.
NGÔN NGỮ: viết bằng ĐÚNG ngôn ngữ của đoạn văn — đoạn tiếng Việt thì tóm tắt
tiếng Việt. KHÔNG dịch.
TÊN RIÊNG: chép chính xác từng ký tự (người, tổ chức, địa danh, hệ thống, luật,
dự án), giữ nguyên toàn bộ dấu ("Đà Nẵng" giữ là "Đà Nẵng", không viết "Da Nang").
Dấu thanh làm thay đổi nghĩa — sai dấu là sai thực thể.
Ưu tiên giữ tên riêng, con số, mốc thời gian thay vì câu chữ chung chung: các tóm
tắt này sẽ được gộp thành tóm tắt tài liệu để trích xuất thực thể.

── intents (2–4 nhãn, tiếng Anh snake_case) ────────────────────────────────────
Nhãn mục đích cho máy đọc (LUÔN là tiếng Anh snake_case, kể cả với văn bản tiếng
Việt — đây là khóa lọc, không phải văn hiển thị). Chọn trong:
  information_retrieval, regulation_compliance, technical_specification,
  financial_reporting, process_description, organisational_info,
  question_answering, data_definition, historical_record, announcement
Chỉ thêm nhãn tự đặt (snake_case) khi không nhãn nào phù hợp.

── skip (boolean) ──────────────────────────────────────────────────────────────
true CHỈ KHI đoạn không có nội dung đáng lập chỉ mục: danh mục tài liệu tham khảo,
tiêu đề/số trang đứng một mình, lời cảm ơn, văn bản pháp lý rập khuôn. Còn lại: false.

Chỉ trả JSON — không markdown, không văn bản thừa:
{"summary":"…","intents":["…"],"skip":false}"""

_SUMMARY_PROMPTS_BY_LANG = {"en": _CHUNK_SUMMARY_SYSTEM, "vi": _CHUNK_SUMMARY_SYSTEM_VI}

SYSTEM_PROMPTS: dict[str, str] = {
    "moderate":      _MODERATE_SYSTEM,
    "chunk-summary": _SUMMARY_PROMPTS_BY_LANG.get(cfg.PROMPT_LANG, _CHUNK_SUMMARY_SYSTEM_VI),
}


# ── LLM helpers ───────────────────────────────────────────────────────────────

async def _llm_generate_json(system_type: str, user: str, max_tokens: int = 512) -> dict:
    # /api/generate with the system prompt baked into the KV snapshot (system_type);
    # only `user` is encoded. Falls back to inlining the system prompt if not registered.
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
        # Try extracting just the JSON object if the LLM added trailing text
        obj_m = _JSON_OBJ.search(cleaned)
        if obj_m:
            try:
                return json.loads(obj_m.group(0))
            except json.JSONDecodeError:
                pass
        log.warning("_llm_generate_json: malformed JSON from LLM (system_type=%s) — returning {}", system_type)
        return {}


async def _vlm_describe(image_bytes: bytes, content_type: str = "image/jpeg") -> str:
    # VLM describe requires a multimodal endpoint (USE_VLM=true).
    # When USE_VLM=false (default), this path is never reached.
    raise NotImplementedError(
        "VLM describe requires USE_VLM=true and a dedicated VLM endpoint. "
        "Set USE_VLM=false to skip image/video processing."
    )


# ── Parsing helpers ────────────────────────────────────────────────────────────

async def _parse_doc(docling, bucket: str, object_key: str, extension: str) -> list[dict]:
    """docling: any object with .parse(bucket, object_key, extension) -> DoclingResult"""
    result = await docling.parse(bucket=bucket, object_key=object_key, extension=extension)
    chunks = []
    for pc in result.chunks:
        chunk: dict = {
            "block_index": pc.block_index,
            "content": pc.content,
            "table_involved": pc.table_involved,
            "table": None,
        }
        if pc.table is not None:
            chunk["table"] = {
                "table_name": pc.table.table_name,
                "description": pc.table.description,
                "data": pc.table.data,
            }
        chunks.append(chunk)
    return chunks


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
    emitter: PipelineEventEmitter | None = None,
) -> list[dict]:
    policies_res = await postgres.read(ReadJoinRequest(
        tenant_id=tenant_id,
        joins_table=["KBFilterPolicy"],
        filters=[WhereFilter(table_name="KBFilterPolicy", column_name="is_active", value=True)],
        limit=100,
    ))

    exact_phrases: list[str] = []
    if policies_res.code == 200:
        for p in (policies_res.data or []):
            policy_config = p.get("config") or {}
            raw_rules = policy_config.get("rules") or []
            # Rules may be plain strings OR structured dicts {"type": ..., "value": ...}
            rule_values = [
                r["value"] if isinstance(r, dict) and "value" in r else str(r)
                for r in raw_rules
            ]
            if p.get("configformat") == PolicyFilteringType.EXACT_MATCH.value:
                exact_phrases.extend(rule_values)
            # NLP/LLM-based policies skipped — exact-match only for reliability

    if emitter:
        await emitter.emit("silver.filter.start", {
            "total": len(chunks),
            "exact_rules": len(exact_phrases),
            "nlp_rules": 0,
        })

    accepted: list[dict] = []
    for chunk in chunks:
        content = chunk["content"]
        block_index = chunk["block_index"]

        matched_phrase = _exact_match_blocked(content, exact_phrases) if exact_phrases else None
        if matched_phrase:
            log.info("Silver filter: exact-match blocked block_index=%d phrase=%r",
                     block_index, matched_phrase)
            if emitter:
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

        if emitter:
            await emitter.emit("silver.filter.accepted", {"index": block_index})
        accepted.append(chunk)

    return accepted


# ── Per-chunk summary + intents ───────────────────────────────────────────────

def _fallback_chunk_summary(content: str) -> dict:
    """Heuristic summary when the LLM can't return valid JSON: first sentence,
    truncated. Splits on Latin sentence punctuation, which Vietnamese also uses."""
    first_sentence = re.split(r"[.!?…]", content, maxsplit=1)[0].strip()
    summary = " ".join((first_sentence or content).split()[:40])
    return {"summary": summary, "intents": ["document_content"], "skip": False}


async def summarize_chunk(content: str) -> dict:
    """One LLM call → {summary, intents, skip}. Never raises — falls back to the
    heuristic summary so a flaky LLM can't fail the whole silver stage."""
    for attempt in range(2):
        try:
            result = await _llm_generate_json(
                system_type="chunk-summary",
                user=content[:3000],
                max_tokens=256,
            )
            summary = str(result.get("summary") or "").strip()
            intents = [s for s in result.get("intents") or [] if isinstance(s, str) and s.strip()]
            if summary:
                return {
                    "summary": summary,
                    "intents": intents or ["document_content"],
                    "skip":    bool(result.get("skip", False)),
                }
        except Exception as exc:
            log.warning("Silver summary: attempt %d failed: %s", attempt + 1, exc)
    return _fallback_chunk_summary(content)


async def _summarize_chunks(
    chunks: list[dict],
    emitter: PipelineEventEmitter | None = None,
) -> list[dict]:
    """Annotate every chunk with summary + intents (bounded-concurrency LLM calls)
    and drop chunks the LLM flags as boilerplate. Table chunks are never dropped —
    the table itself is valuable structured data even if the prose around it isn't."""
    if emitter:
        await emitter.emit("silver.summary.start", {"total": len(chunks)})

    sem = asyncio.Semaphore(cfg.SILVER_SUMMARY_CONCURRENCY)

    async def _one(chunk: dict) -> dict:
        async with sem:
            return await summarize_chunk(chunk["content"])

    results = await asyncio.gather(*(_one(c) for c in chunks))

    kept: list[dict] = []
    for chunk, res in zip(chunks, results):
        if res["skip"] and not chunk.get("table_involved"):
            log.info("Silver summary: block_index=%d flagged boilerplate — dropping",
                     chunk["block_index"])
            if emitter:
                await emitter.emit("silver.summary.skipped", {
                    "index":  chunk["block_index"],
                    "reason": "boilerplate_no_descriptive_content",
                })
            continue
        chunk["summary"] = res["summary"]
        chunk["intents"] = res["intents"]
        if emitter:
            await emitter.emit("silver.summary.complete", {
                "index":   chunk["block_index"],
                "summary": res["summary"],
                "intents": res["intents"],
            })
        kept.append(chunk)
    return kept


# ── Main stage function ────────────────────────────────────────────────────────

async def promote_to_silver(
    minio: MinIOClient,
    postgres: PostgresClient,
    docling,
    mongo: MongoClient,
    kafka_producer: KafkaProducerClient,
    data_id: str,
    tenant_id: str,
    approved_by: str,
    emitter: PipelineEventEmitter | None = None,
) -> dict:
    """
    Load bronze file → parse → filter → store chunks to MongoDB silver staging.
    Updates KBData tier to SILVER and publishes a gold promotion event to Kafka.
    Returns: {data_id, chunks_count, layer}
    """
    data_res = await postgres.read(ReadJoinRequest(
        tenant_id=tenant_id,
        joins_table=["KBData"],
        filters=[WhereFilter(table_name="KBData", column_name="data_id",
                             value=uuid.UUID(data_id))],
        limit=1,
    ))
    if data_res.code != 200 or not data_res.data:
        raise ValueError(f"KBData not found: data_id={data_id}")

    kb_data = data_res.data[0]
    source_type = SourceType(kb_data["source_type"])
    path: str = kb_data["path"]
    extension: str = kb_data["extension"]

    if emitter:
        await emitter.emit("silver.start", {
            "source_type": source_type.value,
            "parser": "docling" if source_type in (SourceType.DOC, SourceType.WEB) else "vlm",
        })

    if source_type in (SourceType.DOC, SourceType.WEB):
        if emitter:
            await emitter.emit("silver.parse.start", {"parser": "InlineDoclingParser"})
        raw_chunks = await _parse_doc(docling, cfg.MINIO_BUCKET, path, extension)
    elif source_type in (SourceType.IMAGE, SourceType.VIDEO):
        file_bytes = await minio.download(cfg.MINIO_BUCKET, path)
        log.info("Silver: downloaded %d bytes data_id=%s", len(file_bytes), data_id)
        if source_type == SourceType.IMAGE:
            if emitter:
                await emitter.emit("silver.parse.start", {"parser": "VLM (image)"})
            raw_chunks = await _parse_image(file_bytes, extension)
        else:
            if emitter:
                await emitter.emit("silver.parse.start", {"parser": "VLM (video frames)"})
            raw_chunks = await _parse_video(file_bytes)
    else:
        raise ValueError(f"Unsupported source_type for silver promotion: {source_type}")

    log.info("Silver: parsed %d chunks data_id=%s", len(raw_chunks), data_id)

    # Emit each parsed chunk
    if emitter:
        for chunk in raw_chunks:
            content = chunk["content"]
            await emitter.emit("silver.chunk.found", {
                "index":         chunk["block_index"],
                "preview":       content[:200],
                "length":        len(content),
                "table_involved": chunk.get("table_involved", False),
                "table_name":    (chunk.get("table") or {}).get("table_name"),
            })

    accepted_chunks = await _filter_chunks(postgres, tenant_id, raw_chunks, emitter)
    log.info("Silver: %d/%d chunks passed filters data_id=%s",
             len(accepted_chunks), len(raw_chunks), data_id)

    # Per-chunk summary + intents (one LLM call per chunk, the only per-chunk LLM
    # work in the whole pipeline — gold reduces these summaries instead of
    # re-reading chunk content). Also drops boilerplate chunks the LLM flags.
    accepted_chunks = await _summarize_chunks(accepted_chunks, emitter)
    log.info("Silver: %d chunks after summary/boilerplate pass data_id=%s",
             len(accepted_chunks), data_id)

    for i, chunk in enumerate(accepted_chunks):
        chunk["block_index"] = i

    mongo_db = mongo.get_client()
    await mongo_db["kb_silver_staging"].replace_one(
        {"data_id": data_id},
        {
            "data_id": data_id,
            "tenant_id": tenant_id,
            "source_type": source_type.value,
            "chunks": accepted_chunks,
        },
        upsert=True,
    )

    async with postgres.get_client() as session:
        await session.execute(
            sa_update(KBDataORM)
            .where(KBDataORM.data_id == uuid.UUID(data_id))
            .values(current_tier=Tier.SILVER.value)
        )
        await session.commit()

    rejected = len(raw_chunks) - len(accepted_chunks)
    tables_found = sum(1 for c in accepted_chunks if c.get("table_involved"))
    await postgres.insert(KBLifecycleHistoryInsert(
        data_id=data_id,
        to_tier=Tier.SILVER,
        from_tier=Tier.BRONZE,
        approved_by=approved_by,
        notes=f"Promoted to silver: {len(accepted_chunks)} chunks accepted, {rejected} rejected",
        pipeline_metadata={
            "stage":           "silver",
            "source_type":     source_type.value,
            "chunks_accepted": len(accepted_chunks),
            "chunks_rejected": rejected,
            "chunks_total":    len(raw_chunks),
            "tables_found":    tables_found,
        },
    ))
    if emitter:
        await emitter.emit("silver.complete", {
            "accepted": len(accepted_chunks),
            "rejected": rejected,
            "total":    len(raw_chunks),
        })

    # NOTE: Do NOT auto-promote to GOLD. Wait for explicit user request via /promote/{data_id}/gold endpoint.
    log.info("Silver complete data_id=%s chunks=%d — waiting for gold promotion request", data_id, len(accepted_chunks))
    return {"data_id": data_id, "chunks_count": len(accepted_chunks), "layer": Tier.SILVER.value}
