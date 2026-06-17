"""Controlled vocabulary for AgentMemories.fact_key, per domain (doc §3.2).

The extraction LLM must pick fact_key from this list for a given domain so
the same fact always lands on the same key across runs (e.g. always
"preferred_report_format", never a mix of that and "report_format_pref").
Anything the LLM returns outside the list collapses to the domain's
catch-all key instead of being dropped.
"""

DEFAULT_DOMAIN = "general"
CATCH_ALL_FACT_KEY = "general_note"

DOMAIN_FACT_KEYS: dict[str, list[str]] = {
    "user_pref": [
        "preferred_report_format",
        "preferred_language",
        "preferred_notification_time",
        "preferred_channel",
        CATCH_ALL_FACT_KEY,
    ],
    "general": [
        "business_context",
        "kpi_definition",
        "org_structure",
        CATCH_ALL_FACT_KEY,
    ],
}


def normalize_fact_key(domain: str | None, fact_key: str | None) -> tuple[str, str]:
    """Coerce (domain, fact_key) into the controlled vocabulary, falling back
    to DEFAULT_DOMAIN / CATCH_ALL_FACT_KEY for anything unrecognized."""
    if domain not in DOMAIN_FACT_KEYS:
        domain = DEFAULT_DOMAIN
    allowed = DOMAIN_FACT_KEYS[domain]
    if not fact_key or fact_key not in allowed:
        fact_key = CATCH_ALL_FACT_KEY
    return domain, fact_key


def vocab_prompt_block() -> str:
    """Render the controlled vocabulary as text to embed in the extraction prompt."""
    return "\n".join(f"- {domain}: {', '.join(keys)}" for domain, keys in DOMAIN_FACT_KEYS.items())
