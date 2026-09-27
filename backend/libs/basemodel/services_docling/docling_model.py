from typing import Any

from pydantic import BaseModel, Field


class PageRegion(BaseModel):
    """Where a parsed block sits on its page, so a citation can open at the spot.

    Coordinates are PDF points with a TOP-LEFT origin (y grows downward),
    converted from the bottom-left origin Docling reports. Normalising here
    rather than at each call site is deliberate: every consumer is an SVG or
    HTML overlay that measures from the top, and a box that silently arrives
    flipped draws a highlight over the wrong part of the page.

    `page_width`/`page_height` travel with the box because points alone cannot
    be overlaid on a rendered image — the viewer needs the page dimensions to
    scale them to whatever raster size it drew.
    """

    page_no: int
    left: float
    top: float
    right: float
    bottom: float
    page_width: float
    page_height: float


class ParsedTable(BaseModel):
    table_name: str
    description: str
    data: dict[str, Any]
    region: PageRegion | None = None


class ParsedChunk(BaseModel):
    block_index: int
    content: str
    table_involved: bool = False
    table: ParsedTable | None = None
    # Absent for formats with no page geometry (HTML, TXT, MD) and for PDFs
    # whose backend reported no provenance — a citation then degrades to
    # "this document" rather than pointing at a page that was never located.
    region: PageRegion | None = None


class DoclingResult(BaseModel):
    has_figures: bool = False
    chunks: list[ParsedChunk] = Field(default_factory=list)
