#!/usr/bin/env bash
# Per-workload Secrets. Values come from one env file you fill in; each
# workload receives only the keys it reads.
#   set -a; . ./secrets.env; set +a; bash deploy/k8s/create-secrets.sh
set -euo pipefail

# app_user: tenant routes set app.tenant_id, cross-tenant reads go through the
# SECURITY DEFINER functions of migration 4a7c2e9f1b3d.
kubectl -n dataagent-service create secret generic auth-api-secrets \
  --from-literal=DATABASE_URL="${APP_DATABASE_URL}" \
  --from-literal=APP_DATABASE_URL="${APP_DATABASE_URL}" \
  --from-literal=OPENBAO_TOKEN="${OPENBAO_TOKEN}" \
  --from-literal=KC_ADMIN_USER="${KEYCLOAK_ADMIN}" \
  --from-literal=KC_ADMIN_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD}" \
  --from-literal=GATEWAY_KEY="${GATEWAY_KEY}" \
  --from-literal=SERVICE_AUTH_SECRET="${SERVICE_AUTH_SECRET}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic api-secrets \
  --from-literal=API_OIDC_ISSUER="${API_OIDC_ISSUER}" \
  --from-literal=DATAAGENT_DB_URL__SVC_ORG="${DATAAGENT_DB_URL__SVC_ORG}" \
  --from-literal=DATAAGENT_DB_URL__SVC_QA="${DATAAGENT_DB_URL__SVC_QA}" \
  --from-literal=DATAAGENT_DB_URL__SVC_QUERY="${DATAAGENT_DB_URL__SVC_QUERY}" \
  --from-literal=DATAAGENT_DB_URL__SVC_WORKSPACE="${DATAAGENT_DB_URL__SVC_WORKSPACE}" \
  --from-literal=DATAAGENT_DB_URL__SVC_INBOX="${DATAAGENT_DB_URL__SVC_INBOX}" \
  --from-literal=DATAAGENT_DB_URL__SVC_EVAL="${DATAAGENT_DB_URL__SVC_EVAL}" \
  --from-literal=DATAAGENT_DB_URL__SVC_AGENT="${DATAAGENT_DB_URL__SVC_AGENT}" \
  --from-literal=DATAAGENT_DB_URL__SVC_ACQUIRE="${DATAAGENT_DB_URL__SVC_ACQUIRE}" \
  --from-literal=DATAAGENT_DB_URL__SVC_CATALOG="${DATAAGENT_DB_URL__SVC_CATALOG}" \
  --from-literal=DATAAGENT_DB_URL__SVC_CONFLICT="${DATAAGENT_DB_URL__SVC_CONFLICT}" \
  --from-literal=DATAAGENT_DB_URL__SVC_CONTEXT="${DATAAGENT_DB_URL__SVC_CONTEXT}" \
  --from-literal=DATAAGENT_DB_URL__SVC_DOCUMENT="${DATAAGENT_DB_URL__SVC_DOCUMENT}" \
  --from-literal=DATAAGENT_DB_URL__SVC_EXEC="${DATAAGENT_DB_URL__SVC_EXEC}" \
  --from-literal=DATAAGENT_DB_URL__SVC_FORECAST="${DATAAGENT_DB_URL__SVC_FORECAST}" \
  --from-literal=DATAAGENT_DB_URL__SVC_GOVERNANCE="${DATAAGENT_DB_URL__SVC_GOVERNANCE}" \
  --from-literal=DATAAGENT_DB_URL__SVC_INGEST="${DATAAGENT_DB_URL__SVC_INGEST}" \
  --from-literal=DATAAGENT_DB_URL__SVC_INTEGRATION="${DATAAGENT_DB_URL__SVC_INTEGRATION}" \
  --from-literal=DATAAGENT_DB_URL__SVC_MONITOR="${DATAAGENT_DB_URL__SVC_MONITOR}" \
  --from-literal=DATAAGENT_DB_URL__SVC_NOTIFY="${DATAAGENT_DB_URL__SVC_NOTIFY}" \
  --from-literal=DATAAGENT_DB_URL__SVC_PACK="${DATAAGENT_DB_URL__SVC_PACK}" \
  --from-literal=DATAAGENT_DB_URL__SVC_PERIOD="${DATAAGENT_DB_URL__SVC_PERIOD}" \
  --from-literal=DATAAGENT_DB_URL__SVC_PIPELINE="${DATAAGENT_DB_URL__SVC_PIPELINE}" \
  --from-literal=DATAAGENT_DB_URL__SVC_SKILL="${DATAAGENT_DB_URL__SVC_SKILL}" \
  --from-literal=DATAAGENT_DB_URL__SVC_AUDIT="${DATAAGENT_DB_URL__SVC_AUDIT}" \
  --from-literal=MINIO_URL="${MINIO_URL}" \
  --dry-run=client -o yaml | kubectl apply -f -

