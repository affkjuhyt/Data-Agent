#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────
# Release Management — Kafka Topics & ACL Setup
# Chạy một lần sau khi stack khởi động: bash deploy/docker/kafka/release-setup.sh
#
# Topics được tạo:
#   release.pipeline.triggered   — CI → Release Worker
#   release.pipeline.status      — Release Worker → Notification
#   release.drift.detected       — Drift Detector → Notification
#   release.rollback.initiated   — Manual / Release Worker → Worker
#   release.scan.completed       — Scan Runner → Release Worker
#
# Kafka Users cần có sẵn (tạo qua kafka-setup.sh trước):
#   admin            — super user (đã có từ auth setup)
#   ci-service       — producer cho pipeline trigger
#   release-worker   — consumer pipeline trigger, producer status
#   drift-detector   — producer drift events
#   notification-consumer — consumer status + drift
# ─────────────────────────────────────────────────────────────────────

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

KAFKA_CONTAINER="${KAFKA_CONTAINER:-aeroflow-kafka}"
BOOTSTRAP="localhost:9092"
ADMIN_PROPS="/tmp/admin_release.props"

# Require passwords via environment — never hardcoded.
# Copy .env.example to .env and set these before running.
: "${KAFKA_ADMIN_PASSWORD:?ERROR: KAFKA_ADMIN_PASSWORD is required}"
: "${CI_SERVICE_KAFKA_PASSWORD:?ERROR: CI_SERVICE_KAFKA_PASSWORD is required}"
: "${RELEASE_WORKER_KAFKA_PASSWORD:?ERROR: RELEASE_WORKER_KAFKA_PASSWORD is required}"
: "${DRIFT_DETECTOR_KAFKA_PASSWORD:?ERROR: DRIFT_DETECTOR_KAFKA_PASSWORD is required}"
: "${NOTIFICATION_CONSUMER_KAFKA_PASSWORD:?ERROR: NOTIFICATION_CONSUMER_KAFKA_PASSWORD is required}"
: "${SCAN_RUNNER_KAFKA_PASSWORD:?ERROR: SCAN_RUNNER_KAFKA_PASSWORD is required}"

# ── Màu sắc output ────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

info "=== Release Kafka Setup ==="

# ── 1. Tạo admin props trong container ───────────────────────────────
info "Tạo admin client config..."
docker exec "$KAFKA_CONTAINER" bash -c "printf 'security.protocol=SASL_PLAINTEXT\nsasl.mechanism=PLAIN\nsasl.jaas.config=org.apache.kafka.common.security.plain.PlainLoginModule required username=\"admin\" password=\"${KAFKA_ADMIN_PASSWORD}\";\n' > $ADMIN_PROPS && chmod 600 $ADMIN_PROPS"

# ── 2. Tạo Kafka users cho release services ──────────────────────────
info "Tạo Kafka SCRAM users cho release services..."

create_user() {
  local username="$1" password="$2"
  docker exec "$KAFKA_CONTAINER" /opt/kafka/bin/kafka-configs.sh \
    --bootstrap-server "$BOOTSTRAP" \
    --command-config "$ADMIN_PROPS" \
    --alter \
    --add-config "SCRAM-SHA-512=[password=$password]" \
    --entity-type users --entity-name "$username" 2>&1 | grep -v "^$" || true
  info "  User: $username"
}

create_user "ci-service"            "$CI_SERVICE_KAFKA_PASSWORD"
create_user "release-worker"        "$RELEASE_WORKER_KAFKA_PASSWORD"
create_user "drift-detector"        "$DRIFT_DETECTOR_KAFKA_PASSWORD"
create_user "notification-consumer" "$NOTIFICATION_CONSUMER_KAFKA_PASSWORD"
create_user "scan-runner"           "$SCAN_RUNNER_KAFKA_PASSWORD"

# ── 3. Tạo topics ─────────────────────────────────────────────────────
info "Tạo release topics..."

create_topic() {
  local topic="$1" partitions="${2:-3}" retention="${3:-2592000000}"  # 30 ngày default
  docker exec "$KAFKA_CONTAINER" /opt/kafka/bin/kafka-topics.sh \
    --bootstrap-server "$BOOTSTRAP" \
    --command-config "$ADMIN_PROPS" \
    --create --if-not-exists \
    --topic "$topic" \
    --partitions "$partitions" \
    --replication-factor 1 \
    --config "retention.ms=$retention" \
    --config "cleanup.policy=delete" 2>&1 | grep -v "^$" || true
  info "  Topic: $topic (partitions=$partitions, retention=${retention}ms)"
}

# Pipeline trigger — ít message nhưng quan trọng, retention 7 ngày
create_topic "release.pipeline.triggered"  3 604800000

# Pipeline status — notification downstream, retention 30 ngày
create_topic "release.pipeline.status"     3 2592000000

# Drift events — alert quan trọng, retention 30 ngày
create_topic "release.drift.detected"      3 2592000000

# Rollback trigger — critical, retention 7 ngày
create_topic "release.rollback.initiated"  3 604800000

# Scan results — từ scan-runner đến release-worker, retention 7 ngày
create_topic "release.scan.completed"      3 604800000

