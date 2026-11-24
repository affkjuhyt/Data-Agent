"""VALIDATE of a hybrid turn (ASK_ENGINE_SPEC §10, AE-13): the figure a document's table cell
states, checked against the data's before the answer is written.

The turn's workspace holds both: the numbers specialist's datasets (query_metric, explore_sql)
and the documents specialist's cells (search_documents, a dataset with `cell`). This module reads
them as documents/reconcile.SourceFigure, runs the comparison, and gives the server's blocks:
the ReconciliationNote, and on a difference the two figures side by side, each with its own label
(and on a conflict a warning). The reporter is told the verdict; it never picks a number.
"""

from __future__ import annotations

import decimal
from typing import Any

from libs.contracts.v1.trust import TrustCode
from libs.language import number as said_number
from services.worker_agent.agentic import blocks as b
from services.worker_agent.agentic.tools.base import ToolContext
from services.worker_agent.agentic.workspace import Dataset, plain
from services.worker_agent.agentic.workspace import number as as_number
from services.worker_agent.documents import reconcile as r
from services.worker_agent.documents.table_query import spoken

# The readings whose figures a document's may be checked against.
RECONCILED_TOOLS = frozenset({"query_metric", "explore_sql"})
# Rows of one dataset compared at most: a hybrid question names one figure, not a ranking.
MAX_ROWS = 50
_COMPARED = " (kỳ so sánh)"


def _decimal(value: Any) -> decimal.Decimal | None:
    n = as_number(value)
    if n is None:
        return None
    return value if isinstance(value, decimal.Decimal) else decimal.Decimal(str(n))


def _measure(ds: Dataset) -> int | None:
    """The column of the figure: the last whose every cell is a number, as persist reads it."""
    numeric = [
        i
        for i, _ in enumerate(ds.columns)
        if ds.rows and all(as_number(row[i]) is not None for row in ds.rows if row[i] is not None)
    ]
    plain_ones = [i for i in numeric if not ds.columns[i].endswith(_COMPARED)]
    found = plain_ones or numeric
    return found[-1] if found else None


def _labels(ctx: ToolContext) -> dict[tuple[str, str], str]:
    values = getattr(getattr(ctx.layer, "vocabulary", None), "values", None) or []
    return {(v.dimension_code, v.value_code): v.label for v in values}


def data_figures(ctx: ToolContext, ds: Dataset) -> list[r.SourceFigure]:
    """A data dataset's figures, one per row (at most MAX_ROWS), with each row's period and
    members: a metric's period and filters, a row's time cell and its other cuts."""
    measure = _measure(ds)
    if measure is None or ds.inference:
        return []
    reading = ds.reading or {}
    today = ctx.as_of or ctx.today
    asked = ctx.period
    period: r.Period | None = None
    if reading.get("period") and " và " not in str(reading["period"]):
        period = r.period_of(str(reading["period"]), today)
    elif not reading and asked is not None:
        # SQL the model wrote has no reading of its own: it was written for the question's period.
        period = r.Period(asked.start, asked.end, asked.label)
    labels = _labels(ctx)
    filtered = tuple(
        labels.get((dim, str(code)), str(code))
        for dim, codes in (reading.get("filters") or {}).items()
        for code in codes
    )
    time_cols = {
        i
        for i, column in enumerate(ds.columns)
        if (reading.get("cuts") or {}).get(column) in set(reading.get("time_cuts") or [])
    }
    certified = ds.trust == "certified"
    name = str(reading.get("metric_name") or ds.title)
    out = []
    for n, row in enumerate(ds.rows[:MAX_ROWS]):
        value = _decimal(row[measure])
        if value is None:
            continue
        row_period = period
        members: list[str] = list(filtered)
        for i, cell in enumerate(row):
            if i == measure or cell is None or (as_number(cell) is not None and i not in time_cols):
                continue
            if i in time_cols:
                row_period = r.period_of(str(plain(cell)), today) or row_period
            elif isinstance(cell, str) and cell.strip():
                members.append(cell.strip())
        out.append(
            r.SourceFigure(
                figure_id=f"{ds.id}:{n}",
                label=name,
                value=value,
                unit=ds.unit,
                trust_code=TrustCode.GOVERNED if certified else TrustCode.EXPLORATORY,
                authority="certified_metric" if certified else "none",
                period=row_period,
                members=tuple(dict.fromkeys(members)),
                as_of=ctx.as_of.isoformat() if ctx.as_of else None,
                basis=f"chỉ số chuẩn {name}" if certified else f"SQL thăm dò: {ds.title}",
            )
        )
    return out


