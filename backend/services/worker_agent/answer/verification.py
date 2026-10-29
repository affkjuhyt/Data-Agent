"""The deterministic checks shown with an answer (R0-8, the VerifierBadge).

R0's `__verification` frame came from the legacy judge. V3 answers on a fast path with no judge
loop, and the A1.6 stream names no verification event, so the result travels with the answer
itself: `number_check.verification` ({checks, verdict, iteration}, libs/contracts/v1/
verification.py), written once with the qa.answer row, read by GET /v1/questions/{id}/answer.

Each check reads only what the answer row holds, so it is the same whenever it is read:
- every number in the text is traced to a figure (number_check, BR-171);
- every figure reads from a query run, a filed result or a computation over other figures;
- a figure for a filed period matches what was filed (reconcile_badge);
- a document answer cites at least one passage.
A refusal or a clarification carries no checks: it states no number.
"""

from __future__ import annotations

from typing import Any

# One pass: the fast path answers once, it does not loop back to a judge.
ITERATION = 1


def _check(name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "passed": passed, "detail": detail}


def _numbers(number_check: dict[str, Any]) -> dict[str, Any]:
    total = int(number_check.get("numbers_total") or 0)
    traced = int(number_check.get("traced") or 0)
    left = int(number_check.get("untraceable_final") or 0)
    detail = (
        f"{traced}/{total} số trong câu trả lời có nguồn" if total else "Câu trả lời không nêu số"
    )
    return _check("Số trong câu có nguồn", left == 0 and traced >= total, detail)


def _sourced(figure: dict[str, Any]) -> bool:
    source = figure.get("source") or {}
    if source.get("expr"):
        return bool(source.get("compute_run_id"))
    return bool(source.get("query_run_id") or source.get("published_result_id"))


def _figures(figures: list[dict[str, Any]]) -> dict[str, Any]:
    missing = [str(f.get("label") or f.get("key")) for f in figures if not _sourced(f)]
    if missing:
        return _check("Mỗi số có lần chạy", False, "Không có lần chạy: " + ", ".join(missing))
    return _check("Mỗi số có lần chạy", True, f"{len(figures)} số, mỗi số có lần chạy truy vấn")


def _filed(reconcile_badge: str | None) -> dict[str, Any]:
    if reconcile_badge == "differs_from_filed":
        return _check("Khớp số đã nộp", False, "Số hiện tại khác số đã nộp của kỳ")
    if reconcile_badge == "matched_filed":
        return _check("Khớp số đã nộp", True, "Khớp số đã nộp của kỳ")
    return _check("Khớp số đã nộp", True, "Kỳ này chưa có số đã nộp")


def verify(
    kind: str,
    number_check: dict[str, Any],
    *,
    figures: list[dict[str, Any]] | None = None,
    citations: list[dict[str, Any]] | None = None,
    reconcile_badge: str | None = None,
    consensus: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """`number_check` with its `verification` added, for an answer of `kind`. `consensus`, for a
    query the model wrote (sqlgen): how many of the ways it was written gave the same result, out
    of how many; the answer's confidence meter (R1-14) reads it."""
    if kind in ("refusal", "clarification"):
        return number_check
    checks = [_numbers(number_check)]
    if kind == "document":
        cited = len(citations or [])
        checks.append(
            _check(
                "Có trích dẫn", cited > 0, f"{cited} đoạn trích" if cited else "Không có đoạn trích"
            )
        )
    else:
        checks += [_figures(figures or []), _filed(reconcile_badge)]
    verdict = "PASS" if all(c["passed"] for c in checks) else "FAIL"
    verification: dict[str, Any] = {"checks": checks, "verdict": verdict, "iteration": ITERATION}
    if consensus is not None:
        agreeing, written = consensus
        verification["consensus"] = {"agreeing": agreeing, "written": written}
        verification["confidence"] = round(agreeing / written, 4) if written else 0.0
    return {**number_check, "verification": verification}