# ── 4. ACL cho ci-service (WRITE pipeline.triggered) ─────────────────
info "Gán ACL cho ci-service..."
docker exec "$KAFKA_CONTAINER" /opt/kafka/bin/kafka-acls.sh \
  --bootstrap-server "$BOOTSTRAP" \
  --command-config "$ADMIN_PROPS" \
  --add \
  --allow-principal "User:ci-service" \
  --operation WRITE --operation DESCRIBE \
  --topic "release.pipeline.triggered"

# ── 5. ACL cho release-worker ─────────────────────────────────────────
info "Gán ACL cho release-worker..."

# READ từ pipeline.triggered
docker exec "$KAFKA_CONTAINER" /opt/kafka/bin/kafka-acls.sh \
  --bootstrap-server "$BOOTSTRAP" \
  --command-config "$ADMIN_PROPS" \
  --add \
  --allow-principal "User:release-worker" \
  --operation READ --operation DESCRIBE \
  --topic "release.pipeline.triggered"

# WRITE vào pipeline.status, rollback.initiated
for topic in "release.pipeline.status" "release.rollback.initiated"; do
  docker exec "$KAFKA_CONTAINER" /opt/kafka/bin/kafka-acls.sh \
    --bootstrap-server "$BOOTSTRAP" \
    --command-config "$ADMIN_PROPS" \
    --add \
    --allow-principal "User:release-worker" \
    --operation WRITE --operation DESCRIBE \
    --topic "$topic"
done

# READ từ scan.completed, rollback.initiated
for topic in "release.scan.completed" "release.rollback.initiated"; do
  docker exec "$KAFKA_CONTAINER" /opt/kafka/bin/kafka-acls.sh \
    --bootstrap-server "$BOOTSTRAP" \
    --command-config "$ADMIN_PROPS" \
    --add \
    --allow-principal "User:release-worker" \
    --operation READ --operation DESCRIBE \
    --topic "$topic"
done

# ConsumerGroup ACL cho release-worker
docker exec "$KAFKA_CONTAINER" /opt/kafka/bin/kafka-acls.sh \
  --bootstrap-server "$BOOTSTRAP" \
  --command-config "$ADMIN_PROPS" \
  --add \
  --allow-principal "User:release-worker" \
  --operation READ \
  --group "release-worker-group"

# ── 6. ACL cho drift-detector ─────────────────────────────────────────
info "Gán ACL cho drift-detector..."
docker exec "$KAFKA_CONTAINER" /opt/kafka/bin/kafka-acls.sh \
  --bootstrap-server "$BOOTSTRAP" \
  --command-config "$ADMIN_PROPS" \
  --add \
  --allow-principal "User:drift-detector" \
  --operation WRITE --operation DESCRIBE \
  --topic "release.drift.detected"

# ── 7. ACL cho scan-runner ────────────────────────────────────────────
info "Gán ACL cho scan-runner..."
docker exec "$KAFKA_CONTAINER" /opt/kafka/bin/kafka-acls.sh \
  --bootstrap-server "$BOOTSTRAP" \
  --command-config "$ADMIN_PROPS" \
  --add \
  --allow-principal "User:scan-runner" \
  --operation WRITE --operation DESCRIBE \
  --topic "release.scan.completed"

# ── 8. ACL cho notification-consumer ─────────────────────────────────
info "Gán ACL cho notification-consumer..."
for topic in "release.pipeline.status" "release.drift.detected"; do
  docker exec "$KAFKA_CONTAINER" /opt/kafka/bin/kafka-acls.sh \
    --bootstrap-server "$BOOTSTRAP" \
    --command-config "$ADMIN_PROPS" \
    --add \
    --allow-principal "User:notification-consumer" \
    --operation READ --operation DESCRIBE \
    --topic "$topic"
done
docker exec "$KAFKA_CONTAINER" /opt/kafka/bin/kafka-acls.sh \
  --bootstrap-server "$BOOTSTRAP" \
  --command-config "$ADMIN_PROPS" \
  --add \
  --allow-principal "User:notification-consumer" \
  --operation READ \
  --group "notification-consumer-group"

# ── 9. __consumer_offsets ACL cho các consumers ───────────────────────
info "Gán ACL __consumer_offsets cho consumers..."
for user in "release-worker" "notification-consumer"; do
  docker exec "$KAFKA_CONTAINER" /opt/kafka/bin/kafka-acls.sh \
    --bootstrap-server "$BOOTSTRAP" \
    --command-config "$ADMIN_PROPS" \
    --add \
    --allow-principal "User:$user" \
    --operation READ --operation WRITE --operation DESCRIBE \
    --topic "__consumer_offsets"
done

# ── 10. Verify topics và ACL ──────────────────────────────────────────
info "=== Verify release topics ==="
docker exec "$KAFKA_CONTAINER" /opt/kafka/bin/kafka-topics.sh \
  --bootstrap-server "$BOOTSTRAP" \
  --command-config "$ADMIN_PROPS" \
  --list | grep "^release\." | while read -r topic; do
    info "  ✅ $topic"
  done

info ""
info "=== Release Kafka Setup DONE ==="
info "Topics: release.pipeline.triggered, release.pipeline.status,"
info "        release.drift.detected, release.rollback.initiated, release.scan.completed"
info "Users:  ci-service, release-worker, drift-detector, scan-runner, notification-consumer"
info ""
info "Kafka Users created: ci-service, release-worker, drift-detector, scan-runner, notification-consumer"
info "Passwords were read from environment variables — see .env.example for the variable names."
