# Local GPU LLM weight (llama-cpp-python)

The `llama` service (compose profile `local-llm`) loads **one GGUF** mounted at
`/models/model.gguf` and offloads all layers to the GPU (`N_GPU_LAYERS=-1`). Drop a
GGUF here named exactly `model.gguf`.

## Recommended for NVIDIA RTX 3060 (12 GB VRAM)

**Qwen2.5-7B-Instruct, quant `Q4_K_M` (~4.7 GB).**
All layers fit on the GPU; with the default `N_CTX=4096` (f16 KV cache) total VRAM use
is ~6–7 GB, leaving headroom to raise context to 8192 if desired. Good
quality/Vietnamese balance for the RAG entity-extraction / canonicalize prompts.

Single-file download (no split parts — simplest to mount as `model.gguf`):

```powershell
# pip install -U "huggingface_hub[cli]"
huggingface-cli download bartowski/Qwen2.5-7B-Instruct-GGUF `
  Qwen2.5-7B-Instruct-Q4_K_M.gguf `
  --local-dir model/ollama/weights
# then rename to the path the service expects:
Move-Item model/ollama/weights/Qwen2.5-7B-Instruct-Q4_K_M.gguf model/ollama/weights/model.gguf
```

## Other fits for 12 GB (pick one, rename to `model.gguf`)

| Model / quant                 | File size | Notes |
|-------------------------------|-----------|-------|
| Qwen2.5-7B-Instruct `Q5_K_M`  | ~5.4 GB   | a bit more quality, still comfortable |
| Qwen2.5-7B-Instruct `Q6_K`    | ~6.3 GB   | near-fp16 quality, fits at 4096 ctx |
| Qwen2.5-3B-Instruct `Q4_K_M`  | ~2.0 GB   | fastest, lower quality, lots of headroom |
| Qwen2.5-14B-Instruct `Q4_K_M` | ~9.0 GB   | **tight** — only at 4096 ctx, OOM risk; not the default |

Avoid fp16 7B (~15 GB) — exceeds 12 GB VRAM.

The GGUF carries Qwen's chat template in its metadata, so `/api/chat` formats prompts
correctly with no code change. `model.gguf` is gitignored (large binary) — each machine
downloads its own.
