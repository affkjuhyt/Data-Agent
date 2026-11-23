"""DF-13: a column a table loaded before holds as text stays text when rows are appended; a real
retype still stops the job as drift."""

from __future__ import annotations

import pytest

from services.worker_ingest import worker

pytestmark = [pytest.mark.unit]


def test_a_legacy_text_column_takes_the_timestamp_it_always_held() -> None:
    existing = {"id": "numeric", "sold_at": "text", "meta": "text"}
    columns = [
        {"name": "id", "type": "numeric"},
        {"name": "sold_at", "type": "timestamptz"},
        {"name": "meta", "type": "jsonb"},
    ]
    worker.keep_legacy_text(existing, columns)
    assert [c["type"] for c in columns] == ["numeric", "text", "text"]
    worker.check_drift(existing, columns)  # no drift


def test_a_retyped_column_is_still_drift() -> None:
    existing = {"id": "numeric", "sold_at": "timestamptz"}
    columns = [{"name": "id", "type": "text"}, {"name": "sold_at", "type": "timestamptz"}]
    worker.keep_legacy_text(existing, columns)
    with pytest.raises(worker.SchemaDrift, match="id numeric → text"):
        worker.check_drift(existing, columns)


def test_a_decoded_json_value_is_written_as_json() -> None:
    assert worker._plain({"kenh": "web", "n": [1, 2]}) == '{"kenh": "web", "n": [1, 2]}'
