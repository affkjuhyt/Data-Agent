"""Getting an answer out of the V3 system under test, for the online tier.

Two ways in, because they answer different questions and the corpus needs the
first one:

  * **`answer_with_contexts`** — the shipped responder prompt, the shipped
    analysis-type guidance, and the case's own contexts as the retrieved
    sources. This isolates the ANSWERING step: given exactly these sources, does
    the answer stay inside them? Retrieval quality is a separate concern with a
    separate corpus, and mixing them produces a hallucination rate that moves
    when the index is reranked.

  * **`ask_live_agent`** — the whole pipeline over SSE, for cases with no
    contexts of their own. End to end, including retrieval.

The live tier uses the public V3 question contract. Context-only cases use the same neutral
answer contract as the worker; they do not import a legacy runtime prompt.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

_LITELLM_BASE = os.getenv("LITELLM_BASE_URL", "http://localhost:8500")
_LITELLM_KEY = os.getenv("LITELLM_API_KEY", "anything")
# "openai/" prefix required: bare litellm.acompletion() (no Router) needs it to
# resolve a custom api_base, matching config_loader.DEV_CONFIG's "openai/responder".
_RESPONDER_MODEL = os.getenv("EVAL_RESPONDER_MODEL", "openai/responder")

# The system prompt the agent builder fills in per agent. Neutral here so the
# metric reflects the platform's own grounding rules rather than one tenant's
# persona.
_BASE_SYSTEM_PROMPT = (
    "Bạn là trợ lý dữ liệu. Chỉ trả lời dựa trên nguồn được cung cấp. "
    "Nếu nguồn không chứa câu trả lời, hãy nói rõ là không có trong nguồn."
)


def _responder_system(analysis_type: str) -> str:
    """The V3 answer contract; worker-agent owns execution and evidence."""
    guidance = {
        "forecast": "Không dự báo nếu chưa có forecast job và bằng chứng được phê duyệt.",
        "anomaly": "Không kết luận bất thường nếu nguồn không có bằng chứng tương ứng.",
    }.get(analysis_type, "")
    return _BASE_SYSTEM_PROMPT + (f"\n\n{guidance}" if guidance else "")


async def answer_with_contexts(
    question: str, contexts: list[str], analysis_type: str = "other"
) -> str:
    import litellm

    sources = "\n\n".join(f"[{i + 1}] {c}" for i, c in enumerate(contexts))
    response = await litellm.acompletion(
        model=_RESPONDER_MODEL,
        api_base=_LITELLM_BASE,
        api_key=_LITELLM_KEY,
        messages=[
            {"role": "system", "content": _responder_system(analysis_type)},
            {"role": "user", "content": f"Nguồn:\n{sources}\n\nCâu hỏi: {question}"},
        ],
        temperature=0,
        max_tokens=512,
        timeout=60,
    )
    return response.choices[0].message.content or ""


async def ask_live_agent(agent_url: str, agent_id: str, data_space_id: str, question: str) -> str:
    """Run one question through the public V3 API and worker-agent.

    ``agent_id`` remains in the function signature for the release-gate CLI contract; V3
    resolves the running analysis agent from the Data Space rather than accepting it on
    ``POST /v1/questions``.
    """
    del agent_id
    access_token = os.environ.get("EVAL_ACCESS_TOKEN")
    if not access_token:
        raise RuntimeError("set EVAL_ACCESS_TOKEN to a Keycloak access token for the online eval")
    headers = {
        "Authorization": f"Bearer {access_token}",
        "X-Data-Space": data_space_id,
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        accepted = await client.post(
            f"{agent_url.rstrip('/')}/v1/questions",
            json={"text": question, "stream": True},
            headers=headers,
        )
        accepted.raise_for_status()
        question_id = accepted.json()["question_id"]
        deadline = asyncio.get_running_loop().time() + 240
        while True:
            view = await client.get(
                f"{agent_url.rstrip('/')}/v1/questions/{question_id}", headers=headers
            )
            view.raise_for_status()
            body = view.json()
            if body["status"] in {"answered", "clarifying", "refused", "failed"}:
                break
            if asyncio.get_running_loop().time() >= deadline:
                raise RuntimeError(f"V3 question timed out: {question_id}")
            await asyncio.sleep(0.5)
        if body["status"] != "answered":
            return str(body.get("status") or "question was not answered")
        answer = await client.get(
            f"{agent_url.rstrip('/')}/v1/answers/{body['answer_id']}", headers=headers
        )
        answer.raise_for_status()
        return str(answer.json().get("text") or "")


async def ask(case: dict[str, Any], *, agent_url: str, agent_id: str, data_space_id: str) -> str:
    """Answer one grounding case the way production would.

    Context-backed cases are classified through the V3 gateway contract before answering.
    """
    contexts = case.get("contexts") or []
    if not contexts:
        return await ask_live_agent(agent_url, agent_id, data_space_id, case["question"])

    from model.eval.scorers.intent import planner_classifier

    try:
        classified = await planner_classifier(case["question"])
        analysis_type = classified.get("analysis_type", "other")
    # A classifier outage must not silently turn into "no guidance", which would
    # make the forecast cases look better than the system is — so this catches
    # everything and the case is still answered, ungrounded guidance and all.
    except Exception:  # noqa: BLE001
        analysis_type = "other"

    return await answer_with_contexts(case["question"], contexts, analysis_type)


# ---------------------------------------------------------------- the golden tier (EV-09)

_DONE = frozenset({"answered", "clarifying", "refused", "failed", "cancelled"})
DATA_SPACE_HEADER = "X-Data-Space"


class Login:
    """The bearer token of the golden tier: EVAL_ACCESS_TOKEN as given, or a password grant for
    EVAL_USER / EVAL_PASS on the realm's frontend client, taken again for every case so a run
    longer than a token's life does not fail halfway."""

    def __init__(self, keycloak_url: str) -> None:
        self.keycloak_url = keycloak_url.rstrip("/")
        self.token = os.environ.get("EVAL_ACCESS_TOKEN", "")
        self.user = os.environ.get("EVAL_USER", "")
        self._password = os.environ.get("EVAL_PASS", "")
        if not self.token and not (self.user and self._password):
            raise RuntimeError(
                "the golden tier asks as a person: set EVAL_ACCESS_TOKEN, or EVAL_USER and EVAL_PASS"
            )

    async def bearer(self, client: httpx.AsyncClient) -> str:
        if not self.user:
            return self.token
        r = await client.post(
            f"{self.keycloak_url}/realms/aeroflow/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "aeroflow-frontend",
                "username": self.user,
                "password": self._password,
            },
        )
        body = r.json()
        if "access_token" not in body:
            # The body names the error, never the password.
            raise RuntimeError(f"login failed for {self.user}: {body.get('error_description')}")
        return str(body["access_token"])


