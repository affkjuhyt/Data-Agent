#!/usr/bin/env bash
# ─── Kafka ACL Setup ──────────────────────────────────────────────────
# Configures per-topic ACLs so each service can only access what it needs.
# Run AFTER docker compose up (Kafka must be healthy).
#
# Usage:
#   bash deploy/docker/kafka/kafka-setup.sh
#
# Idempotent: safe to re-run.

set -uo pipefail

# Load .env from repo root (two levels up from deploy/docker/kafka/) if not already set
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/../../../.env"   # repo root
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck source=/dev/null
  source "$ENV_FILE"
  set +a
fi

KAFKA_CONTAINER="${KAFKA_CONTAINER:-aeroflow-kafka}"
BOOTSTRAP="localhost:9092"

# Admin credentials (inter-broker user, has full access)
ADMIN_USER="${KAFKA_ADMIN_USER:-admin}"
: "${KAFKA_ADMIN_PASSWORD:?ERROR: KAFKA_ADMIN_PASSWORD is required — copy .env.example to .env}"
ADMIN_PASS="$KAFKA_ADMIN_PASSWORD"

echo "🔐 Configuring Kafka ACLs..."
echo "   Container : $KAFKA_CONTAINER"
echo "   Bootstrap : $BOOTSTRAP"

# Write admin client properties into the container.
# The file is removed at the end of this script to avoid credential leakage.
docker exec "$KAFKA_CONTAINER" bash -c "
printf 'security.protocol=SASL_PLAINTEXT\nsasl.mechanism=PLAIN\nsasl.jaas.config=org.apache.kafka.common.security.plain.PlainLoginModule required username=\"${ADMIN_USER}\" password=\"${ADMIN_PASS}\";\n' > /tmp/admin.props
chmod 600 /tmp/admin.props
"

run_kafka() {
  docker exec "$KAFKA_CONTAINER" bash -c "$1"
}

# ── 1. Create topics explicitly ────────────────────────────────────────
echo ""
echo "📋 Creating topics..."

# ⚠️  replication-factor=1 is for single-broker dev only.
# In production (3+ brokers) use --replication-factor 3 for __consumer_offsets.
run_kafka "/opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server $BOOTSTRAP \
  --command-config /tmp/admin.props \
  --create --if-not-exists \
  --topic __consumer_offsets \
  --partitions 50 \
  --replication-factor 1 \
  --config cleanup.policy=compact" \
  && echo "  → topic: __consumer_offsets (50 partitions, compact)"

run_kafka "/opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server $BOOTSTRAP \
  --command-config /tmp/admin.props \
  --create --if-not-exists \
  --topic audit.auth.events \
  --partitions 3 \
  --replication-factor 1 \
  --config retention.ms=2592000000 \
  --config cleanup.policy=delete" \
  && echo "  → topic: audit.auth.events (3 partitions, 30d retention)"

# Data-access audit: who ran which query. One event per query rather than per
# login, so more partitions; retention deliberately LONGER than auth events,
# because this is the record an investigation reads back and 30 days is shorter
# than most incidents take to surface.
run_kafka "/opt/kafka/bin/kafka-topics.sh   --bootstrap-server $BOOTSTRAP   --command-config /tmp/admin.props   --create --if-not-exists   --topic audit.data.access   --partitions 6   --replication-factor 1   --config retention.ms=7776000000   --config cleanup.policy=delete"   && echo "  → topic: audit.data.access (6 partitions, 90d retention)"

run_kafka "/opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server $BOOTSTRAP \
  --command-config /tmp/admin.props \
  --create --if-not-exists \
  --topic release.pipeline.triggered \
  --partitions 3 \
  --replication-factor 1" \
  && echo "  → topic: release.pipeline.triggered"

run_kafka "/opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server $BOOTSTRAP \
  --command-config /tmp/admin.props \
  --create --if-not-exists \
  --topic release.pipeline.status \
  --partitions 3 \
  --replication-factor 1" \
  && echo "  → topic: release.pipeline.status"

run_kafka "/opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server $BOOTSTRAP \
  --command-config /tmp/admin.props \
  --create --if-not-exists \
  --topic release.rollback.initiated \
  --partitions 3 \
  --replication-factor 1" \
  && echo "  → topic: release.rollback.initiated"

