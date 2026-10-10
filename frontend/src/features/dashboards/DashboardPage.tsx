import { useEffect, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Card } from '../../design-system/Card';
import { EmptyState } from '../../design-system/EmptyState';
import { Icon } from '../../design-system/Icon';
import { KpiCard } from '../../design-system/KpiCard';
import { Notice } from '../../design-system/Notice';
import { Select } from '../../design-system/Select';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import {
  getDashboard,
  listDashboards,
  summarizeDashboard,
  type Dashboard,
  type DashboardBars,
  type DashboardLine,
} from '../../lib/v1/dashboards';
import { useV1 } from '../../lib/v1/useV1';
import { cn } from '../../lib/utils';
import { ScheduleDialog } from './ScheduleDialog';
import { AccessDenied, forbiddenOf } from '../access/AccessDenied';

/*
 * "v3.1 · Dashboard có tóm tắt AI" (2140:15741, UC-08-03) and its "Dữ liệu cũ" state
 * (2165:18428): the filters and the send schedule in the header; a banner when a source behind
 * some tiles is late, those tiles marked with a clock; what the agent finds worth noting, in a
 * primary-bordered block with "Viết lại"; four KPI tiles; the 12-month trend and the change by
 * region. /analysis/dashboard opens the first dashboard, /analysis/dashboard/<id> a given one.
 * /v1/dashboards is a PROPOSAL (lib/v1/dashboards.ts).
 */
const PENDING = 'Màn này đang được làm.';

export default function DashboardPage() {
  const { routeId } = useAppState();
  const list = useV1(routeId ? null : listDashboards, [routeId]);

  if (routeId) return <DashboardView id={routeId} />;
  return (
    <AsyncRegion
      state={list}
      what="dashboard"
      isEmpty={(page) => page.items.length === 0}
      empty={
        <div className="px-da-3xl py-da-xl">
          <EmptyState
            icon="chart"
            title="Chưa có dashboard nào"
            description="Ghim kết quả từ một phiên làm việc để dựng dashboard đầu tiên."
          />
        </div>
      }
    >
      {(page) => <DashboardView id={page.items[0].id} />}
    </AsyncRegion>
  );
}

function DashboardView({ id }: { id: string }) {
  const [filters, setFilters] = useState<Record<string, string>>({});
  const dashboard = useV1(() => getDashboard(id, filters), [id, JSON.stringify(filters)]);
  // A dashboard in a Data Space the viewer may not read: the designed "Thiếu quyền" state.
  const forbidden = forbiddenOf(dashboard.error);
  if (forbidden) return <AccessDenied access={forbidden} />;
  return (
    <AsyncRegion state={dashboard} what="dashboard">
      {(d) => (
        <DashboardBody
          dashboard={d}
          filters={filters}
          onFilter={(key, value) => setFilters((prev) => ({ ...prev, [key]: value }))}
          onChanged={dashboard.reload}
        />
      )}
    </AsyncRegion>
  );
}

