"""Unit tests for the page/bbox provenance the Docling parser now records
(T7 in docs/archive/v1-ankt/TECH-DESIGN.md).

Docling always reported `prov` (page_no + bbox); ParsedChunk simply dropped it,
so a citation could name a document but never a place inside it. These tests
pin the two things that are easy to get silently wrong: the coordinate origin,
and which page a chunk assembled from several items is attributed to.
"""
from __future__ import annotations

import pytest

pytest.importorskip("docling", reason="docling not installed in this environment")

from libs.ingestion.docling_parser import _merge_regions, _regions

pytestmark = pytest.mark.unit

SIZES = {1: (595.0, 842.0), 2: (595.0, 842.0)}


def _region(page_no: int, left: float, top: float, right: float, bottom: float) -> dict:
    return {
        "page_no": page_no,
        "left": left, "top": top, "right": right, "bottom": bottom,
        "page_width": 595.0, "page_height": 842.0,
    }


# ── _regions: extraction and origin conversion ────────────────────────────────

class _Bbox:
    """Stands in for docling_core's BoundingBox with a bottom-left origin."""

    def __init__(self, left: float, top: float, right: float, bottom: float):
        self.l, self.t, self.r, self.b = left, top, right, bottom

    def to_top_left_origin(self, page_height: float):
        return _Bbox(self.l, page_height - self.t, self.r, page_height - self.b)


class _Prov:
    def __init__(self, page_no, bbox):
        self.page_no, self.bbox = page_no, bbox


class _Item:
    def __init__(self, prov):
        self.prov = prov


def test_flips_the_origin_so_top_is_above_bottom():
    # Docling reports bottom-left origin, where t=751 is ABOVE b=616. An overlay
    # measures from the top, so a box handed over unflipped highlights the
    # mirror image of the text it cites.
    [region] = _regions(_Item([_Prov(1, _Bbox(72.0, 751.488, 354.8, 616.688))]), SIZES)

    assert region["top"] < region["bottom"]
    assert (region["top"], region["bottom"]) == (842.0 - 751.488, 842.0 - 616.688)
    assert (region["page_no"], region["left"], region["right"]) == (1, 72.0, 354.8)


def test_carries_page_dimensions_so_a_viewer_can_scale():
    [region] = _regions(_Item([_Prov(1, _Bbox(72.0, 700.0, 300.0, 650.0))]), SIZES)

    assert (region["page_width"], region["page_height"]) == (595.0, 842.0)


def test_item_without_provenance_yields_nothing():
    assert _regions(_Item([]), SIZES) == []
    assert _regions(object(), SIZES) == []


def test_page_with_no_recorded_size_is_skipped():
    # Without the page height there is no way to flip the origin, and guessing
    # one would produce a box that looks valid and points somewhere else.
    assert _regions(_Item([_Prov(99, _Bbox(72.0, 700.0, 300.0, 650.0))]), SIZES) == []


def test_a_malformed_box_costs_coordinates_not_text():
    class _Broken(_Bbox):
        def to_top_left_origin(self, page_height: float):
            raise ValueError('backend reported a degenerate box')

    assert _regions(_Item([_Prov(1, _Broken(0, 0, 0, 0))]), SIZES) == []


def test_an_item_spanning_two_pages_reports_both():
    item = _Item([
        _Prov(1, _Bbox(72.0, 200.0, 300.0, 100.0)),
        _Prov(2, _Bbox(72.0, 800.0, 300.0, 700.0)),
    ])

    assert [r["page_no"] for r in _regions(item, SIZES)] == [1, 2]


# ── _merge_regions: one anchor per chunk ──────────────────────────────────────

def test_no_regions_means_no_anchor():
    assert _merge_regions([]) is None


def test_single_region_passes_through():
    assert _merge_regions([_region(1, 72, 90, 354, 225)]) == _region(1, 72, 90, 354, 225)


def test_boxes_on_one_page_union():
    merged = _merge_regions([
        _region(1, 72, 90, 300, 120),
        _region(1, 90, 150, 354, 225),
    ])

    assert (merged["left"], merged["top"]) == (72, 90)
    assert (merged["right"], merged["bottom"]) == (354, 225)


def test_a_chunk_spanning_pages_anchors_to_where_it_starts():
    """A box spanning two pages is not a place a reader can be sent to, so the
    citation points at the first page and unions only what is on it."""
    merged = _merge_regions([
        _region(2, 72, 700, 300, 780),
        _region(1, 72, 600, 300, 700),
        _region(1, 80, 500, 350, 600),
    ])

    assert merged["page_no"] == 1
    assert (merged["top"], merged["bottom"]) == (500, 700)
    assert merged["right"] == 350


def test_merge_does_not_mix_page_dimensions():
    landscape = dict(_region(2, 10, 10, 20, 20), page_width=842.0, page_height=595.0)
    merged = _merge_regions([landscape, _region(1, 72, 90, 354, 225)])

    assert merged["page_no"] == 1
    assert (merged["page_width"], merged["page_height"]) == (595.0, 842.0)
