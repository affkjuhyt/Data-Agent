"""A condition on a number a question asks for (GEN-22, read with the packs of GEN-31): "khoản vay
có giá trị trên 1 tỷ", "orders with an amount over 1 billion", "giá trị từ 500 triệu đến 1 tỷ".

Read before the rule pass and taken out of the question, like a grain: "1 tỷ" left in would be
read as nothing, and "trên" as a filler. Only a dimension of the Data Space whose column is a
number is compared, and it must be named: a bare "trên 1 tỷ" says what is over a billion no
more than a person reading it would know. The conditions become compiler.Filter; the compiler
checks the metric allows the dimension (BR-81), as for any filter.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass

from services.worker_agent.semantic.compiler import Filter
from services.worker_agent.understand.packs import PACKS
from services.worker_agent.understand.text import norm

_NUMBER = r"(\d+(?:[.,]\d+)*)"
_LINK = r"(?:\s+(?:la|co|is|of|with|có|là))?"


@dataclass(frozen=True)
class FoundCondition:
    condition: Filter
    label: str  # as the person wrote it, e.g. "giá trị trên 1 tỷ"


@dataclass(frozen=True)
class FoundConditions:
    conditions: list[FoundCondition]
    # The question without the conditions' words, as the person wrote the rest.
    rest: str


def _flat(text: str) -> str:
    """Lower case, no accents, one character for one character of the NFC text, so a match's
    span is the same span of what the person wrote."""
    out = []
    for c in unicodedata.normalize("NFC", text).lower():
        base = unicodedata.normalize("NFD", c)[0]
        out.append("d" if c == "đ" else base)
    return "".join(out)


def to_number(written: str, multiplier: float) -> float:
    """A separator followed by three digits groups thousands ("1.500 tỷ", "1,500 million",
    "1.000.000"); one followed by other than three is the decimal mark ("1,5 tỷ", "1.5 billion").
    The same in either language, so the org's decimal mark need not be known."""
    groups = re.split(r"[.,]", written)
    if len(groups) == 2 and len(groups[1]) != 3:
        value = float(f"{groups[0]}.{groups[1]}")
    else:
        value = float("".join(groups))
    value *= multiplier
    return float(int(value)) if value.is_integer() else value


def find_conditions(question: str, numeric: Mapping[str, str]) -> FoundConditions | None:
    """`numeric`: the normalised names of the space's number dimensions → their codes."""
    text = unicodedata.normalize("NFC", question)
    flat = _flat(text)
    found: list[FoundCondition] = []
    spans: list[tuple[int, int]] = []
    names = sorted(numeric, key=len, reverse=True)
    for pack in PACKS:
        units = "|".join(sorted(map(re.escape, pack.units), key=len, reverse=True))
        unit = rf"(?:\s*({units})\b)?" if units else "()"
        amount = rf"{_NUMBER}{unit}"
        for name in names:
            written_name = r"\s+".join(map(re.escape, name.split()))
            for between in pack.between:
                shape = between.format(low=amount, high=amount).replace(" ", r"\s*")
                for m in re.finditer(rf"\b{written_name}{_LINK}\s+{shape}", flat):
                    if any(s < m.end() and m.start() < e for s, e in spans):
                        continue
                    # "từ 1 đến 2 tỷ": a low end without its unit takes the high end's.
                    high_unit = pack.units.get(m.group(4) or "", 1.0)
                    low = to_number(m.group(1), pack.units.get(m.group(2) or "", high_unit))
                    high = to_number(m.group(3), high_unit)
                    found.append(
                        FoundCondition(
                            Filter(numeric[name], "between", (low, high)),
                            text[m.start() : m.end()],
                        )
                    )
                    spans.append((m.start(), m.end()))
            for words, op in pack.comparators:
                pattern = rf"\b{written_name}{_LINK}\s*(?:{words})\s*{amount}"
                for m in re.finditer(pattern, flat):
                    if any(s < m.end() and m.start() < e for s, e in spans):
                        continue
                    value = to_number(m.group(1), pack.units.get(m.group(2) or "", 1))
                    found.append(
                        FoundCondition(
                            Filter(numeric[name], op, (value,)), text[m.start() : m.end()]
                        )
                    )
                    spans.append((m.start(), m.end()))
    if not found:
        return None
    rest = text
    for start, end in sorted(spans, reverse=True):
        rest = rest[:start] + " " + rest[end:]
    return FoundConditions(found, " ".join(rest.split()))


def numeric_dimensions(dimensions: Mapping[str, object]) -> dict[str, str]:
    """The number dimensions of a compiler layer, by normalised name and code."""
    out: dict[str, str] = {}
    for code, dim in dimensions.items():
        if getattr(dim, "value_type", None) in ("integer", "decimal") and not getattr(
            dim, "is_time", False
        ):
            out[norm(str(getattr(dim, "name", code)))] = code
            out.setdefault(norm(code.replace("_", " ")), code)
    return out
