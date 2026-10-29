"""agent_config.data_scope read as an allow-list, shared by an agent's runs (runs.py) and the Q&A
path when it answers for an agent version's eval run (questions.py, agent_evals.py)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from libs.dataagent_db.models import catalog
from services.worker_agent.understand.resolver import Vocabulary


@dataclass(frozen=True)
class DataScope:
    """agent_config.data_scope as an allow-list (BR: never more than the owner may read): the
    metrics (code or id), tables (schema.table or id) and sources (name or id) a run may use;
    None is no narrowing."""

    metrics: frozenset[str] | None = None
    tables: frozenset[str] | None = None
    sources: frozenset[str] | None = None

    @classmethod
    def of(cls, raw: Mapping[str, Any] | None) -> DataScope:
        def names(key: str) -> frozenset[str] | None:
            values = (raw or {}).get(key)
            return frozenset(str(v) for v in values) if values else None

        return cls(metrics=names("metrics"), tables=names("tables"), sources=names("sources"))

    def narrow(self, vocab: Vocabulary) -> Vocabulary:
        if self.metrics is None:
            return vocab
        kept = [
            m for m in vocab.metrics if m.code in self.metrics or str(m.metric_id) in self.metrics
        ]
        ids = {m.metric_id for m in kept}
        return replace(
            vocab,
            metrics=kept,
            synonyms={
                phrase: target
                for phrase, target in vocab.synonyms.items()
                if target[0] != "metric" or target[1] in ids
            },
        )

    def allows(self, dataset: catalog.Dataset, source: catalog.Source | None) -> bool:
        table = self.tables is None or bool(
            {f"{dataset.schema_name}.{dataset.table_name}", str(dataset.id)} & self.tables
        )
        origin = self.sources is None or (
            source is not None and bool({source.name, str(source.id)} & self.sources)
        )
        return table and origin
