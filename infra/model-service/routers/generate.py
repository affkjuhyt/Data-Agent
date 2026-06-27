"""LLM gateway endpoint. Callers (ingestion, lightrag) send generic input —
prompt/messages (+ optional system_prompt and json_schema) — and an optional chat
model NAME. The registry resolves the name (or the default chat model) to an entry
whose injected caller knows how to reach it (e.g. OpenAI /v1/chat/completions on a
Colab tunnel or the local llama-server). Switching backends is a single env change,
exactly like /embed and /rerank."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backends import registry, Capability

router = APIRouter()


class GenerateRequest(BaseModel):
    prompt: str | None = None                  # user message (used if `messages` absent)
    system_prompt: str = ""                     # optional system message (caller owns the text)
    messages: list[dict] | None = None          # full chat messages (overrides prompt/system_prompt)
    json_schema: dict | None = None             # strict JSON schema (caller owns its shape)
    json_mode: bool = True                       # if true and no schema → json_object mode
    max_tokens: int = 512
    temperature: float = 0.1
    model: str | None = None                     # chat model NAME; default chat model when absent


class GenerateResponse(BaseModel):
    content: str


@router.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest) -> GenerateResponse:
    entry = registry.by_name(req.model) if req.model else registry.default(Capability.CHAT)
    if entry is None:
        if req.model:
            raise HTTPException(422, f"chat model '{req.model}' not in registry")
        raise HTTPException(503, "no LLM backend available (set REMOTE_LLM_URL)")

    if req.json_schema is not None:
        response_format = {"type": "json_schema",
                           "json_schema": {"name": "out", "strict": True, "schema": req.json_schema}}
    elif req.json_mode:
        response_format = {"type": "json_object"}
    else:
        response_format = None

    # Full messages → chat(); a raw prompt → generate() (the caller builds the messages).
    if req.messages is not None:
        content = entry.caller.chat(req.messages, response_format,
                                    max_tokens=req.max_tokens, temperature=req.temperature)
    else:
        content = entry.caller.generate(req.prompt or "", req.system_prompt, response_format,
                                        max_tokens=req.max_tokens, temperature=req.temperature)
    return GenerateResponse(content=content)
