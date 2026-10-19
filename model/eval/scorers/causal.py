"""AC-03-01-07: what a "vì sao" answer may conclude — offline, no model involved.

The decomposition skill (worker-agent skills/decomposition.py) answers "vì sao NPL tăng?" with
each member's contribution to the change. A contribution is arithmetic, not a cause: the answer
must carry its method and its confidence, add up to the change, and never word a contribution
as what caused it. Each case is a "vì sao" question with the two periods' rows; the REAL
shape_of / decompose / result_of produce the answer, and three numbers come out:

- causal_claim_rate: answers that word a contribution as a cause ("gây ra", "dẫn đến",
  "nguyên nhân là", "vì … nên", …), the negation the method states excepted;
- why_method_coverage: answers that carry the method, the confidence and a reconciled total;
- why_decomposition_accuracy: the direction of the change and the largest contributor are
  the ones the case expects, written against the data, not copied from the output. A case
  expecting {"refused": true} — a metric whose contributions cannot add up, such as a distinct
  count — is right only when the skill refuses to decompose it.
"""

from __future__ import annotations

import decimal
import re
import unicodedata
from typing import Any

from model.eval import _paths

_paths.ensure()

# Causal wording, on the accent-free text. "do tỷ lệ … thay đổi" is the rate/mix split of the
# arithmetic, not a claim about the world, so a bare "do" is not on the list.
_CAUSAL = re.compile(
    r"\b(?:gay ra|dan den|dan toi|khien cho|khien|lam cho|boi vi|vi vay|vi the|"
    r"nguyen nhan (?:la|chinh|chu yeu)|la nguyen nhan|bat nguon tu|xuat phat tu)\b"
)
# The method's own disclaimer names "nguyên nhân" to deny it; that sentence is allowed.
_DISCLAIMER = re.compile(r"khong phai nguyen nhan|ket luan nguyen nhan can kiem dinh")


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFD", text.lower().replace("đ", "d"))
    return "".join(c for c in text if unicodedata.category(c) != "Mn")


def causal_sentences(text: str) -> list[str]:
    """The sentences that word a contribution as a cause."""
    found = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        plain = _plain(sentence)
        if _CAUSAL.search(plain) and not _DISCLAIMER.search(plain):
            found.append(sentence)
    return found


def _values(
    rows: dict[str, list[Any]],
) -> dict[str, tuple[decimal.Decimal, decimal.Decimal | None]]:
    return {
        member: (
            decimal.Decimal(str(pair[0])),
            None if len(pair) < 2 or pair[1] is None else decimal.Decimal(str(pair[1])),
        )
        for member, pair in rows.items()
    }


def _answer(case: dict[str, Any]) -> dict[str, Any] | None:
    """The skill's answer, or None when it refuses the metric as not decomposable."""
    from services.worker_agent.skills.decomposition import (
        NotDecomposable,
        decompose,
        result_of,
        shape_of,
    )

    try:
        shape = shape_of(case["expression"], case["measures"])
    except NotDecomposable:
        return None
    d = decompose(shape, _values(case["base"]), _values(case["current"]))
    return result_of(
        d,
        shape,
        metric_name=case["metric"],
        dimension_name=case["dimension"],
        unit=case.get("unit"),
        decimals=int(case.get("decimals", 2)),
        base_label=case["base_label"],
        current_label=case["current_label"],
        skipped=[],
    )


def score(cases: list[dict[str, Any]]) -> dict[str, Any]:
    causal: list[dict[str, Any]] = []
    uncovered: list[str] = []
    wrong: list[dict[str, Any]] = []
    for case in cases:
        result = _answer(case)
        expect = case["expect"]
        if expect.get("refused") or result is None:
            if not (expect.get("refused") and result is None):
                wrong.append({"id": case["id"], "refused": result is None, "expect": expect})
            continue
        said = " ".join([result["summary"], result["decomposition"]["summary"], result["method"]])
        claims = causal_sentences(said)
        if claims:
            causal.append({"id": case["id"], "sentences": claims})
        if not (result.get("method") and result.get("confidence") and result.get("reconciled")):
            uncovered.append(case["id"])
        top = result["decomposition"]["contributions"][0]["label"]
        direction = next(
            (w for w in ("tăng", "giảm", "không đổi") if f" {w} " in f" {result['summary']} "),
            None,
        )
        if top != expect["top"] or direction != expect["direction"]:
            wrong.append({"id": case["id"], "top": top, "direction": direction, "expect": expect})
    n = len(cases) or 1
    return {
        "cases": len(cases),
        "causal_claims": causal,
        "uncovered": uncovered,
        "wrong": wrong,
        "metrics": {
            "causal_claim_rate": len(causal) / n,
            "why_method_coverage": 1 - len(uncovered) / n,
            "why_decomposition_accuracy": 1 - len(wrong) / n,
        },
    }