def document_figure(ctx: ToolContext, ds: Dataset) -> r.SourceFigure | None:
    cell = ds.cell
    if cell is None or not ds.rows:
        return None
    value = _decimal(ds.rows[0][-1])
    if value is None:
        return None
    row, column = str(cell.get("row") or ""), str(cell.get("column") or "")
    caption, document = str(cell.get("caption") or ""), str(cell.get("document") or "")
    # The column names the period most often ("Năm 2024 > Thực hiện"), a transposed table its
    # row, a one-period table its caption or the document's title.
    period = next(
        (
            p
            for text in (column, row, caption, document)
            if (p := r.period_of(text, ctx.as_of or ctx.today)) is not None
        ),
        None,
    )
    return r.SourceFigure(
        figure_id=ds.id,
        label=f"{document}: {row} - {column}" if document else f"{row} - {column}",
        value=value,
        unit=ds.unit,
        trust_code=TrustCode.DOCUMENT,
        authority="original" if cell.get("superseded_by") else "document",
        period=period,
        context=" ".join((row, column, caption, document)),
        as_of=cell.get("effective_from"),
        cite=cell.get("cite"),
        basis=f"ô {row}, cột {column}" + (f", {caption}" if caption else ""),
    )


def check(ctx: ToolContext) -> r.Reconciliation | None:
    """The turn's document figure checked against its data figures; None when the turn did not
    read both (not every hybrid answer reads a table cell)."""
    datasets = list(ctx.ws.datasets.values())
    documents = [f for ds in datasets if (f := document_figure(ctx, ds)) is not None]
    if not documents:
        return None
    data = [f for ds in datasets if ds.tool in RECONCILED_TOOLS for f in data_figures(ctx, ds)]
    return r.reconcile(data, documents)


# ---------------------------------------------------------------- what the answer says


def _pct(value: float | None) -> str:
    return said_number(value or 0.0, 2, "vi-VN") + "%"


def _amount(f: r.SourceFigure) -> str:
    unit = r.unit_of(f.unit)
    if unit in ("percent", "VND", "USD"):
        return spoken(f.value, unit)
    return f"{spoken(f.value, None)} {f.unit}" if f.unit else spoken(f.value, None)


def _cited(f: r.SourceFigure) -> str:
    return f" [{f.cite}]" if f.cite is not None else ""


def verdict_text(rec: r.Reconciliation) -> str:
    """The verdict in one sentence, its numbers the sources' own (the server writes it)."""
    data, doc = rec.data, rec.document
    if rec.verdict == "agree":
        return (
            f"Số của tài liệu{_cited(doc)} ({_amount(doc)}) khớp với {data.basis}"
            f" ({_amount(data)}): lệch {_pct(rec.residual_pct)}, trong dung sai"
            f" {_pct(rec.tolerance_pct)}."
        )
    if rec.verdict == "authority":
        return (
            f"Số của tài liệu{_cited(doc)} ({_amount(doc)}) lệch {_pct(rec.residual_pct)} so với"
            f" {data.basis} ({_amount(data)}), ngoài dung sai {_pct(rec.tolerance_pct)}. Theo luật"
            " thẩm quyền, câu trả lời dùng số của chỉ số đã chứng nhận; số của tài liệu được nêu"
            " để đối chiếu."
        )
    if rec.verdict == "conflict":
        return (
            f"Tài liệu{_cited(doc)} ghi {_amount(doc)}, còn {data.basis} cho {_amount(data)}: lệch"
            f" {_pct(rec.residual_pct)}, ngoài dung sai {_pct(rec.tolerance_pct)}. Không có luật"
            " thẩm quyền để chọn số nào đúng; cần người phụ trách dữ liệu xem lại."
        )
    return "Chưa so được số của tài liệu với dữ liệu: " + "; ".join(rec.open) + "."


