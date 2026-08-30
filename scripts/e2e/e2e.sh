#!/usr/bin/env bash
# Data Agent — end-to-end runner: real stack, real gateway, real LLM (host ollama).
#
#   scripts/e2e/e2e.sh all            # preflight → up → setup → seed → test → ui
#   scripts/e2e/e2e.sh up             # bring the agent tier up (idempotent)
#   scripts/e2e/e2e.sh setup          # Kong routes/JWT, Keycloak dev passwords, OpenBao AppRole + DB secret
#   scripts/e2e/e2e.sh seed           # demo warehouse + metric contracts + e2e agent (Postgres + Mongo)
#   scripts/e2e/e2e.sh smoke "Doanh thu thuần tháng này theo vùng"   # print SSE frames
#   scripts/e2e/e2e.sh test           # pytest -m e2e (API level)
#   scripts/e2e/e2e.sh ui             # Playwright: real Keycloak login → Hỏi dữ liệu → answer
#   scripts/e2e/e2e.sh status | logs [svc…] | token | down [-v]
#
# Layers: infra/docker-compose.yml (infra) + .app.yml (services) + .agent-test.yml
# (litellm → host ollama, OTEL off) + .local.yml (alembic migrate + tenant seed).
# Only the services one agent turn needs are started — not the KB tier.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
cd "$ROOT"

# Own compose project ⇒ own volumes. A developer's day-to-day stack (project
# "infra") keeps its Postgres/Keycloak/KB data untouched; E2E always starts from
# a fresh database so migrations + seed are reproducible.
E2E_PROJECT=${E2E_PROJECT:-dataagent-e2e}
COMPOSE=(docker compose -p "$E2E_PROJECT" --profile runtime -f infra/docker-compose.yml -f infra/docker-compose.app.yml
         -f infra/docker-compose.agent-test.yml -f infra/docker-compose.local.yml)
# dev (default): existing images + bind-mounted source, build only what is
# missing. build: rebuild every image (slow — full pip install per service).
E2E_MODE=${E2E_MODE:-dev}
if [ "$E2E_MODE" = "dev" ]; then COMPOSE+=(-f infra/docker-compose.e2e-dev.yml); fi
SERVICES=(postgres redis mongo kafka minio openbao litellm keycloak kong-migration kong
          migrate seed sandbox-executor workflow-runtime orchestrator memory-consumer)

