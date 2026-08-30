#!/bin/bash
# Custom launch script: pre-seeds SCRAM-SHA-512 users at storage format time.
# Replaces /opt/kafka/docker/launch in the apache/kafka:3.7.0 image.
#
# ⚠️  Production notes:
#   - Passwords must be injected via environment variables (never hardcoded here).
#   - --ignore-formatted means password changes are NOT applied after the first format.
#     To rotate credentials on an existing cluster, use kafka-configs.sh --alter
#     on a running broker instead of re-formatting storage.
#   - SCRAM-SHA-512 (iterations=4096+) is used here; SHA-256 is acceptable but
#     SHA-512 provides stronger key derivation.
set -e

if [ -z "${KAFKA_CLUSTER_ID}" ]; then
  echo "ERROR: KAFKA_CLUSTER_ID is required"
  exit 1
fi

# Require passwords to be provided via environment — fail fast if missing.
: "${KAFKA_ADMIN_PASSWORD:?ERROR: KAFKA_ADMIN_PASSWORD env var is required}"
: "${KAFKA_AUDIT_BRIDGE_PASSWORD:?ERROR: KAFKA_AUDIT_BRIDGE_PASSWORD env var is required}"
: "${KAFKA_AUDIT_CONSUMER_PASSWORD:?ERROR: KAFKA_AUDIT_CONSUMER_PASSWORD env var is required}"

/opt/kafka/bin/kafka-storage.sh format \
    -t "${KAFKA_CLUSTER_ID}" \
    -c /mnt/shared/config/server.properties \
    --add-scram "SCRAM-SHA-512=[name=admin,iterations=4096,password=${KAFKA_ADMIN_PASSWORD}]" \
    --add-scram "SCRAM-SHA-512=[name=audit-bridge,iterations=4096,password=${KAFKA_AUDIT_BRIDGE_PASSWORD}]" \
    --add-scram "SCRAM-SHA-512=[name=audit-consumer,iterations=4096,password=${KAFKA_AUDIT_CONSUMER_PASSWORD}]" \
    --ignore-formatted

exec /opt/kafka/bin/kafka-server-start.sh /mnt/shared/config/server.properties
