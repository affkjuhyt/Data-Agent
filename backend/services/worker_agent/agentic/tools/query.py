"""Tools that read data: a certified metric through the compiler (governed), or SQL the model
writes over the linked tables (exploratory, sqlgen's N-version vote). Both run through the
executor, so row and column policies apply and every run is recorded (invariant 15)."""

from __future__ import annotations

import dataclasses
import datetime
import json
import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from libs.contracts.v1.provider import ProviderRequest
from libs.dataagent_db import scoped_session
from libs.dataagent_db.models import catalog
from libs.sql_guard import SqlRejected, dialect_of
from services.worker_agent.agentic import prompts
from services.worker_agent.agentic.grounded import unnamed
from services.worker_agent.agentic.tools.base import Tool, ToolContext, ToolResult
from services.worker_agent.failures import is_transient
from services.worker_agent.llm import ModelUnavailable
from services.worker_agent.query.executor import QueryOutcome, run_query
from services.worker_agent.semantic import analysis, compare, coverage
from services.worker_agent.semantic import compiler as c
from services.worker_agent.sqlgen import audit_rules
from services.worker_agent.sqlgen.answer import _keep_one_source
from services.worker_agent.sqlgen.check import fingerprint
from services.worker_agent.sqlgen.generate import schema_text
from services.worker_agent.sqlgen.run import explore
from services.worker_agent.sqlgen.schema import link
from services.worker_agent.sqlgen.space import load_space
from services.worker_agent.understand import analyst
from services.worker_agent.understand.comparison import find_comparison
from services.worker_agent.understand.grain import GRAIN_LABELS, find_grain
from services.worker_agent.understand.packs.base import iso_answer
from services.worker_agent.understand.resolver import Vocabulary, metric_words, quantity_words
from services.worker_agent.understand.text import norm

ROLE = "svc_qa"
QUERYABLE = ("active", "testing")
# Dimension codes that name a part of the calendar, as a model writes them when it makes one up.
_CALENDAR_PART = re.compile(r"^(ngay|tuan|thang|quy|nam|day|week|month|quarter|year)(_|$)")
# The grains query_metric takes (a fiscal one is read through the period, not the grain).
_GRAINS = frozenset({"day", "week", "month", "quarter", "year"})
# "Top 10 sản phẩm …", "3 đại lý có doanh thu cao nhất", "2 quốc gia … thấp nhất".
_TOP_COUNT = re.compile(r"^(?:top\s*)?(\d{1,3})\s+[a-z]|\btop\s*(\d{1,3})\b")
_CHANGE_ASKED = re.compile(r"\b(?:giam|tang|sut|tut|decrease|increase|drop|rise|grow)\w*\b")
_LOWEST = re.compile(r"\b(?:thap nhat|it nhat|nho nhat|kem nhat|lowest|least|smallest|bottom)\b")


def _calendar_cut(
    question: str, vocab: Vocabulary, allowed: set[uuid.UUID]
) -> dict[str, list[str]]:
    """A part of the calendar the space models as a cut of its own, named with its number in the
    question ("năm tài chính 2013", "quý tài chính 3"): that cut, filtered on that number."""
    text = norm(question)
    out: dict[str, list[str]] = {}
    for d in vocab.dimensions:
        if d.is_time or d.dimension_id not in allowed or not _CALENDAR_PART.match(d.code):
            continue
        if m := re.search(rf"\b{re.escape(norm(d.name))} (\d{{1,4}})\b", text):
            out[d.code] = [m.group(1)]
    return out


# "luỹ kế từ đầu năm đến hết tháng 6/2013", "từ đầu năm tới nay": the year up to the period named.
_FROM_YEAR_START = re.compile(r"\btu dau nam\b")


def _relative_reading(ctx: ToolContext, args: QueryMetricArgs, times: set[str]) -> QueryMetricArgs:
    """The comparison and the span a question names in words, which the 4B planner left out:
    "so với cùng kỳ năm trước" / "so với quý trước" read one period alone (F0, 17/11), "luỹ kế từ
    đầu năm đến hết tháng 6/2013" read June alone. The question names them; the reading follows."""
    found = getattr(ctx, "period", None)
    if found is not None and _FROM_YEAR_START.search(norm(ctx.question)):
        start = datetime.date(found.end.year if found.end.month > 1 else found.end.year - 1, 1, 1)
        if start < found.start:
            asked_cut_along_time = find_grain(ctx.question) is not None or times & set(
                args.group_by
            )
            args = args.model_copy(
                update={
                    "period": f"{start.isoformat()}/{found.end.isoformat()}",
                    # One figure for the span unless the question cuts it along time.
                    **({} if asked_cut_along_time else {"analysis": None, "grain": None}),
                }
            )
    compared = find_comparison(ctx.question)
    if compared is not None and args.compare_to is None and args.analysis is None:
        update: dict[str, Any] = {"compare_to": compared.compare_to}
        if not args.period and found is not None:
            update["period"] = f"{found.start.isoformat()}/{found.end.isoformat()}"
        args = args.model_copy(update=update)
    return args


