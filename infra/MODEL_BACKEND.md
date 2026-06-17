# Model backend — how to switch local ↔ Colab GPU (CLI)

## Architecture (why this is one switch)

`model-service` is the **single model gateway**. Everything that needs a model —
the **ingestion worker** and the **lightrag retrieval pipeline** — calls only:

| endpoint | used for | callers |
|---|---|---|
| `POST /embed` | text → vector | lightrag query-embed, reembed.py, load_corpus, gold/silver |
| `POST /rerank` | rerank candidates | lightrag rerank step |
| `POST /generate` | LLM text/JSON | lightrag `canonicalize`, ingestion `llm_client` |

**Callers never know the backend.** They send generic input (`texts`, or
`prompt`/`messages` + optional `json_schema`). model-service decides *where* each
call runs, per env. So switching the LLM/embed/rerank backend is a model-service
env change — no code, no caller change.

## The env knobs (on `model-service`)

| var | effect |
|---|---|
| `REMOTE_EMBED_URL` | set → `/embed` forwards to this server; empty → local CPU model (`EMBED_MODEL`) |
| `REMOTE_RERANK_URL` | set → `/rerank` forwards there; empty → local CPU reranker |
| `REMOTE_API_KEY` | bearer key for the embed/rerank server |
| `REMOTE_LLM_URL` | set → `/generate` forwards to this OpenAI-compatible server (e.g. Colab vLLM); empty → 503 (no local LLM) |
| `REMOTE_LLM_KEY` | bearer key for the LLM server |

## CLI (the easy way) — `infra/model-backend.sh`

```bash
# show current routing + tunnel reachability
./infra/model-backend.sh status

# point embed/rerank + LLM at Colab GPU tunnels (one command)
./infra/model-backend.sh colab \
  --embed-url https://<embed-tunnel>.trycloudflare.com --embed-key kb-emb-xxxx \
  --llm-url   https://<vllm-tunnel>.trycloudflare.com  --llm-key  kb-IT-xxxx
#   (rerank shares the embed tunnel; add --rerank-url to override)
#   (omit --embed-* to keep embed local; omit --llm-* to keep LLM local)

# revert everything to local CPU
./infra/model-backend.sh local
```

The script exports the env vars and runs `docker compose up -d --no-deps model-service`,
then waits for healthy. (Windows: run it from Git Bash.)

## Running the Colab GPU servers

- **Embed + rerank:** open `model/embedding/embed_rerank_colab.ipynb` in Colab (GPU
  runtime), run all cells → it prints `TUNNEL_URL` + `API_KEY`. Use those as
  `--embed-url` / `--embed-key`.
- **LLM:** open `model/vllm/vllm_qwen_colab.ipynb` → prints its `TUNNEL_URL` + `API_KEY`.
  Use as `--llm-url` / `--llm-key`.

## ⚠️ The one rule: embed dim must match the index

The embed model fixes the Qdrant vector dimension. The corpus is currently indexed
at **1024-d (AITeamVN)**.
- Switching **LLM** or **rerank**: safe any time (no index dependency).
- Switching **embed** to a different model/dim (e.g. back to local e5-small 384-d):
  you **must re-embed** first, or every search fails with a dim mismatch:
  ```
  ./infra/model-backend.sh colab --embed-url <new> --embed-key <key> ...
  docker cp testing/benchmark/reembed.py kb-test-backend:/tmp/
  docker exec kb-test-backend python /tmp/reembed.py     # recreates collections at the new dim
  ```

## Raw docker compose (no script)

```bash
REMOTE_EMBED_URL=https://… REMOTE_RERANK_URL=https://… REMOTE_API_KEY=… \
REMOTE_LLM_URL=https://…  REMOTE_LLM_KEY=… \
docker compose -f infra/docker-compose.kb-ui-testing.yml up -d --no-deps model-service
```

## Troubleshooting

- Retrieval returns 500 / "Retrieval failed" → run `status`. If a tunnel shows
  **DOWN (HTTP 000)** the Colab session/quick-tunnel died. Restart that Colab
  notebook (it prints a NEW url), then re-point with `colab --embed-url <new> …`.
- `/generate` → 503 "no LLM backend configured" → `REMOTE_LLM_URL` is empty and
  there is no local LLM; set the LLM tunnel.
- Quick-tunnels rotate URLs on every Colab restart — always re-run the `colab`
  command with the fresh URL; nothing else needs to change.
