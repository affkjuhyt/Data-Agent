import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Notice } from '../../design-system/Notice';
import { Segmented } from '../../design-system/Segmented';
import {
  SortableTh,
  TablePager,
  TableToolbar,
  tableEmptyRow,
  useTableView,
} from '../../design-system/tableView';
import { StatusDot } from '../../design-system/StatusDot';
import { formatElapsed, formatNumber } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import {
  getAgentOpsSummary,
  type AgentOpsSummary,
  type OpsIssue,
  type OpsPeriod,
} from '../../lib/v1/adminPlatform';
import { useV1 } from '../../lib/v1/useV1';
import { formatMillions, formatRunDuration, pct } from './format';
import { OpsKpi } from './parts';

/*
 * "v3.1 · Vận hành agent" (2058:1356, UC-13-06 and UC-11-04): the roll-up of every agent over
 * 24 h / 7 / 30 days from GET /v1/admin/agent-usage. Five KPIs, runs per day stacked by outcome
 * (done alone, needed a person, failed), the per-agent table and "Cần xử lý" on the right (agents
 * near their monthly budget). A3 has no written summary, HITL response time or eval score per
 * agent, so the frame's summary line and "Đánh giá" column are not drawn.
 */
const PERIODS: { value: OpsPeriod; label: string }[] = [
  { value: '24h', label: '24 giờ' },
  { value: '7d', label: '7 ngày' },
  { value: '30d', label: '30 ngày' },
];

const PREVIOUS: Record<OpsPeriod, string> = {
  '24h': 'so với hôm qua',
  '7d': 'so với tuần trước',
  '30d': 'so với tháng trước',
};

const ISSUE_TONE = { error: 'error', warning: 'warning', info: 'info' } as const;
const BAR_MAX_PX = 171;

const signed = (n: number, digits = 1) =>
  `${n > 0 ? '+' : n < 0 ? '−' : ''}${formatNumber(Math.abs(n), { maxFractionDigits: digits })}`;
const duration = (s: number | null) => (s === null ? '—' : formatRunDuration(s));
const costNumber = (value: string): number | null => {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
};

export default function AgentOperationsPage() {
  const { navigate } = useAppState();
  const [period, setPeriod] = useState<OpsPeriod>('7d');
  const summary = useV1(() => getAgentOpsSummary(period), [period]);

  const openIssue = (issue: OpsIssue) => {
    const { kind, target_id } = issue.action;
    if (kind === 'run') navigate('agents', 'RUN', target_id);
    else if (kind === 'eval') navigate('agents', 'EVALUATE', target_id);
    else navigate('agents', 'AGENT', target_id);
  };

  return (
    <div className="flex flex-col gap-da-lg px-da-3xl py-da-xl">
      <PageHeader
        breadcrumb={['Agents', 'Vận hành']}
        title="Vận hành agent"
        actions={
          <Segmented<OpsPeriod> label="Kỳ" items={PERIODS} value={period} onChange={setPeriod} />
        }
      />
      <AsyncRegion state={summary} what="số liệu vận hành" skeletonRows={6}>
        {(data) => (
          <OpsBody data={data} onIssue={openIssue} onAgent={() => navigate('agents', 'RUNS')} />
        )}
      </AsyncRegion>
    </div>
  );
}