run_kafka "/opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server $BOOTSTRAP \
  --command-config /tmp/admin.props \
  --create --if-not-exists \
  --topic memory.extraction.requested \
  --partitions 3 \
  --replication-factor 1 \
  --config retention.ms=604800000 \
  --config cleanup.policy=delete" \
  && echo "  → topic: memory.extraction.requested (3 partitions, 7d retention)"

run_kafka "/opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server $BOOTSTRAP \
  --command-config /tmp/admin.props \
  --create --if-not-exists \
  --topic memory.extraction.dlq \
  --partitions 1 \
  --replication-factor 1 \
  --config retention.ms=2592000000 \
  --config cleanup.policy=delete" \
  && echo "  → topic: memory.extraction.dlq (1 partition, 30d retention)"

# sandbox_schema_docs.docx §2.8 — DDL-change events that trigger a targeted
# re-crawl instead of waiting for the next cron tick. Producer side (a
# Snowflake Stream → Kafka bridge) is external infra, out of this repo's
# scope — schema-crawler-worker only ever consumes this topic.
run_kafka "/opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server $BOOTSTRAP \
  --command-config /tmp/admin.props \
  --create --if-not-exists \
  --topic ddl.changes \
  --partitions 3 \
  --replication-factor 1 \
  --config retention.ms=604800000 \
  --config cleanup.policy=delete" \
  && echo "  → topic: ddl.changes (3 partitions, 7d retention)"

# ── 2. Enable ACLs (authorizer must be configured) ─────────────────────
echo ""
echo "🛡️  Applying ACLs..."

# audit-bridge: WRITE only to audit.auth.events
run_kafka "/opt/kafka/bin/kafka-acls.sh \
  --bootstrap-server $BOOTSTRAP \
  --command-config /tmp/admin.props \
  --add \
  --allow-principal User:audit-bridge \
  --operation Write \
  --operation Describe \
  --topic audit.auth.events" \
  && echo "  → audit-bridge: Write + Describe on audit.auth.events"

# audit-consumer: READ from audit.auth.events + consumer group access
run_kafka "/opt/kafka/bin/kafka-acls.sh \
  --bootstrap-server $BOOTSTRAP \
  --command-config /tmp/admin.props \
  --add \
  --allow-principal User:audit-consumer \
  --operation Read \
  --operation Describe \
  --topic audit.auth.events" \
  && echo "  → audit-consumer: Read + Describe on audit.auth.events"

# workflow-runtime PRODUCES data-access events. Without this ACL the send is
# rejected and every audit record is lost with only a client-side warning —
# the failure mode an audit can least afford.
run_kafka "/opt/kafka/bin/kafka-acls.sh   --bootstrap-server $BOOTSTRAP   --command-config /tmp/admin.props   --add   --allow-principal User:workflow-runtime   --operation Write   --operation Describe   --topic audit.data.access"   && echo "  → workflow-runtime: Write + Describe on audit.data.access"

run_kafka "/opt/kafka/bin/kafka-acls.sh   --bootstrap-server $BOOTSTRAP   --command-config /tmp/admin.props   --add   --allow-principal User:audit-consumer   --operation Read   --operation Describe   --topic audit.data.access"   && echo "  → audit-consumer: Read + Describe on audit.data.access"

run_kafka "/opt/kafka/bin/kafka-acls.sh \
  --bootstrap-server $BOOTSTRAP \
  --command-config /tmp/admin.props \
  --add \
  --allow-principal User:audit-consumer \
  --operation Read \
  --group audit-consumer-group" \
  && echo "  → audit-consumer: Read on group audit-consumer-group"

# release-worker: WRITE + READ on release topics + CREATE (needed if auto-create is disabled)
run_kafka "/opt/kafka/bin/kafka-acls.sh \
  --bootstrap-server $BOOTSTRAP \
  --command-config /tmp/admin.props \
  --add \
  --allow-principal User:release-worker \
  --operation Write \
  --operation Read \
  --operation Describe \
  --operation Create \
  --topic release.pipeline.triggered" \
  && echo "  → release-worker: Write + Read + Create on release.pipeline.triggered"

run_kafka "/opt/kafka/bin/kafka-acls.sh \
  --bootstrap-server $BOOTSTRAP \
  --command-config /tmp/admin.props \
  --add \
  --allow-principal User:release-worker \
  --operation Write \
  --operation Read \
  --operation Describe \
  --operation Create \
  --topic release.pipeline.status" \
  && echo "  → release-worker: Write + Read + Create on release.pipeline.status"

