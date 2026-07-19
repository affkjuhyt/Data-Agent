from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class SupportedExtension(str, Enum):
    PDF = ".pdf"
    DOCX = ".docx"
    PPTX = ".pptx"
    HTML = ".html"
    HTM = ".htm"
    TXT = ".txt"
    MD = ".md"


class ParsedTable(BaseModel):
    table_name: str
    description: str
    data: dict[str, Any]


class ParsedChunk(BaseModel):
    block_index: int
    content: str
    table_involved: bool = False
    table: Optional[ParsedTable] = None


class DoclingResult(BaseModel):
    has_figures: bool = False
    chunks: list[ParsedChunk] = Field(default_factory=list)
