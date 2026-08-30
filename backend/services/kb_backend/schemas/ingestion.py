"""Ingestion schemas — request (web ingest), response (promote)."""
from __future__ import annotations

from pydantic import BaseModel


class WebIngestRequest(BaseModel):
    name: str
    url: str
    web_name: str | None = None
    language: str = "english"
    abstract: str | None = None
    knowledge_base_id: str | None = None


class PromoteResponse(BaseModel):
    data_id: str
    stage: str
    message: str
