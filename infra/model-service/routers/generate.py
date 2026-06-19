"""LLM gateway endpoint. Callers (ingestion, lightrag) send generic input —
prompt/messages (+ optional system_prompt and json_schema) — and never know which
backend serves it or where. The active chat backend (backends/) decides: remote
(e.g. Colab vLLM) when REMOTE_LLM_URL is set, otherwise a local backend if one is
implemented. Switching backends is a single env change, exactly like /embed and /rerank."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backends import provider

router = APIRouter()


class GenerateRequest(BaseModel):
    prompt: str | None = None                  # user message (used if `messages` absent)
    system_prompt: str = ""                     # optional system message (caller owns the text)
    messages: list[dict] | None = None          # full chat messages (overrides prompt/system_prompt)
    json_schema: dict | None = None             # strict JSON schema (caller owns its shape)
    json_mode: bool = True                       # if true and no schema → json_object mode
    max_tokens: int = 512
    temperature: float = 0.1


class GenerateResponse(BaseModel):
    content: str


@router.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest) -> GenerateResponse:
    backend = provider.active_chat()
    if backend is None:
        raise HTTPException(503, "no LLM backend available (set REMOTE_LLM_URL or enable a local backend)")

    if req.messages is not None:
        messages = req.messages
    else:
        messages = []
        if req.system_prompt:
            messages.append({"role": "system", "content": req.system_prompt})
        messages.append({"role": "user", "content": req.prompt or ""})

    if req.json_schema is not None:
        response_format = {"type": "json_schema",
                           "json_schema": {"name": "out", "strict": True, "schema": req.json_schema}}
    elif req.json_mode:
        response_format = {"type": "json_object"}
    else:
        response_format = None

    content = backend.chat(messages, response_format, req.max_tokens, req.temperature)
    return GenerateResponse(content=content)
