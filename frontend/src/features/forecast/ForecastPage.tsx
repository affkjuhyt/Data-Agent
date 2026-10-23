import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { Card } from '../../design-system/Card';
import { EmptyState } from '../../design-system/EmptyState';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { Select } from '../../design-system/Select';
import { useToast } from '../../design-system/Toast';
import { csvCell } from '../../lib/csv';
import { cn } from '../../lib/utils';
import {
  addForecastToSession,
  formatOne,
  formatSigned,
  formatTwo,
  getForecast,
  listForecasts,
  reloadScenario,
  runScenario,
  type Forecast,
  type ForecastPoint,
  type ForecastScenario,
} from '../../lib/v1/forecast';
import { useV1 } from '../../lib/v1/useV1';
import { cutoffShort, ForecastChart, ForecastLegend } from './ForecastChart';
import { DataTable, downloadCsv, type Column } from './parts';
import { AccessDenied, forbiddenOf } from '../access/AccessDenied';

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

const timeVN = (iso: string) =>
  new Date(iso).toLocaleTimeString('vi-VN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Asia/Ho_Chi_Minh',
  });

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
    [`Dữ liệu đến ${dateVN(f.data_cutoff)}${f.unit_label ? ` · đơn vị ${f.unit_label}` : ''}`],
  ];
  return [...meta, head, ...rows].map((row) => row.map(csvCell).join(',')).join('\n');
}

export default function ForecastPage() {
  const { routeId } = useAppState();
  // From the rail without an id: the first forecast of the Data Space.
  const list = useV1(['listForecasts', routeId], routeId ? null : listForecasts);
  const id = routeId || list.data?.items[0]?.id;
  const forecast = useV1(['getForecast', id], id ? () => getForecast(id) : null);
  // A forecast in a Data Space the viewer may not read: the designed "Thiếu quyền" state.
  const forbidden = forbiddenOf(forecast.error);
  if (forbidden) return <AccessDenied access={forbidden} />;
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
      {(f) => <ForecastBody key={f.id} forecast={f} />}
    </AsyncRegion>
  );
}

