"""Screen audit catalog covers every ROUTE_MAP page and records AccessDenied wiring."""

from __future__ import annotations

from collections import Counter

import pytest

from tests.screen_audit.catalog import build_catalog, load_api_overlay, route_map_keys

pytestmark = [pytest.mark.unit]


def test_apis_yaml_covers_every_route_map_page() -> None:
    keys = {
        f"{area}/{page}" for area, pages in route_map_keys().items() for page in pages
    }
    overlay = set(load_api_overlay())
    assert overlay == keys, (
        f"missing={sorted(keys - overlay)} extra={sorted(overlay - keys)}"
    )


def test_catalog_builds_one_screen_per_route() -> None:
    from pathlib import Path

    screens = build_catalog()
    keys = [s.key for s in screens]
    assert len(keys) == len(set(keys))
    assert len(screens) == sum(len(pages) for pages in route_map_keys().values())
    repo = Path(__file__).resolve().parents[3]
    absent = [s.component for s in screens if not (repo / s.component).is_file()]
    assert not absent, absent


def test_access_denied_wiring_is_scanned() -> None:
    screens = build_catalog()
    wired = [s.key for s in screens if s.wires_access_denied]
    # Detail pages known to call forbiddenOf, plus AsyncRegion (central Thiếu quyền path).
    for key in (
        "data/SOURCE",
        "data/DOCUMENT",
        "analysis/FORECAST",
        "analysis/REPORT",
        "agents/TEAM",
    ):
        assert key in wired, key
    # Most ROUTE_MAP pages use AsyncRegion; a minority (forms/static) do not.
    assert Counter(s.wires_access_denied for s in screens)[True] >= 80
    assert any(not s.wires_access_denied for s in screens)