def named_members(
    question: str, vocab: Vocabulary, allowed: set[uuid.UUID]
) -> dict[str, list[str]]:
    """The members the question names whole, by the value index, on cuts the metric allows:
    "loại Specialty Bike Shop", "khu vực Pacific", "Rent". The longest name wins where two
    overlap ("Trade Sales" over "Sales"); one-letter or numeric codes are never read as names."""
    text = f" {norm(question)} "
    dims = {d.code: d for d in vocab.dimensions if d.dimension_id in allowed and not d.is_time}
    # A member named like a metric ("Inventory", "Gross Margin", "Revenue" are accounts too)
    # is read as the metric, not as a filter.
    metric_phrases = {f" {norm(m.name)} " for m in vocab.metrics} | {
        f" {p} " for p, t in vocab.synonyms.items() if t[0] == "metric"
    }
    found: list[tuple[str, str, str]] = []
    for v in vocab.values:
        label = norm(v.label)
        if v.dimension_code not in dims or len(label) < 3 or label.replace(" ", "").isdigit():
            continue
        if any(f" {label} " in phrase for phrase in metric_phrases):
            continue
        if f" {label} " in text:
            found.append((label, v.dimension_code, v.value_code))
    # A member's approved synonyms name it too ("khách hàng nữ" → Giới tính F): a member whose
    # values are withheld from the index (personal data) is still named by them.
    for phrase, target in vocab.synonyms.items():
        if target[0] == "value" and target[1] in dims and f" {phrase} " in text:
            found.append((phrase, str(target[1]), str(target[2])))
    found.sort(key=lambda f: -len(f[0]))
    out: dict[str, list[str]] = {}
    taken: list[str] = []
    for label, dim, code in found:
        if any(len(longer) > len(label) and f" {label} " in f" {longer} " for longer in taken):
            continue
        taken.append(label)
        out.setdefault(dim, []).append(code)
    # A name held by several cuts ("Pacific" as a region and a territory group) says no cut.
    by_name: dict[str, list[str]] = {}
    for dim, codes in out.items():
        for code in codes:
            by_name.setdefault(norm(code), []).append(dim)
    return {
        dim: [c for c in codes if len(by_name[norm(c)]) == 1]
        for dim, codes in out.items()
        if any(len(by_name[norm(c)]) == 1 for c in codes)
    }


_BREAKDOWN = re.compile(r"\b(?:theo|tung|moi|cac|nhung|by|per|each)\b")


def _asks_breakdown(question: str, vocab: Vocabulary, top: tuple[int, bool] | None) -> bool:
    """Whether the question asks figures by a cut at all: "theo …", "từng/mỗi/các …", a count
    ("5 sản phẩm …"), or a cut named whole ("Cơ cấu doanh thu nhóm sản phẩm")."""
    text = f" {norm(question)} "
    if top is not None or _BREAKDOWN.search(text):
        return True
    # A metric's name is no cut's: "Doanh thu đại lý" holds the cut name "Đại lý".
    # Longest first: "Doanh thu" taken out of "Doanh thu đại lý" left "đại lý", a cut's name.
    for name in sorted((norm(m.name) for m in vocab.metrics), key=len, reverse=True):
        text = text.replace(f" {name} ", " ")
    names = [norm(d.name) for d in vocab.dimensions if not d.is_time]
    names += [p for p, t in vocab.synonyms.items() if t[0] == "dimension"]
    return any(f" {name} " in text for name in names if name)


def _glossed(question: str, missing: list[str], members: dict[str, list[str]]) -> list[str]:
    """The unnamed words left once a member's gloss is set aside: "Chi phí lương Salaries",
    "Chi phí thuê Rent", "Phúc lợi nhân viên Employee Benefits" name an account in English after
    saying it in Vietnamese, and "phí", "thuê" were refused as quantities no metric measures
    (18/11). Up to three words right before a member the reading filters on are its gloss."""
    if not missing or not members:
        return missing
    words = norm(question).split()
    glossed: set[str] = set()
    for code in (member for codes in members.values() for member in codes):
        name = norm(str(code)).split()
        for i in range(len(words) - len(name) + 1):
            if words[i : i + len(name)] == name:
                glossed |= set(words[max(0, i - 3) : i])
    return [w for w in missing if w not in glossed]


def cut_after_count(question: str, vocab: Vocabulary, allowed: set[uuid.UUID]) -> str | None:
    """The cut a ranking question counts ("3 nhóm sản phẩm có …", "Top 5 tiểu nhóm sản phẩm …"):
    the cut whose whole name or synonym follows the count, the longest that does."""
    text = norm(question)
    m = _TOP_COUNT.search(text)
    if m is None:
        return None
    after = text[m.end(1) if m.group(1) else m.end(2) :].strip()
    names: list[tuple[str, str]] = [
        (norm(d.name), d.code)
        for d in vocab.dimensions
        if d.dimension_id in allowed and not d.is_time
    ]
    by_id = {str(d.dimension_id): d.code for d in vocab.dimensions if d.dimension_id in allowed}
    names += [
        (phrase, by_id[target[1]])
        for phrase, target in vocab.synonyms.items()
        if target[0] == "dimension" and target[1] in by_id
    ]
    hits = [(name, code) for name, code in names if f"{after} ".startswith(f"{name} ")]
    return max(hits, key=lambda h: len(h[0]))[1] if hits else None


def asked_top(question: str) -> tuple[int, bool] | None:
    """How many members a ranking question asks for, and whether the highest first."""
    text = norm(question)
    m = _TOP_COUNT.search(text)
    if m is None:
        return None
    count = int(m.group(1) or m.group(2))
    if not 0 < count <= 100:
        return None
    return count, _LOWEST.search(text) is None


def named_cut(question: str, vocab: Vocabulary, allowed: set[uuid.UUID]) -> str | None:
    """The cut the question names after "theo"/"by" ("cơ cấu doanh thu theo nhóm sản phẩm"), by
    its full name; the longest when several fit ("tiểu nhóm sản phẩm" over "sản phẩm")."""
    text = f" {norm(question)} "
    best = None
    for d in vocab.dimensions:
        if d.is_time or d.dimension_id not in allowed:
            continue
        name = norm(d.name)
        named = bool(name) and re.search(rf" (theo|by) {re.escape(name)} ", text) is not None
        if named and (best is None or len(name) > len(norm(best.name))):
            best = d
    return best.code if best else None


_TWO_YEARS = re.compile(r"\bnam ((?:19|20)\d{2}) (?:va|voi|so voi|,) (?:nam )?((?:19|20)\d{2})\b")