async def _turn(
    client: httpx.AsyncClient,
    base: str,
    headers: dict[str, str],
    text: str,
    conversation_id: str | None,
    timeout_s: float,
) -> dict[str, Any]:
    body: dict[str, Any] = {"text": text}
    if conversation_id:
        body["conversation_id"] = conversation_id
    accepted = await client.post(f"{base}/v1/questions", json=body, headers=headers)
    accepted.raise_for_status()
    question_id = accepted.json()["question_id"]
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        view = await client.get(f"{base}/v1/questions/{question_id}", headers=headers)
        view.raise_for_status()
        state: dict[str, Any] = view.json()
        if state["status"] in _DONE:
            return state
        if loop.time() >= deadline:
            return {**state, "status": "timeout"}
        await asyncio.sleep(1.0)


async def ask_golden(
    case: dict[str, Any],
    *,
    api_url: str,
    data_space: str,
    login: Login,
    timeout_s: float = 300,
) -> Any:
    """One golden case through the public API, as a person asks it: each user turn of its history
    first, in one conversation, then its question; the answer (AnswerView, AE-06's envelope) and
    its receipt's id. The case's as_of is the snapshot's last day, not the question's: the data was
    loaded after it, so a point-in-time ask on it would see nothing."""
    from model.eval.scorers.golden import Asked

    base = api_url.rstrip("/")
    started = asyncio.get_running_loop().time()
    async with httpx.AsyncClient(timeout=60) as client:
        headers = {
            "Authorization": f"Bearer {await login.bearer(client)}",
            DATA_SPACE_HEADER: data_space,
        }
        conversation_id: str | None = None
        for turn in case.get("history") or []:
            if turn.get("role") != "user":
                continue
            state = await _turn(client, base, headers, turn["text"], conversation_id, timeout_s)
            conversation_id = state["conversation_id"]
        state = await _turn(client, base, headers, case["question"], conversation_id, timeout_s)
        asked = Asked(
            status=state["status"], question_id=state["id"], answer_id=state.get("answer_id")
        )
        if asked.answer_id:
            answer = await client.get(f"{base}/v1/answers/{asked.answer_id}", headers=headers)
            answer.raise_for_status()
            asked.answer = answer.json()
            receipt = await client.get(
                f"{base}/v1/answers/{asked.answer_id}/receipt", headers=headers
            )
            # 404: an answer with no figure, or one outside the receipts' reach, has none.
            if receipt.status_code == 200:
                asked.receipt_id = str(receipt.json().get("id"))
    asked.seconds = asyncio.get_running_loop().time() - started
    return asked