function OpsBody({
  data,
  onIssue,
  onAgent,
}: {
  data: AgentOpsSummary;
  onIssue: (issue: OpsIssue) => void;
  onAgent: () => void;
}) {
  const maxDay = Math.max(1, ...data.series.map((d) => d.auto + d.human + d.error));
  const view = useTableView(data.agents, {
    search: (agent) =>
      `${agent.name} ${agent.runs} ${pct(agent.auto_rate, 0)} ${agent.hitl_requests} ${duration(agent.avg_duration_s)} ${formatMillions(agent.cost)}`,
    filters: [
      { id: 'hitl', label: 'HITL', value: (agent) => (agent.hitl_requests > 0 ? 'Có' : 'Không') },
    ],
    sorts: [
      { id: 'name', value: (agent) => agent.name },
      { id: 'runs', value: (agent) => agent.runs },
      { id: 'auto', value: (agent) => agent.auto_rate },
      { id: 'hitl', value: (agent) => agent.hitl_requests },
      { id: 'duration', value: (agent) => agent.avg_duration_s },
      { id: 'cost', value: (agent) => costNumber(agent.cost) },
    ],
    pageSize: 50,
  });
  return (
    <>
      <div className="grid grid-cols-5 gap-da-md max-[1279px]:grid-cols-3">
        <OpsKpi
          label="Lượt chạy"
          value={formatNumber(data.runs)}
          note={
            data.runs_change_ratio === null
              ? undefined
              : `${signed(data.runs_change_ratio * 100, 0)}% ${PREVIOUS[data.period]}`
          }
        />
        <OpsKpi
          label="Tự hoàn tất"
          value={pct(data.auto_rate)}
          note={
            data.auto_rate_change_pp === null
              ? undefined
              : `${signed(data.auto_rate_change_pp)} điểm`
          }
          noteTone={(data.auto_rate_change_pp ?? 0) >= 0 ? 'success' : 'error'}
        />
        <OpsKpi
          label="Cần người"
          value={formatNumber(data.hitl_requests)}
          note={
            data.hitl_median_response_s == null
              ? undefined
              : `trung vị phản hồi ${formatElapsed(data.hitl_median_response_s)}`
          }
        />
        <OpsKpi label="Lỗi" value={`${formatNumber(data.errors)} · ${pct(data.error_rate)}`} />
        <OpsKpi
          label="Chi phí"
          value={formatMillions(data.cost)}
          note={
            data.budget_used_ratio === null
              ? undefined
              : `${pct(data.budget_used_ratio, 0)} ngân sách tháng`
          }
          noteTone={(data.budget_used_ratio ?? 0) > 0.9 ? 'warning' : 'muted'}
        />
      </div>

      <div className="flex items-start gap-da-lg max-[1279px]:flex-col">
        <div className="flex min-w-0 flex-1 flex-col gap-da-lg max-[1279px]:w-full">
          <section className="flex flex-col gap-da-md rounded-lg border border-border bg-card px-da-xl py-da-lg">
            <h2 className="text-foreground da-heading-3">Lượt chạy theo ngày</h2>
            <div className="flex gap-da-lg">
              <Legend className="bg-chart-1">Tự hoàn tất</Legend>
              <Legend className="bg-chart-3">Cần người</Legend>
              <Legend className="bg-error">Lỗi</Legend>
            </div>
            <div
              role="img"
              aria-label={data.series
                .map(
                  (d) => `${d.label}: ${d.auto} tự hoàn tất, ${d.human} cần người, ${d.error} lỗi`,
                )
                .join('; ')}
              className="flex h-[191px] items-end justify-between"
            >
              {data.series.map((day) => (
                <div key={day.label} className="flex flex-col items-center gap-da-xs">
                  <div className="flex w-9 flex-col">
                    {(
                      [
                        ['bg-error', day.error],
                        ['bg-chart-3', day.human],
                        ['bg-chart-1', day.auto],
                      ] as const
                    ).map(([color, n]) =>
                      n > 0 ? (
                        <div
                          key={color}
                          className={color}
                          style={{ height: Math.max(3, Math.round((n / maxDay) * BAR_MAX_PX)) }}
                        />
                      ) : null,
                    )}
                  </div>
                  <span className="text-muted-foreground da-caption">{day.label}</span>
                </div>
              ))}
            </div>
          </section>

          <div className="flex flex-col gap-da-md">
            <TableToolbar view={view} placeholder="Tìm agent…" searchLabel="Tìm agent" />
            <div className="overflow-hidden rounded-lg border border-border bg-card">
              <table className="w-full border-collapse">
                <thead>
                  <tr className="bg-subtle text-muted-foreground da-label">
                    <SortableTh
                      view={view}
                      column="name"
                      className="px-da-lg py-da-sm text-left font-medium"
                    >
                      Agent
                    </SortableTh>
                    <SortableTh
                      view={view}
                      column="runs"
                      className="w-[60px] py-da-sm text-right font-medium"
                    >
                      Lượt
                    </SortableTh>
                    <SortableTh
                      view={view}
                      column="auto"
                      className="w-[98px] py-da-sm text-right font-medium"
                    >
                      Tự hoàn tất
                    </SortableTh>
                    <SortableTh
                      view={view}
                      column="hitl"
                      className="w-[68px] py-da-sm text-right font-medium"
                    >
                      HITL
                    </SortableTh>
                    <SortableTh
                      view={view}
                      column="duration"
                      className="w-[108px] py-da-sm text-right font-medium"
                    >
                      Thời gian TB
                    </SortableTh>
                    <SortableTh
                      view={view}
                      column="cost"
                      className="w-[98px] py-da-sm pr-da-lg text-right font-medium"
                    >
                      Chi phí
                    </SortableTh>
                  </tr>
                </thead>
                <tbody>
                  {view.rows.map((agent) => {
                    return (
                      <tr
                        key={agent.agent_id}
                        className="border-t border-border text-foreground hover:bg-subtle"
                      >
                        <td className="px-da-lg py-da-sm da-body-small">
                          <button type="button" onClick={onAgent} className="text-left">
                            {agent.name}
                          </button>
                        </td>
                        <td className="py-da-sm text-right da-mono">{agent.runs}</td>
                        <td className="py-da-sm text-right da-mono">{pct(agent.auto_rate, 0)}</td>
                        <td className="py-da-sm text-right da-mono">{agent.hitl_requests}</td>
                        <td className="py-da-sm text-right da-mono">
                          {duration(agent.avg_duration_s)}
                        </td>
                        <td className="py-da-sm pr-da-lg text-right da-mono">
                          {formatMillions(agent.cost)}
                        </td>
                      </tr>
                    );
                  })}
                  {view.total === 0 && tableEmptyRow(6)}
                </tbody>
              </table>
            </div>
            <TablePager view={view} />
          </div>
        </div>

        <section
          aria-labelledby="ops-issues"
          className="flex w-[400px] shrink-0 flex-col gap-da-md max-[1279px]:w-full"
        >
          <h2 id="ops-issues" className="text-foreground da-heading-2">
            Cần xử lý
          </h2>
          {data.issues.length === 0 && (
            <p className="text-muted-foreground da-body-small">Không có việc cần xử lý.</p>
          )}
          {data.issues.map((issue) => (
            <article
              key={issue.id}
              className="flex flex-col items-start gap-da-sm rounded-lg border border-border bg-card px-da-lg py-da-md"
            >
              <StatusDot tone={ISSUE_TONE[issue.severity]} className="whitespace-normal">
                <span className="font-medium">{issue.title}</span>
              </StatusDot>
              <p className="text-muted-foreground da-caption">{issue.detail}</p>
              <Button onClick={() => onIssue(issue)}>{issue.action.label}</Button>
            </article>
          ))}
        </section>
      </div>
    </>
  );
}

function Legend({ className, children }: { className: string; children: string }) {
  return (
    <span className="inline-flex items-center gap-da-xs text-muted-foreground da-caption">
      <span aria-hidden className={cn('size-[10px] rounded-sm', className)} />
      {children}
    </span>
  );
}
