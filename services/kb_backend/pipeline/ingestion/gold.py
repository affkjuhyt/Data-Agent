# Gold stage logic has moved to infra/kb-ingestion-worker/gold.py.
# The kb-backend API does not run gold promotion — it queues a Kafka message
# and the kb-ingestion-worker container handles all processing.
