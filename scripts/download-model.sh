#!/usr/bin/env bash
set -euo pipefail

MODELS_DIR="${LLAMA_MODELS_DIR:-/home/linhnt1/llama.cpp/models}"
MODEL_FILE="${LLAMA_MODEL_FILE:-qwen2.5-7b-instruct-q4_k_m.gguf}"
HF_REPO="${HF_REPO:-bartowski/Qwen2.5-7B-Instruct-GGUF}"
HF_FILE="${HF_FILE:-Qwen2.5-7B-Instruct-Q4_K_M.gguf}"

TARGET="$MODELS_DIR/$MODEL_FILE"

echo "==> Models directory: $MODELS_DIR"
mkdir -p "$MODELS_DIR"

if [[ -f "$TARGET" ]]; then
  echo "==> Model already exists: $TARGET"
  exit 0
fi

echo "==> Downloading $HF_REPO / $HF_FILE ..."
echo "    This is ~4.7GB, please wait..."

# Try huggingface-cli first, fall back to wget
if command -v hf &>/dev/null; then
  hf download "$HF_REPO" "$HF_FILE" --local-dir "$MODELS_DIR"
  if [[ "$HF_FILE" != "$MODEL_FILE" ]]; then
    mv "$MODELS_DIR/$HF_FILE" "$TARGET"
  fi
elif command -v wget &>/dev/null; then
  wget -q --show-progress \
    "https://huggingface.co/$HF_REPO/resolve/main/$HF_FILE" \
    -O "$TARGET"
else
  curl -L --progress-bar \
    "https://huggingface.co/$HF_REPO/resolve/main/$HF_FILE" \
    -o "$TARGET"
fi

echo "==> Done: $TARGET ($(du -sh "$TARGET" | cut -f1))"