run_kafka "/opt/kafka/bin/kafka-acls.sh \
  --bootstrap-server $BOOTSTRAP \
  --command-config /tmp/admin.props \
  --add \
  --allow-principal User:release-worker \
  --operation Write \
  --operation Read \
  --operation Describe \
  --operation Create \
  --topic release.rollback.initiated" \
  && echo "  → release-worker: Write + Read + Create on release.rollback.initiated"

run_kafka "/opt/kafka/bin/kafka-acls.sh \
  --bootstrap-server $BOOTSTRAP \
  --command-config /tmp/admin.props \
  --add \
  --allow-principal User:release-worker \
  --operation Read \
  --group release-worker-group" \
  && echo "  → release-worker: Read on group release-worker-group"

# workflow-runtime: WRITE only to memory.extraction.requested
run_kafka "/opt/kafka/bin/kafka-acls.sh \
  --bootstrap-server $BOOTSTRAP \
  --command-config /tmp/admin.props \
  --add \
  --allow-principal User:workflow-runtime \
  --operation Write \
  --operation Describe \
  --topic memory.extraction.requested" \
  && echo "  → workflow-runtime: Write + Describe on memory.extraction.requested"

# memory-consumer: READ from memory.extraction.requested, WRITE to the DLQ topic
run_kafka "/opt/kafka/bin/kafka-acls.sh \
  --bootstrap-server $BOOTSTRAP \
  --command-config /tmp/admin.props \
  --add \
  --allow-principal User:memory-consumer \
  --operation Read \
  --operation Describe \
  --topic memory.extraction.requested" \
  && echo "  → memory-consumer: Read + Describe on memory.extraction.requested"

run_kafka "/opt/kafka/bin/kafka-acls.sh \
  --bootstrap-server $BOOTSTRAP \
  --command-config /tmp/admin.props \
  --add \
  --allow-principal User:memory-consumer \
  --operation Write \
  --operation Describe \
  --topic memory.extraction.dlq" \
  && echo "  → memory-consumer: Write + Describe on memory.extraction.dlq"

run_kafka "/opt/kafka/bin/kafka-acls.sh \
  --bootstrap-server $BOOTSTRAP \
  --command-config /tmp/admin.props \
  --add \
  --allow-principal User:memory-consumer \
  --operation Read \
  --group memory-consumer-group" \
  && echo "  → memory-consumer: Read on group memory-consumer-group"

# schema-crawler-worker: READ from ddl.changes
run_kafka "/opt/kafka/bin/kafka-acls.sh \
  --bootstrap-server $BOOTSTRAP \
  --command-config /tmp/admin.props \
  --add \
  --allow-principal User:schema-crawler-worker \
  --operation Read \
  --operation Describe \
  --topic ddl.changes" \
  && echo "  → schema-crawler-worker: Read + Describe on ddl.changes"

run_kafka "/opt/kafka/bin/kafka-acls.sh \
  --bootstrap-server $BOOTSTRAP \
  --command-config /tmp/admin.props \
  --add \
  --allow-principal User:schema-crawler-worker \
  --operation Read \
  --group schema-crawler-worker-group" \
  && echo "  → schema-crawler-worker: Read on group schema-crawler-worker-group"

# ── 3. Show current ACL summary ────────────────────────────────────────
echo ""
echo "📊 Current ACLs:"
run_kafka "/opt/kafka/bin/kafka-acls.sh \
  --bootstrap-server $BOOTSTRAP \
  --command-config /tmp/admin.props \
  --list"

# ── 4. Remove temp credentials file ───────────────────────────────────
docker exec "$KAFKA_CONTAINER" bash -c "rm -f /tmp/admin.props"
echo ""
echo "🔑 Removed /tmp/admin.props from container."

echo ""
echo "✅ Kafka ACL setup complete."
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  audit-bridge      → WRITE  audit.auth.events"
echo "  audit-consumer    → READ   audit.auth.events (group: audit-consumer-group)"
echo "  release-worker    → WRITE+READ+CREATE release.* topics"
echo "  workflow-runtime  → WRITE  memory.extraction.requested"
echo "  memory-consumer   → READ   memory.extraction.requested (group: memory-consumer-group), WRITE memory.extraction.dlq"
echo "  schema-crawler-worker → READ ddl.changes (group: schema-crawler-worker-group)"
echo "  admin             → ALL    (inter-broker + management)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