# worker-agent: answers as svc_qa, opens HITL requests as svc_inbox, releases approved audit exports
# as svc_audit_worker (that one update only), claims outbox rows as svc_outbox_relay in both databases (docs/v3/06-api-v1.md §6),
# runs queries in place as svc_query with source credentials read from OpenBao, writes schema
# profiles as svc_context and tile snapshots as svc_workspace, and applies accepted proposals to
# skills as svc_skill and to pack installs as svc_pack; answer exports go to MinIO (da-exports).
kubectl -n dataagent-service create secret generic worker-agent-secrets \
  --from-literal=DATAAGENT_DB_URL__SVC_QA="${DATAAGENT_DB_URL__SVC_QA}" \
  --from-literal=DATAAGENT_DB_URL__SVC_OUTBOX_RELAY="${DATAAGENT_DB_URL__SVC_OUTBOX_RELAY}" \
  --from-literal=DATAAGENT_DB_URL__SVC_INBOX="${DATAAGENT_DB_URL__SVC_INBOX}" \
  --from-literal=DATAAGENT_DB_URL__SVC_AUDIT_WORKER="${DATAAGENT_DB_URL__SVC_AUDIT_WORKER}" \
  --from-literal=DATAAGENT_DB_URL__SVC_OUTBOX_RELAY_AUDIT="${DATAAGENT_DB_URL__SVC_OUTBOX_RELAY_AUDIT}" \
  --from-literal=DATAAGENT_DB_URL__SVC_CATALOG="${DATAAGENT_DB_URL__SVC_CATALOG}" \
  --from-literal=DATAAGENT_DB_URL__SVC_QUERY="${DATAAGENT_DB_URL__SVC_QUERY}" \
  --from-literal=DATAAGENT_DB_URL__SVC_CONTEXT="${DATAAGENT_DB_URL__SVC_CONTEXT}" \
  --from-literal=DATAAGENT_DB_URL__SVC_WORKSPACE="${DATAAGENT_DB_URL__SVC_WORKSPACE}" \
  --from-literal=DATAAGENT_DB_URL__SVC_SKILL="${DATAAGENT_DB_URL__SVC_SKILL}" \
  --from-literal=DATAAGENT_DB_URL__SVC_PACK="${DATAAGENT_DB_URL__SVC_PACK}" \
  --from-literal=MINIO_URL="${MINIO_URL}" \
  --from-literal=OPENBAO_ROLE_ID="${WORKER_AGENT_OPENBAO_ROLE_ID}" \
  --from-literal=OPENBAO_SECRET_ID="${WORKER_AGENT_OPENBAO_SECRET_ID}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic kb-backend-secrets \
  --from-literal=KB_BACKEND_URL_ASYNC="${KB_BACKEND_URL_ASYNC}" \
  --from-literal=MINIO_URL="${MINIO_URL}" \
  --from-literal=GATEWAY_KEY="${GATEWAY_KEY}" \
  --from-literal=SERVICE_AUTH_SECRET="${SERVICE_AUTH_SECRET}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic model-service-secrets \
  --from-literal=POSTGRES_DSN="${APP_DATABASE_URL}" \
  --from-literal=REMOTE_API_KEY="${REMOTE_API_KEY}" \
  --from-literal=REMOTE_LLM_KEY="${REMOTE_LLM_KEY}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic kb-promote-silver-secrets \
  --from-literal=POSTGRES_URL="${KB_INGESTION_WORKER_URL_ASYNC}" \
  --from-literal=MINIO_URL="${MINIO_URL}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic kb-promote-gold-secrets \
  --from-literal=POSTGRES_URL="${KB_INGESTION_WORKER_URL_ASYNC}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic kb-conflict-resolution-secrets \
  --from-literal=POSTGRES_URL="${KB_INGESTION_WORKER_URL_ASYNC}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic kb-structured-data-secrets \
  --from-literal=POSTGRES_URL="${KB_INGESTION_WORKER_URL_ASYNC}" \
  --from-literal=MINIO_URL="${MINIO_URL}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic sandbox-executor-secrets \
  --from-literal=MINIO_ACCESS_KEY="${MINIO_ROOT_USER}" \
  --from-literal=MINIO_SECRET_KEY="${MINIO_ROOT_PASSWORD}" \
  --from-literal=OPENBAO_ROLE_ID="${SANDBOX_EXECUTOR_OPENBAO_ROLE_ID}" \
  --from-literal=OPENBAO_SECRET_ID="${SANDBOX_EXECUTOR_OPENBAO_SECRET_ID}" \
  --from-literal=SERVICE_AUTH_SECRET="${SERVICE_AUTH_SECRET}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic platform-workers-audit-secrets \
  --from-literal=DB_PASSWORD="${APP_USER_PASSWORD}" \
  --from-literal=KAFKA_SASL_PASSWORD="${AUDIT_CONSUMER_KAFKA_PASSWORD}" \
  --from-literal=KEYCLOAK_ADMIN="${KEYCLOAK_ADMIN}" \
  --from-literal=KEYCLOAK_ADMIN_PASSWORD="${KEYCLOAK_ADMIN_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic platform-workers-memory-secrets \
  --from-literal=DB_PASSWORD="${APP_USER_PASSWORD}" \
  --from-literal=KAFKA_SASL_PASSWORD="${MEMORY_CONSUMER_KAFKA_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic platform-workers-schema-crawler-secrets \
  --from-literal=DB_PASSWORD="${APP_USER_PASSWORD}" \
  --from-literal=KAFKA_SASL_PASSWORD="${SCHEMA_CRAWLER_WORKER_KAFKA_PASSWORD}" \
  --from-literal=OPENBAO_ROLE_ID="${PLATFORM_WORKERS_OPENBAO_ROLE_ID}" \
  --from-literal=OPENBAO_SECRET_ID="${PLATFORM_WORKERS_OPENBAO_SECRET_ID}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic platform-workers-web-crawler-secrets \
  --from-literal=DB_PASSWORD="${APP_USER_PASSWORD}" \
  --from-literal=INGEST_DB_PASSWORD="${CRAWLER_SVC_PASSWORD}" \
  --from-literal=MINIO_URL="${MINIO_URL}" \
  --from-literal=KAFKA_SASL_PASSWORD="${WEB_CRAWLER_WORKER_KAFKA_PASSWORD}" \
  --from-literal=OPENBAO_ROLE_ID="${PLATFORM_WORKERS_OPENBAO_ROLE_ID}" \
  --from-literal=OPENBAO_SECRET_ID="${PLATFORM_WORKERS_OPENBAO_SECRET_ID}" \
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
  --from-literal=DB_PASSWORD="${PGBOUNCER_AUTH_PASSWORD}" \
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
  --from-literal=PGBOUNCER_AUTH_PASSWORD="${PGBOUNCER_AUTH_PASSWORD}" \
  --from-literal=APP_USER_PASSWORD="${APP_USER_PASSWORD}" \
  --from-literal=DB_ROLE_PASSWORDS="${DB_ROLE_PASSWORDS:-{\}}" \
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
  --from-literal=DEMO_PASSWORD_PLATFORM_ADMIN="${DEMO_PASSWORD_PLATFORM_ADMIN:-}" \
  --from-literal=DEMO_PASSWORD_HELIOS_ADMIN="${DEMO_PASSWORD_HELIOS_ADMIN:-}" \
  --from-literal=DEMO_PASSWORD_HELIOS_ENGINEER="${DEMO_PASSWORD_HELIOS_ENGINEER:-}" \
  --from-literal=DEMO_PASSWORD_HELIOS_VIEWER="${DEMO_PASSWORD_HELIOS_VIEWER:-}" \
  --from-literal=DEMO_PASSWORD_AI_ENGINEER="${DEMO_PASSWORD_AI_ENGINEER:-}" \
  --from-literal=DEMO_PASSWORD_EXECUTIVE_VIEWER="${DEMO_PASSWORD_EXECUTIVE_VIEWER:-}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic eval-secrets \
  --from-literal=LITELLM_API_KEY="${LITELLM_API_KEY}" \
  --from-literal=AGENT_ID="${EVAL_AGENT_ID}" \
  --from-literal=EVAL_ACCESS_TOKEN="${EVAL_ACCESS_TOKEN:-}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-service create secret generic postgres-backup-secrets \
  --from-literal=PGPASSWORD="${POSTGRES_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n dataagent-database create secret generic postgres-restore-secrets \
  --from-literal=PGPASSWORD="${POSTGRES_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -
