"""Model names for the LiteLLM gateway."""


def sdk_model_name(alias: str | None, default: str) -> str:
    """The `model=` value litellm's SDK needs for a gateway alias.

    Agents and guardrails store the alias the gateway lists (`responder`). The
    SDK must know the provider to format the request, and every alias is served
    on the gateway's OpenAI-compatible API, hence `openai/`. A name that already
    names its provider is passed through; no alias means `default`.
    """
    if not alias:
        return default
    return alias if "/" in alias else f"openai/{alias}"
