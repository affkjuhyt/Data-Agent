"""Tools that read data: a certified metric through the compiler (governed), or SQL the model
writes over the linked tables (exploratory, sqlgen's N-version vote). Both run through the
executor, so row and column policies apply and every run is recorded (invariant 15)."""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

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
from services.worker_agent.semantic import analysis, compare
from services.worker_agent.semantic import compiler as c
from services.worker_agent.sqlgen.answer import _keep_one_source
from services.worker_agent.sqlgen.generate import schema_text
from services.worker_agent.sqlgen.run import explore
from services.worker_agent.sqlgen.schema import link
from services.worker_agent.sqlgen.space import load_space
from services.worker_agent.understand import analyst
from services.worker_agent.understand.packs.base import iso_answer
from services.worker_agent.understand.resolver import metric_words, quantity_words

ROLE = "svc_qa"
QUERYABLE = ("active", "testing")

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
        # A quantity the question asks that no certified metric of the catalogue names ("khách
        # hàng" where only units sold exist): no reading of this one will answer it.
        vocab = ctx.layer.vocabulary
        catalogue: set[str] = set()
        for m in analyst._visible(ctx.layer).values():
            catalogue |= metric_words(m, vocab)
        missing = unnamed(
            quantity_words(
                ctx.question, vocab, today=ctx.today, group_by=args.group_by, filters=args.filters
            ),
            catalogue,
        )
        if missing:
            return ToolResult(
                False,
                f"không chỉ số chuẩn nào đo '{' '.join(missing)}' mà câu hỏi hỏi; "
                "dùng explore_sql cho phần đó",
                detail={"unnamed": missing},
            )
        version = ctx.layer.versions[entry.metric_id]
        period = None
        if args.period:
            found = iso_answer(args.period)
            if found is None:
                return ToolResult(
                    False, f"kỳ {args.period} không đúng dạng ISO", failure="arguments"
                )
            period = c.Period(found.start, found.end)
        allowed = {
            d.code
            for d in ctx.layer.vocabulary.dimensions
            if d.dimension_id in version.allowed_dimension_ids or d.is_time
        }
        bad = [g for g in [*args.group_by, *args.filters] if g not in allowed]
        if bad:
            return ToolResult(
                False,
                f"chỉ số {args.metric} không cho chia/lọc theo {bad}; xem describe_metric",
                failure="arguments",
            )
        request = c.Request(
            version,
            group_by=list(args.group_by),
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
        columns = [
            names.get(col)
            or (
                entry.name
                if col == "value"
                else _COLUMN_LABEL.get(col, col).format(metric=entry.name)
            )
            for col in compiled.columns
        ]
        cut_names = ", ".join(names.get(g, g) for g in args.group_by)
        ds = ctx.ws.add(
            title=args.title or (entry.name + (f" theo {cut_names}" if cut_names else "")),
            columns=columns,
            rows=[list(r) for r in outcome.rows],
            trust="certified" if compiled.certified else "exploratory",
            query_run_ids=[str(outcome.query_run_id)],
            metric_version_ids=[str(compiled.metric_version_id)],
            step_id=ctx.step_id,
            tool=self.name,
            unit=compiled.unit,
            truncated=bool(outcome.truncated),
            sources=[str(i) for i in compiled.dataset_ids],
            reading={
                "metric": args.metric,
                "metric_id": str(entry.metric_id),
                "group_by": list(args.group_by),
                "filters": {k: list(v) for k, v in args.filters.items()},
            },
        )
        detail["dataset"] = ds.id
        if not outcome.rows:
            return ToolResult(
                True, f"{ds.id}: 0 dòng (không có dữ liệu cho kỳ/bộ lọc này)", [ds.id], detail
            )
        return ToolResult(True, ds.preview(), [ds.id], detail)


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
        "khi không có chỉ số chuẩn phù hợp: viết SQL trên các bảng của Data Space (nhiều cách viết, "
        "bỏ phiếu kết quả); kết quả là dataset 'exploratory', kém tin cậy hơn chỉ số chuẩn"
    )
    Args = ExploreSqlArgs

    async def run(self, ctx: ToolContext, args: ExploreSqlArgs) -> ToolResult:  # type: ignore[override]
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

        e = await explore(
            ctx.model,
            args.question,
            linked,
            dialect_of(source.kind),
            space.schema.examples,
            run_sql,
        )
        for name in e.models:
            ctx.models.append({"task": "text_to_sql", "model_name": name, "tokens": e.tokens})
        detail = {
            "source": source.name,
            "candidates": len(e.tried),
            "agreeing": e.agreeing,
            "query_run_id": str(e.query_run_id) if e.query_run_id else None,
        }
        if e.decision == "failed" or e.query_run_id is None:
            return ToolResult(False, "không cách viết SQL nào chạy được", detail=detail)
        audit = await _audit(
            ctx, args.question, schema_text(linked), e.sql or "", e.columns, e.rows
        )
        if audit is not None and not audit.passed:
            # RES-02: it ran, but it is not what was asked (a quota read as revenue, one channel
            # read as all of them): the planner hears why and plans again.
            detail["audit"] = audit.reason[:200]
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
