#!/usr/bin/env bash
# Ingestion throughput test — measures docs/sec through the medallion pipeline
# (bronze → silver → gold). Unlike the CCU tests (retrieval, LLM bypassed), this
# deliberately drives the LLM extraction + embedding path, so it measures the
# ingestion tier's real capacity — which is gated by the single Kafka worker +
# model-service /generate, NOT by HTTP (promote returns instantly; the worker does
# the work asynchronously). See KB-SCALE-10K.md §6.
#
# Stages per doc: POST /bronze (sync) → POST /promote/{id}/silver (async, wait for
# "queued for gold") → POST /promote/{id}/gold (async, wait for status PUBLISHED).
# Throughput = N / wall-clock(first bronze → last PUBLISHED).
#
#   N=6 scripts/loadtest/ingestion_throughput.sh
#
# Env: N (docs, default 6), API (default http://localhost:8050 = kb-backend direct),
#      TENANT, TIMEOUT (per-stage seconds, default 240).
set -o pipefail  # not -u: empty associative-array refs abort under set -u

API=${API:-http://localhost:8050}
TENANT=${TENANT:-a0000000-0000-0000-0000-000000000001}
N=${N:-6}
TIMEOUT=${TIMEOUT:-240}

H=(-H "X-Kong-Verified: true" -H "X-Tenant-Id: $TENANT"
   -H "X-User-Id: 11111111-1111-1111-1111-111111111111"
   -H "X-User-Name: platform-admin"
   -H "X-User-Roles: platform-admin,tenant-admin,kb-admin,engineer")

TS=$(date +%s)
# Use an external document if DOC_FILE is set (e.g. a large multi-chunk file);
# otherwise generate a small single-chunk doc.
if [ -n "${DOC_FILE:-}" ] && [ -f "${DOC_FILE}" ]; then
  DOC="${DOC_FILE}"; OWN_DOC=0
else
  OWN_DOC=1
  DOC=$(mktemp /tmp/ingdoc.XXXXXX.md)
  cat > "$DOC" <<'EOF'
# Nghị định về bảo vệ dữ liệu cá nhân

Nghị định này quy định về điều kiện bảo vệ dữ liệu cá nhân, trách nhiệm của tổ
chức, cá nhân xử lý dữ liệu, và quyền của chủ thể dữ liệu. Tổ chức xử lý dữ liệu
phải có sự đồng ý của chủ thể dữ liệu trước khi thu thập. Chủ thể dữ liệu có
quyền yêu cầu xóa, chỉnh sửa và rút lại sự đồng ý. Việc chuyển dữ liệu ra nước
ngoài phải tuân thủ quy định về đánh giá tác động. Cơ quan chuyên trách có trách
nhiệm thanh tra, kiểm tra và xử lý vi phạm theo quy định của pháp luật.
EOF
fi
echo "  doc: $DOC ($(wc -c < "$DOC" | tr -d ' ') bytes)"

log_of() { curl -s "${H[@]}" "$API/api/pipeline/ingestion/documents/$1/logs"; }

echo "=== INGESTION THROUGHPUT — N=$N docs, API=$API ==="
declare -a IDS
t_start=$(date +%s.%N)

# --- Stage 1: bronze (sync) for all N, unique names ---
for i in $(seq 1 "$N"); do
  name="loadtest-$TS-$i-$RANDOM"
  id=$(curl -s "${H[@]}" -X POST "$API/api/pipeline/ingestion/bronze" \
    -F "file=@$DOC;type=text/markdown" -F "name=$name" \
    -F "abstract=ingestion throughput test doc $i" \
    -F "language=vietnamese" -F "source_type=doc" \
    -F 'doc_metadata={"source_type":"doc","doc_type":"policy","author":null,"published_date":null}' \
    | python3 -c "import sys,json;print(json.load(sys.stdin).get('data_id',''))" 2>/dev/null)
  if [ -z "$id" ]; then echo "  bronze FAILED for doc $i"; else IDS+=("$id"); fi
done
t_bronze=$(date +%s.%N)
echo "  bronze: ${#IDS[@]}/$N ok in $(echo "$t_bronze - $t_start" | bc)s"

# --- Stage 2: promote all to silver, wait for "queued for gold" ---
for id in "${IDS[@]}"; do curl -s "${H[@]}" -X POST "$API/api/pipeline/ingestion/promote/$id/silver" >/dev/null; done
echo "  silver: promoted ${#IDS[@]}, waiting..."
declare -A SILVER_DONE
deadline=$(( $(date +%s) + TIMEOUT ))
while [ ${#SILVER_DONE[@]} -lt ${#IDS[@]} ] && [ "$(date +%s)" -lt "$deadline" ]; do
  for id in "${IDS[@]}"; do
    [ -n "${SILVER_DONE[$id]:-}" ] && continue
    l=$(log_of "$id")
    if echo "$l" | grep -q "queued for gold"; then SILVER_DONE[$id]=1
    elif echo "$l" | grep -q '"status": *"FAILED"'; then SILVER_DONE[$id]=FAIL; fi
  done
  sleep 3
done
t_silver=$(date +%s.%N)
echo "  silver done: ${#SILVER_DONE[@]}/${#IDS[@]} in $(echo "$t_silver - $t_bronze" | bc)s"

# --- Stage 3: promote all to gold, wait for PUBLISHED ---
for id in "${IDS[@]}"; do [ "${SILVER_DONE[$id]:-}" = "1" ] && curl -s "${H[@]}" -X POST "$API/api/pipeline/ingestion/promote/$id/gold" >/dev/null; done
echo "  gold: promoted, waiting for PUBLISHED..."
declare -A GOLD_DONE
deadline=$(( $(date +%s) + TIMEOUT ))
while [ ${#GOLD_DONE[@]} -lt ${#IDS[@]} ] && [ "$(date +%s)" -lt "$deadline" ]; do
  for id in "${IDS[@]}"; do
    [ -n "${GOLD_DONE[$id]:-}" ] && continue
    l=$(log_of "$id")
    if echo "$l" | grep -q '"status": *"PUBLISHED"'; then GOLD_DONE[$id]=1
    elif echo "$l" | grep -q '"status": *"FAILED"'; then GOLD_DONE[$id]=FAIL; fi
  done
  sleep 3
done
t_end=$(date +%s.%N)

pub=0; for id in "${IDS[@]}"; do [ "${GOLD_DONE[$id]:-}" = "1" ] && pub=$((pub+1)); done
wall=$(echo "$t_end - $t_start" | bc)
gold_wall=$(echo "$t_end - $t_silver" | bc)
echo ""
echo "=== KẾT QUẢ ==="
printf "  PUBLISHED:        %d/%d\n" "$pub" "$N"
printf "  wall (bronze→gold): %.1fs\n" "$wall"
printf "  silver stage:     %.1fs\n" "$(echo "$t_silver - $t_bronze" | bc)"
printf "  gold stage:       %.1fs\n" "$gold_wall"
if [ "$pub" -gt 0 ]; then
  printf "  throughput (e2e): %.3f docs/s  (%.1fs/doc)\n" "$(echo "scale=3; $pub / $wall" | bc)" "$(echo "scale=1; $wall / $pub" | bc)"
fi
[ "${OWN_DOC:-1}" = "1" ] && rm -f "$DOC"
