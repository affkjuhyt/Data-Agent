"""How far each Data Purpose may travel through the pipeline.

Only Production data belongs in the knowledge base (Gold: embeddings + graph
that agents search). Training and Evaluation data stop at Silver — parsed and
kept in Postgres, never embedded — so it cannot mix into answers, and the
steward still sees it in Data Layers / Source Registry. The tag was already
hidden from search by a Qdrant filter; this keeps it from being written there
at all."""
from __future__ import annotations

DEFAULT_PURPOSE = "production"

MAX_TIER_BY_PURPOSE: dict[str, str] = {
    "production": "gold",
    "training": "silver",
    "evaluation": "silver",
}


def reaches_gold(purpose: str | None) -> bool:
    return MAX_TIER_BY_PURPOSE.get(purpose or DEFAULT_PURPOSE, "gold") == "gold"
