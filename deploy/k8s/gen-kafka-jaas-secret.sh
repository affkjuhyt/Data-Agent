#!/usr/bin/env bash
# Renders deploy/docker/kafka/kafka-broker-jaas.config.example with the Kafka
# passwords from secrets.env and applies it as the kafka-jaas Secret — the one
# piece deploy/k8s/create-secrets.sh doesn't cover (six passwords that must
# never sit in an image layer, per deploy/k8s/README.md).
#
#   set -a; . deploy/k8s/secrets.env; set +a
#   bash deploy/k8s/gen-kafka-jaas-secret.sh
set -euo pipefail
cd "$(dirname "$0")"

: "${KAFKA_ADMIN_PASSWORD:?run gen-secrets-env.sh and source secrets.env first}"

sed -e "s/REPLACE_WITH_KAFKA_ADMIN_PASSWORD/$KAFKA_ADMIN_PASSWORD/g" \
    -e "s/REPLACE_WITH_AUDIT_CONSUMER_PASSWORD/$AUDIT_CONSUMER_KAFKA_PASSWORD/g" \
    -e "s/REPLACE_WITH_WORKFLOW_RUNTIME_PASSWORD/$WORKFLOW_RUNTIME_KAFKA_PASSWORD/g" \
    -e "s/REPLACE_WITH_MEMORY_CONSUMER_PASSWORD/$MEMORY_CONSUMER_KAFKA_PASSWORD/g" \
    -e "s/REPLACE_WITH_SCHEMA_CRAWLER_WORKER_PASSWORD/$SCHEMA_CRAWLER_WORKER_KAFKA_PASSWORD/g" \
    -e "s/REPLACE_WITH_WEB_CRAWLER_WORKER_PASSWORD/$WEB_CRAWLER_WORKER_KAFKA_PASSWORD/g" \
    ../docker/kafka/kafka-broker-jaas.config.example |
kubectl -n dataagent-database create secret generic kafka-jaas \
  --from-file=kafka-broker-jaas.config=/dev/stdin \
  --dry-run=client -o yaml | kubectl apply -f -