function DashboardBody({
  dashboard: d,
  filters,
  onFilter,
  onChanged,
}: {
  dashboard: Dashboard;
  filters: Record<string, string>;
  onFilter: (key: string, value: string) => void;
  onChanged: () => void;
}) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [summary, setSummary] = useState(d.summary);
  useEffect(() => setSummary(d.summary), [d.summary]);
  const [rewriting, setRewriting] = useState(false);
  const [scheduling, setScheduling] = useState(false);

  const rewrite = async () => {
    setRewriting(true);
    try {
      setSummary(await summarizeDashboard(d.id, filters));
      toast({ tone: 'success', message: 'Agent đã viết lại phần tóm tắt' });
    } catch (e) {
      toast({ tone: 'error', message: `Không viết lại được: ${(e as Error).message}` });
    } finally {
      setRewriting(false);
    }
  };

  const kpis = d.tiles.filter((t) => t.kind === 'kpi');
  const charts = d.tiles.filter((t): t is DashboardLine | DashboardBars => t.kind !== 'kpi');

  return (
    <div className="flex flex-col gap-da-xl px-da-3xl py-da-xl">
      <PageHeader
        breadcrumb={['Phân tích', 'Dashboard']}
        title={d.title}
        actions={
          <>
            {d.filters.map((f) => (
              <Select
                key={f.key}
                label={f.label}
                className="w-[140px]"
                value={f.value}
                options={f.options.map((o) => ({
                  value: o.value,
                  label: `${f.label}: ${o.label}`,
                }))}
                onChange={(value) => onFilter(f.key, value)}
              />
            ))}
            <Button icon="clock" onClick={() => setScheduling(true)}>
              {d.schedule ? `Lịch gửi: ${d.schedule.text}` : 'Đặt lịch gửi'}
            </Button>
            <Tooltip content={PENDING}>
              <Button kind="ghost" disabled>
                Chia sẻ
              </Button>
            </Tooltip>
          </>
        }
      />

      {d.stale && (
        <Notice
          tone="warning"
          action={
            <Button onClick={() => navigate('data', 'SOURCE', d.stale!.source_id)}>
              Xem nguồn
            </Button>
          }
        >
          {d.stale.message}
        </Notice>
      )}

      {summary && (
        <section
          aria-labelledby="dashboard-summary"
          className="flex flex-col gap-da-md rounded-lg border border-primary px-da-xl py-da-lg"
        >
          <div className="flex items-center gap-da-md">
            <Icon name="agent" className="text-primary" />
            <h2 id="dashboard-summary" className="min-w-0 flex-1 text-foreground da-heading-2">
              Điều đáng chú ý tuần này
            </h2>
            <Button kind="ghost" icon="refresh" loading={rewriting} onClick={rewrite}>
              Viết lại
            </Button>
          </div>
          <ul className="flex flex-col gap-da-md">
            {summary.lines.map((line) => (
              <li key={line} className="text-foreground da-body">
                · {line}
              </li>
            ))}
          </ul>
        </section>
      )}

      {kpis.length > 0 && (
        <div className="grid grid-cols-4 gap-da-lg max-[1023px]:grid-cols-2">
          {kpis.map((k) => (
            <KpiCard
              key={k.id}
              label={k.label}
              value={k.value}
              delta={k.delta}
              state={k.stale ? 'stale' : 'default'}
              status={
                k.stale ? (
                  <Icon name="clock" className="text-warning" label="Dữ liệu cũ" />
                ) : undefined
              }
            />
          ))}
        </div>
      )}

      {charts.length > 0 && (
        <div className="grid grid-cols-2 items-start gap-da-lg max-[1023px]:grid-cols-1">
          {charts.map((c) => (
            <Card
              key={c.id}
              size="sm"
              title={
                <span className="flex items-center gap-da-sm">
                  {c.title}
                  {c.stale && <Icon name="clock" className="text-warning" label="Dữ liệu cũ" />}
                </span>
              }
            >
              {c.kind === 'line' ? <LineChart chart={c} /> : <BarList chart={c} />}
            </Card>
          ))}
        </div>
      )}

      <ScheduleDialog
        open={scheduling}
        dashboard={d}
        onClose={() => setScheduling(false)}
        onSaved={() => {
          setScheduling(false);
          onChanged();
        }}
      />
    </div>
  );
}

const W = 560;
const H = 180;
const PAD = { x: 10, top: 29, bottom: 48 };

/** The trend as one line, the last point marked and labelled, as drawn in the frame. */
function LineChart({ chart }: { chart: DashboardLine }) {
  const values = chart.points.map((p) => p.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const xy = chart.points.map((p, i) => [
    PAD.x + (i * (W - 2 * PAD.x)) / Math.max(1, chart.points.length - 1),
    PAD.top + ((max - p.value) / span) * (H - PAD.top - PAD.bottom),
  ]);
  const last = xy.at(-1);
  const fmt = (v: number) => v.toLocaleString('vi-VN');
  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="h-[180px] w-full max-w-[560px]"
      role="img"
      aria-label={`${chart.title}: ${chart.points.map((p) => `${p.label} ${fmt(p.value)}`).join(', ')}`}
    >
      <polyline
        points={xy.map(([x, y]) => `${x},${y}`).join(' ')}
        fill="none"
        strokeWidth={1.5}
        className="stroke-chart-1"
      />
      {last && (
        <>
          <circle cx={last[0]} cy={last[1]} r={5} className="fill-chart-3" />
          <text
            x={last[0] - 12}
            y={last[1] + 4}
            textAnchor="end"
            className="fill-foreground da-label"
          >
            {chart.last_label}
          </text>
        </>
      )}
    </svg>
  );
}

const BAR_MAX = 300;

/**
 * Change by region: the largest rise in chart-3, other rises in chart-6, falls in chart-2 —
 * the colours of the frame; the signed number beside each bar says the direction in words.
 */
function BarList({ chart }: { chart: DashboardBars }) {
  const max = Math.max(...chart.bars.map((b) => Math.abs(b.value))) || 1;
  const top = Math.max(...chart.bars.map((b) => b.value));
  return (
    <ul className="flex flex-col gap-da-md">
      {chart.bars.map((b) => (
        <li key={b.label} className="flex items-center gap-[10px]">
          <span className="w-[110px] shrink-0 truncate text-foreground da-body-small">
            {b.label}
          </span>
          <span
            aria-hidden
            className={cn(
              'h-[14px] max-w-[50%] shrink-0 rounded-[3px]',
              b.value < 0 ? 'bg-chart-2' : b.value === top ? 'bg-chart-3' : 'bg-chart-6',
            )}
            style={{ width: `${Math.max(4, (Math.abs(b.value) / max) * BAR_MAX)}px` }}
          />
          <span className="shrink-0 text-muted-foreground da-mono">{b.text}</span>
        </li>
      ))}
    </ul>
  );
}
