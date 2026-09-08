#!/usr/bin/env bash
# Per-workload Secrets. Values come from one env file you fill in; each
# workload receives only the keys it reads.
#   set -a; . ./secrets.env; set +a; bash deploy/k8s/create-secrets.sh
set -euo pipefail

kubectl -n dataagent-service create secret generic auth-api-secrets \
  --from-literal=DATABASE_URL="${DATABASE_URL}" \
  --from-literal=APP_DATABASE_URL="${APP_DATABASE_URL}" \
  --from-literal=OPENBAO_TOKEN="${OPENBAO_TOKEN}" \
  --from-literal=KC_ADMIN_USER="${KEYCLOAK_ADMIN}" \
  --from-literal=KC_ADMIN_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD}" \
  --from-literal=GATEWAY_KEY="${GATEWAY_KEY}" \
  --from-literal=SERVICE_AUTH_SECRET="${SERVICE_AUTH_SECRET}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic flow-builder-secrets \
  --from-literal=DATABASE_URL="${DATABASE_URL}" \
  --from-literal=GATEWAY_KEY="${GATEWAY_KEY}" \
  --from-literal=SERVICE_AUTH_SECRET="${SERVICE_AUTH_SECRET}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic kb-backend-secrets \
  --from-literal=POSTGRES_URL="${POSTGRES_URL_ASYNC}" \
  --from-literal=KB_BACKEND_URL_ASYNC="${KB_BACKEND_URL_ASYNC}" \
  --from-literal=MINIO_URL="${MINIO_URL}" \
  --from-literal=GATEWAY_KEY="${GATEWAY_KEY}" \
  --from-literal=SERVICE_AUTH_SECRET="${SERVICE_AUTH_SECRET}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic release-worker-secrets \
  --from-literal=DATABASE_URL="${DATABASE_URL}" \
  --from-literal=KAFKA_PASSWORD="${RELEASE_WORKER_KAFKA_PASSWORD}" \
  --from-literal=MINIO_ACCESS_KEY="${MINIO_ROOT_USER}" \
  --from-literal=MINIO_SECRET_KEY="${MINIO_ROOT_PASSWORD}" \
  --from-literal=GATEWAY_KEY="${GATEWAY_KEY}" \
  --from-literal=SERVICE_AUTH_SECRET="${SERVICE_AUTH_SECRET}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic workflow-runtime-secrets \
  --from-literal=DATABASE_URL="${DATABASE_URL}" \
  --from-literal=RUNTIME_DB_URL="${RUNTIME_DB_URL}" \
  --from-literal=LITELLM_API_KEY="${LITELLM_API_KEY}" \
  --from-literal=LANGFUSE_PUBLIC_KEY="${LANGFUSE_PUBLIC_KEY}" \
  --from-literal=LANGFUSE_SECRET_KEY="${LANGFUSE_SECRET_KEY}" \
  --from-literal=OPENBAO_ROLE_ID="${OPENBAO_ROLE_ID}" \
  --from-literal=OPENBAO_SECRET_ID="${OPENBAO_SECRET_ID}" \
  --from-literal=KAFKA_SASL_PASSWORD="${WORKFLOW_RUNTIME_KAFKA_PASSWORD}" \
  --from-literal=MINIO_ACCESS_KEY="${MINIO_ROOT_USER}" \
  --from-literal=MINIO_SECRET_KEY="${MINIO_ROOT_PASSWORD}" \
  --from-literal=GATEWAY_KEY="${GATEWAY_KEY}" \
  --from-literal=SERVICE_AUTH_SECRET="${SERVICE_AUTH_SECRET}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic model-service-secrets \
  --from-literal=POSTGRES_DSN="${POSTGRES_DSN}" \
  --from-literal=REMOTE_API_KEY="${REMOTE_API_KEY}" \
  --from-literal=REMOTE_LLM_KEY="${REMOTE_LLM_KEY}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic kb-ingestion-worker-secrets \
  --from-literal=POSTGRES_URL="${POSTGRES_URL_ASYNC}" \
  --from-literal=MINIO_URL="${MINIO_URL}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic sandbox-executor-secrets \
  --from-literal=MINIO_ACCESS_KEY="${MINIO_ROOT_USER}" \
  --from-literal=MINIO_SECRET_KEY="${MINIO_ROOT_PASSWORD}" \
  --from-literal=SNOWFLAKE_PASSWORD="${SNOWFLAKE_CRAWLER_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic platform-workers-audit-secrets \
  --from-literal=DB_PASSWORD="${POSTGRES_PASSWORD}" \
  --from-literal=KAFKA_SASL_PASSWORD="${AUDIT_CONSUMER_KAFKA_PASSWORD}" \
  --from-literal=KEYCLOAK_ADMIN="${KEYCLOAK_ADMIN}" \
  --from-literal=KEYCLOAK_ADMIN_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic platform-workers-memory-secrets \
  --from-literal=DB_PASSWORD="${POSTGRES_PASSWORD}" \
  --from-literal=KAFKA_SASL_PASSWORD="${MEMORY_CONSUMER_KAFKA_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic platform-workers-schema-crawler-secrets \
  --from-literal=DB_PASSWORD="${POSTGRES_PASSWORD}" \
  --from-literal=KAFKA_SASL_PASSWORD="${SCHEMA_CRAWLER_WORKER_KAFKA_PASSWORD}" \
  --from-literal=SNOWFLAKE_CRAWLER_PASSWORD="${SNOWFLAKE_CRAWLER_PASSWORD}" \
  --from-literal=POSTGRES_CRAWLER_PASSWORD="${POSTGRES_CRAWLER_PASSWORD}" \
  --from-literal=CLICKHOUSE_CRAWLER_PASSWORD="${CLICKHOUSE_CRAWLER_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic frontend-secrets \
  --from-literal=VITE_KEYCLOAK_URL="${KEYCLOAK_PUBLIC_URL}" \
  --from-literal=VITE_API_BASE_URL="${PUBLIC_API_URL}" \
  --from-literal=VITE_API_URL="${PUBLIC_API_URL}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-database create secret generic postgres-secrets \
  --from-literal=POSTGRES_PASSWORD="${POSTGRES_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-database create secret generic neo4j-secrets \
  --from-literal=NEO4J_AUTH="${NEO4J_AUTH}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-database create secret generic clickhouse-secrets \
  --from-literal=CLICKHOUSE_USER="${CLICKHOUSE_USER}" \
  --from-literal=CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-database create secret generic minio-secrets \
  --from-literal=MINIO_ROOT_USER="${MINIO_ROOT_USER}" \
  --from-literal=MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-database create secret generic kafka-secrets \
  --from-literal=KAFKA_ADMIN_PASSWORD="${KAFKA_ADMIN_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-database create secret generic pgbouncer-secrets \
  --from-literal=AUTH_PASSWORD="${PGBOUNCER_AUTH_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic kong-secrets \
  --from-literal=KONG_PG_PASSWORD="${POSTGRES_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic keycloak-secrets \
  --from-literal=KEYCLOAK_ADMIN="${KEYCLOAK_ADMIN}" \
  --from-literal=KEYCLOAK_ADMIN_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD}" \
  --from-literal=KC_DB_PASSWORD="${POSTGRES_PASSWORD}" \
  --from-literal=KC_HOSTNAME_URL="${KEYCLOAK_PUBLIC_URL}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic litellm-secrets \
  --from-literal=DATABASE_URL="${LITELLM_DATABASE_URL}" \
  --from-literal=OPENAI_API_KEY="${OPENAI_API_KEY}" \
  --from-literal=GEMINI_API_KEY="${GEMINI_API_KEY}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic langfuse-secrets \
  --from-literal=DATABASE_URL="${LANGFUSE_DATABASE_URL}" \
  --from-literal=SALT="${LANGFUSE_SALT}" \
  --from-literal=ENCRYPTION_KEY="${LANGFUSE_ENCRYPTION_KEY}" \
  --from-literal=NEXTAUTH_SECRET="${LANGFUSE_NEXTAUTH_SECRET}" \
  --from-literal=NEXTAUTH_URL="${LANGFUSE_PUBLIC_URL}" \
  --from-literal=CLICKHOUSE_USER="${CLICKHOUSE_USER}" \
  --from-literal=CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD}" \
  --from-literal=LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID="${MINIO_ROOT_USER}" \
  --from-literal=LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY="${MINIO_ROOT_PASSWORD}" \
  --from-literal=LANGFUSE_S3_MEDIA_UPLOAD_ACCESS_KEY_ID="${MINIO_ROOT_USER}" \
  --from-literal=LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY="${MINIO_ROOT_PASSWORD}" \
  --from-literal=LANGFUSE_INIT_PROJECT_PUBLIC_KEY="${LANGFUSE_PUBLIC_KEY}" \
  --from-literal=LANGFUSE_INIT_PROJECT_SECRET_KEY="${LANGFUSE_SECRET_KEY}" \
  --from-literal=LANGFUSE_INIT_USER_EMAIL="${LANGFUSE_INIT_EMAIL}" \
  --from-literal=LANGFUSE_INIT_USER_PASSWORD="${LANGFUSE_INIT_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic langfuse-worker-secrets \
  --from-literal=DATABASE_URL="${LANGFUSE_DATABASE_URL}" \
  --from-literal=SALT="${LANGFUSE_SALT}" \
  --from-literal=ENCRYPTION_KEY="${LANGFUSE_ENCRYPTION_KEY}" \
  --from-literal=CLICKHOUSE_USER="${CLICKHOUSE_USER}" \
  --from-literal=CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD}" \
  --from-literal=LANGFUSE_S3_EVENT_UPLOAD_ACCESS_KEY_ID="${MINIO_ROOT_USER}" \
  --from-literal=LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY="${MINIO_ROOT_PASSWORD}" \
  --from-literal=LANGFUSE_S3_MEDIA_UPLOAD_ACCESS_KEY_ID="${MINIO_ROOT_USER}" \
  --from-literal=LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY="${MINIO_ROOT_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic grafana-secrets \
  --from-literal=GF_SECURITY_ADMIN_PASSWORD="${GRAFANA_ADMIN_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic migrate-secrets \
  --from-literal=DATABASE_URL="${DATABASE_URL}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic kong-migration-secrets \
  --from-literal=KONG_PG_PASSWORD="${POSTGRES_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic db-init-secrets \
  --from-literal=PGPASSWORD="${POSTGRES_PASSWORD}" \
  --from-literal=APP_USER_PASSWORD="${APP_USER_PASSWORD}" \
  --from-literal=RUNTIME_WRITER_PASSWORD="${RUNTIME_WRITER_PASSWORD}" \
  --from-literal=SANDBOX_READER_PASSWORD="${SANDBOX_READER_PASSWORD}" \
  --from-literal=KB_BACKEND_PASSWORD="${KB_BACKEND_PASSWORD}" \
  --from-literal=PGBOUNCER_AUTH_PASSWORD="${PGBOUNCER_AUTH_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic seed-secrets \
  --from-literal=PGPASSWORD="${POSTGRES_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic minio-init-secrets \
  --from-literal=MINIO_ROOT_USER="${MINIO_ROOT_USER}" \
  --from-literal=MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic setup-secrets \
  --from-literal=KEYCLOAK_ADMIN="${KEYCLOAK_ADMIN}" \
  --from-literal=KEYCLOAK_ADMIN_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD}" \
  --from-literal=KEYCLOAK_PUBLIC_URL="${KEYCLOAK_PUBLIC_URL}" \
  --from-literal=SMOKE_PASSWORD="${SMOKE_PASSWORD}" \
  --from-literal=GATEWAY_KEY="${GATEWAY_KEY}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic eval-secrets \
  --from-literal=LITELLM_API_KEY="${LITELLM_API_KEY}" \
  --from-literal=AGENT_ID="${EVAL_AGENT_ID}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic postgres-backup-secrets \
  --from-literal=PGPASSWORD="${POSTGRES_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-database create secret generic postgres-restore-secrets \
  --from-literal=PGPASSWORD="${POSTGRES_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -
