# Silver stage logic has moved to infra/kb-ingestion-worker/silver.py.
# The kb-backend API does not run silver promotion — it queues a Kafka message
# and the kb-ingestion-worker container handles all processing.
