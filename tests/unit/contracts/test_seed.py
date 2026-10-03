"""The seed sources parse with the contract (bổ sung 30/09 to NOP-150).

docs/v2/data/sources-oil-gold.yaml was written against SourceConfigV2 and
MappingRules; reading it here, not a copy, makes any drift between the seed and
the contract a red test instead of a surprise at Bulk Import.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from libs.contracts.v1 import MappingRules, SourceConfigV2

pytestmark = [pytest.mark.unit, pytest.mark.contract, pytest.mark.req("NOP-150")]

SEED = Path(__file__).resolve().parents[3] / "docs" / "v2" / "data" / "sources-oil-gold.yaml"

# Keys of a seed entry that are not SourceConfigV2: Bulk Import's v1 fields, the
# meaning (`mapping`, a separate MappingRules), source-level `locale` (only for
# sources without a profile), and `series_role`, which belongs to the registry
# (NOP-117). A key in neither list fails the test below.
NOT_SOURCE_CONFIG = {
    "id", "publisher", "title", "connector", "format", "sample_urls", "landing_url",
    "api_base", "variables", "schedule", "promotion_mode", "verified", "notes",
    "example", "examples", "mapping", "locale", "series_role",
}  # fmt: skip

# The 13 sources that come with both a reading profile and a mapping.
PROFILED = {
    "eia-brent-spot-daily", "eia-wti-spot-daily", "eia-wti-futures-m1-terminated",
    "eia-us-crude-stocks-weekly", "gpr-daily", "wb-pink-sheet-monthly", "imf-pcps-oil-gold",
    "lbma-gold-pm-json", "vn-pnj-gold-price", "vn-doji-gold-price", "vn-24h-gold-fx",
    "vn-vcb-fx-xml", "eval-brent-feed-b",
}  # fmt: skip


@pytest.fixture(scope="module")
def sources() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = yaml.safe_load(SEED.read_text(encoding="utf-8"))["sources"]
    return entries


def _config_keys(entry: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in entry.items() if k not in NOT_SOURCE_CONFIG}


def test_every_seed_key_is_known(sources: list[dict[str, Any]]) -> None:
    fields = {f.alias or name for name, f in SourceConfigV2.model_fields.items()}
    unknown = {(e["id"], k) for e in sources for k in e if k not in NOT_SOURCE_CONFIG | fields}
    assert not unknown, f"seed keys neither in SourceConfigV2 nor in NOT_SOURCE_CONFIG: {unknown}"


def test_every_seed_source_is_a_valid_source_config(sources: list[dict[str, Any]]) -> None:
    for entry in sources:
        SourceConfigV2.model_validate(_config_keys(entry))


def test_the_profiled_sources_are_exactly_the_thirteen(sources: list[dict[str, Any]]) -> None:
    with_profile = {e["id"] for e in sources if "extraction_profile" in e}
    with_mapping = {e["id"] for e in sources if "mapping" in e}
    assert with_profile == PROFILED
    assert with_mapping == PROFILED


@pytest.mark.parametrize("source_id", sorted(PROFILED))
def test_profile_and_mapping_parse(sources: list[dict[str, Any]], source_id: str) -> None:
    entry = next(e for e in sources if e["id"] == source_id)
    config = SourceConfigV2.model_validate(_config_keys(entry))
    rules = MappingRules.model_validate(entry["mapping"])
    assert config.extraction_profile is not None
    assert rules.values
