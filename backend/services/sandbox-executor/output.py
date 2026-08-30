"""Output truncation (doc §1.3, "Lớp Result") — applied before any sandbox
result crosses back into the agent's conversation context."""

DEFAULT_MAX_OUTPUT_CHARS = 10_000


def truncate_output(text: str, max_chars: int = DEFAULT_MAX_OUTPUT_CHARS) -> tuple[str, bool]:
    if text is None:
        return "", False
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True
