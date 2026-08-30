# llama-server — local LLM backend

A CPU `llama-server` (llama.cpp) serving **Qwen3-8B** with an OpenAI-compatible API.
This is the **local** alternative to the Colab/cloudflared tunnels in
[`../vllm`](../vllm) — same interface, no public URL, runs on the host.

## What `model_service` sees

`model_service` (deploy/docker/model_service) treats this **exactly like a cloudflare/Colab
tunnel**. Its LLM gateway (`remote.py`) does:

```
POST {REMOTE_LLM_URL}/v1/chat/completions     # generate
GET  {REMOTE_LLM_URL}/v1/models               # auto-discover model id
```

It does not know or care whether `REMOTE_LLM_URL` is a tunnel, Colab, or this local
server — point it at the right host:port and it works:

| Backend | `REMOTE_LLM_URL` |
|---|---|
| Colab vLLM / cloudflared | `https://<tunnel>.trycloudflare.com` |
| **This llama-server**, model_service in a container | `http://host.docker.internal:8088` |
| This llama-server, model_service on the host | `http://localhost:8088` |

The `kb-e2e` overlay already defaults `REMOTE_LLM_URL` to
`http://host.docker.internal:8088`, so starting the server below is all that's needed.

## Usage

```bash
# 1. Download the GGUF weights (~4.7 GB, once)
bash model/llama-server/download-model.sh

# 2. Start the server on :8088 (host, not Docker — start BEFORE the stack)
bash model/llama-server/start-llama.sh &

# 3. Verify
curl http://localhost:8088/health
curl http://localhost:8088/v1/models

# 4. (optional) Benchmark
python3 model/llama-server/benchmark_llm.py
```

## Configuration (env overrides)

| Var | Default | Purpose |
|---|---|---|
| `LLAMA_BIN` | `/home/<user>/llama.cpp/build/bin/llama-server` | compiled binary path |
| `LLAMA_MODELS_DIR` | `/home/<user>/llama.cpp/models` | where the GGUF lives |
| `LLAMA_MODEL_FILE` | `qwen3-8b-instruct-q4_k_m.gguf` | model file name |
| `LLAMA_PORT` | `8088` | listen port (match `REMOTE_LLM_URL`) |
| `LLAMA_THREADS` | `16` | CPU threads |
| `LLAMA_CTX_SIZE` | `8192` | context window |
| `LLAMA_N_PARALLEL` | `4` | concurrent slots |

`--embeddings` is enabled, so `/v1/embeddings` is also available if you ever want to
route `REMOTE_EMBED_URL` here too (kb-e2e currently keeps embeddings on the small
local model / a separate remote).
