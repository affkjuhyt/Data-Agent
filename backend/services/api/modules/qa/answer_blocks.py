"""Build the composable answer blocks served to chat clients.

The database answer remains the source of truth. Blocks are a response projection so the
renderer can evolve without changing the stored answer or requiring a migration for every new
presentation type.
"""

from __future__ import annotations

from typing import Any

STANDARD_TRUST = "Theo chỉ số chuẩn"


def _json(value: Any) -> Any:
    return (
        str(value)
        if value is not None and not isinstance(value, (str, int, float, bool, list, dict))
        else value
    )


def _figure_rows(figures: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for figure in figures:
        dimensions = figure.get("dimensions") if isinstance(figure.get("dimensions"), dict) else {}
        row = {str(key): _json(value) for key, value in dimensions.items()}
        row["value"] = _json(figure.get("value"))
        if figure.get("unit") is not None:
            row["unit"] = str(figure["unit"])
        rows.append(row)
    return rows


def _caveats(answer: Any, figures: list[dict[str, Any]]) -> list[str]:
    """What the reader should keep in mind: failed checks, figures not read through a certified
    metric, a figure that differs from what was filed, an answer outside every data product."""
    items: list[str] = []
    check = answer.number_check if isinstance(getattr(answer, "number_check", None), dict) else {}
    verification = check.get("verification") if isinstance(check.get("verification"), dict) else {}
    for item in verification.get("checks") or []:
        if isinstance(item, dict) and item.get("passed") is False and item.get("detail"):
            items.append(str(item["detail"]))
    for trust in dict.fromkeys(str(f["trust"]) for f in figures if f.get("trust")):
        if trust != STANDARD_TRUST:
            items.append(f"Số liệu {trust}.")
    if getattr(answer, "reconcile_badge", None) == "differs_from_filed":
        items.append("Số hiện tại khác số đã nộp của kỳ.")
    if getattr(answer, "product_scope", None) == "out_of_scope":
        items.append("Câu trả lời nằm ngoài mọi sản phẩm dữ liệu, không có biên nhận.")
    return list(dict.fromkeys(items))


def blocks_of(answer: Any) -> list[dict[str, Any]]:
    """Project one stored answer into non-empty, independently renderable blocks. The sql block
    names the query run only: its SQL is read through /v1/query-runs/{id}/sql, which checks
    view_detail and audits the read."""
    answer_id = str(answer.id)
    blocks: list[dict[str, Any]] = []
    text = str(answer.text or "")
    if text:
        blocks.append({"type": "markdown", "id": f"{answer_id}-markdown", "content": text})

    figures = [figure for figure in (answer.figures or []) if isinstance(figure, dict)]
    for index, figure in enumerate(figures):
        if figure.get("value") is None:
            continue
        dimensions = figure.get("dimensions") if isinstance(figure.get("dimensions"), dict) else {}
        blocks.append(
            {
                "type": "metric",
                "id": str(figure.get("key") or f"{answer_id}-metric-{index}"),
                "label": str(figure.get("label") or figure.get("key") or "Kết quả"),
                "value": _json(figure.get("value")),
                "unit": figure.get("unit"),
                "dimensions": {str(key): _json(value) for key, value in dimensions.items()},
            }
        )

    chart = answer.chart_spec if isinstance(answer.chart_spec, dict) else None
    if chart:
        blocks.append({"type": "chart", "id": f"{answer_id}-chart", "spec": chart})

    rows = _figure_rows(figures)
    if rows:
        columns = list(dict.fromkeys(key for row in rows for key in row))
        blocks.append(
            {
                "type": "table",
                "id": f"{answer_id}-table",
                "title": "Bảng dữ liệu",
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
            }
        )

    for index, figure in enumerate(figures):
        query_run_id = figure.get("query_run_id")
        if query_run_id:
            blocks.append(
                {
                    "type": "sql",
                    "id": f"{answer_id}-sql-{index}",
                    "query_run_id": str(query_run_id),
                }
            )
            break

    citations = [citation for citation in (answer.citations or []) if isinstance(citation, dict)]
    if citations:
        blocks.append({"type": "citations", "id": f"{answer_id}-citations", "items": citations})
    caveats = _caveats(answer, figures)
    if caveats:
        blocks.append({"type": "caveats", "id": f"{answer_id}-caveats", "items": caveats})
    return blocks
