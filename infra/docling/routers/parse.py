from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import minio_client
import parser as doc_parser

router = APIRouter()


class ParseRequest(BaseModel):
    bucket: str
    object_key: str
    extension: str
    max_chunk_chars: int = 1500


class ParsedTableResponse(BaseModel):
    table_name: str
    description: str
    data: dict[str, Any]


class ParsedChunkResponse(BaseModel):
    block_index: int
    content: str
    table_involved: bool = False
    table: Optional[ParsedTableResponse] = None


class ParseResponse(BaseModel):
    has_figures: bool
    chunks: list[ParsedChunkResponse]


@router.post("/parse", response_model=ParseResponse)
def parse(req: ParseRequest) -> ParseResponse:
    if not doc_parser.is_ready():
        raise HTTPException(503, "Converter not loaded")
    try:
        file_bytes = minio_client.download(req.bucket, req.object_key)
    except Exception as exc:
        raise HTTPException(502, f"MinIO download failed: {exc}")
    try:
        result = doc_parser.run_parse(file_bytes, req.extension, req.max_chunk_chars)
    except Exception as exc:
        raise HTTPException(500, f"Parse failed: {exc}")

    chunks = [
        ParsedChunkResponse(
            block_index=c["block_index"],
            content=c["content"],
            table_involved=c["table_involved"],
            table=ParsedTableResponse(**c["table"]) if c.get("table") else None,
        )
        for c in result["chunks"]
    ]
    return ParseResponse(has_figures=result["has_figures"], chunks=chunks)
