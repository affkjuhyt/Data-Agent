"""AE-13: the api serves an answer's reconciliation and its SOURCE_CONFLICT refusal as the worker
stored them in qa.answer.envelope, so a screen and the golden scorer read them by code."""

from __future__ import annotations

import pytest

from libs.contracts.v1 import trust as answer_trust
from services.api.modules.qa.schemas import AnswerTrust

pytestmark = [pytest.mark.unit, pytest.mark.req("AE-13")]


def test_the_envelope_keeps_the_note_and_the_refusal() -> None:
    note = {"verdict": "conflict", "sources": [], "residual": {"pct": 4.0}}
    _, envelope = answer_trust.trust(
        "agentic",
        "data",
        figures=[{"key": "b1-0", "trust": "Theo tài liệu"}],
        reconciliation=note,
    )
    served = AnswerTrust.model_validate(envelope)
    assert served.trust_code == "INSUFFICIENT"
    assert served.reconciliation == note
    assert served.refusal == {"code": "SOURCE_CONFLICT", "next_action": "contact_steward"}
    # An answer before AE-13 has neither.
    _, older = answer_trust.trust("governed", "data", figures=[{"trust": "Theo chỉ số chuẩn"}])
    plain = AnswerTrust.model_validate(older)
    assert plain.reconciliation is None and plain.refusal is None
