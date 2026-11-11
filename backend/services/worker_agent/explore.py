"""When the asked period has no data, look for the periods that do (an Ask turn's recovery step).

A governed answer that comes back empty for its period used to end in "Không có dữ liệu trong
khoảng thời gian được hỏi." An analyst would first look where the data is: the same metric, same
filters, cut along time at the grain of the question with no period, then answer for the nearest
period that has a figure and say so plainly. That probe and the re-run both go through the
compiler, so the answer stays on the certified metric; only the period differs, and the answer's
text and caveats name both periods.
"""

from __future__ import annotations

import datetime
import re
import unicodedata
from collections.abc import Sequence
from typing import Any

from dateutil.relativedelta import relativedelta

from libs.language import norm
from services.worker_agent.answer.comparison import period_label
from services.worker_agent.semantic.compiler import Period

D = datetime.date


def grain_of(period: Period) -> str:
    """The grain of the asked period: a year, a quarter, a month, a week or a day."""
    days = (period.end - period.start).days
    if days >= 360:
        return "year"
    if days >= 88:
        return "quarter"
    if days >= 28:
        return "month"
    if days >= 7:
        return "week"
    return "day"


def _add_months(start: D, months: int) -> D:
    # relativedelta ships no types: the sum is annotated so it stays a date to mypy.
    shifted: D = start + relativedelta(months=months)
    return shifted.replace(day=1)


def bucket(start: D, grain: str) -> Period:
    """The period one grain long that begins at `start` (a probe row's time bucket)."""
    if grain == "year":
        return Period(start, D(start.year + 1, start.month, start.day))
    if grain == "quarter":
        return Period(start, _add_months(start, 3))
    if grain == "month":
        return Period(start, _add_months(start, 1))
    if grain == "week":
        return Period(start, start + datetime.timedelta(days=7))
    return Period(start, start + datetime.timedelta(days=1))


# The compiler's time labels (semantic/compiler.py _at_grain): '2014', '2014-Q3', '2014-08',
# '2014-08-04'. A fiscal label ('FY…') is left out: its calendar start is not in the text.
_LABEL = re.compile(r"^(\d{4})(?:-Q([1-4])|-(\d{2})(?:-(\d{2}))?)?$")


def _as_date(value: Any) -> D | None:
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, D):
        return value
    if not isinstance(value, str):
        return None
    match = _LABEL.match(value[:10])
    if match is None:
        return None
    year, quarter, month, day = match.groups()
    try:
        if quarter:
            return D(int(year), 3 * int(quarter) - 2, 1)
        return D(int(year), int(month or 1), int(day or 1))
    except ValueError:
        return None


def periods_with_data(rows: Sequence[Sequence[Any]]) -> list[D]:
    """The time buckets of the probe (time column, value) that hold a figure, oldest first."""
    found = {
        start
        for row in rows
        if len(row) >= 2 and row[-1] is not None and (start := _as_date(row[0])) is not None
    }
    return sorted(found)


def nearest(found: Sequence[D], asked: Period) -> D | None:
    """The latest period before the asked one; when the data all comes later, the first one."""
    before = [start for start in found if start < asked.start]
    if before:
        return before[-1]
    after = [start for start in found if start >= asked.end]
    return after[0] if after else None


def note(asked: Period, chosen: Period, found: Sequence[D], grain: str) -> str:
    """What the answer says first: the period asked had no data, where the data is, what is shown."""
    first, last = bucket(found[0], grain), bucket(found[-1], grain)
    span = (
        f"dữ liệu chỉ có {period_label(first)}"
        if first == last
        else f"dữ liệu có từ {period_label(first)} đến {period_label(last)}"
    )
    return (
        f"Không có dữ liệu cho {period_label(asked)}; {span}. "
        f"Dưới đây là số của {period_label(chosen)}."
    )


def _folded(text: str) -> tuple[str, list[int]]:
    """`text` folded as libs.language.fold folds it, character by character, with the index in
    `text` of each folded character, so a span found in the folded text maps back to the words."""
    out: list[str] = []
    where: list[int] = []
    for at, ch in enumerate(unicodedata.normalize("NFC", text)):
        base = unicodedata.normalize("NFD", ch.lower().replace("đ", "d"))
        for c in base:
            if unicodedata.category(c) != "Mn":
                out.append(c)
                where.append(at)
    return "".join(out), where


def _span_in(text: str, folded_words: str) -> tuple[int, int] | None:
    """Where in `text` (NFC) the folded words are, as a slice; None when they are not."""
    folded, where = _folded(text)
    at = folded.find(folded_words) if folded_words else -1
    if at < 0:
        return None
    return where[at], where[at + len(folded_words) - 1] + 1


def as_written(question: str, name: str) -> str:
    """The folded name as the person wrote it in the question ("Atlantis"), else as it is."""
    text = unicodedata.normalize("NFC", question)
    found = _span_in(text, name)
    return text[found[0] : found[1]] if found else name


# Words that only place a name ("ở Atlantis", "tại Hà Nội"): taken out with it.
_PLACING = re.compile(r"(?:\b(?:ở|tại|của|trong|thuộc)\s+)?[“\"]?$", re.IGNORECASE)


def without(question: str, name: str) -> str:
    """The question without a name found nowhere in the data, and the word placing it, so the
    model does not write "ở Atlantis" beside a figure that is not filtered by it."""
    text = unicodedata.normalize("NFC", question)
    found = _span_in(text, name)
    if found is None:
        return question
    head, tail = text[: found[0]], text[found[1] :]
    head = _PLACING.sub("", head)
    tail = re.sub(r"^[”\"]", "", tail)
    return " ".join(f"{head} {tail}".split())


def retold(question: str, span: str, shown: str) -> str:
    """The question as the model is shown it when another period is answered: the words the asked
    period was read from (its folded `span`) replaced by the period shown, so the answer does not
    say "năm nay" beside 2014's figures; not found, the period shown follows the question."""
    text = unicodedata.normalize("NFC", question)
    found = _span_in(text, span)
    if found is None:
        return f"{question} (số của {shown})"
    return text[: found[0]] + shown + text[found[1] :]


_QUOTED = re.compile(r"“([^”]+)”|\"([^\"]+)\"")


def _capitalised_runs(text: str) -> list[str]:
    """Runs of words that begin with a capital letter, the question's first word excepted."""
    runs: list[list[str]] = []
    current: list[str] = []
    for at, word in enumerate(re.findall(r"[^\W\d_][\w]*|\S", text)):
        if at > 0 and word[:1].isupper():
            current.append(word)
            continue
        if current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return [" ".join(run) for run in runs]


def unread_names(question: str, rest: str, *, known: str = "") -> list[str]:
    """Names in the question the reader did not place, folded: quoted phrases and runs of
    capitalised words (the first word excepted) whose words are all still in `rest`, the folded
    words left unread. These are the words worth looking up among the members in the data; words
    of `known` (the metric's own name: "Internet" of "Số khách hàng mua qua Internet") are not."""
    left = set(rest.split()) - set(norm(known).split())
    text = unicodedata.normalize("NFC", question)
    quoted = [m.group(1) or m.group(2) for m in _QUOTED.finditer(text)]
    names: list[str] = []
    for raw in [*quoted, *_capitalised_runs(_QUOTED.sub(" ", text))]:
        name = norm(raw)
        if name and name not in names and all(w in left for w in name.split()):
            names.append(name)
    return names


def matching_member(members: Sequence[Any], name: str) -> str | None:
    """The member whose folded words are the name's (exact, so "america" is not North America)."""
    for member in members:
        if member is not None and norm(str(member)) == name:
            return str(member)
    return None
