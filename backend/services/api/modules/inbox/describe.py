"""A proposal's change in words (P07 "Duyệt đề xuất", section "Thay đổi", UC-07-03).

Each producer writes `diff` in the shape of its object (worker_agent/semantic_review.py,
metric_changes.py, proposals.py, reviews.py), with ids where a reviewer needs names: a reviewer
shown that JSON could not decide ("đọc như này không hiểu gì"). `describe` turns it into one
sentence saying what accepting does and the fields it changes, current against proposed, with the
ids resolved here, server-side, under the caller's organisation and Data Space (an id they cannot
read is shown as UNREADABLE, never as itself). The evaluation the change ran with and an empty
preview are said in words too.

A kind or a shape this module does not know reads as the proposal's title and its top-level
scalars: `describe` never raises, and `diff` is still sent as is beside the summary.
"""

from __future__ import annotations

import datetime
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.dataagent_db.models import eval as evaluation
from libs.dataagent_db.models import inbox, org
from services.api.kernel.auth import Principal
from services.api.modules.catalog.public import dataset_names, sources_in_data_space
from services.api.modules.context.public import NameKind, names_in_data_space
from services.api.modules.inbox.schemas import ProposalSummary, SummaryField
from services.api.modules.skill.public import skill_names

logger = logging.getLogger(__name__)

UNREADABLE = "(không xem được)"

TARGET_KIND = {
    "metric": "chỉ số",
    "dimension": "chiều",
    "measure": "độ đo",
    "entity": "thực thể",
    "column": "cột",
}
SYNONYM_TARGET: dict[str, NameKind] = {
    "metric": "metric",
    "dimension": "dimension",
    "value": "dimension",
    "measure": "measure",
    "entity": "entity",
}
# The en dash is the screen's own wording (P07), not a mistyped hyphen.
CARDINALITY = {"many_to_one": "nhiều–một", "one_to_many": "một–nhiều", "one_to_one": "một–một"}  # noqa: RUF001
SCOPE = {"personal": "Cá nhân", "data_space": "Data Space", "org": "Toàn tổ chức"}

# metric_version's compared fields (worker_agent/metric_changes.py _COMPARED), in that order.
METRIC_FIELDS = {
    "display_name": "Tên hiển thị",
    "expression": "Công thức",
    "filters": "Bộ lọc",
    "unit": "Đơn vị",
    "decimals": "Số chữ số thập phân",
    "period_semantics": "Cách tính theo kỳ",
    "time_grain": "Độ hạt thời gian",
    "additivity": "Khả năng cộng gộp",
}
METRIC_VALUES = {
    "period_semantics": {
        "sum_over_period": "Cộng dồn trong kỳ",
        "end_of_period": "Giá trị cuối kỳ",
        "average_over_period": "Trung bình trong kỳ",
    },
    "time_grain": {
        "day": "Ngày",
        "week": "Tuần",
        "month": "Tháng",
        "quarter": "Quý",
        "year": "Năm",
        "fiscal_quarter": "Quý tài chính",
        "fiscal_year": "Năm tài chính",
    },
    "additivity": {
        "full": "Cộng được theo mọi chiều",
        "semi": "Không cộng theo thời gian",
        "none": "Không cộng được",
    },
}
FILTER_OPS = {
    "in": "thuộc",
    "not_in": "không thuộc",
    "is_null": "trống",
    "is_not_null": "không trống",
}

# What a describer may ask a name for: the context module's kinds, and the catalog's two.
Kind = NameKind | Literal["dataset", "source"]


@dataclass(frozen=True)
class Name:
    name: str
    # The object's code (a metric's TON_KHO); for a dataset, schema.table.
    code: str | None = None
    # A dataset's table name alone, as a join is written (fact_sales.customer_key).
    table: str | None = None


Names = dict[tuple[Kind, str], Name]


class _Shape(Exception):
    """The diff has not the shape of its kind: the summary falls back to the generic reading."""


