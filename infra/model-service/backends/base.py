from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


# ── Wire schemas (request/response models for the endpoints in app.py) ────────────
class EmbedRequest(BaseModel):
    texts: list[str]
    model_id: str | None = None

class EmbedResponse(BaseModel):
    embeddings: list[list[float]]
    model_id: str | None


class RerankRequest(BaseModel):
    query: str
    documents: list[str]
    top_n: int = 5
    model_id: str | None = None


class RerankResult(BaseModel):
    index: int
    relevance_score: float


class RerankResponse(BaseModel):
    results: list[RerankResult]


class GenerateRequest(BaseModel):
    prompt: str | None = None
    system_prompt: str = ""
    messages: list[dict] | None = None
    json_schema: dict | None = None
    json_mode: bool = True
    max_tokens: int = 512
    temperature: float = 0.1
    model: str | None = None


class GenerateResponse(BaseModel):
    content: str


@runtime_checkable
class EmbedCaller(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


@runtime_checkable
class RerankCaller(Protocol):
    def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        ...


@runtime_checkable
class ChatCaller(Protocol):
    def chat(self, messages: list[dict], response_format: dict | None, **opts) -> str:
        """Chat-completion from a full messages list."""
        ...

    def generate(self, prompt: str, system_prompt: str, response_format: dict | None, **opts) -> str:
        """Convenience: build a (system?, user) messages list from a raw prompt and chat."""
        ...

