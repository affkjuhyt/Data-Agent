# model/vllm — Qwen on Colab GPU via vLLM

Alternative LLM backend to the local llama.cpp service (`model/ollama`, which stays
untouched): **Qwen2.5-Instruct** served by **vLLM** on a Google Colab GPU, exposed to
the local stack through a **Cloudflare quick tunnel**.

## Files

| File | Purpose |
|---|---|
| `vllm_qwen_colab.ipynb` | Upload to Colab, Run all — installs vLLM, serves Qwen, opens the tunnel |
| `schemas.py` | JSON Schemas equivalent to the GBNF grammars in `backend/services/kb-ingestion-worker/llama_client.py` |
| `test_endpoint.py` | Smoke test against the tunnel URL (stdlib only) |

## Quickstart

1. Upload `vllm_qwen_colab.ipynb` to [Colab](https://colab.research.google.com), pick a
   **GPU runtime** (L4 recommended on Pro; A100 auto-selects Qwen2.5-14B-AWQ), **Run all**.
2. The banner cell prints `TUNNEL_URL` and `API_KEY`.
3. Verify from this repo:

   ```bash
   python model/vllm/test_endpoint.py https://xxx.trycloudflare.com --api-key kb-...
   ```

## How JSON enforcement works here

The llama.cpp service enforces JSON with hand-written **GBNF grammars**
(`grammar_gbnf` payload field). vLLM does the same token-masking (via xgrammar) from a
standard **JSON Schema** in the OpenAI request body — no custom fields:

```jsonc
POST {TUNNEL_URL}/v1/chat/completions
{
  "model": "Qwen/Qwen2.5-7B-Instruct",
  "messages": [...],
  "response_format": {
    "type": "json_schema",
    "json_schema": { "name": "entity_intent", "strict": true, "schema": { ... } }
  }
}
```

`schemas.py` holds the two schemas (`ENTITY_INTENT_SCHEMA`, `RELATIONSHIP_SCHEMA`) plus a
`response_format()` helper. `{"type": "json_object"}` (any valid JSON, no schema) also works.

Two things the llama.cpp path needed that vanish here:

- **X-System-Type KV snapshots** → vLLM `--enable-prefix-caching` caches repeated system
  prompts automatically.
- **GBNF single-line rules / identifier restrictions** → plain JSON Schema.

## Wiring the worker (not done yet)

`backend/services/kb-ingestion-worker/llama_client.py` speaks the Ollama-shaped API
(`/api/generate`, `/api/chat` + GBNF) and is untouched. To use this backend the worker
needs a sibling client that calls `/v1/chat/completions` with `response_format` from
`schemas.py`, selected via env (e.g. `LLM_BACKEND=vllm`, `VLLM_BASE_URL=<tunnel>`,
`VLLM_API_KEY=<key>`).

## Troubleshooting

**`ImportError: libcudart.so.13`** — the newest vLLM PyPI wheel links against CUDA 13,
but Colab's driver only supports CUDA 12.x
([vllm#43435](https://github.com/vllm-project/vllm/issues/43435)). The install cell
handles this: it reads the driver's CUDA version from `nvidia-smi` and installs the
matching `+cu12x` wheel from vLLM's GitHub Releases with torch from the matching
PyTorch index. If you install manually, don't use plain `pip install vllm`.

## Caveats

- **New URL every run** — quick tunnels mint a random `*.trycloudflare.com` per session.
- **Colab session limits** — Pro runtimes last up to ~24 h, disconnect on idle. Dev/testing
  only.
- **Keep `--api-key`** — the tunnel URL is reachable from the public internet.
- **Weights format** — vLLM pulls HF safetensors from the Hub; it does not use the local
  GGUF file in `model/ollama/weights/`.