def two_years(question: str) -> tuple[int, int] | None:
    """The two adjacent years a question compares ("năm 2012 và 2013"), earlier first."""
    m = _TWO_YEARS.search(norm(question))
    if m is None:
        return None
    a, b = sorted((int(m.group(1)), int(m.group(2))))
    return (a, b) if b - a == 1 else None


_MONTH = re.compile(r"\bthang (\d{1,2})[/-]((?:19|20)\d{2})\b")
_QUARTER = re.compile(r"\b(?:quy |q)([1-4])[/-]((?:19|20)\d{2})\b")
_ANY_YEARS = re.compile(r"\bnam ((?:19|20)\d{2}) (?:va|voi|so voi|,) (?:nam )?((?:19|20)\d{2})\b")

Side = tuple[c.Period, str]


def _months_on(day: datetime.date, months: int) -> datetime.date:
    index = day.year * 12 + day.month - 1 + months
    return datetime.date(index // 12, index % 12 + 1, 1)


def two_periods(question: str) -> tuple[Side, Side] | None:
    """Two periods of one kind a question compares — months ("tháng 8/2013 với tháng 3/2013"),
    quarters ("quý 3/2013 so với quý 4/2012") or years ("năm 2011 và 2013") — earlier first, each
    with the words it is said by. Adjacent years are two_years' (read as one comparison)."""
    text = norm(question)
    found: list[Side] = []
    for m, y in dict.fromkeys(_MONTH.findall(text)):
        if 1 <= int(m) <= 12:
            start = datetime.date(int(y), int(m), 1)
            found.append((c.Period(start, _months_on(start, 1)), f"tháng {int(m)}/{y}"))
    if len(found) != 2:
        found = []
        for q, y in dict.fromkeys(_QUARTER.findall(text)):
            start = datetime.date(int(y), 3 * int(q) - 2, 1)
            found.append((c.Period(start, _months_on(start, 3)), f"quý {q}/{y}"))
    if len(found) != 2:
        found = []
        years = _ANY_YEARS.search(text)
        if years and years.group(1) != years.group(2) and two_years(question) is None:
            for y in (int(years.group(1)), int(years.group(2))):
                found.append((c.Period(datetime.date(y, 1, 1), datetime.date(y + 1, 1, 1)), str(y)))
    if len(found) != 2:
        return None
    a, b = sorted(found, key=lambda s: s[0].start)
    return a, b


def side_by_side(
    codes: list[str],
    labels: list[str],
    rows_a: list[list[Any]],
    rows_b: list[list[Any]],
    names: tuple[str, str],
) -> tuple[list[str], list[list[Any]]]:
    """Two readings of one metric, one per period, as one table: the cuts, then each period's
    value ("Doanh thu 2011", "Doanh thu 2013"). A member one period lacks is empty there."""
    value = codes.index("value")
    keys = [i for i in range(len(codes)) if i != value]
    merged: dict[tuple[Any, ...], list[Any]] = {}
    for row in rows_a:
        merged[tuple(row[i] for i in keys)] = [row[value], None]
    for row in rows_b:
        merged.setdefault(tuple(row[i] for i in keys), [None, None])[1] = row[value]
    columns = [labels[i] for i in keys] + list(names)
    return columns, [[*key, *values] for key, values in merged.items()]


_COLUMN_LABEL = {
    "base_value": "{metric} (kỳ so sánh)",
    "share": "Tỷ trọng",
    "cumulative": "Luỹ kế",
}


class QueryMetricArgs(BaseModel):
    metric: str = Field(description="mã chỉ số (từ search_catalog)")
    group_by: list[str] = Field(
        default_factory=list, description="mã các chiều để chia, có thể rỗng"
    )
    filters: dict[str, list[str]] = Field(
        default_factory=dict, description="{mã chiều: [mã giá trị]} (từ search_values)"
    )
    period: str | None = Field(
        default=None,
        description="kỳ ISO: '2013', '2013-Q3', '2013-09', '2013-01-01/2013-07-01' (cuối loại trừ) hoặc null",
    )
    grain: Literal["day", "week", "month", "quarter", "year"] | None = Field(
        default=None, description="độ chi tiết khi chia theo chiều thời gian, hoặc null"
    )
    top_n: int | None = Field(default=None, description="chỉ lấy N dòng đầu, hoặc null")
    top_desc: bool = True
    compare_to: Literal["previous_period", "same_period_last_year"] | None = Field(
        default=None, description="so với kỳ trước / cùng kỳ năm trước, hoặc null"
    )
    analysis: Literal["share_of_total", "running_total"] | None = Field(
        default=None, description="tỷ trọng trên tổng / luỹ kế, hoặc null"
    )
    title: str | None = Field(default=None, description="tên ngắn của bảng kết quả")

    @model_validator(mode="before")
    @classmethod
    def _null_words(cls, data: Any) -> Any:
        """The planner writes "none" or "null" as text for an optional argument it means to leave
        out; on 16/11/2026 `analysis: "none"` failed the schema, the step failed, and a stock
        question the catalogue answers was never read."""
        if not isinstance(data, dict):
            return data
        return {
            k: None if k in _OPTIONAL and isinstance(v, str) and v.strip().lower() in _NULLS else v
            for k, v in data.items()
        }


_NULLS = frozenset({"", "none", "null", "nil"})
_OPTIONAL = frozenset({"period", "grain", "top_n", "compare_to", "analysis", "title"})


async def _source_for(ctx: ToolContext, dataset_id: uuid.UUID) -> catalog.Source | None:
    from services.worker_agent import questions  # the internal workspace source lives there

    dataset = ctx.layer.datasets[dataset_id]
    if dataset.schema_name == "workspace" and dataset.source_id is None:
        return questions._internal_workspace_source(ctx.space_id, ctx.org_id)
    async with scoped_session(ROLE, ctx.org_id, ctx.user_id) as session:
        source = await session.get(catalog.Source, dataset.source_id)
        if source is not None:
            session.expunge(source)
    return source


def _secrets() -> Any:
    from services.worker_agent import questions

    return questions.secrets()


class QueryMetric(Tool):
    name = "query_metric"
    title = "Truy vấn chỉ số"
    description = (
        "lấy số liệu của một chỉ số chuẩn (đã duyệt), có thể chia theo chiều, lọc, theo kỳ, "
        "so sánh kỳ, tỷ trọng hoặc luỹ kế; kết quả là một dataset tin cậy 'certified'"
    )
    Args = QueryMetricArgs

    async def run(self, ctx: ToolContext, args: QueryMetricArgs) -> ToolResult:  # type: ignore[override]
        visible = analyst._visible(ctx.layer)
        entry = visible.get(args.metric)
        if entry is None:
            # A metric the asker is denied reads exactly like one that does not exist, since the
            # observation reaches the timeline: naming it would tell the asker which denied
            # metrics there are. Only the failure kind differs, so it is not replanned.
            denied = any(
                m.code == args.metric and m.metric_id in ctx.layer.blocked_metric_ids
                for m in ctx.layer.vocabulary.metrics
            )
            return ToolResult(
                False,
                f"không có chỉ số {args.metric} (hoặc bạn không được xem); dùng search_catalog",
                failure="forbidden" if denied else "logic",
            )
        vocab = ctx.layer.vocabulary
        version = ctx.layer.versions[entry.metric_id]
        times_all = {d.code for d in vocab.dimensions if d.is_time}
        if not [g for g in args.group_by if g not in times_all]:
            # "Cơ cấu doanh thu theo nhóm sản phẩm" read with no cut gave one total, then the
            # grounding check refused "nhóm sản phẩm" as a quantity no metric measures (the 4B
            # planner left group_by empty, 2 runs of 2 on 16/11). The question names the cut;
            # the metric must allow it.
            cut = named_cut(ctx.question, vocab, set(version.allowed_dimension_ids))
            if cut is not None:
                args = args.model_copy(update={"group_by": [*args.group_by, cut]})
        asked_grain = find_grain(ctx.question)
        if (
            asked_grain is not None
            and asked_grain.grain in _GRAINS
            and args.grain != asked_grain.grain
            and not times_all & set(args.group_by)
        ):
            # "Doanh thu Internet theo quý năm 2013" was read with grain=year, one total, then
            # rebuilt with Python and exploratory SQL (F0, 17/11). The question names the grain.
            args = args.model_copy(update={"grain": asked_grain.grain})
        allowed_ids = set(version.allowed_dimension_ids)
        top = asked_top(ctx.question)
        counted = cut_after_count(ctx.question, vocab, allowed_ids) if top else None
        if counted is not None and counted not in args.group_by:
            # "3 nhóm sản phẩm có doanh thu đại lý cao nhất" was cut by Tiểu nhóm sản phẩm
            # (holdout, 18/11): the cut the question counts is the one named after the count.
            args = args.model_copy(
                update={"group_by": [g for g in args.group_by if g in times_all] + [counted]}
            )
        members = named_members(ctx.question, vocab, allowed_ids)
        if members:
            # "Doanh thu đại lý loại Specialty Bike Shop" was cut by product with no filter,
            # "khu vực Pacific" filtered on the territory where Pacific is no member (holdout,
            # 18/11): a member the question names whole is filtered on the cut that holds it.
            named = {norm(c) for codes in members.values() for c in codes}
            filters: dict[str, list[str]] = {}
            misplaced: set[str] = set()
            for dim, values in args.filters.items():
                kept = [v for v in values if dim in members or norm(str(v)) not in named]
                if len(kept) < len(values):
                    misplaced.add(dim)
                if kept:
                    filters[dim] = kept
            for dim, codes in members.items():
                filters[dim] = list(dict.fromkeys([*filters.get(dim, []), *codes]))
            args = args.model_copy(
                update={
                    "filters": filters,
                    "group_by": [g for g in args.group_by if g not in misplaced],
                }
            )
        if args.compare_to is None and not _asks_breakdown(ctx.question, vocab, top):
            # A breakdown the question does not ask ("Tồn kho cuối tháng 12/2013" read by
            # sub-category, "… loại Specialty Bike Shop" by product): one figure, as asked.
            args = args.model_copy(
                update={"group_by": [g for g in args.group_by if g in times_all]}
            )
        if top is not None and [g for g in args.group_by if g not in times_all]:
            # "3 đại lý có doanh thu cao nhất" read again without top_n gave 488 dealers in name
            # order, and the answer called the first three alphabetically the highest (F0, 17/11).
            # The question says how many, and which end.
            args = args.model_copy(update={"top_n": top[0], "top_desc": top[1]})
        # Which year each side of a two-year comparison is, to name its columns.
        side_labels: dict[str, str] = {}
        years = two_years(ctx.question)
        if years and args.compare_to is None and not times_all & set(args.group_by):
            # "So sánh doanh thu theo vùng năm 2012 và 2013" was read as one period summed over
            # both years, or as 2012 alone, and answered "no data for 2012" (16/11). Two adjacent
            # years are the later one against the same period a year before.
            earlier, later = years
            args = args.model_copy(
                update={"period": str(later), "compare_to": "same_period_last_year", "grain": None}
            )
            side_labels = {
                "value": f"{entry.name} {later}",
                "base_value": f"{entry.name} {earlier}",
            }
        if args.compare_to is not None and args.top_n and _CHANGE_ASKED.search(norm(ctx.question)):
            # "Vùng nào giảm nhiều nhất?" over two years was read top_n=1: the compiler ranks by
            # the value, so it kept the largest region, which rose (F4, 17/11). Which member rose
            # or fell most is read from all of them.
            args = args.model_copy(update={"top_n": None})
        # Two periods of one kind compared, other than two adjacent years ("tháng 8/2013 với tháng
        # 3/2013", "năm 2011 và 2013"): read once per period and set side by side here. The planner
        # read them twice and merged them in Python, which the 4B model wrote broken (16/11).
        pair: tuple[Side, Side] | None = None
        if (
            not side_labels
            and args.compare_to is None
            and args.analysis is None
            and not times_all & set(args.group_by)
        ):
            pair = two_periods(ctx.question)
            if pair is not None:
                args = args.model_copy(update={"period": None, "grain": None})
        calendar_cut = _calendar_cut(ctx.question, vocab, set(version.allowed_dimension_ids))
        if calendar_cut and pair is None and not side_labels:
            # "Doanh thu Internet năm tài chính 2013": the space models its fiscal year as a cut
            # (AdventureWorks names it by the year it starts in), so it is read as that cut, not as
            # calendar 2013 or a period the default calendar guesses (F0, 17/11: three items).
            args = args.model_copy(
                update={"filters": {**args.filters, **calendar_cut}, "period": None}
            )
        elif pair is None and not side_labels:
            args = _relative_reading(ctx, args, times_all)
        # A quantity the question asks that no certified metric of the catalogue names ("khách
        # hàng" where only units sold exist): no reading of this one will answer it.
        catalogue: set[str] = set()
        for m in analyst._visible(ctx.layer).values():
            catalogue |= metric_words(m, vocab)
        missing = unnamed(
            quantity_words(
                ctx.question, vocab, today=ctx.today, group_by=args.group_by, filters=args.filters
            ),
            catalogue,
        )
        everywhere = {d.dimension_id for d in vocab.dimensions}
        foreign = {
            dim: codes
            for dim, codes in named_members(ctx.question, vocab, everywhere).items()
            if dim not in {d.code for d in vocab.dimensions if d.dimension_id in allowed_ids}
        }
        if foreign:
            # "Doanh số Trade Sales thực tế" read as Chỉ tiêu doanh số, which has no accounts:
            # "trade" was refused as a quantity and the turn said "no data" (18/11). The member
            # names the cut; the metrics that cut by it are offered for the step read again.
            dim = next(iter(foreign))
            by_id = {d.code: d for d in vocab.dimensions}
            able = [
                m.name
                for m in analyst._visible(ctx.layer).values()
                if by_id[dim].dimension_id in ctx.layer.versions[m.metric_id].allowed_dimension_ids
            ]
            return ToolResult(
                False,
                f"“{', '.join(foreign[dim])}” thuộc chiều {by_id[dim].name}, chỉ số {entry.name}"
                f" không chia theo chiều này; chỉ số chia được theo {by_id[dim].name}:"
                f" {', '.join(able[:5]) or 'không có'}",
                failure="arguments",
            )
        missing = _glossed(ctx.question, missing, members)
        cut_named = [
            d.name
            for d in vocab.dimensions
            if not d.is_time and set(norm(d.name).split()) & set(missing)
        ]
        if (
            missing
            and cut_named
            and all(any(w in norm(name).split() for name in cut_named) for w in missing)
        ):
            # Only a cut's words left ("khu vực", "quốc gia", "sản phẩm"): the reading missed
            # the cut, it is no quantity the catalogue lacks. Refused as a missing metric, the
            # turn said "no data" for "Doanh thu Internet khu vực Europe" (F0, 17/11); as an
            # argument error the step is read again with the cut.
            return ToolResult(
                False,
                f"câu hỏi nêu {', '.join(cut_named[:3])} nhưng cách đọc chưa chia (group_by) hay"
                " lọc (filters) theo chiều đó: đọc lại, chia hoặc lọc theo đúng chiều câu hỏi nêu",
                failure="arguments",
            )
        if missing:
            # RES-04: do not send the planner to invent the quantity with explore_sql.
            return ToolResult(
                False,
                f"không chỉ số chuẩn nào đo '{' '.join(missing)}' mà câu hỏi hỏi; "
                "không tự tính bằng SQL thăm dò — tìm chỉ số khác (search_catalog) hoặc "
                "nói rõ không trả lời được",
                detail={"unnamed": missing},
            )
        period = None
        moved: tuple[c.Period, c.Period] | None = None
        if args.period:
            found = iso_answer(args.period)
            if found is None:
                return ToolResult(
                    False, f"kỳ {args.period} không đúng dạng ISO", failure="arguments"
                )
            period = c.Period(found.start, found.end)
            # The planner was told the asked period has no data and still asked for it: read the
            # latest whole period of the same length instead, and say so (AGC-10). The model
            # proposes the period; where the data is decides.
            move = ctx.period_moves.get(args.metric)
            if move is not None and period.start < move[0].end and move[0].start < period.end:
                moved, period = (period, move[1]), move[1]
        if pair is not None:
            period = pair[0][0]
        allowed = {
            d.code
            for d in ctx.layer.vocabulary.dimensions
            if d.dimension_id in version.allowed_dimension_ids or d.is_time
        }
        # A grain already cuts the period by day, month, quarter or year. A code the catalogue does
        # not have that names a calendar part ("thang_tai_chinh", made up beside nam_tai_chinh)
        # only asks for that cut again, so it is dropped rather than failing the reading.
        known = {d.code for d in ctx.layer.vocabulary.dimensions}
        calendar = [
            g for g in args.group_by if args.grain and g not in known and _CALENDAR_PART.match(g)
        ]
        group_by = [g for g in args.group_by if g not in calendar]
        times = {d.code for d in vocab.dimensions if d.is_time}
        if args.grain and not times & set(group_by) and find_grain(ctx.question):
            # The catalogue the planner reads lists no time dimension, so "theo tháng" arrives as
            # a grain alone, which the compiler would ignore and answer with one total. The cut is
            # along the metric's own time, as the governed path makes it (BR-80). Only when the
            # question asks a cut along time: the model also sets a grain unasked ("cơ cấu theo
            # nhóm" with grain=month), and cutting that by month split each group into months.
            try:
                time_cut = c.time_dimension_of(version, ctx.layer.layer)
            except c.CompileError:
                time_cut = None
            if time_cut is None:
                return ToolResult(
                    False,
                    f"chỉ số {args.metric} không có chiều thời gian để chia theo {args.grain}",
                    failure="logic",
                )
            group_by.append(time_cut.code)
        by_code = {d.code: d for d in vocab.dimensions}
        asked_text = f" {norm(ctx.question)} "
        stray = [
            f
            for f in args.filters
            if period is not None
            and _CALENDAR_PART.match(f)
            and f" {norm(by_code[f].name if f in by_code else f)} " not in asked_text
        ]
        if stray:
            # "Top 10 sản phẩm theo doanh thu năm 2013" was read for the calendar year AND
            # filtered nam_tai_chinh=2013: the two overlap on January to June, and half the
            # revenue was gone without a word (17/11). A period is read as the period; a calendar
            # part is a filter only when the question names it ("năm tài chính 2013").
            args = args.model_copy(
                update={"filters": {k: v for k, v in args.filters.items() if k not in stray}}
            )
        # Nor a cut: "Doanh thu đại lý năm 2012 so với cùng kỳ năm trước" was cut by Năm tài
        # chính, and two fiscal years of a calendar one were read against each other (18/11).
        group_by = [
            g
            for g in group_by
            if g not in by_code
            or by_code[g].is_time
            or not _CALENDAR_PART.match(g)
            or f" {norm(by_code[g].name)} " in asked_text
        ]
        bad = [g for g in [*group_by, *args.filters] if g not in allowed]
        if bad:
            cuts = ", ".join(sorted(allowed - {d.code for d in vocab.dimensions if d.is_time}))
            return ToolResult(
                False,
                f"chỉ số {args.metric} không cho chia/lọc theo {bad}; chia theo thời gian thì dùng"
                f" grain (day, week, month, quarter, year), không đưa vào group_by; các chiều"
                f" được phép: {cuts}",
                failure="arguments",
            )
        request = c.Request(
            version,
            group_by=group_by,
            filters={k: list(v) for k, v in args.filters.items()},
            period=period,
            grain=args.grain,
            top_n=args.top_n if args.top_n and 0 < args.top_n <= 1000 else None,
            top_desc=args.top_desc,
        )
        try:
            first = c.compile_metric(request, ctx.layer.layer, dialect="postgres")
            source = await _source_for(ctx, first.dataset_ids[0])
            if source is None or source.mode != "federated" or source.status not in QUERYABLE:
                return ToolResult(
                    False,
                    "bảng của chỉ số nằm trên nguồn chưa được phép truy vấn tại chỗ",
                    failure="unavailable",
                )
            dialect = dialect_of(source.kind)
            if args.analysis == analysis.SHARE_OF_TOTAL:
                compiled = analysis.compile_share(request, ctx.layer.layer, dialect=dialect)
            elif args.analysis == analysis.RUNNING_TOTAL:
                compiled = analysis.compile_running(request, ctx.layer.layer, dialect=dialect)
            elif args.compare_to is not None:
                compiled = compare.compile_comparison(
                    request, ctx.layer.layer, args.compare_to, dialect=dialect
                )[0]
            else:
                compiled = c.compile_metric(request, ctx.layer.layer, dialect=dialect)
        except (c.CompileError, SqlRejected) as exc:
            # No data for the period or no join path is the data, not the arguments: rewriting
            # them would only compile to the same refusal.
            data = getattr(exc, "code", "") in ("NO_DATA_FOR_PERIOD", "NO_JOIN_PATH")
            return ToolResult(
                False,
                f"không compile được: {exc}",
                failure="logic" if data else "arguments",
            )
        outcome = await run_query(
            org_id=ctx.org_id,
            data_space_id=ctx.space_id,
            principal_id=ctx.user_id,
            caller_kind="question",
            caller_id=ctx.question_id,
            source=source,
            sql=compiled.sql,
            secrets=_secrets(),
            metric_version_ids=[compiled.metric_version_id],
            as_of=ctx.as_of,
        )
        detail: dict[str, Any] = {
            "metric": entry.name,
            "source": source.name,
            "query_run_id": str(outcome.query_run_id),
            "row_count": len(outcome.rows or []),
            "truncated": bool(outcome.truncated),
        }
        if not outcome.ok:
            return ToolResult(
                False,
                "nguồn không trả kết quả kịp"
                if outcome.status == "timeout"
                else "truy vấn lỗi ở nguồn",
                detail=detail,
                failure="transient" if is_transient(outcome.status, outcome.error) else "logic",
            )
        names = {d.code: d.name for d in ctx.layer.vocabulary.dimensions}
        if args.grain in GRAIN_LABELS:
            # A time cut read at a grain is that grain ("Tháng"), not the date column it is read
            # from ("Ngày đặt hàng (Internet)"): it names the chart's axis and its title.
            for code in times & set(group_by):
                names[code] = GRAIN_LABELS[args.grain].capitalize()
        columns = [
            names.get(col)
            or side_labels.get(col)
            or (
                entry.name
                if col == "value"
                else _COLUMN_LABEL.get(col, col).format(metric=entry.name)
            )
            for col in compiled.columns
        ]
        cut_names = ", ".join(
            names.get(g, g).lower() if g in times else names.get(g, g) for g in group_by
        )
        title = args.title or (entry.name + (f" theo {cut_names}" if cut_names else ""))
        rows = [list(r) for r in outcome.rows]
        query_runs = [str(outcome.query_run_id)]
        if pair is not None:
            second = dataclasses.replace(request, period=pair[1][0])
            try:
                compiled_b = c.compile_metric(second, ctx.layer.layer, dialect=dialect)
            except (c.CompileError, SqlRejected) as exc:
                return ToolResult(
                    False, f"không compile được kỳ {pair[1][1]}: {exc}", failure="logic"
                )
            outcome_b = await run_query(
                org_id=ctx.org_id,
                data_space_id=ctx.space_id,
                principal_id=ctx.user_id,
                caller_kind="question",
                caller_id=ctx.question_id,
                source=source,
                sql=compiled_b.sql,
                secrets=_secrets(),
                metric_version_ids=[compiled_b.metric_version_id],
                as_of=ctx.as_of,
            )
            if not outcome_b.ok:
                return ToolResult(
                    False,
                    f"truy vấn kỳ {pair[1][1]} lỗi ở nguồn",
                    detail=detail,
                    failure="transient"
                    if is_transient(outcome_b.status, outcome_b.error)
                    else "logic",
                )
            columns, rows = side_by_side(
                list(compiled.columns),
                columns,
                rows,
                [list(r) for r in outcome_b.rows],
                (f"{entry.name} {pair[0][1]}", f"{entry.name} {pair[1][1]}"),
            )
            query_runs.append(str(outcome_b.query_run_id))
            title = args.title or (
                entry.name
                + (f" theo {cut_names[:1].lower()}{cut_names[1:]}" if cut_names else "")
                + f", {pair[0][1]} so với {pair[1][1]}"
            )
        if side_labels and years:
            # "Doanh thu theo vùng bán hàng, 2012 so với 2013": a cut's name lowercased inside the
            # sentence, and the earlier year first, so the chart reads left to right in time.
            title = args.title or (
                f"{entry.name} theo {cut_names[:1].lower()}{cut_names[1:]}, "
                f"{years[0]} so với {years[1]}"
            )
            cols = list(compiled.columns)
            if "value" in cols and "base_value" in cols:
                order = list(range(len(cols)))
                v, base = cols.index("value"), cols.index("base_value")
                order[v], order[base] = base, v
                columns = [columns[i] for i in order]
                rows = [[r[i] for i in order] for r in rows]
        if moved is not None:
            title = f"{title} ({coverage.iso(moved[1])})"
            detail["period_moved"] = {
                "asked": coverage.iso(moved[0]),
                "read": coverage.iso(moved[1]),
            }
        ds = ctx.ws.add(
            title=title,
            columns=columns,
            rows=rows,
            trust="certified" if compiled.certified else "exploratory",
            query_run_ids=query_runs,
            metric_version_ids=[str(compiled.metric_version_id)],
            step_id=ctx.step_id,
            tool=self.name,
            unit=compiled.unit,
            truncated=bool(outcome.truncated),
            sources=[str(i) for i in compiled.dataset_ids],
            reading={
                "metric": args.metric,
                "metric_id": str(entry.metric_id),
                # What sets this reading apart from another of the same metric: a merge of the two
                # in run_python names its columns by them, not by pandas' "_x"/"_y".
                "metric_name": entry.name,
                "period": f"{pair[0][1]} và {pair[1][1]}"
                if pair is not None
                else coverage.iso(period)
                if period is not None
                else None,
                "group_by": group_by,
                "grain": args.grain,
                "filters": {k: list(v) for k, v in args.filters.items()},
                # Which column is which cut: a point picked on the chart names its column's label,
                # Khám phá needs the dimension (ComposedBlocks' point menu, "Mở trong Khám phá").
                "cuts": {
                    label: code
                    for label, code in zip(columns, compiled.columns, strict=False)
                    if code in group_by
                },
                "time_cuts": sorted(times & set(group_by)),
            },
        )
        detail["dataset"] = ds.id
        if calendar:
            detail["dropped_group_by"] = calendar
        said = (
            f"Kỳ {coverage.iso(moved[0])} không có dữ liệu; đã đọc kỳ đầy đủ gần nhất"
            f" {coverage.iso(moved[1])} thay thế.\n"
            if moved is not None
            else ""
        )
        if not rows:
            return ToolResult(
                True,
                f"{said}{ds.id}: 0 dòng (không có dữ liệu cho kỳ/bộ lọc này)",
                [ds.id],
                detail,
            )
        return ToolResult(True, said + ds.preview(), [ds.id], detail)


class SqlAudit(BaseModel):
    passed: bool = True
    reason: str = ""


async def _audit(
    ctx: ToolContext,
    step_question: str,
    schema: str,
    sql: str,
    columns: list[str],
    rows: list[list[Any]],
) -> SqlAudit | None:
    """The model's read of whether a statement that ran answers what was asked (RES-02, DataAgent's
    semantic consistency check); None when the model is not there, and the result stands."""
    sample = [dict(zip(columns, r, strict=False)) for r in rows[:5]]
    user = (
        f"<question>\n{ctx.question}\n</question>\n<step>\n{step_question}\n</step>\n"
        f"<schema>\n{schema}\n</schema>\n<sql>\n{sql}\n</sql>\n"
        f"<result columns={json.dumps(columns, ensure_ascii=False)}>\n"
        f"{json.dumps(sample, ensure_ascii=False, default=str)}\n</result>"
    )
    try:
        reply = await ctx.model.json_chat(
            "planner", prompts.AUDIT_SQL, user, SqlAudit, max_tokens=200
        )
    except ModelUnavailable:
        return None
    return reply.content


class ExploreSqlArgs(BaseModel):
    question: str = Field(description="câu hỏi dữ liệu cụ thể mà SQL cần trả lời")
    title: str | None = Field(default=None, description="tên ngắn của bảng kết quả")


class ExploreSql(Tool):
    name = "explore_sql"
    title = "Viết SQL thăm dò"
    # Several candidate queries, each under the source's timeout, and a model audit.
    timeout_seconds = 300.0
    description = (
        "khi danh mục đã có chỉ số khớp ý hỏi nhưng cần SQL thăm dò: viết SQL trên các bảng của "
        "Data Space (nhiều cách viết, bỏ phiếu kết quả); kết quả 'exploratory'. Cấm tự tính chỉ "
        "số không có trong danh mục"
    )
    Args = ExploreSqlArgs

    async def run(self, ctx: ToolContext, args: ExploreSqlArgs) -> ToolResult:  # type: ignore[override]
        # RES-04: refuse before writing SQL when no metric in the space names the quantity
        # (churn, made-up rates). Catalogued quantities may still be explored.
        vocab = ctx.layer.vocabulary
        catalogue: set[str] = set()
        for m in vocab.metrics:
            catalogue |= metric_words(m, vocab)
        missing = unnamed(
            quantity_words(args.question or ctx.question, vocab, today=ctx.today),
            catalogue,
        )
        if missing:
            return ToolResult(
                False,
                f"không chỉ số nào trong danh mục đo '{' '.join(missing)}' mà câu hỏi hỏi; "
                "không tự tính bằng SQL thăm dò — tìm chỉ số (search_catalog) hoặc nói rõ "
                "không trả lời được",
                detail={"unnamed": missing},
            )
        async with scoped_session(ROLE, ctx.org_id, ctx.user_id) as session:
            space = await load_space(session, ctx.space_id, ctx.user_id)
            linked = link(args.question, space.schema)
            if not linked.tables:
                return ToolResult(False, "không tìm thấy bảng nào liên quan tới câu hỏi")
            linked, src = _keep_one_source(linked, space.source_of)
            source = None if src is None else await session.get(catalog.Source, src)
            if source is not None:
                session.expunge(source)
        if source is None:
            from services.worker_agent import questions

            source = questions._internal_workspace_source(ctx.space_id, ctx.org_id)
        if source.mode != "federated" or source.status not in QUERYABLE:
            return ToolResult(False, "nguồn chưa được phép truy vấn tại chỗ", failure="unavailable")
        from services.worker_agent import questions

        providers = questions.provider_registry()

        async def run_sql(sql: str) -> QueryOutcome:
            request = ProviderRequest(
                provider="relational",
                org_id=ctx.org_id,
                data_space_id=ctx.space_id,
                principal_id=ctx.user_id,
                source_id=source.id,
                sql=sql,
                caller_kind="question",
                caller_id=ctx.question_id,
            )
            envelope = await providers.get(request.provider).execute(request)
            return QueryOutcome(
                query_run_id=envelope.provenance.query_run_id
                if envelope.provenance is not None
                else uuid.uuid4(),
                status="succeeded" if envelope.error is None else "failed",
                columns=[column.model_dump() for column in envelope.columns],
                rows=[list(row.values()) for row in envelope.rows],
                truncated=envelope.truncated,
                sql=sql,
                error=envelope.error,
            )

        dialect = dialect_of(source.kind)
        # The catalogue's hold on the statement (AE-07): checked before it runs, unlike _audit.
        rules = (
            audit_rules.context_for(args.question or ctx.question, ctx.layer, today=ctx.today)
            if audit_rules.enabled()
            else None
        )
        # One semantic-audit miss → ban that SQL and try explore once more this step (RES-02);
        # a second miss goes back to the planner with the ban kept on the turn.
        last_reason = ""
        for attempt in range(2):
            e = await explore(
                ctx.model,
                args.question,
                linked,
                dialect,
                space.schema.examples,
                run_sql,
                refuse=tuple(ctx.refused_sql),
                audit=rules,
            )
            for name in e.models:
                ctx.models.append({"task": "text_to_sql", "model_name": name, "tokens": e.tokens})
            detail = {
                "source": source.name,
                "candidates": len(e.tried),
                "agreeing": e.agreeing,
                "query_run_id": str(e.query_run_id) if e.query_run_id else None,
                "refused": len(ctx.refused_sql),
            }
            rejected = [p for t in e.tried if t.get("status") == "rejected" for p in t["problems"]]
            if rejected:
                detail["audit_rules"] = sorted(set(rejected))[:5]
            if e.decision == "failed" or e.query_run_id is None:
                if last_reason:
                    return ToolResult(
                        False,
                        f"SQL chưa đúng ý câu hỏi: {last_reason}",
                        detail={**detail, "audit": last_reason[:200]},
                    )
                return ToolResult(False, "không cách viết SQL nào chạy được", detail=detail)
            audit = await _audit(
                ctx, args.question, schema_text(linked), e.sql or "", e.columns, e.rows
            )
            if audit is not None and not audit.passed:
                # Quota read as revenue, one channel as all of them, …
                last_reason = audit.reason
                if e.sql:
                    mark = fingerprint(e.sql, dialect)
                    if mark and mark not in ctx.refused_sql:
                        ctx.refused_sql.append(mark)
                detail["audit"] = audit.reason[:200]
                if attempt == 0:
                    continue
                return ToolResult(False, f"SQL chưa đúng ý câu hỏi: {audit.reason}", detail=detail)
            ds = ctx.ws.add(
                title=args.title or args.question[:80],
                columns=list(e.columns),
                rows=[list(r) for r in e.rows],
                trust="exploratory",
                consensus=(e.agreeing, len(e.tried)),
                sql=e.sql,
                agreement_clear=e.decision == "answer",
                query_run_ids=[str(e.query_run_id)],
                step_id=ctx.step_id,
                tool=self.name,
                truncated=bool(e.truncated),
            )
            detail["dataset"] = ds.id
            note = (
                f" (chỉ {e.agreeing}/{len(e.tried)} cách viết cho cùng kết quả: cần nói rõ là chưa chắc)"
                if e.decision == "ask"
                else ""
            )
            return ToolResult(True, ds.preview() + note, [ds.id], detail)
        return ToolResult(False, "không cách viết SQL nào chạy được")
