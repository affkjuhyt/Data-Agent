"""The server's trust code (AE-04): every sentence the worker writes maps to one code, and an
answer is only as trusted as its weakest figure or block."""

from __future__ import annotations

import pytest

from libs.contracts.v1 import TrustCode
from libs.contracts.v1.trust import LABELS, code_of, trust
from services.worker_agent.agentic.workspace import TRUST_LABEL
from services.worker_agent.questions import NOT_STANDARD, OUT_OF_SCOPE, STANDARD
from services.worker_agent.sqlgen.answer import TRUST
from services.worker_agent.sqlgen.verified import TRUST_VERIFIED

pytestmark = pytest.mark.unit


def test_every_sentence_the_worker_writes_has_one_code() -> None:
    sentences = [STANDARD, NOT_STANDARD, OUT_OF_SCOPE, TRUST_VERIFIED, TRUST, *TRUST_LABEL.values()]
    assert set(sentences) == set(LABELS)
    assert code_of(STANDARD) is TrustCode.GOVERNED
    assert code_of(TRUST_VERIFIED) is TrustCode.VERIFIED
    assert code_of(TRUST) is TrustCode.EXPLORATORY
    assert code_of(TRUST_LABEL["document"]) is TrustCode.DOCUMENT
    assert code_of("") is None
    assert code_of("một câu lạ") is None


def test_one_exploratory_number_makes_the_answer_exploratory() -> None:
    code, envelope = trust(
        "agentic",
        "data",
        figures=[
            {"key": "doanh_thu", "trust": STANDARD, "display_state": "shown"},
            {"key": "ty_le", "trust": TRUST, "display_state": "shown"},
        ],
        blocks=[{"trust_label": TRUST_LABEL["certified"]}],
        stopped="budget",
    )
    assert code is TrustCode.EXPLORATORY
    assert envelope == {
        "version": 1,
        "path": "agentic",
        "trust_code": "EXPLORATORY",
        "figures": {"doanh_thu": "GOVERNED", "ty_le": "EXPLORATORY"},
        "blocks": ["GOVERNED"],
        "stopped": "budget",
    }


def test_a_share_of_a_governed_figure_is_derived() -> None:
    code, _ = trust(
        "governed",
        "data",
        figures=[{"trust": STANDARD, "source": {"expr": "a / b"}, "display_state": "shown"}],
    )
    assert code is TrustCode.DERIVED


def test_a_share_of_an_exploratory_figure_stays_exploratory() -> None:
    code, _ = trust(
        "explore",
        "data",
        figures=[{"trust": TRUST, "source": {"expr": "a / b"}}],
    )
    assert code is TrustCode.EXPLORATORY


def test_a_masked_figure_is_insufficient() -> None:
    code, envelope = trust(
        "governed",
        "data",
        figures=[{"key": "npl", "trust": STANDARD, "display_state": "masked"}],
    )
    assert code is TrustCode.INSUFFICIENT
    assert envelope["figures"] == {"npl": "INSUFFICIENT"}


def test_a_refusal_is_insufficient_even_when_a_figure_is_governed() -> None:
    code, _ = trust("governed", "refusal", figures=[{"trust": STANDARD}])
    assert code is TrustCode.INSUFFICIENT


def test_a_document_answer_needs_a_citation() -> None:
    cited, _ = trust("document", "document", cited=True)
    bare, _ = trust("document", "document", cited=False)
    assert cited is TrustCode.DOCUMENT
    assert bare is TrustCode.INSUFFICIENT
