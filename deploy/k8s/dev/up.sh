#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

SVC=dataagent-service
DB=dataagent-database
export SECRETS_ENV=dev/secrets.env KEYS_FILE=dev/.openbao-keys.json

load_secrets() { set -a; . ./secrets.env; set +a; }

job() {
  kubectl -n "${3:-$SVC}" delete job "$1" --ignore-not-found
  kubectl apply -f "job/$1.yaml"
  kubectl -n "${3:-$SVC}" wait --for=condition=complete "job/$1" --timeout="${2:-10m}"
}

load_secrets
kubectl apply -f namespace.yaml
kubectl apply -f configmap.yaml -f rbac.yaml \
  -f networkpolicy-service.yaml -f networkpolicy-database.yaml -f networkpolicy-sandbox.yaml
bash ../create-secrets.sh
bash ../gen-kafka-jaas-secret.sh
bash ../create-configmaps.sh

kubectl apply -f database/
for s in postgres mongo redis qdrant neo4j kafka minio clickhouse openbao; do
  kubectl -n "$DB" rollout status "statefulset/$s" --timeout=10m
done
kubectl -n "$DB" rollout status deployment/pgbouncer --timeout=5m

bash ../bootstrap-openbao.sh
load_secrets
bash ../create-secrets.sh

job migrate
job kong-migration
job db-init 5m
job minio-init 5m
job kafka-topics 5m

kubectl apply -f service/
for d in $(kubectl -n "$SVC" get deploy -o name); do
  kubectl -n "$SVC" rollout status "$d" --timeout=10m
done

job setup
job seed
kubectl apply -f job/postgres-backup.yaml -f job/postgres-restore.yaml

kubectl get pods -A -l app.kubernetes.io/part-of=dataagent
kubectl get svc -A --field-selector spec.type=NodePort
