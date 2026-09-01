"""Backward-compatible facade for the modular silver ingestion pipeline."""

from services.kb_ingestion_worker.silver_pipeline.filtering import (
    _exact_match_blocked,
    _filter_chunks,
    _llm_policy_blocked,
)
from services.kb_ingestion_worker.silver_pipeline.orchestrator import (
    _parse_source,
    promote_to_silver,
)
from services.kb_ingestion_worker.silver_pipeline.parsing import (
    _WEB_USER_AGENT,
    _docling_chunks_to_dicts,
    _extract_main_content,
    _fetch_url,
    _fetch_url_rendered,
    _fetch_url_static,
    _parse_doc,
    _parse_image,
    _parse_video,
    _parse_web,
)
from services.kb_ingestion_worker.silver_pipeline.persistence import (
    _load_kb_data,
    _persist_silver,
    _rollback_silver_staging,
    _rollback_silver_tier,
)
from services.kb_ingestion_worker.silver_pipeline.shared import (
    _JSON_FENCE,
    _JSON_OBJ,
    _clean_chunk_entities,
    _clean_chunk_relationships,
    _fallback_chunk_summary,
    _llm_generate_json,
    _strip_surrogates,
    _vlm_describe,
    summarize_chunk,
)
from services.kb_ingestion_worker.silver_pipeline.summarization import _summarize_chunks

__all__ = ["promote_to_silver", "summarize_chunk"]
