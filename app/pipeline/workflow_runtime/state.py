from typing import List, Optional
from typing_extensions import TypedDict


class AgentState(TypedDict):
    query: str
    messages: List[dict]
    memory_context: List[dict]
    tool_calls: List[dict]
    kb_chunks: List[dict]
    kb_error: Optional[str]
    mcp_results: List[dict]
    mcp_errors: List[dict]
    rrf_results: List[dict]
    reranked_chunks: List[dict]
    response: str
    guardrail_triggered: bool
    guardrail_message: str
    guardrail_stage: Optional[str]
    guardrail_reason: Optional[str]
    config: dict
