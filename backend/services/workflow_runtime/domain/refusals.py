"""Why an agent turn ends without an answer (V3 R0-7).

Two paths used to answer anyway: a config the database could not return became
DEV_CONFIG — another agent's prompt, model and tools — and a plan the model
could not produce became a document search on the raw question, as if the
planner had chosen it. The asker could not tell either from a real answer.

Each is now a RefusalError carrying a libs.contracts ErrorCode. Whatever runs the
turn (conversation_runner) turns it into a `__refusal` frame; nothing below that
decides what the user is told.
"""

from __future__ import annotations

from libs.contracts.v1 import ErrorCode, NextAction, Refusal


class RefusalError(Exception):
    """A turn that cannot be answered, for a reason the user is told by code."""

    code: ErrorCode = ErrorCode.INSUFFICIENT_EVIDENCE

    def __init__(
        self,
        detail: str,
        *,
        message: str | None = None,
        next_action: NextAction | None = None,
    ) -> None:
        # `detail` is for the log and the trace only; it may name tables or
        # carry a driver error, so it never reaches the frame.
        super().__init__(detail)
        self.message = message
        self.next_action = next_action

    def refusal(self, detail_id: str) -> Refusal:
        return Refusal.of(
            self.code, detail_id=detail_id, message=self.message, next_action=self.next_action
        )


class AgentConfigError(RefusalError):
    code = ErrorCode.AGENT_CONFIG_ERROR


class PlannerError(RefusalError):
    code = ErrorCode.PLANNER_ERROR
