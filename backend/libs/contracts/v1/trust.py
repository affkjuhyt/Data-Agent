"""How far an answer can be trusted, as a code (AE-04, docs/blueprint/ARCHITECTURE.md §9.5).

The worker sets it, the database keeps it (qa.answer.trust_code) and the screens map it to their
own words: no screen reads a level back out of a Vietnamese label. An answer is as trusted as the
weakest of its figures and blocks.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Any, Literal


class TrustCode(StrEnum):
    # A query a steward approved for this very question (sqlgen/verified.py).
    VERIFIED = "VERIFIED"
    # A certified metric read through the semantic compiler, inside a data product.
    GOVERNED = "GOVERNED"
    # Worked out deterministically from figures at least GOVERNED (a share, a change, a total).
    DERIVED = "DERIVED"
    # SQL a model wrote, a metric not yet certified, or one outside every data product.
    EXPLORATORY = "EXPLORATORY"
    # What the Data Space's documents say, cited passage by passage.
    DOCUMENT = "DOCUMENT"
    # Nothing to stand on: a refusal, a clarification, a figure masked or out of time.
    INSUFFICIENT = "INSUFFICIENT"


# Strongest first.
TRUST_ORDER: tuple[TrustCode, ...] = (
    TrustCode.VERIFIED,
    TrustCode.GOVERNED,
    TrustCode.DERIVED,
    TrustCode.EXPLORATORY,
    TrustCode.DOCUMENT,
    TrustCode.INSUFFICIENT,
)


def weakest(codes: Iterable[TrustCode]) -> TrustCode | None:
    """The least trusted of `codes`, None when there are none."""
    found = list(codes)
    return max(found, key=TRUST_ORDER.index) if found else None


# ---------------------------------------------------------------- an answer's code
# The rule of AE-04 (ASK_ENGINE_SPEC §7), here since AE-06 so the api reads an older answer by it
# too: how far an answer can be trusted, set by the server for every path.
#
# Each path still writes its figures and blocks with the sentence a person reads ("Theo chỉ số
# chuẩn", "Khám phá (truy vấn do mô hình viết)", …); `code_of` reads that sentence as a TrustCode in
# this one place, so no screen has to. An answer's code is the weakest of its figures' and blocks'
# (ARCHITECTURE §9.5): one exploratory number makes the whole answer exploratory.
#
# * a figure that is not shown (masked because its data product is overdue, or with no data in
#   time, BR-178) is INSUFFICIENT;
# * a figure worked out from others (`source.expr`: a change, a share) is DERIVED when what it was
#   worked out from is at least GOVERNED, and as weak as that otherwise;
# * a refusal or a clarification is INSUFFICIENT; an answer with no figure and no block code is
#   DOCUMENT when it cites passages, INSUFFICIENT when it does not.
#
# `trust` returns the code and the envelope qa.answer keeps beside it: the path, each figure's and
# block's code and why an agent turn stopped early. The rest of the AnswerEnvelope (text, figures,
# blocks, citations) is in the answer's own columns; the API assembles it (AE-06), the receipt seals
# it (AE-05).

Path = Literal["verified", "governed", "agentic", "explore", "document"]

# The sentences the worker writes (questions.STANDARD, NOT_STANDARD, OUT_OF_SCOPE;
# sqlgen/verified.TRUST_VERIFIED; sqlgen/answer.TRUST; agentic/workspace.TRUST_LABEL and
# FORECAST_LABEL), each with
# its code. tests/unit/worker_agent/test_envelope.py holds every one of them to this table.
LABELS: dict[str, TrustCode] = {
    "Đã kiểm (truy vấn đã duyệt)": TrustCode.VERIFIED,
    "Theo chỉ số chuẩn": TrustCode.GOVERNED,
    "chưa có định nghĩa chuẩn": TrustCode.EXPLORATORY,
    "Ngoài phạm vi data product, không có biên nhận": TrustCode.EXPLORATORY,
    "Khám phá (truy vấn do mô hình viết)": TrustCode.EXPLORATORY,
    "SQL thăm dò, chưa qua chỉ số chuẩn": TrustCode.EXPLORATORY,
    "Dự báo thử nghiệm, chưa phải số liệu thực": TrustCode.EXPLORATORY,
    "Tính thêm từ dữ liệu đã truy vấn": TrustCode.DERIVED,
    "Theo tài liệu": TrustCode.DOCUMENT,
}
NO_ANSWER = ("refusal", "clarification")


def code_of(label: Any) -> TrustCode | None:
    """The code of a trust sentence; None for no sentence or one this table does not know."""
    return LABELS.get(str(label).strip()) if label else None


def _at_least(code: TrustCode, bar: TrustCode) -> bool:
    return TRUST_ORDER.index(code) <= TRUST_ORDER.index(bar)


def figure_code(figure: Mapping[str, Any]) -> TrustCode | None:
    if figure.get("display_state") not in (None, "shown"):
        return TrustCode.INSUFFICIENT
    base = code_of(figure.get("trust"))
    source = figure.get("source")
    if base is not None and isinstance(source, Mapping) and source.get("expr"):
        return TrustCode.DERIVED if _at_least(base, TrustCode.GOVERNED) else base
    return base


def block_code(block: Mapping[str, Any]) -> TrustCode | None:
    return code_of(block.get("trust_label") or block.get("trust"))


def trust(
    path: Path | None,
    kind: str,
    *,
    figures: Sequence[Mapping[str, Any]] = (),
    blocks: Sequence[Mapping[str, Any]] = (),
    cited: bool = False,
    stopped: str | None = None,
    ceiling: TrustCode | None = None,
    why: Sequence[str] = (),
) -> tuple[TrustCode, dict[str, Any]]:
    """(the answer's code, the envelope qa.answer keeps).

    `ceiling` (AE-21) is the most the data behind the answer allows, whatever its figures say:
    a table read with a quality warning (a failing lower rule, or a block a steward acknowledged)
    caps the answer at EXPLORATORY. `why` names the tables; the envelope keeps both."""
    by_figure = {str(f.get("key") or i): figure_code(f) for i, f in enumerate(figures)}
    by_block = [block_code(b) if isinstance(b, Mapping) else None for b in blocks]
    if kind in NO_ANSWER:
        code = TrustCode.INSUFFICIENT
    else:
        found = weakest(c for c in [*by_figure.values(), *by_block] if c is not None)
        code = found or (TrustCode.DOCUMENT if cited else TrustCode.INSUFFICIENT)
        if ceiling is not None and found is not None:
            code = weakest([code, ceiling]) or code
    envelope: dict[str, Any] = {
        "version": 1,
        "path": path,
        "trust_code": code.value,
        "figures": {k: c.value if c else None for k, c in by_figure.items()},
        "blocks": [c.value if c else None for c in by_block],
    }
    if stopped is not None:
        envelope["stopped"] = stopped
    if ceiling is not None:
        envelope["ceiling"] = {"trust_code": ceiling.value, "why": list(why)}
    return code, envelope
