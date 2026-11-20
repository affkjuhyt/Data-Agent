"""An answer as the api serves it, with how far it can be trusted (AE-06).

The worker writes qa.answer.trust_code and its trust map (envelope) since v3_0045 (AE-04). An
older answer has neither: it is read by the same rule (libs/contracts/v1/trust.py) from its own
figures, blocks and citations, so every answer the api serves carries a code and no screen maps a
Vietnamese label back to a level.
"""

from __future__ import annotations

from typing import Any

from libs.contracts.v1 import TrustCode
from libs.contracts.v1 import trust as answer_trust
from services.api.modules.qa.answer_blocks import blocks_of
from services.api.modules.qa.schemas import AnswerTrust, AnswerView

_SET_HERE = ("trust_code", "trust", "blocks")


def trust_of(answer: Any) -> tuple[TrustCode, dict[str, Any]]:
    stored = getattr(answer, "envelope", None)
    if answer.trust_code and isinstance(stored, dict):
        return TrustCode(answer.trust_code), stored
    return answer_trust.trust(
        None,
        str(answer.kind),
        figures=[f for f in answer.figures or [] if isinstance(f, dict)],
        blocks=[b for b in answer.blocks or [] if isinstance(b, dict)],
        cited=bool(answer.citations),
    )


def answer_view(answer: Any, *, with_blocks: bool = False) -> AnswerView:
    """The answer row as AnswerView; `with_blocks` projects the old answers' blocks too
    (answer_blocks.blocks_of), as GET /v1/answers/{id} does."""
    code, envelope = trust_of(answer)
    fields = {
        name: getattr(answer, name)
        for name in AnswerView.model_fields
        if name not in _SET_HERE and hasattr(answer, name)
    }
    return AnswerView.model_validate(
        {
            **fields,
            "blocks": blocks_of(answer) if with_blocks else (answer.blocks or []),
            "trust_code": code,
            "trust": AnswerTrust.model_validate(envelope),
        }
    )
