#!/usr/bin/env bash
# Quick-start llama-server trực tiếp (không cần Docker)
# Dùng binary đã compile sẵn tại /home/linhnt1/llama.cpp/build/bin/llama-server
set -euo pipefail

LLAMA_BIN="${LLAMA_BIN:-/home/linhnt1/llama.cpp/build/bin/llama-server}"
MODELS_DIR="${LLAMA_MODELS_DIR:-/home/linhnt1/llama.cpp/models}"
MODEL_FILE="${LLAMA_MODEL_FILE:-qwen2.5-7b-instruct-q4_k_m.gguf}"
MODEL_PATH="$MODELS_DIR/$MODEL_FILE"

HOST="${LLAMA_HOST:-0.0.0.0}"
PORT="${LLAMA_PORT:-8088}"
THREADS="${LLAMA_THREADS:-12}"
CTX="${LLAMA_CTX_SIZE:-8192}"
N_PARALLEL="${LLAMA_N_PARALLEL:-2}"

if [[ ! -f "$MODEL_PATH" ]]; then
  echo "Model not found: $MODEL_PATH"
  echo "Run: bash scripts/download-model.sh"
  exit 1
fi

export LD_LIBRARY_PATH="/home/linhnt1/llama.cpp/build/bin:${LD_LIBRARY_PATH:-}"

echo "==> Starting llama-server"
echo "    Model:   $MODEL_PATH"
echo "    Address: http://$HOST:$PORT"
echo "    Threads: $THREADS  |  Context: $CTX  |  Parallel: $N_PARALLEL"

exec "$LLAMA_BIN" \
  --model "$MODEL_PATH" \
  --host "$HOST" \
  --port "$PORT" \
  --threads "$THREADS" \
  --threads-batch "$THREADS" \
  --ctx-size "$CTX" \
  --parallel "$N_PARALLEL" \
  --batch-size 512 \
  --flash-attn on \
  --log-disable
