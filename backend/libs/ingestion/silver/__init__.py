"""Backward-compatible facade for the modular silver ingestion pipeline."""

from libs.ingestion.silver.filtering import (
    _exact_match_blocked,
    _filter_chunks,
    _llm_policy_blocked,
)
from libs.ingestion.silver.orchestrator import (
    _parse_source,
    promote_to_silver,
)
from libs.ingestion.silver.parsing import (
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
from libs.ingestion.silver.persistence import (
    _load_kb_data,
    _persist_silver,
    _rollback_silver_staging,
    _rollback_silver_tier,
)
from libs.ingestion.silver.shared import (
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
from libs.ingestion.silver.summarization import _summarize_chunks

__all__ = ["promote_to_silver", "summarize_chunk"]