function ForecastBody({ forecast }: { forecast: Forecast }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  const [added, setAdded] = useState(false);
  // The scenario is the screen's: the api keeps it under its name, the base forecast is untouched.
  const [scenario, setScenario] = useState<ForecastScenario | null>(forecast.scenario);
  const f: Forecast = { ...forecast, scenario };
  const sessionId = f.session_id;

  const add = async () => {
    if (!sessionId) return;
    setAdding(true);
    try {
      await addForecastToSession(sessionId, f);
      setAdded(true);
      toast({ tone: 'success', message: 'Đã thêm dự báo vào phiên' });
    } catch (e) {
      toast({ tone: 'error', message: (e as Error).message });
    } finally {
      setAdding(false);
    }
  };

  const facts = [
    f.model.algorithm_label,
    `MASE backtest ${f.model.mase === null ? '—' : formatTwo(f.model.mase)}`,
    `dữ liệu đến ${dateVN(f.data_cutoff)}`,
    `tạo ${timeVN(f.created_at)}`,
    f.unit_label && `đơn vị ${f.unit_label}`,
  ].filter(Boolean);

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Phân tích', 'Dự báo', f.metric_name]}
        title={`Dự báo ${lowerFirst(f.metric_name)} · ${f.horizon_label}`}
        description={
          <>
            {f.model.id ? (
              <button
                type="button"
                className="text-primary hover:underline"
                onClick={() => navigate('analysis', 'FORECAST_MODEL', f.model.id)}
              >
                Mô hình {f.model.version_label}
              </button>
            ) : (
              'Chưa có phiên bản mô hình'
            )}
            {` · ${facts.join(' · ')}`}
          </>
        }
        actions={
          <>
            <Button onClick={() => downloadCsv(`du-bao-${f.id}.csv`, csvOf(f))}>Xuất</Button>
            {sessionId && (
              <Button kind="primary" loading={adding} disabled={added} onClick={add}>
                {added ? 'Đã thêm vào phiên' : 'Thêm vào phiên'}
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
            {f.interval_note && (
              <p className="text-muted-foreground da-caption">{f.interval_note}</p>
            )}
          </Card>
          <PeriodTable forecast={f} />
        </div>
        <aside className="flex w-[384px] shrink-0 flex-col gap-da-xl">
          {f.contributions.items.length > 0 && <Contributions forecast={f} />}
          <ScenarioCard forecast={f} onScenario={setScenario} />
          <Card size="sm" title="Giả định">
            <p className="text-muted-foreground da-caption">{f.assumptions_text || '—'}</p>
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

const CHANGE_KINDS: { value: string; label: string }[] = [
  { value: 'tăng', label: 'Tăng' },
  { value: 'giảm', label: 'Giảm' },
  { value: 'giữ nguyên', label: 'Giữ nguyên' },
];

/*
 * UC-15-05: one exogenous variable the model uses, fixed from a period on. The assumption is
 * structured (variable, change, value, from) — the api runs it on the trained model and keeps it
 * under its name; the result is read back by scenario id once computed.
 */
function ScenarioCard({
  forecast,
  onScenario,
}: {
  forecast: Forecast;
  onScenario: (scenario: ForecastScenario | null) => void;
}) {
  const scenario = forecast.scenario;
  const variables = forecast.scenario_variables;
  const [feature, setFeature] = useState<string | null>(variables[0] ?? null);
  const [change, setChange] = useState<string>('tăng');
  const [value, setValue] = useState('');
  const [from, setFrom] = useState<string | null>(forecast.points[0]?.period ?? null);
  const [name, setName] = useState('');
  const [busy, setBusy] = useState<'run' | 'reload' | null>(null);
  const [error, setError] = useState<string>();

  const amount = value.trim() === '' ? null : Number(value.replace(',', '.'));
  const needsValue = change !== 'giữ nguyên';
  const fromLabel = forecast.points.find((p) => p.period === from)?.label ?? '';
  const defaultName = feature
    ? `${feature} ${change}${needsValue && amount !== null ? ` ${value.trim()}` : ''} từ ${fromLabel}`
    : '';
  const ready =
    feature !== null && from !== null && (!needsValue || (amount !== null && !Number.isNaN(amount)));

  const run = async () => {
    if (!feature || !from) return;
    setBusy('run');
    setError(undefined);
    try {
      onScenario(
        await runScenario(forecast, {
          name: name.trim() || defaultName,
          feature,
          change_kind: change,
          value: needsValue ? amount : null,
          from_period: from,
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const reload = async () => {
    if (!scenario) return;
    setBusy('reload');
    setError(undefined);
    try {
      onScenario(await reloadScenario(forecast, scenario));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  if (variables.length === 0)
    return (
      <Card size="sm" title="Kịch bản">
        <p className="text-muted-foreground da-caption">
          Mô hình không dùng biến ngoại sinh nào, nên không có kịch bản áp dụng.
        </p>
      </Card>
    );

  if (scenario)
    return (
      <Card size="sm" title="Kịch bản">
        <p className="text-foreground da-body-strong">{scenario.text}</p>
        <p className="text-muted-foreground da-caption">{scenario.disclaimer}</p>
        {scenario.summary && <p className="text-foreground da-body-small">{scenario.summary}</p>}
        {error && <Notice tone="error">{error}</Notice>}
        <p className="text-muted-foreground da-caption">Đã lưu với tên này, mở lại được sau.</p>
        <div className="flex justify-end gap-da-md">
          {scenario.points.length === 0 && (
            <Button loading={busy === 'reload'} onClick={reload}>
              Tải kết quả
            </Button>
          )}
          <Button kind="primary" onClick={() => onScenario(null)}>
            Bỏ kịch bản
          </Button>
        </div>
      </Card>
    );

  return (
    <Card size="sm" title="Kịch bản">
      <Field label="Biến">
        <Select
          label="Biến của kịch bản"
          options={variables.map((v) => ({ value: v, label: v }))}
          value={feature}
          onChange={setFeature}
        />
      </Field>
      <div className="flex gap-da-md">
        <div className="min-w-0 flex-1">
          <Field label="Thay đổi">
            <Select label="Thay đổi" options={CHANGE_KINDS} value={change} onChange={setChange} />
          </Field>
        </div>
        <div className="min-w-0 flex-1">
          <Field label="Mức">
            <Input
              aria-label="Mức thay đổi"
              inputMode="decimal"
              placeholder="0,5"
              disabled={!needsValue}
              value={needsValue ? value : ''}
              onChange={(event) => setValue(event.target.value)}
              className="w-full"
            />
          </Field>
        </div>
      </div>
      <Field label="Từ kỳ">
        <Select
          label="Từ kỳ"
          options={forecast.points.map((p) => ({ value: p.period, label: p.label }))}
          value={from}
          onChange={setFrom}
        />
      </Field>
      <Field label="Tên kịch bản">
        <Input
          aria-label="Tên kịch bản"
          placeholder={defaultName}
          value={name}
          onChange={(event) => setName(event.target.value)}
          className="w-full"
        />
      </Field>
      {error && <Notice tone="error">{error}</Notice>}
      <div className="flex justify-end gap-da-md">
        <Button kind="primary" loading={busy === 'run'} disabled={!ready} onClick={run}>
          Chạy kịch bản
        </Button>
      </div>
    </Card>
  );
}
