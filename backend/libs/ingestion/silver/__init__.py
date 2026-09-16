"""Public surface of the modular silver ingestion pipeline.

The implementation lives in filtering / orchestrator / parsing / persistence /
shared / summarization. Only the two names below are imported elsewhere; the
private helpers are reached through their own module.
"""

from libs.ingestion.silver.orchestrator import promote_to_silver
from libs.ingestion.silver.shared import summarize_chunk

__all__ = ["promote_to_silver", "summarize_chunk"]
