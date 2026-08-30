"""In-process loader/cache for LOCAL models — the "call directly" side of a
registry entry (the remote side goes over HTTP via backends.callers).

Pure loader: given a model name, return the loaded object, caching by name so each
model is loaded once. The registry holds the returned handle on its ModelEntry;
`backends.sync` is the only caller. Named model_store (not "models") to avoid
confusion with the ./models weights directory."""
from __future__ import annotations

import logging

from sentence_transformers import CrossEncoder, SentenceTransformer

from device import detect_device

log = logging.getLogger(__name__)

_embed: dict[str, SentenceTransformer] = {}
_rerank: dict[str, CrossEncoder] = {}


def load_embed(name: str) -> SentenceTransformer:
    """Return the SentenceTransformer for `name`, loading + caching on first use."""
    model = _embed.get(name)
    if model is None:
        dev = detect_device()
        log.info("Loading embedding model %s on device=%s …", name, dev)
        model = _embed[name] = SentenceTransformer(name, device=dev)
        log.info("Embedding model %s ready", name)
    return model


def load_rerank(name: str) -> CrossEncoder:
    """Return the CrossEncoder reranker for `name`, loading + caching on first use."""
    model = _rerank.get(name)
    if model is None:
        dev = detect_device()
        log.info("Loading reranker model %s on device=%s …", name, dev)
        model = _rerank[name] = CrossEncoder(name, device=dev)
        log.info("Reranker model %s ready", name)
    return model
