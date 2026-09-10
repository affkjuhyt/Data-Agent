from __future__ import annotations

from libs.ingestion.gold.shared import _llm_generate_json
from libs.ingestion import config as cfg
from libs.ingestion.pipeline_events import NULL_EMITTER, PipelineEventEmitter
from libs.ingestion.prompts import GOLD_SYSTEM_PROMPTS as SYSTEM_PROMPTS


def _batch_texts(texts: list[str], budget_chars: int) -> list[list[str]]:
    batches: list[list[str]] = []
    current: list[str] = []
    size = 0
    for text in texts:
        if current and size + len(text) > budget_chars:
            batches.append(current)
            current, size = [], 0
        current.append(text)
        size += len(text)
    if current:
        batches.append(current)
    return batches


async def _reduce_batch(texts: list[str]) -> str:
    numbered = "\n".join(f"{idx + 1}. {text}" for idx, text in enumerate(texts))
    try:
        result, _, _ = await _llm_generate_json(
            SYSTEM_PROMPTS["doc-reduce"],
            numbered,
            max_tokens=1024,
            system_type="doc-reduce",
        )
        summary = str(result.get("summary") or "").strip()
        if summary:
            return summary
    except Exception:
        pass
    return " ".join(texts)[: cfg.DOC_SUMMARY_BATCH_CHARS]


async def _reduce_summaries(
    summaries: list[str],
    emitter: PipelineEventEmitter = NULL_EMITTER,
) -> str:
    texts = [summary for summary in summaries if summary.strip()]
    if not texts:
        return ""

    depth = 0
    while depth < cfg.DOC_SUMMARY_MAX_DEPTH:
        total = sum(len(text) for text in texts)
        if len(texts) == 1 and total <= cfg.DOC_SUMMARY_TARGET_CHARS:
            break
        batches = _batch_texts(texts, cfg.DOC_SUMMARY_BATCH_CHARS)
        await emitter.emit(
            "gold.doc.reduce.step",
            {"depth": depth, "inputs": len(texts), "batches": len(batches), "chars": total},
        )
        reduced = [await _reduce_batch(batch) for batch in batches]
        if sum(len(text) for text in reduced) >= total and len(reduced) == len(texts):
            texts = reduced
            break
        texts = reduced
        depth += 1

    doc_summary = " ".join(texts)
    if len(doc_summary) > cfg.DOC_SUMMARY_TARGET_CHARS:
        doc_summary = doc_summary[: cfg.DOC_SUMMARY_TARGET_CHARS]
    return doc_summary
