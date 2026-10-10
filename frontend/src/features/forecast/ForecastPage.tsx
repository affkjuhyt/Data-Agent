import { useEffect, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { Card } from '../../design-system/Card';
import { EmptyState } from '../../design-system/EmptyState';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { useToast } from '../../design-system/Toast';
import { csvCell } from '../../lib/csv';
import { cn } from '../../lib/utils';
import {
  addForecastToSession,
  clearScenario,
  formatOne,
  formatSigned,
  formatTwo,
  getForecast,
  listForecasts,
  runScenario,
  saveScenario,
  type Forecast,
  type ForecastPoint,
} from '../../lib/v1/forecast';
import { useV1 } from '../../lib/v1/useV1';
import { cutoffShort, ForecastChart, ForecastLegend } from './ForecastChart';
import { DataTable, type Column } from './parts';

/*
 * "P15 · Kết quả dự báo & kịch bản" (2391:15164, UC-15-01, UC-15-05): history, forecast and its
 * 80/95% intervals, the table by period, what drives the change, the assumptions, and a what-if
 * scenario read as a conditional calculation, never as a causal effect. The model version, its
 * backtest MASE and the data cut-off are always in the header; a stale forecast says so first.
 */
const lowerFirst = (text: string) => text.charAt(0).toLowerCase() + text.slice(1);

const dateVN = (iso: string) => {
  const [year, month, day] = iso.slice(0, 10).split('-');
  return `${day}/${month}/${year}`;
};

const timeVN = (iso: string) => iso.slice(11, 16);

function csvOf(f: Forecast): string {
  const scenario = new Map(f.scenario?.points.map((p) => [p.period, p.value]));
  const head = ['Kỳ', 'Dự báo', 'Thấp 80%', 'Cao 80%', 'Thấp 95%', 'Cao 95%'];
  if (f.scenario) head.push(f.scenario.short_label);
  const rows = f.points.map((p) => {
    const row: (string | number)[] = [p.label, p.mean, p.lo80, p.hi80, p.lo95, p.hi95];
    if (f.scenario) row.push(scenario.get(p.period) ?? '');
    return row;
  });
  const meta = [
    [`Mô hình ${f.model.version_label} · ${f.model.algorithm_label}`],
    [`Dữ liệu đến ${dateVN(f.data_cutoff)} · đơn vị ${f.unit_label}`],
  ];
  return [...meta, head, ...rows].map((row) => row.map(csvCell).join(',')).join('\n');
}

function download(name: string, text: string) {
  // A BOM so Excel opens the Vietnamese text as UTF-8.
  const url = URL.createObjectURL(new Blob(['﻿', text], { type: 'text/csv;charset=utf-8' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

export default function ForecastPage() {
  const { routeId } = useAppState();
  // From the rail without an id: the first forecast of the Data Space.
  const list = useV1(routeId ? null : listForecasts, [routeId]);
  const id = routeId || list.data?.items[0]?.id;
  const forecast = useV1(id ? () => getForecast(id) : null, [id]);
  if (!routeId && list.error) {
    return (
      <AsyncRegion state={list} what="dự báo">
        {() => null}
      </AsyncRegion>
    );
  }
  if (!routeId && list.data && list.data.items.length === 0) {
    return (
      <div className="px-da-3xl pt-da-xl">
        <EmptyState
          icon="chart"
          title="Chưa có dự báo nào"
          description="Hỏi một câu về tương lai trong Hỏi đáp; agent dự báo tạo kết quả ở đây."
        />
      </div>
    );
  }
  return (
    <AsyncRegion state={forecast} what="dự báo">
      {(f) => <ForecastBody forecast={f} onChanged={forecast.reload} />}
    </AsyncRegion>
  );
}

function ForecastBody({ forecast, onChanged }: { forecast: Forecast; onChanged: () => void }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  const f = forecast;

  const add = async () => {
    setAdding(true);
    try {
      await addForecastToSession(f.id);
      toast({ tone: 'success', message: 'Đã thêm dự báo vào phiên' });
      onChanged();
    } catch (e) {
      toast({ tone: 'error', message: (e as Error).message });
    } finally {
      setAdding(false);
    }
  };

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Phân tích', 'Dự báo', f.metric_name]}
        title={`Dự báo ${lowerFirst(f.metric_name)} · ${f.horizon_label}`}
        description={
          <>
            <button
              type="button"
              className="text-primary hover:underline"
              onClick={() => navigate('analysis', 'FORECAST_MODEL', f.model.id)}
            >
              Mô hình {f.model.version_label}
            </button>
            {` · ${f.model.algorithm_label} · MASE backtest ${formatTwo(f.model.mase)} · dữ liệu đến ${dateVN(f.data_cutoff)} · tạo ${timeVN(f.created_at)} · đơn vị ${f.unit_label}`}
          </>
        }
        actions={
          <>
            <Button onClick={() => download(`du-bao-${f.id}.csv`, csvOf(f))}>Xuất</Button>
            {f.session_id && (
              <Button kind="primary" loading={adding} disabled={f.added_to_session} onClick={add}>
                {f.added_to_session ? 'Đã thêm vào phiên' : 'Thêm vào phiên'}
              </Button>
            )}
          </>
        }
      />
      <div className="flex flex-wrap items-start gap-da-xl px-da-3xl pb-da-2xl pt-da-xl">
        <div className="flex min-w-[520px] flex-1 flex-col gap-da-xl">
          {f.label === 'stale' && (
            <Notice tone="warning">
              Dự báo cũ: {f.stale_note}. Chỉ dùng để xem xu hướng; mô hình {f.model.version_label}{' '}
              đang suy giảm.
            </Notice>
          )}
          <Card size="sm" title="Lịch sử, dự báo và khoảng tin cậy">
            <div className="flex flex-wrap items-center gap-da-xl">
              <LabelBadge forecast={f} />
              <ForecastLegend scenarioLabel={f.scenario?.short_label} />
            </div>
            <ForecastChart forecast={f} />
            <p className="text-muted-foreground da-caption">{f.interval_note}</p>
          </Card>
          <PeriodTable forecast={f} />
        </div>
        <aside className="flex w-[384px] shrink-0 flex-col gap-da-xl">
          <Contributions forecast={f} />
          <ScenarioCard forecast={f} onChanged={onChanged} />
          <Card size="sm" title="Giả định">
            <p className="text-muted-foreground da-caption">{f.assumptions_text}</p>
          </Card>
        </aside>
      </div>
    </div>
  );
}

function LabelBadge({ forecast }: { forecast: Forecast }) {
  if (forecast.label === 'approved')
    return (
      <Badge tone="success">
        {forecast.approved_by_name ? `Đã duyệt · ${forecast.approved_by_name}` : 'Đã duyệt'}
      </Badge>
    );
  if (forecast.label === 'stale') return <Badge tone="warning">Dự báo cũ · suy giảm</Badge>;
  return <Badge tone="info">Thử nghiệm</Badge>;
}

function PeriodTable({ forecast }: { forecast: Forecast }) {
  const scenario = forecast.scenario;
  const byPeriod = new Map(scenario?.points.map((p) => [p.period, p.value]));
  const columns: Column<ForecastPoint>[] = [
    { head: 'Kỳ', cell: (p: ForecastPoint) => p.label },
    { head: 'Dự báo', cell: (p: ForecastPoint) => formatOne(p.mean) },
    {
      head: 'Khoảng 80%',
      cell: (p: ForecastPoint) => `${formatOne(p.lo80)} – ${formatOne(p.hi80)}`,
    },
    {
      head: 'Khoảng 95%',
      cell: (p: ForecastPoint) => `${formatOne(p.lo95)} – ${formatOne(p.hi95)}`,
    },
  ];
  if (scenario) {
    columns.push(
      {
        head: scenario.short_label,
        cell: (p) => {
          const value = byPeriod.get(p.period);
          return value === undefined ? '—' : formatOne(value);
        },
      },
      {
        head: 'Chênh',
        cell: (p) => {
          const value = byPeriod.get(p.period);
          if (value === undefined) return '—';
          const delta = Math.round((value - p.mean) * 10) / 10;
          return (
            <span className={cn(delta > 0 && 'text-success', delta < 0 && 'text-error')}>
              {formatSigned(delta)}
            </span>
          );
        },
      },
    );
  }
  return (
    <DataTable
      label="Dự báo theo kỳ"
      columns={columns}
      rows={forecast.points}
      rowKey={(p) => p.period}
    />
  );
}

/** "Đóng góp vào thay đổi…": one bar per driver, its length relative to the largest. */
function Contributions({ forecast }: { forecast: Forecast }) {
  const { title, items } = forecast.contributions;
  const max = Math.max(...items.map((i) => Math.abs(i.value)), 0.0001);
  return (
    <Card size="sm" title={title}>
      <ul className="flex flex-col gap-da-lg">
        {items.map((item) => (
          <li key={item.label} className="flex items-center gap-da-md">
            <span className="w-[150px] shrink-0 text-foreground da-caption">{item.label}</span>
            <span
              aria-hidden
              className={cn('h-2.5 rounded-[2px]', item.value < 0 ? 'bg-error' : 'bg-info')}
              style={{ width: `${Math.max(4, (Math.abs(item.value) / max) * 150)}px` }}
            />
            <span className="text-foreground da-label">{formatSigned(item.value)}</span>
          </li>
        ))}
      </ul>
    </Card>
  );
}

function ScenarioCard({ forecast, onChanged }: { forecast: Forecast; onChanged: () => void }) {
  const toast = useToast();
  const scenario = forecast.scenario;
  const [text, setText] = useState(scenario?.text ?? '');
  const [busy, setBusy] = useState<'run' | 'save' | 'clear' | null>(null);
  const [error, setError] = useState<string>();
  useEffect(() => setText(forecast.scenario?.text ?? ''), [forecast.scenario?.text]);

  const act = async (kind: 'run' | 'save' | 'clear') => {
    setBusy(kind);
    setError(undefined);
    try {
      if (kind === 'run') await runScenario(forecast.id, text.trim());
      if (kind === 'save') {
        await saveScenario(forecast.id);
        toast({ tone: 'success', message: 'Đã lưu kịch bản' });
      }
      if (kind === 'clear') await clearScenario(forecast.id);
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const edited = text.trim() !== (scenario?.text ?? '');
  return (
    <Card size="sm" title="Kịch bản">
      <Input
        aria-label="Giả định của kịch bản"
        placeholder="Ví dụ: Lãi suất điều hành +0,5 điểm từ T12/2026"
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && edited && text.trim()) void act('run');
        }}
      />
      {error && <Notice tone="error">{error}</Notice>}
      {scenario && !edited && (
        <>
          <p className="text-muted-foreground da-caption">{scenario.disclaimer}</p>
          <p className="text-foreground da-body-small">{scenario.summary}</p>
        </>
      )}
      <div className="flex justify-end gap-da-md">
        {scenario && !edited ? (
          <>
            <Button loading={busy === 'clear'} onClick={() => act('clear')}>
              Bỏ kịch bản
            </Button>
            <Button
              kind="primary"
              loading={busy === 'save'}
              disabled={scenario.saved}
              onClick={() => act('save')}
            >
              {scenario.saved ? 'Đã lưu kịch bản' : 'Lưu kịch bản'}
            </Button>
          </>
        ) : (
          <Button
            kind="primary"
            loading={busy === 'run'}
            disabled={!text.trim()}
            onClick={() => act('run')}
          >
            Chạy kịch bản
          </Button>
        )}
      </div>
    </Card>
  );
}