def prompt(rec: r.Reconciliation) -> str:
    """What the reporter is told: the verdict, which it words around and never overturns."""
    verdict = {
        "agree": "hai nguồn khớp",
        "authority": "hai nguồn lệch; dùng số của chỉ số đã chứng nhận, nêu cả số của tài liệu",
        "conflict": "hai nguồn lệch, không có luật thẩm quyền: không chọn số nào là đáp án",
        "not_comparable": "không so được hai nguồn (" + "; ".join(rec.open) + ")",
    }[rec.verdict]
    return (
        f"<reconciliation>Server đã đối chiếu số của tài liệu với số của dữ liệu: {verdict}."
        " Server tự thêm khối đối chiếu; câu trả lời không tự kết luận khác và không gõ lại số"
        " ngoài các {{F}}.</reconciliation>"
    )


def blocks(ctx: ToolContext, rec: r.Reconciliation, first_id: int) -> list[dict[str, Any]]:
    """The server's blocks for the verdict, in the order they are shown: on a conflict a warning,
    on any difference both figures side by side, then the note."""
    out: list[dict[str, Any]] = []
    said = verdict_text(rec)
    if rec.verdict == "conflict":
        out.append(
            {
                "type": "callout",
                "id": f"b{first_id + len(out)}",
                "composed": True,
                "tone": "warning",
                "title": "Hai nguồn ghi số khác nhau",
                "content": said,
            }
        )
    if rec.differs:
        shown = _side_by_side(ctx, rec, f"b{first_id + len(out)}")
        if shown is not None:
            out.append(shown)
    out.append(
        {
            "type": "reconciliation",
            "id": f"b{first_id + len(out)}",
            "composed": True,
            "title": "Đối chiếu tài liệu và dữ liệu",
            "content": said,
            "note": rec.note(),
        }
    )
    return out


def _side_by_side(ctx: ToolContext, rec: r.Reconciliation, block_id: str) -> dict[str, Any] | None:
    """The two figures as one KPI block, each item with its own dataset and trust label."""
    ds_id, _, n = rec.data.figure_id.partition(":")
    ds = ctx.ws.get(ds_id)
    measure = _measure(ds) if ds is not None else None
    if ds is None or measure is None:
        return None
    data_kpi = b.KpiDraft(label=rec.data.label, dataset=ds.id, column=ds.columns[measure])
    if len(ds.rows) > 1:
        row = ds.rows[int(n or 0)]
        # The row by a column whose value no other row shares.
        key = next(
            (
                i
                for i in range(len(ds.columns))
                if i != measure and sum(1 for x in ds.rows if x[i] == row[i]) == 1
            ),
            None,
        )
        if key is None:
            return None
        data_kpi = data_kpi.model_copy(
            update={"match_column": ds.columns[key], "match_value": str(plain(row[key]))}
        )
    doc = ctx.ws.get(rec.document.figure_id)
    if doc is None:
        return None
    draft = b.BlockDraft(
        type="kpi",
        kpis=[
            data_kpi,
            b.KpiDraft(label=rec.document.label, dataset=doc.id, column=doc.columns[-1]),
        ],
    )
    try:
        return b.materialize(draft, ctx.ws, block_id)
    except b.BlockError:
        return None
