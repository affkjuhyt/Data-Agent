"""The two things the Exec vault service reaches outside PostgreSQL, as ports on app.state.

- `exec_transit` (TransitKeys): the vault's own RSA-4096 key in OpenBao Transit (BR-189). The api
  creates it and reads its public half; it never decrypts (only svc_exec_worker's OpenBao policy
  may, tab 5 G10.1).
- `exec_private_questions` (PrivateQuestions): the ephemeral channel to the local-model agent run
  of UC-16-02. Question text and answers live there for at most 15 minutes and never in a table,
  the outbox or a log (BR-193), so they cannot go through exec.outbox like other work.

Neither adapter exists in the api yet. Without one the routes answer 503 (source_unavailable for
Transit, model_unavailable for private questions) instead of pretending: tests and the deploy
wiring set them on app.state.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Protocol

from fastapi import Request

from services.api.kernel.problems import Problem
from services.api.kernel.sse import Event


@dataclass(frozen=True)
class TransitKey:
    public_key_pem: str
    version: int


class TransitKeys(Protocol):
    async def create(self, name: str) -> TransitKey:
        """Creates the RSA-4096 key `name` if missing (idempotent) and returns its latest version."""
        ...

    async def public_key(self, name: str, version: int) -> TransitKey: ...


@dataclass(frozen=True)
class PrivateQuestion:
    id: uuid.UUID
    org_id: uuid.UUID
    owner_id: uuid.UUID
    text: str
    context: list[str] = field(default_factory=list)
    as_of: str | None = None


class ModelUnavailable(Exception):
    """The local model is saturated; BR-193 forbids falling back to a frontier provider."""


class PrivateQuestions(Protocol):
    async def submit(self, question: PrivateQuestion) -> None: ...

    async def events(
        self, *, org_id: uuid.UUID, owner_id: uuid.UUID, question_id: uuid.UUID
    ) -> list[Event] | None:
        """The question's events so far, or None once its state expired (or never was the
        caller's)."""
        ...


def transit(request: Request) -> TransitKeys:
    keys: TransitKeys | None = getattr(request.app.state, "exec_transit", None)
    if keys is None:
        raise Problem(503, "source_unavailable", "The vault key service is not configured.")
    return keys


def private_questions(request: Request) -> PrivateQuestions | None:
    channel: PrivateQuestions | None = getattr(request.app.state, "exec_private_questions", None)
    return channel
