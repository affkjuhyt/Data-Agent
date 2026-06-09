#!/usr/bin/env bash
# Quick-start llama-server trực tiếp (không cần Docker)
# Dùng binary đã compile sẵn tại /home/linhnt1/llama.cpp/build/bin/llama-server
set -euo pipefail

LLAMA_BIN="${LLAMA_BIN:-/home/linhnt1/llama.cpp/build/bin/llama-server}"
MODELS_DIR="${LLAMA_MODELS_DIR:-/home/linhnt1/llama.cpp/models}"
MODEL_FILE="${LLAMA_MODEL_FILE:-qwen3-8b-instruct-q4_k_m.gguf}"
MODEL_PATH="$MODELS_DIR/$MODEL_FILE"

HOST="${LLAMA_HOST:-0.0.0.0}"
PORT="${LLAMA_PORT:-8088}"
# Dùng tất cả 16 physical cores (không có hyperthreading trên máy này)
THREADS="${LLAMA_THREADS:-16}"
CTX="${LLAMA_CTX_SIZE:-8192}"
# Tăng lên 4 slot song song — RAM còn 117GB, mỗi slot KV cache ~500MB
N_PARALLEL="${LLAMA_N_PARALLEL:-4}"

if [[ ! -f "$MODEL_PATH" ]]; then
  echo "Model not found: $MODEL_PATH"
  echo "Run: bash scripts/download-model.sh"
  exit 1
fi

export LD_LIBRARY_PATH="/home/linhnt1/llama.cpp/build/bin:${LD_LIBRARY_PATH:-}"

echo "==> Starting llama-server (CPU-optimized)"
echo "    Model:    $MODEL_PATH"
echo "    Address:  http://$HOST:$PORT"
echo "    Threads:  $THREADS  |  Context: $CTX  |  Parallel: $N_PARALLEL"

exec "$LLAMA_BIN" \
  --model        "$MODEL_PATH" \
  --host         "$HOST" \
  --port         "$PORT" \
  \
  --threads       "$THREADS" \
  --threads-batch "$THREADS" \
  --prio          2 \
  --prio-batch    2 \
  --poll          100 \
  --cpu-strict    0 \
  \
  --ctx-size     "$CTX" \
  --parallel     "$N_PARALLEL" \
  --keep         -1 \
  \
  --batch-size   2048 \
  --ubatch-size  512 \
  \
  --cache-type-k q8_0 \
  --cache-type-v q8_0 \
  \
  --no-mmap \
  --mlock \
  \
  --flash-attn on \
  --log-disable
