"""The V2 / legacy storage boundary, and the one test for a V2 structured source.

Which store owns which data is docs/v2/03-target-design.md §2 and §2.1.

Why this exists: V2 numbers (ObservationCandidate / Observation) and V2 business
decisions must never land in the v1 structured-KB path (`kb_structured_data`,
schema `kb_structured`), in MongoDB, or in the Silver/Gold chunk + embedding path
(docs/v2/00 invariant 4, ADR-V2-09, ADR-V2-34, ADR-V2-38). A crawled source with
an `extraction_profile` in its `KBSourceConfig.config` is read cell by cell by
`series_extractor` (docs/v2/03 §5); cutting the same file into text chunks would
give the agent a second, unchecked copy of the same numbers with no unit and no
`available_at`.

Two things keep that boundary:

  * import-linter's `v2-data-boundary` contract, for Python imports;
  * this module, for the paths import-linter cannot see — a Kafka topic name is
    a string, so a V2 module that publishes `kb.promote.silver` or a crawler that
    auto-promotes a structured source imports nothing forbidden. The guards in
    `platform_workers/workers/web_crawler/worker.py`, `libs/ingestion` (Silver and
    Gold consumers) and `kb_backend` (promote / propose routes) all ask
    `is_v2_structured_source`, and `tests/unit/kb_backend/test_v2_storage_boundary.py`
    scans V2 packages for the names in `LEGACY_IMPORTS` / `LEGACY_TOPICS`.

Pure: no database driver, no network, no service imports (.importlinter keeps
`libs.shared` a leaf).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

EXTRACTION_PROFILE_KEY = "extraction_profile"

# Error code a kb_backend route returns (HTTP 409) when asked to push a V2
# structured source's document down the legacy path. Moves to the shared error
# catalogue (`domains/errors.py`, P0-09 contract) once that lands on main.
V2_STRUCTURED_SOURCE_ERROR = "V2_STRUCTURED_SOURCE_LEGACY_PATH"

# Topics of the legacy document / structured-KB path. Silver and Gold are the
# chunk + embedding path; `kb.structured.propose` feeds `kb_structured_data`.
LEGACY_TOPICS: frozenset[str] = frozenset(
    {"kb.promote.silver", "kb.promote.gold", "kb.structured.propose"}
)

# Module prefixes a V2 package must not import: the v1 structured-KB service,
# every MongoDB client, and the Silver/Gold ingestion stages.
LEGACY_IMPORTS: tuple[str, ...] = (
    "services.kb_structured_data",
    "libs.database_connector.mongo_connector",
    "pymongo",
    "motor",
    "libs.ingestion.silver",
    "libs.ingestion.gold",
)


def is_v2_structured_source(config: Mapping[str, Any] | None) -> bool:
    """True when a source's active `KBSourceConfig.config` declares an
    extraction profile — that source's numbers belong to `series_extractor`,
    not to the legacy document path. An empty or null profile does not count:
    it is a half-filled form, not a declaration."""
    if not config:
        return False
    return bool(config.get(EXTRACTION_PROFILE_KEY))
