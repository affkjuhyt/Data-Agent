#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# model-backend.sh — switch where model-service runs each model type, via env.
#
# model-service is the single GATEWAY. Callers (ingestion, lightrag) never know the
# backend; they just hit model-service /embed, /rerank, /generate. This script flips
# those three between LOCAL (CPU models baked in the image) and a REMOTE Colab GPU
# server (cloudflared tunnel), then recreates model-service. No file edits.
#
# USAGE
#   ./model-backend.sh status
#   ./model-backend.sh local
#   ./model-backend.sh colab  --embed-url URL --embed-key KEY \
#                             --llm-url URL --llm-key KEY [--rerank-url URL]
#       (rerank shares the embed tunnel unless --rerank-url is given)
#       (omit --embed-* to leave embed local; omit --llm-* to leave LLM local)
#
# CAVEAT: the EMBED model determines the Qdrant vector dimension. Switching embed to
# a DIFFERENT model/dim requires re-embedding the corpus (reembed.py) or searches
# fail with a dim mismatch. Rerank and LLM switches are safe any time (no index).
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
COMPOSE="docker compose -f ${HERE}/docker-compose.kb-ui-testing.yml"
SVC="model-service"
CONTAINER="kb-test-model-service"

recreate() {
  echo "recreating ${SVC} …"
  $COMPOSE up -d --no-deps "$SVC"
  echo -n "waiting for healthy "
  for _ in $(seq 1 45); do
    s=$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo none)
    [ "$s" = healthy ] && { echo " ok"; break; }
    echo -n "."; sleep 2
  done
  echo
}

case "${1:-status}" in
  status)
    echo "── routing (env on ${CONTAINER}) ──"
    docker exec "$CONTAINER" env 2>/dev/null \
      | grep -E 'REMOTE_EMBED_URL|REMOTE_RERANK_URL|REMOTE_LLM_URL|EMBED_MODEL|RERANK_MODEL' \
      | sort | sed 's/^/  /'
    echo "── reachability ──"
    for pair in "embed:/health" "llm:/v1/models"; do :; done
    eu=$(docker exec "$CONTAINER" sh -c 'echo $REMOTE_EMBED_URL')
    lu=$(docker exec "$CONTAINER" sh -c 'echo $REMOTE_LLM_URL')
    lk=$(docker exec "$CONTAINER" sh -c 'echo $REMOTE_LLM_KEY')
    if [ -n "$eu" ]; then
      ec=$(curl -s -m 8 -o /dev/null -w '%{http_code}' "$eu/health" || true)
      echo "  embed tunnel  $eu  ->  HTTP ${ec}  ($([ "$ec" = 000 ] && echo DOWN || echo up))"
    else echo "  embed: LOCAL"; fi
    if [ -n "$lu" ]; then
      lc=$(curl -s -m 8 -o /dev/null -w '%{http_code}' -H "Authorization: Bearer ${lk}" "$lu/v1/models" || true)
      echo "  llm   tunnel  $lu  ->  HTTP ${lc}  ($([ "$lc" = 000 ] && echo DOWN || echo up))"
    else echo "  llm:   LOCAL"; fi
    ;;

  local)
    export REMOTE_EMBED_URL= REMOTE_RERANK_URL= REMOTE_API_KEY= REMOTE_LLM_URL= REMOTE_LLM_KEY=
    echo "→ all model types LOCAL (CPU). NOTE: local embed = e5-small (384-d); if the index"
    echo "  was built with a 1024-d model you must reembed.py or searches will dim-mismatch."
    recreate
    ;;

  colab)
    shift
    EMBED_URL= EMBED_KEY= RERANK_URL= LLM_URL= LLM_KEY=
    while [ $# -gt 0 ]; do
      case "$1" in
        --embed-url)  EMBED_URL="$2"; shift 2;;
        --embed-key)  EMBED_KEY="$2"; shift 2;;
        --rerank-url) RERANK_URL="$2"; shift 2;;
        --llm-url)    LLM_URL="$2"; shift 2;;
        --llm-key)    LLM_KEY="$2"; shift 2;;
        *) echo "unknown flag: $1" >&2; exit 2;;
      esac
    done
    # rerank shares the embed tunnel unless overridden
    [ -z "$RERANK_URL" ] && RERANK_URL="$EMBED_URL"
    export REMOTE_EMBED_URL="$EMBED_URL"
    export REMOTE_RERANK_URL="$RERANK_URL"
    export REMOTE_API_KEY="$EMBED_KEY"
    export REMOTE_LLM_URL="$LLM_URL"
    export REMOTE_LLM_KEY="$LLM_KEY"
    echo "→ embed:  ${EMBED_URL:-LOCAL}"
    echo "→ rerank: ${RERANK_URL:-LOCAL}"
    echo "→ llm:    ${LLM_URL:-LOCAL}"
    recreate
    ;;

  *) echo "usage: $0 {status|local|colab ...}" >&2; exit 2;;
esac