def _record(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _Shape
    return value


def _str(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _Shape
    return value


def _text(value: Any) -> str | None:
    """A diff value as the reviewer reads it."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "có" if value else "không"
    if isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return str(value)
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "))


def _date(value: Any) -> str | None:
    if not isinstance(value, str):
        return _text(value)
    try:
        return datetime.date.fromisoformat(value[:10]).strftime("%d/%m/%Y")
    except ValueError:
        return value


def _field(label: str, after: Any = None, before: Any = None) -> SummaryField:
    return SummaryField(label=label, before=_text(before), after=_text(after))


def _named(names: Names, kind: Kind, key: Any) -> Name | None:
    return names.get((kind, str(key))) if key else None


def _label(name: Name | None, *, with_code: bool = False) -> str:
    if name is None:
        return UNREADABLE
    if with_code and name.code and name.code != name.name:
        return f"{name.name} ({name.code})"
    return name.name


# ---------------------------------------------------------------- the evaluation in words


def _note_of(
    items: int, passed: int, regressions: int, improved: int, *, baseline: bool = True
) -> str | None:
    if items <= 0:
        return None
    head = f"Bộ đánh giá {items} câu: "
    if regressions > 0:
        return (
            f"{head}{passed} câu đạt với thay đổi này; {regressions} câu đang đạt sẽ không đạt"
            " nữa. Xem từng câu trước khi chấp nhận."
        )
    if not baseline:
        return f"{head}{passed} câu đạt với thay đổi này; chưa có lần chạy trước để so."
    if improved > 0:
        text = f"{head}{passed} câu đạt với thay đổi này ({improved} câu tốt lên), không câu nào tệ đi."
    else:
        text = f"{head}{passed} câu đạt trước và sau thay đổi, không câu nào tệ đi."
    if passed < items:
        # A low pass rate without regressions is where the set already stood: said as such, so the
        # reviewer does not reject the change for it.
        text += (
            " Các câu chưa đạt có từ trước, không do thay đổi này:"
            f" bộ đánh giá hiện mới đạt {passed}/{items}."
        )
    return text


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _checks_note(diff: Any, run: evaluation.EvalRun | None, changed: int | None) -> str | None:
    if run is not None:
        if run.status == "running":
            return "Bộ đánh giá đang chạy; chưa có kết quả."
        if run.status == "failed":
            return "Bộ đánh giá chạy lỗi; chưa có kết quả để xem."
        return _note_of(
            run.item_count,
            run.passed,
            run.regressions,
            max(changed or 0, run.regressions) - run.regressions,
            baseline=run.baseline_run_id is not None or run.regressions > 0,
        )
    if not isinstance(diff, dict):
        return None
    measured = diff.get("impact") if isinstance(diff.get("impact"), dict) else diff.get("eval")
    if not isinstance(measured, dict):
        return None
    if "items" not in measured:
        note = measured.get("note")
        return note if isinstance(note, str) and note else None
    regressions = _int(measured.get("regressions"))
    moved = measured.get("changed_items")
    moved_count = len(moved) if isinstance(moved, list) else 0
    if "regressions" not in measured:
        # A data product's bar (reviews.py) counts passes only: nothing to compare against.
        items, passed = _int(measured.get("items")), _int(measured.get("passed"))
        return f"Bộ đánh giá {items} câu: {passed} câu đạt." if items > 0 else None
    return _note_of(
        _int(measured.get("items")),
        _int(measured.get("passed")),
        regressions,
        max(moved_count - regressions, 0),
    )


def _preview_note(diff: dict[str, Any]) -> str | None:
    withheld = diff.get("preview_withheld")
    if isinstance(withheld, str) and withheld:
        return f"Chưa xem trước được: {withheld}"
    preview = diff.get("preview")
    if not isinstance(preview, list) or not preview:
        return None
    figures = ("new", "current", "filed")
    if all(isinstance(r, dict) and all(r.get(k) is None for k in figures) for r in preview):
        return f"Chưa có số liệu trong {len(preview)} kỳ gần nhất để xem trước."
    return None


# ---------------------------------------------------------------- per kind


class _Ask:
    """The lookups a describer makes, each answered under the caller (context/skill public)."""

    def __init__(
        self,
        session: AsyncSession,
        principal: Principal,
        data_space: uuid.UUID,
        target_ref: uuid.UUID | None,
    ):
        self.session, self.principal, self.data_space = session, principal, data_space
        self.target_ref = target_ref

    async def names(self, wanted: Mapping[Kind, Iterable[Any]]) -> Names:
        asked = {kind: [str(v) for v in values if v] for kind, values in wanted.items()}
        out: Names = {}
        # Each module names its own objects through its own read rule: a personal table is its
        # owner's (catalog BR-111), a knowledge page its readers' (context BR-163).
        datasets, sources = asked.pop("dataset", []), asked.pop("source", [])
        if datasets:
            found = await dataset_names(self.principal, self.data_space, _uuids(datasets))
            for value in datasets:
                d = found.get(uuid.UUID(_canon(value))) if _is_uuid(value) else None
                if d is not None:
                    qualified = f"{d.schema_name}.{d.table_name}"
                    out[("dataset", value)] = Name(
                        name=d.display_name or qualified, code=qualified, table=d.table_name
                    )
        if sources:
            summaries = await sources_in_data_space(
                self.principal, self.data_space, _uuids(sources)
            )
            for value in sources:
                src = summaries.get(uuid.UUID(_canon(value))) if _is_uuid(value) else None
                if src is not None:
                    out[("source", value)] = Name(name=src.name)
        # What is left is the context module's to name.
        context_kinds = cast(dict[NameKind, list[str]], asked)
        for (kind, value), name in (
            await names_in_data_space(self.principal, self.data_space, context_kinds)
        ).items():
            out[(kind, value)] = Name(name=name.name, code=name.code)
        return out

    async def users(self, ids: Iterable[Any]) -> dict[str, str]:
        wanted = {str(u) for u in _uuids(ids)}
        if not wanted:
            return {}
        user = org.AppUser
        rows = await self.session.execute(
            select(user.id, user.display_name).where(
                user.id.in_(wanted), user.org_id == self.principal.org_id
            )
        )
        return {str(r.id): r.display_name for r in rows.all()}

    async def skills(self, ids: Iterable[Any]) -> dict[str, str]:
        found = await skill_names(
            self.principal, self.data_space, _uuids(ids), under_review=self.target_ref
        )
        return {str(k): v for k, v in found.items()}


def _uuids(values: Iterable[Any]) -> set[uuid.UUID]:
    out = set()
    for value in values:
        try:
            out.add(uuid.UUID(str(value)))
        except ValueError:
            continue
    return out


def _is_uuid(value: Any) -> bool:
    try:
        uuid.UUID(str(value))
    except ValueError:
        return False
    return True


def _canon(value: Any) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        return str(value)


Described = tuple[str, list[SummaryField]]


async def _synonym(d: dict[str, Any], ask: _Ask) -> Described:
    phrase, kind = _str(d.get("phrase")), _str(d.get("target_kind"))
    value = d.get("target_value")
    # A value synonym points at the dimension the value belongs to (context _TARGETS); a column
    # has no name of its own to look up.
    looked = SYNONYM_TARGET.get(kind)
    found = None
    if looked is not None:
        names = await ask.names({looked: [d.get("target_id")]})
        found = _named(names, looked, d.get("target_id"))
    # A metric is known by its code as much as its name; a dimension by its name.
    target = _label(found, with_code=looked == "metric")
    word = TARGET_KIND.get("dimension" if kind == "value" else kind, kind)
    points_to = f"{word} {target}"
    if kind == "value" and isinstance(value, str) and value:
        headline = f"Thêm từ đồng nghĩa “{phrase}” cho giá trị “{value}” của {points_to}."
    else:
        headline = f"Thêm từ đồng nghĩa “{phrase}” cho {points_to}."
    fields = [_field("Cụm từ", phrase), _field("Trỏ tới", points_to)]
    if value not in (None, ""):
        fields.append(_field("Giá trị", value))
    return headline, fields


def _filters(value: Any, names: Names) -> str | None:
    if value is None:
        return None
    if not isinstance(value, list):
        return _text(value)
    if not value:
        return "Không có"
    parts = []
    for f in value:
        if not isinstance(f, dict) or not (f.get("dimension") or f.get("column")):
            parts.append(_text(f) or "")
            continue
        subject = f.get("dimension") or f.get("column")
        dim = _named(names, "dimension_code", subject) if f.get("dimension") else None
        who = dim.name if dim else str(subject)
        op = str(f.get("op") or "in").lower()
        values = f.get("values", f.get("value"))
        shown = ", ".join(str(v) for v in values) if isinstance(values, list) else _text(values)
        if op in ("is_null", "is_not_null"):
            parts.append(f"{who} {FILTER_OPS[op]}")
        elif op == "between" and isinstance(values, list) and len(values) == 2:
            parts.append(f"{who} từ {values[0]} đến {values[1]}")
        else:
            parts.append(f"{who} {FILTER_OPS.get(op, op)} {shown}")
    return "; ".join(parts)


def _metric_value(name: str, value: Any, names: Names) -> str | None:
    if name == "filters":
        return _filters(value, names)
    mapped = METRIC_VALUES.get(name, {})
    return mapped.get(value, _text(value)) if isinstance(value, str) else _text(value)


def _filter_codes(changes: dict[str, Any]) -> list[str]:
    out = []
    side = changes.get("filters")
    for value in (side.get("before"), side.get("after")) if isinstance(side, dict) else ():
        for f in value if isinstance(value, list) else ():
            if isinstance(f, dict) and f.get("dimension"):
                out.append(str(f["dimension"]))
    return out


async def _metric_change(d: dict[str, Any], ask: _Ask) -> Described:
    changes = _record(d.get("changes") or {})
    added = changes.get("dimensions_added", d.get("dimensions_added")) or []
    removed = changes.get("dimensions_removed", d.get("dimensions_removed")) or []
    if not isinstance(added, list) or not isinstance(removed, list):
        raise _Shape
    names = await ask.names(
        {
            "metric": [d.get("metric_id")],
            # Codes, or the id itself where the producer found no code (metric_changes.changes).
            "dimension_code": [*added, *removed, *_filter_codes(changes)],
            "dimension": [*added, *removed],
        }
    )
    metric = _named(names, "metric", d.get("metric_id"))
    code = d.get("metric_code") if isinstance(d.get("metric_code"), str) else None
    renamed = changes.get("display_name")
    shown = (
        renamed["after"]
        if isinstance(renamed, dict) and isinstance(renamed.get("after"), str)
        else metric.name
        if metric
        else None
    )
    who = f"{shown} ({code})" if shown and code and shown != code else shown or code or UNREADABLE
    action = d.get("action")
    verb = "Chứng nhận" if action == "certify" else "Thay đổi"
    headline = f"{verb} chỉ số {who}"
    if d.get("version") is not None:
        headline += f", phiên bản {d['version']}"
    if d.get("replaces_version") is not None:
        headline += f", thay phiên bản {d['replaces_version']}"
    headline += "."

    fields = []
    ordered = [*METRIC_FIELDS, *(k for k in changes if k not in METRIC_FIELDS)]
    for key in ordered:
        side = changes.get(key)
        if not isinstance(side, dict) or not ({"before", "after"} & set(side)):
            continue
        fields.append(
            SummaryField(
                label=METRIC_FIELDS.get(key, key),
                before=_metric_value(key, side.get("before"), names),
                after=_metric_value(key, side.get("after"), names),
            )
        )

    def dim(code: Any) -> str:
        found = _named(names, "dimension_code", code) or _named(names, "dimension", code)
        return found.name if found else UNREADABLE

    # The diff lists only what moves, so the field says that, not the whole list before and after.
    allowed = []
    if added:
        allowed.append("thêm " + ", ".join(dim(c) for c in added))
    if removed:
        allowed.append("bỏ " + ", ".join(dim(c) for c in removed))
    if allowed:
        said = " · ".join(allowed)
        fields.append(_field("Chiều được phép", said[0].upper() + said[1:]))
    return headline, fields


def _side(names: Names, dataset: Any, columns: Any) -> tuple[str, str]:
    """(table.columns as a join reads, the dataset as the field shows it)."""
    cols = [str(c) for c in columns] if isinstance(columns, list) else []
    found = _named(names, "dataset", dataset)
    listed = ", ".join(cols)
    if found is None:
        return f"{UNREADABLE}.{listed}", f"{UNREADABLE} ({listed})"
    table = found.table or found.code or found.name
    joined = f"{table}.{cols[0]}" if len(cols) == 1 else f"{table}.({listed})" if cols else table
    shown = f"{found.code} ({listed})"
    if found.name != found.code:
        shown = f"{found.name} · {shown}"
    return joined, shown


async def _relationship(d: dict[str, Any], ask: _Ask) -> Described:
    if not d.get("from_dataset_id") or not d.get("to_dataset_id"):
        raise _Shape
    names = await ask.names({"dataset": [d["from_dataset_id"], d["to_dataset_id"]]})
    left, from_shown = _side(names, d["from_dataset_id"], d.get("from_columns"))
    right, to_shown = _side(names, d["to_dataset_id"], d.get("to_columns"))
    card = CARDINALITY.get(str(d.get("cardinality")), _text(d.get("cardinality")) or "")
    joined = f"{left} → {right}" + (f" ({card})" if card else "")
    if d.get("action") == "retire":
        # What is removed is shown as current, with nothing proposed in its place.
        fields = [
            _field("Từ", before=from_shown),
            _field("Đến", before=to_shown),
            _field("Kiểu nối", before=card or None),
        ]
        if d.get("reason"):
            fields.append(_field("Lý do", d["reason"]))
        return f"Gỡ quan hệ nối {joined}.", fields
    fields = [_field("Từ", from_shown), _field("Đến", to_shown), _field("Kiểu nối", card or None)]
    return f"Nối bảng {joined}.", fields


async def _example(d: dict[str, Any], ask: _Ask) -> Described:
    question = _str(d.get("question"))
    fields = [_field("Câu hỏi", question), _field("SQL", d.get("sql"))]
    if isinstance(d.get("rows"), int):
        fields.append(_field("Chạy thử", f"{d['rows']} dòng"))
    return f"Thêm câu hỏi mẫu “{question}” và câu SQL trả lời nó.", fields


async def _glossary(d: dict[str, Any], ask: _Ask) -> Described:
    term = _str(d.get("term"))
    return f"Thêm thuật ngữ “{term}” vào bảng thuật ngữ.", [
        _field("Thuật ngữ", term),
        _field("Định nghĩa", d.get("definition")),
    ]


async def _data_product(d: dict[str, Any], ask: _Ask) -> Described:
    if not d.get("data_product_id"):
        raise _Shape
    names = await ask.names({"data_product": [d["data_product_id"]]})
    users = await ask.users([d.get("assignee_id")])
    found = _named(names, "data_product", d["data_product_id"])
    product = _label(found, with_code=True)
    headline = f"Kích hoạt data product {product}"
    if d.get("version") is not None:
        headline += f", phiên bản {d['version']}"
    fields = [_field("Data product", product)]
    if d.get("version") is not None:
        fields.append(_field("Phiên bản", d["version"]))
    if d.get("assignee_id"):
        fields.append(_field("Người duyệt", users.get(_canon(d["assignee_id"]), UNREADABLE)))
    return headline + ".", fields


async def _skill_publish(d: dict[str, Any], ask: _Ask) -> Described:
    if not d.get("skill_id"):
        raise _Shape
    name = (await ask.skills([d["skill_id"]])).get(_canon(d["skill_id"]), UNREADABLE)
    scope = SCOPE.get(str(d.get("scope")), _text(d.get("scope")))
    if d.get("action") == "share":
        before = SCOPE.get(str(d.get("previous_scope")), _text(d.get("previous_scope")))
        headline = f"Mở rộng phạm vi kỹ năng {name}"
        headline += f" từ {before} ra {scope}." if before else f" ra {scope}."
        return headline, [_field("Kỹ năng", name), _field("Phạm vi", scope, before)]
    headline = f"Phát hành kỹ năng {name}"
    fields = [_field("Kỹ năng", name)]
    if d.get("version") is not None:
        headline += f", phiên bản {d['version']}"
        fields.append(_field("Phiên bản", d["version"]))
    if scope:
        fields.append(_field("Phạm vi", scope))
    return headline + ".", fields


async def _writeback(d: dict[str, Any], ask: _Ask) -> Described:
    table = _str(d.get("target_table"))
    names = await ask.names({"source": [d.get("source_id")], "dataset": [d.get("from_dataset_id")]})
    source = _named(names, "source", d.get("source_id"))
    dataset = _named(names, "dataset", d.get("from_dataset_id"))
    counts = d.get("diff_summary")
    counts = counts if isinstance(counts, dict) else {}
    rows = {
        k: counts[k] for k in ("inserts", "updates", "deletes") if isinstance(counts.get(k), int)
    }
    said = [
        f"{verb} {rows[k]} dòng"
        for k, verb in (("inserts", "thêm"), ("updates", "sửa"), ("deletes", "xoá"))
        if rows.get(k)
    ]
    headline = f"Ghi ngược vào bảng {table}"
    if d.get("source_id"):
        headline += f" của {_label(source)}"
    headline += (": " + ", ".join(said) + ".") if said else "."
    fields = [_field("Bảng đích", table)]
    if d.get("source_id"):
        fields.append(_field("Nguồn", _label(source)))
    if d.get("from_dataset_id"):
        fields.append(_field("Lấy từ", dataset.code if dataset else UNREADABLE))
    for key, label in (("inserts", "Dòng thêm"), ("updates", "Dòng sửa"), ("deletes", "Dòng xoá")):
        if key in rows:
            fields.append(_field(label, rows[key]))
    return headline, fields


async def _source_authority(d: dict[str, Any], ask: _Ask) -> Described:
    kind, ranking = _str(d.get("data_kind")), d.get("ranking")
    if not isinstance(ranking, list):
        raise _Shape
    names = await ask.names({"source": ranking})
    ranked = [_label(_named(names, "source", s)) for s in ranking]
    order = ", rồi ".join(ranked) if len(ranked) <= 2 else ", ".join(ranked)
    return f"Đặt thứ tự thẩm quyền nguồn cho {kind}: {order}.", [
        _field("Loại dữ liệu", kind),
        _field("Thứ tự nguồn", " · ".join(f"{i}. {n}" for i, n in enumerate(ranked, 1))),
    ]


async def _document_validity(d: dict[str, Any], ask: _Ask, target: uuid.UUID | None) -> Described:
    names = await ask.names({"document": [target, d.get("supersedes_id")]})
    title = _label(_named(names, "document", target))
    fields = [_field("Văn bản", title)]
    if d.get("effective_from"):
        fields.append(_field("Hiệu lực từ", _date(d["effective_from"])))
    if d.get("effective_to"):
        fields.append(_field("Hiệu lực đến", _date(d["effective_to"])))
    if d.get("supersedes_id"):
        replaced = _label(_named(names, "document", d["supersedes_id"]))
        fields.append(_field("Thay thế văn bản", replaced))
        headline = f"Ghi nhận văn bản {title} thay thế {replaced}."
    else:
        span = " ".join(
            part
            for part in (
                f"từ {_date(d['effective_from'])}" if d.get("effective_from") else "",
                f"đến {_date(d['effective_to'])}" if d.get("effective_to") else "",
            )
            if part
        )
        headline = f"Đặt hiệu lực cho văn bản {title}" + (f": {span}." if span else ".")
    if d.get("excerpt"):
        fields.append(_field("Trích đoạn", d["excerpt"]))
    return headline, fields


async def _knowledge_page(d: dict[str, Any], ask: _Ask) -> Described:
    if not d.get("page_id"):
        raise _Shape
    names = await ask.names({"knowledge_page": [d["page_id"]]})
    page = _label(_named(names, "knowledge_page", d["page_id"]))
    headline = f"Phục vụ trang tri thức {page}"
    fields = [_field("Trang", page)]
    if d.get("version") is not None:
        headline += f", phiên bản {d['version']}"
        fields.append(_field("Phiên bản", d["version"]))
    for key, label in (("origin", "Nguồn gốc"), ("change_size", "Mức thay đổi")):
        if d.get(key) is not None:
            fields.append(_field(label, d[key]))
    return headline + ".", fields


DESCRIBERS: dict[str, Callable[[dict[str, Any], _Ask], Awaitable[Described]]] = {
    "synonym": _synonym,
    "metric_change": _metric_change,
    "relationship": _relationship,
    "example": _example,
    "glossary": _glossary,
    "data_product": _data_product,
    "skill_publish": _skill_publish,
    "writeback": _writeback,
    "source_authority": _source_authority,
    "knowledge_page": _knowledge_page,
}


def _fallback(row: inbox.Proposal) -> list[SummaryField]:
    if not isinstance(row.diff, dict):
        return []
    return [
        _field(str(key), value)
        for key, value in row.diff.items()
        if isinstance(value, str | int | float | bool) and value != ""
    ]


async def describe(
    session: AsyncSession,
    row: inbox.Proposal,
    principal: Principal,
    *,
    run: evaluation.EvalRun | None = None,
    changed: int | None = None,
) -> ProposalSummary:
    """The proposal in words. `session` is the inbox's (it reads org.app_user); the names of other
    modules' objects are asked of them as `principal`. `run` and `changed` are the proposal's eval
    run and how many of its items changed result, when it has one."""
    checks_note = preview_note = None
    try:
        checks_note = _checks_note(row.diff, run, changed)
        if isinstance(row.diff, dict):
            preview_note = _preview_note(row.diff)
    except Exception:  # noqa: BLE001 - a summary never fails the review page
        logger.warning("inbox | proposal %s: evaluation not described", row.id, exc_info=True)
    try:
        if not isinstance(row.diff, dict):
            raise _Shape
        ask = _Ask(session, principal, row.data_space_id, row.target_ref)
        if row.kind == "document_validity":
            headline, fields = await _document_validity(row.diff, ask, row.target_ref)
        elif row.kind in DESCRIBERS:
            headline, fields = await DESCRIBERS[row.kind](row.diff, ask)
        else:
            raise _Shape
    except _Shape:
        headline, fields = row.title, _fallback(row)
    except Exception:  # noqa: BLE001 - a summary never fails the review page
        # A lookup that fails must not take the review page with it: the title and the raw diff
        # are still there to decide on.
        logger.warning("inbox | proposal %s: change not described", row.id, exc_info=True)
        headline, fields = row.title, _fallback(row)
    return ProposalSummary(
        headline=headline, fields=fields, checks_note=checks_note, preview_note=preview_note
    )
