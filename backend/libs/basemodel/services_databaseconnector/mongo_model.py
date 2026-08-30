"""Mongo collection contracts shared across services.

Postgres is the conflict INDEX — ids, type, severity, status, resolution choice —
and holds the stable final text block. Mongo holds what Postgres has no business
storing: the batch's in-progress merge text, and each conflicting chunk's payload
(content, embedding, entities). The ingestion worker writes both and the KB backend
reads them, so the collection names live here rather than in each service.
"""

# One document per conflict batch — the progressively merged text of the block it is
# anchored on. Which block that is (KBConflictBatch.existing_block_id) and which
# conflicts belong to it (KBConflict.batch_id) are Postgres's to answer.
#   {batch_id, tenant_id, knowledge_base, existing_summary,
#    working_content, working_summary, created_at, updated_at}
CONFLICT_RESOLUTION_COLLECTION = "kb_conflict_resolution"

# One document per conflict — the incoming chunk, replayed by the resolver and shown
# as the UI's incoming snapshot. Keyed by (tenant_id, conflict_id).
#   {conflict_id, tenant_id, batch_id, data_id, block_index, approved_by,
#    content, summary, embedding, entities, relationships, table, table_involved,
#    embed_model_id, conflict_criteria_scores, created_at}
CONFLICT_PAYLOAD_COLLECTION = "kb_conflict_payload"