TENANT=a0000000-0000-0000-0000-000000000001
export E2E_AGENT_ID=${E2E_AGENT_ID:-0000000e-2e00-4000-8000-000000000001}
export E2E_USER=${E2E_USER:-helios-engineer}
export E2E_PASS=${E2E_PASS:-H3lios@Eng001!}
export E2E_KONG=${E2E_KONG:-http://localhost:8000}
export E2E_KEYCLOAK=${E2E_KEYCLOAK:-http://localhost:8080}
KONG_ADMIN=${KONG_ADMIN:-http://localhost:8900}
OPENBAO=${OPENBAO:-http://localhost:8300}
OLLAMA=${OLLAMA:-http://localhost:11434}
OLLAMA_MODEL=${OLLAMA_MODEL:-qwen2.5:3b}     # what infra/litellm/config.agent-local.yaml routes to
DB_SECRET_KEY=e2e_dw_password
DB_SECRET_VALUE=sandbox_reader_dev_pw         # infra/sql/create-extra-dbs.sql
OUT=scripts/e2e/out
mkdir -p "$OUT"

# ── helpers ─────────────────────────────────────────────────────────────────
log()  { printf '\033[1;36m▶ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✔ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m⚠ %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m✖ %s\033[0m\n' "$*" >&2; exit 1; }

env_get() { grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2- || true; }
env_set() {  # env_set KEY VALUE — append or replace in .env
  if grep -qE "^$1=" .env 2>/dev/null; then
    sed -i.bak -E "s|^$1=.*|$1=$2|" .env && rm -f .env.bak
  else
    printf '\n%s=%s\n' "$1" "$2" >> .env
  fi
}
ensure_env() {  # ensure_env KEY DEFAULT — set only when empty
  local cur; cur=$(env_get "$1")
  if [ -z "$cur" ]; then env_set "$1" "$2"; ok ".env: $1 generated"; fi
}
wait_for() {  # wait_for "label" seconds cmd…
  local label=$1 secs=$2; shift 2
  for _ in $(seq 1 "$secs"); do
    if "$@" >/dev/null 2>&1; then ok "$label"; return 0; fi
    sleep 1
  done
  die "timeout waiting for $label"
}
py() { python3 -c "$@"; }

token() {
  curl -sf -X POST "$E2E_KEYCLOAK/realms/aeroflow/protocol/openid-connect/token" \
    -d grant_type=password -d client_id=aeroflow-frontend \
    -d "username=$E2E_USER" -d "password=$E2E_PASS" \
    | py 'import sys,json; print(json.load(sys.stdin).get("access_token",""))'
}

# ── steps ───────────────────────────────────────────────────────────────────
preflight() {
  log "preflight"
  command -v docker >/dev/null || die "docker not installed"
  docker info >/dev/null 2>&1 || die "docker daemon not running"
  command -v python3 >/dev/null || die "python3 required"
  [ -f .env ] || die ".env missing — copy .env.example first"

  # Secrets the fail-closed gateway chain needs (same value in every service).
  ensure_env GATEWAY_KEY "$(openssl rand -hex 24)"
  ensure_env SERVICE_AUTH_SECRET "$(openssl rand -hex 32)"
  ensure_env KAFKA_ADMIN_PASSWORD "admin-dev-pw"

  # LLM tier: host ollama with the model litellm is pinned to.
  if ! curl -sf "$OLLAMA/api/tags" >/dev/null; then
    warn "ollama not reachable at $OLLAMA — starting 'ollama serve' in the background"
    (nohup ollama serve > "$OUT/ollama.log" 2>&1 &)
    wait_for "ollama" 30 curl -sf "$OLLAMA/api/tags"
  fi
  if ! curl -sf "$OLLAMA/api/tags" | grep -q "\"$OLLAMA_MODEL\""; then
    log "pulling $OLLAMA_MODEL"; ollama pull "$OLLAMA_MODEL"
  fi
  ok "ollama has $OLLAMA_MODEL"
  # One resident model only — a second one on the same GPU swap-thrashes every
  # LLM call (AGENT-E2E.md §5). Unload anything else that is loaded.
  curl -sf "$OLLAMA/api/ps" | py '
import sys,json,urllib.request
for m in json.load(sys.stdin).get("models",[]):
    if m["name"] != sys.argv[1]:
        urllib.request.urlopen(urllib.request.Request("'"$OLLAMA"'/api/generate", data=json.dumps({"model":m["name"],"keep_alive":0}).encode(), headers={"Content-Type":"application/json"}))
        print("unloaded", m["name"])' "$OLLAMA_MODEL" || true
  ok "preflight done"
}

ensure_images() {  # dev mode: build only services whose image is not present
  local missing
  missing=$("${COMPOSE[@]}" config --format json 2>/dev/null | py '
import sys, json, subprocess
cfg = json.load(sys.stdin); proj = cfg.get("name", "infra")
wanted = set(sys.argv[1].split(","))
for name, svc in cfg["services"].items():
    if name not in wanted or "build" not in svc: continue
    image = svc.get("image") or f"{proj}-{name}"
    if subprocess.run(["docker","image","inspect",image],capture_output=True).returncode != 0:
        print(name)' "$(IFS=,; echo "${SERVICES[*]}")")
  if [ -n "$missing" ]; then
    warn "building missing images: $(echo $missing | tr '\n' ' ')"
    # shellcheck disable=SC2086
    "${COMPOSE[@]}" build $missing
  fi
}

evict_other_project() {  # container_name is fixed per service — another project's stack must be stopped first
  local others
  others=$(docker ps -a --filter "name=dataagent-" --format '{{.ID}} {{.Label "com.docker.compose.project"}}' | awk -v p="$E2E_PROJECT" '$2!="" && $2!=p {print $1}')
  if [ -n "$others" ]; then
    warn "removing dataagent-* containers of another compose project (their volumes are kept)"
    # shellcheck disable=SC2086
    docker rm -f $others >/dev/null
  fi
}

up() {
  log "docker compose up (${#SERVICES[@]} services, mode=$E2E_MODE, project=$E2E_PROJECT)"
  evict_other_project
  if [ "$E2E_MODE" = "dev" ]; then
    ensure_images
    "${COMPOSE[@]}" up -d --no-build "${SERVICES[@]}"
  else
    "${COMPOSE[@]}" up -d --build "${SERVICES[@]}"
  fi
  wait_for "postgres"  90 docker exec dataagent-postgres pg_isready -U admin
  # Keycloak (KC_DB_SCHEMA=keycloak) does not create its schema; the compose
  # init script that should is only run on a fresh volume — make it certain.
  docker exec dataagent-postgres psql -U admin -d dataagent -qc "CREATE SCHEMA IF NOT EXISTS keycloak; CREATE SCHEMA IF NOT EXISTS kong;" >/dev/null
  wait_for "kong admin" 120 curl -sf "$KONG_ADMIN/status"
  wait_for "keycloak"  180 curl -sf "$E2E_KEYCLOAK/realms/aeroflow/.well-known/openid-configuration"
  wait_for "litellm"   120 curl -sf http://localhost:4000/health/liveliness
  wait_for "openbao"   60  curl -s "$OPENBAO/v1/sys/health"
  for c in dataagent-workflow-runtime dataagent-orchestrator dataagent-sandbox-executor; do
    wait_for "$c running" 60 sh -c "docker inspect -f '{{.State.Status}}' $c | grep -q running"
  done
  # migrate/seed are one-shot: make sure they exited 0
  for c in migrate seed; do
    local code; code=$("${COMPOSE[@]}" ps -a --format '{{.Service}} {{.ExitCode}}' | awk -v s="$c" '$1==s{print $2}')
    [ "${code:-0}" = "0" ] || die "$c exited with $code — see: ${COMPOSE[*]} logs $c"
  done
  ok "stack is up"
}

setup_kong() {
  log "kong routes + JWT plugin + Keycloak dev passwords (infra/kong/kong-setup.sh)"
  KONG_ADMIN_URL="$KONG_ADMIN" KEYCLOAK_URL="$E2E_KEYCLOAK" KEYCLOAK_PUBLIC_URL="$E2E_KEYCLOAK" \
  KEYCLOAK_NETWORK_URL=http://keycloak:8080 GATEWAY_KEY="$(env_get GATEWAY_KEY)" \
    bash infra/kong/kong-setup.sh > "$OUT/kong-setup.log" 2>&1 || { tail -30 "$OUT/kong-setup.log"; die "kong-setup failed (log: $OUT/kong-setup.log)"; }
  ok "kong configured (log: $OUT/kong-setup.log)"
}

setup_openbao() {
  log "openbao: init/unseal, KV v2 'secret', read-only AppRole, e2e DB password"
  # Init/unseal over the API from the host. Keys go to $OUT/openbao-keys.json
  # (this stack's own volume; the file is git-ignored). A vault that is
  # initialised but whose keys we do not have cannot be unsealed — recreate it.
  local keys="$OUT/openbao-keys.json" health
  # The named volume is created root-owned while the server runs as `openbao`
  # (uid 100) — "failed to persist keyring: permission denied" on init.
  docker exec -u 0 dataagent-openbao chown -R openbao:openbao /openbao/data
  health=$(curl -s "$OPENBAO/v1/sys/health" || true)
  if echo "$health" | grep -q '"initialized":true' && [ ! -f "$keys" ]; then
    warn "OpenBao is initialised but $keys is missing — recreating its volume (E2E project only)"
    "${COMPOSE[@]}" rm -sf openbao >/dev/null
    docker volume rm "${E2E_PROJECT}_openbao_data" >/dev/null
    "${COMPOSE[@]}" up -d --no-build openbao >/dev/null
    wait_for "openbao" 60 curl -s "$OPENBAO/v1/sys/health"
    health=$(curl -s "$OPENBAO/v1/sys/health" || true)
  fi
  if echo "$health" | grep -q '"initialized":false'; then
    curl -sf -X POST -d '{"secret_shares":1,"secret_threshold":1}' "$OPENBAO/v1/sys/init" > "$keys"
    chmod 600 "$keys"; ok "OpenBao initialised → $keys"
  fi
  if curl -s "$OPENBAO/v1/sys/health" | grep -q '"sealed":true'; then
    local unseal; unseal=$(py 'import sys,json; print(json.load(open(sys.argv[1]))["keys_base64"][0])' "$keys")
    curl -sf -X POST -d "{\"key\":\"$unseal\"}" "$OPENBAO/v1/sys/unseal" >/dev/null; ok "OpenBao unsealed"
  fi
  local root; root=$(py 'import sys,json; print(json.load(open(sys.argv[1]))["root_token"])' "$keys")
  local H=(-H "X-Vault-Token: $root" -H "Content-Type: application/json")

  # KV v2 at secret/ (server mode does not mount it by default)
  if ! curl -sf "${H[@]}" "$OPENBAO/v1/sys/mounts" | grep -q '"secret/"'; then
    curl -sf "${H[@]}" -X POST -d '{"type":"kv","options":{"version":"2"}}' "$OPENBAO/v1/sys/mounts/secret" >/dev/null
    ok "mounted kv-v2 at secret/"
  fi
  # policy + approle (same shape as infra/openbao/openbao-approle-setup.sh)
  curl -sf "${H[@]}" -X POST -d '{"policy":"path \"secret/data/tenants/*\" { capabilities = [\"read\"] }"}' \
    "$OPENBAO/v1/sys/policies/acl/workflow-runtime-read-policy" >/dev/null
  curl -sf "${H[@]}" "$OPENBAO/v1/sys/auth" | grep -q '"approle/"' \
    || curl -sf "${H[@]}" -X POST -d '{"type":"approle"}' "$OPENBAO/v1/sys/auth/approle" >/dev/null
  curl -sf "${H[@]}" -X POST -d '{"token_policies":"workflow-runtime-read-policy","token_ttl":"1h","token_max_ttl":"4h"}' \
    "$OPENBAO/v1/auth/approle/role/workflow-runtime" >/dev/null
  local role_id secret_id
  role_id=$(curl -sf "${H[@]}" "$OPENBAO/v1/auth/approle/role/workflow-runtime/role-id" | py 'import sys,json; print(json.load(sys.stdin)["data"]["role_id"])')
  secret_id=$(curl -sf "${H[@]}" -X POST "$OPENBAO/v1/auth/approle/role/workflow-runtime/secret-id" | py 'import sys,json; print(json.load(sys.stdin)["data"]["secret_id"])')
  env_set OPENBAO_ROLE_ID "$role_id"; env_set OPENBAO_SECRET_ID "$secret_id"
  ok "AppRole workflow-runtime → .env (OPENBAO_ROLE_ID/OPENBAO_SECRET_ID)"

  # the demo warehouse password, where warehouse_secrets.py will look for it
  curl -sf "${H[@]}" -X POST -d "{\"data\":{\"value\":\"$DB_SECRET_VALUE\",\"note\":\"e2e sandbox_reader\"}}" \
    "$OPENBAO/v1/secret/data/tenants/$TENANT/$DB_SECRET_KEY" >/dev/null
  ok "secret/data/tenants/$TENANT/$DB_SECRET_KEY written"

  log "recreating workflow-runtime with the AppRole env"
  "${COMPOSE[@]}" up -d --no-deps workflow-runtime >/dev/null
  wait_for "workflow-runtime running" 60 sh -c "docker inspect -f '{{.State.Status}}' dataagent-workflow-runtime | grep -q running"
}

setup_kafka() {
  # Topics + per-principal ACLs (audit.data.access etc.). Without them the
  # runtime's audit producer waits 60 s for topic metadata on EVERY query.
  log "kafka topics + ACLs (infra/kafka/kafka-setup.sh)"
  KAFKA_CONTAINER=dataagent-kafka bash infra/kafka/kafka-setup.sh > "$OUT/kafka-setup.log" 2>&1 \
    || { tail -15 "$OUT/kafka-setup.log"; die "kafka-setup failed (log: $OUT/kafka-setup.log)"; }
  ok "kafka configured (log: $OUT/kafka-setup.log)"
}

setup_keycloak() {
  # The realm's browser flow is "Browser MFA": every browser login must enrol
  # an authenticator app, which Playwright cannot do. This E2E stack has its
  # own Keycloak (own volume), so switch it to the standard `browser` flow
  # (password + OTP only if the user has one). Production keeps MFA.
  log "keycloak: browser flow → 'browser' (E2E stack only)"
  local admin
  admin=$(curl -sf -X POST "$E2E_KEYCLOAK/realms/master/protocol/openid-connect/token" \
    -d grant_type=password -d client_id=admin-cli -d username=admin -d password=admin \
    | py 'import sys,json; print(json.load(sys.stdin).get("access_token",""))')
  [ -n "$admin" ] || die "could not get a Keycloak admin token (admin/admin)"
  curl -sf -X PUT "$E2E_KEYCLOAK/admin/realms/aeroflow" -H "Authorization: Bearer $admin" \
    -H "Content-Type: application/json" -d '{"browserFlow":"browser"}' >/dev/null
  ok "keycloak browser flow = browser"
}

setup() { setup_kong; setup_keycloak; setup_kafka; setup_openbao; ok "setup done"; }

seed() {
  log "seed: postgres (agent, metrics, secret row, demo warehouse)"
  docker exec -i dataagent-postgres psql -U admin -d dataagent -v ON_ERROR_STOP=1 -q < scripts/e2e/seed.sql
  log "seed: mongo (flow nodes/edges)"
  docker exec -i dataagent-mongo mongosh --quiet dataagent < scripts/e2e/seed-flow.js
  # graph + metric caches live in the runtime process — restart so a re-seed is visible
  "${COMPOSE[@]}" restart workflow-runtime >/dev/null
  wait_for "workflow-runtime running" 60 sh -c "docker inspect -f '{{.State.Status}}' dataagent-workflow-runtime | grep -q running"
  ok "seed done (agent $E2E_AGENT_ID)"
}

smoke() {
  local q=${1:-"Doanh thu thuần tháng này theo vùng"}
  local tok; tok=$(token); [ -n "$tok" ] || die "could not get a token for $E2E_USER (run: $0 setup)"
  log "POST /api/orchestrate/stream — \"$q\" (agent $E2E_AGENT_ID)"
  local body; body=$(py 'import json,sys; print(json.dumps({"query":sys.argv[1],"agent_id":sys.argv[2],"user_id":"e2e","messages":[]}))' "$q" "$E2E_AGENT_ID")
  curl -sN -X POST "$E2E_KONG/api/orchestrate/stream" \
    -H "Authorization: Bearer $tok" -H 'Content-Type: application/json' -d "$body" \
    | tee "$OUT/smoke.sse" | python3 scripts/e2e/sse_pretty.py
  echo; ok "raw frames saved to $OUT/smoke.sse"
}

test_api() {
  log "pytest -m e2e (LIVE_STACK=1)"
  local PY=${E2E_PYTHON:-python3}
  LIVE_STACK=1 LIVE_KONG_URL="$E2E_KONG" LIVE_KEYCLOAK_URL="$E2E_KEYCLOAK" \
    "$PY" -m pytest tests/integration/test_agent_answer_e2e.py -m e2e -v -s -p no:cacheprovider -W ignore::UserWarning "$@"
}

test_ui() {
  log "playwright UI e2e (real Keycloak login)"
  node scripts/e2e/ui.e2e.mjs
}

status() {
  "${COMPOSE[@]}" ps
  echo; for u in "$KONG_ADMIN/status" "$E2E_KEYCLOAK/realms/aeroflow" "http://localhost:4000/health/liveliness" "$OPENBAO/v1/sys/health" "$OLLAMA/api/tags"; do
    printf '%-60s %s\n' "$u" "$(curl -s -o /dev/null -w '%{http_code}' "$u")"
  done
}

case "${1:-}" in
  preflight) preflight ;;
  up)        up ;;
  setup)     setup ;;
  seed)      seed ;;
  keycloak)  setup_keycloak ;;
  smoke)     shift; smoke "$@" ;;
  test)      shift; test_api "$@" ;;
  ui)        test_ui ;;
  token)     token ;;
  status)    status ;;
  logs)      shift; "${COMPOSE[@]}" logs -f --tail=200 "$@" ;;
  down)      shift; "${COMPOSE[@]}" down "$@" ;;
  all)       preflight; up; setup; seed; test_api; test_ui ;;
  *) sed -n '2,14p' "$0"; exit 1 ;;
esac
