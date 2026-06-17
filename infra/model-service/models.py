"""
Module-level model state.

Startup path:
  load(default_embed, rerank_name) — loads the default embed model + reranker
  synchronously so the service can serve immediately.

Background path (via registry.register_with_retry):
  ensure_loaded(model_name) — called for every model row found in KBModel;
  no-ops if the model is already in _embed_models.

Lookup:
  get_embed_for_name(name)  — returns the SentenceTransformer for a specific model
  get_embed()               — returns the default embed model (cfg.EMBED_MODEL)
"""
from __future__ import annotations

import logging

from sentence_transformers import CrossEncoder, SentenceTransformer

log = logging.getLogger(__name__)

# model_name (str) → loaded SentenceTransformer
_embed_models: dict[str, SentenceTransformer] = {}
_rerank_model: CrossEncoder | None = None


def load(default_embed_name: str, rerank_name: str) -> None:
    """Load the default embed model + reranker at startup."""
    global _rerank_model
    ensure_loaded(default_embed_name)
    log.info("Loading reranker model %s …", rerank_name)
    _rerank_model = CrossEncoder(rerank_name)
    log.info("Startup models ready")


def ensure_loaded(model_name: str) -> None:
    """Load model_name if it is not already in the store."""
    if model_name in _embed_models:
        return
    log.info("Loading embedding model %s …", model_name)
    _embed_models[model_name] = SentenceTransformer(model_name)
    log.info("Embedding model %s ready", model_name)


def get_embed_for_name(name: str) -> SentenceTransformer | None:
    return _embed_models.get(name)


def get_embed() -> SentenceTransformer:
    """Return the default embed model. Raises if nothing is loaded yet."""
    if not _embed_models:
        raise RuntimeError("No embedding models loaded — call load() first")
    import config as cfg
    m = _embed_models.get(cfg.EMBED_MODEL)
    if m:
        return m
    return next(iter(_embed_models.values()))


def get_rerank() -> CrossEncoder:
    if _rerank_model is None:
        raise RuntimeError("Reranker not loaded — call load() first")
    return _rerank_model


def is_ready() -> bool:
    return bool(_embed_models) and _rerank_model is not None
