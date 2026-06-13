"""
LLM backend dispatcher — gold/silver/conflict_resolver import this instead of a
concrete client.

Backend selection (config.py):
  LLM_BACKEND=vllm   → vllm_client  (OpenAI-compatible vLLM, schema-guided JSON;
                                     e.g. the Colab server from model/vllm/)
  LLM_BACKEND=llama  → llama_client (Ollama-compatible llama.cpp, GBNF grammars)

When LLM_BACKEND is unset, vllm is chosen automatically iff VLLM_BASE_URL is set,
so pointing the worker at a tunnel URL is the only switch needed.
"""
from __future__ import annotations

import logging

import config as cfg

log = logging.getLogger(__name__)

if cfg.LLM_BACKEND == "vllm":
    import vllm_client as _backend
    log.info("LLM backend: vllm (%s)", cfg.VLLM_BASE_URL or "VLLM_BASE_URL not set!")
else:
    import llama_client as _backend
    log.info("LLM backend: llama (%s)", cfg.LLAMA_BASE_URL)

BACKEND = cfg.LLM_BACKEND

generate_json  = _backend.generate_json
generate_plain = _backend.generate_plain
chat_plain     = _backend.chat_plain
chat_json      = _backend.chat_json
