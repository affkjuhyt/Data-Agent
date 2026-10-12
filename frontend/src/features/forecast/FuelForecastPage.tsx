import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { Card, CardLink } from '../../design-system/Card';
import { Dialog } from '../../design-system/Dialog';
import { EmptyState } from '../../design-system/EmptyState';
import { Notice } from '../../design-system/Notice';
import { Radio } from '../../design-system/Radio';
import { StatusDot } from '../../design-system/StatusDot';
import { Tabs } from '../../design-system/Tabs';
import { useToast } from '../../design-system/Toast';
import { cn } from '../../lib/utils';
import {
  clearFundScenario,
  formatPercent,
  formatWhole,
  formatWholeSigned,
  getForecastLedger,
  getFuelPriceForecast,
  listFuelPriceForecasts,
  rerunFuelPriceForecast,
  runFundScenario,
  type ForecastLedgerRow,
  type FuelFundLevel,
  type FuelPriceForecast,
} from '../../lib/v1/forecast';
import { useV1 } from '../../lib/v1/useV1';
import { AccessDenied, forbiddenOf } from '../access/AccessDenied';
import { DistributionChart, WaterfallChart } from './FuelCharts';
import {
  dayMonth,
  dayMonthTime,
  dayMonthYear,
  FollowScheduleButton,
  hasInterval,
} from './fuelParts';
import { DataTable, Stat } from './parts';

/*
 * "P15 · Dự báo giá xăng · phân phối và phân rã" (2422:15221, UC-15-07, SCR-70): the pre-period
 * bulletin on the web. The ceiling of the next adjustment period for one fuel as a simulated
 * distribution with its 80/95% intervals, what moves it from the previous ceiling, the
 * probability of each Fund level next to the sentence that the Fund is the ministries' decision
 * (BR-184), the certified formula parameters in force (BR-182), how much of the world-price
 * window is still simulated, and the last five scored periods of the ledger.
 */

/** BR-184: shown next to the Fund probabilities, word for word. */
export const FUND_DISCLAIMER =
  'Quyết định Quỹ do liên Bộ quyết; mô hình chỉ ước xác suất theo các kỳ đã qua.';

export default function FuelForecastPage() {
  const { routeId } = useAppState();
  // From the rail without an id: the first fuel of the latest run.
  const list = useV1(routeId ? null : listFuelPriceForecasts, [routeId]);
  const id = routeId || list.data?.items[0]?.id;
  const forecast = useV1(id ? () => getFuelPriceForecast(id) : null, [id]);
  const forbidden = forbiddenOf(forecast.error);
  if (forbidden) return <AccessDenied access={forbidden} />;
  if (!routeId && list.error) {
    return (
      <AsyncRegion state={list} what="dự báo giá">
        {() => null}
      </AsyncRegion>
    );
  }
  if (!routeId && list.data && list.data.items.length === 0) {
    return (
      <div className="px-da-3xl pt-da-xl">
        <EmptyState
          icon="chart"
          title="Chưa có dự báo giá nào"
          description="Dự báo giá trần được chạy trước mỗi kỳ điều hành và hiện ở đây."
        />
      </div>
    );
  }
  return (
    <AsyncRegion state={forecast} what="dự báo giá">
      {(f) => <FuelForecastBody forecast={f} onChanged={forecast.reload} />}
    </AsyncRegion>
  );
}

function FuelForecastBody({
  forecast: f,
  onChanged,
}: {
  forecast: FuelPriceForecast;
  onChanged: () => void;
}) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [rerunning, setRerunning] = useState(false);
  const [scenarioOpen, setScenarioOpen] = useState(false);
  const d = f.distribution;
  const shown = hasInterval(d);
  const tag = f.illustrative ? ' · minh hoạ' : '';

  const rerun = async () => {
    setRerunning(true);
    try {
      await rerunFuelPriceForecast(f.id);
      toast({ tone: 'success', message: 'Đã chạy lại mô phỏng với giá thế giới mới nhất' });
      onChanged();
    } catch (e) {
      toast({ tone: 'error', message: (e as Error).message });
    } finally {
      setRerunning(false);
    }
  };

  const change = d.median - f.previous.ceiling;

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Phân tích', 'Dự báo', 'Giá xăng dầu trong nước']}
        title={
          <span className="flex flex-wrap items-center gap-[10px]">
            Dự báo giá trần · kỳ {dayMonthYear(f.period_date)}
            {f.illustrative && <Badge tone="warning">Số dự báo minh hoạ</Badge>}
          </span>
        }
        description={`Kỳ ${dayMonthYear(f.period_date)}, ${f.period_note} · chạy ${dayMonthTime(f.run_at)} · ${f.model.version_label} · ${formatWhole(f.model.simulations)} lần mô phỏng · ${f.source_note}`}
        actions={
          <>
            <Button loading={rerunning} onClick={rerun}>
              Chạy lại
            </Button>
            <Button onClick={() => setScenarioOpen(true)}>Kịch bản Quỹ</Button>
            <FollowScheduleButton seriesId={f.series_id} following={f.following_schedule} />
            <Button kind="primary" onClick={() => navigate('analysis', 'ASK')}>
              Hỏi tiếp
            </Button>
          </>
        }
      />
      <div className="flex flex-wrap items-start gap-da-xl px-da-3xl pb-da-2xl pt-da-xl">
        <div className="flex min-w-0 flex-[1_1_640px] flex-col gap-da-lg">
          <Tabs
            label="Mặt hàng"
            items={f.products.map((p) => ({ value: p.forecast_id, label: p.label }))}
            value={f.id}
            onChange={(next) => navigate('analysis', 'FUEL_FORECAST', next)}
          />
          {shown ? (
            <>
              <div className="flex flex-wrap gap-da-lg">
                <Stat
                  stat={{
                    label: `Giá trần kỳ trước · ${dayMonth(f.previous.period_date)}`,
                    value: formatWhole(f.previous.ceiling),
                    note: `${f.unit_label} · ${f.previous.source_text}`,
                    tone: 'default',
                  }}
                />
                <Stat
                  stat={{
                    label: `Trung vị kỳ ${dayMonth(f.period_date)}`,
                    value: formatWhole(d.median),
                    note: `${formatWholeSigned(change)} ${f.unit_label} so kỳ trước${tag}`,
                    tone: change > 0 ? 'warning' : change < 0 ? 'success' : 'default',
                  }}
                />
                <Stat
                  stat={{
                    label: 'P(tăng)',
                    value: formatPercent(d.p_increase),
                    note: `xác suất giá trần tăng${tag}`,
                    tone: 'default',
                  }}
                />
              </div>
              <Card
                size="sm"
                title={`Phân phối giá trần ${f.product_label} kỳ ${dayMonth(f.period_date)}${tag}`}
              >
                <DistributionLegend forecast={f} />
                <DistributionChart forecast={f} />
                {f.fund_scenario && <FundScenarioNotice forecast={f} onChanged={onChanged} />}
              </Card>
            </>
          ) : (
            <Notice tone="error">
              Dự báo kỳ này thiếu khoảng tin cậy nên không hiển thị. Chạy lại để mô phỏng lại phân
              phối.
            </Notice>
          )}
          <div className="flex flex-wrap items-start gap-da-lg">
            <Card
              size="sm"
              title={`Phân rã chênh lệch kỳ vọng so kỳ trước (${f.unit_label})`}
              className="min-w-0 flex-[1_1_380px]"
            >
              <WaterfallChart forecast={f} />
              <p className="text-muted-foreground da-caption">{f.decomposition.source_note}</p>
            </Card>
            <FundCard forecast={f} />
          </div>
        </div>
        <aside className="flex w-[392px] max-w-full shrink-0 flex-col gap-da-lg">
          <ParametersCard forecast={f} />
          <WorldPriceCard forecast={f} />
          <RecentLedgerCard forecast={f} />
        </aside>
      </div>
      <FundScenarioDialog
        open={scenarioOpen}
        forecast={f}
        onClose={() => setScenarioOpen(false)}
        onDone={() => {
          setScenarioOpen(false);
          onChanged();
        }}
      />
    </div>
  );
}

function DistributionLegend({ forecast: f }: { forecast: FuelPriceForecast }) {
  const d = f.distribution;
  const items = [
    {
      swatch: 'bg-info opacity-35',
      text: `Khoảng 80%: ${formatWhole(d.lo80)} – ${formatWhole(d.hi80)}`,
    },
    {
      swatch: 'bg-info opacity-15',
      text: `Khoảng 95%: ${formatWhole(d.lo95)} – ${formatWhole(d.hi95)}`,
    },
    { swatch: 'bg-info', text: `Trung vị ${formatWhole(d.median)}` },
    { swatch: 'bg-foreground', text: `Giá kỳ trước ${formatWhole(f.previous.ceiling)}` },
  ];
  return (
    <ul className="flex flex-wrap items-center gap-da-xl" aria-label="Chú giải">
      {items.map((item) => (
        <li key={item.text} className="flex items-center gap-da-sm">
          <span className={cn('h-2 w-[14px] rounded-[2px]', item.swatch)} aria-hidden />
          <span className="text-muted-foreground da-caption">{item.text}</span>
        </li>
      ))}
    </ul>
  );
}

function FundScenarioNotice({
  forecast: f,
  onChanged,
}: {
  forecast: FuelPriceForecast;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const s = f.fund_scenario;
  if (!s) return null;
  const clear = async () => {
    setBusy(true);
    try {
      await clearFundScenario(f.id);
      onChanged();
    } catch (e) {
      toast({ tone: 'error', message: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };
  return (
    <Notice
      tone="info"
      action={
        <Button size="sm" kind="ghost" loading={busy} onClick={clear}>
          Bỏ kịch bản
        </Button>
      }
    >
      Kịch bản Quỹ {s.fund_level_label}: trung vị {formatWhole(s.median)} · khoảng 80%{' '}
      {formatWhole(s.lo80)} – {formatWhole(s.hi80)} · khoảng 95% {formatWhole(s.lo95)} –{' '}
      {formatWhole(s.hi95)}. Phép tính có điều kiện, không phải dự báo quyết định Quỹ.
    </Notice>
  );
}

function FundCard({ forecast: f }: { forecast: FuelPriceForecast }) {
  const likeliest = Math.max(...f.fund_levels.map((l) => l.probability));
  const isLikeliest = (l: FuelFundLevel) => l.probability === likeliest;
  return (
    <Card
      size="sm"
      title={`Quỹ bình ổn kỳ ${dayMonth(f.period_date)}${f.illustrative ? ' · minh hoạ' : ''}`}
      className="min-w-0 flex-[1_1_300px]"
    >
      <DataTable
        label="Xác suất theo mức Quỹ"
        rows={f.fund_levels}
        rowKey={(l) => l.label}
        rowClassName={(l) => (isLikeliest(l) ? 'bg-accent' : undefined)}
        columns={[
          { head: 'Mức Quỹ', width: 'w-[60%]', cell: (l) => l.label },
          {
            head: 'Xác suất',
            cell: (l) => (
              <span className={cn(isLikeliest(l) && 'font-semibold')}>
                {formatPercent(l.probability)}
              </span>
            ),
          },
        ]}
      />
      <p className="rounded-md bg-accent px-[10px] py-da-md text-foreground da-caption">
        {FUND_DISCLAIMER}
      </p>
    </Card>
  );
}

function ParametersCard({ forecast: f }: { forecast: FuelPriceForecast }) {
  const { navigate } = useAppState();
  return (
    <Card size="sm" title={`Tham số đang dùng · ${f.product_label}`}>
      <ul className="flex flex-col gap-da-md" aria-label="Tham số công thức">
        {f.parameters.map((p) => (
          <li key={p.key} className="flex flex-col gap-[2px]">
            <div className="flex items-center gap-da-md">
              <span className="min-w-0 flex-1 truncate text-foreground da-mono">{p.key}</span>
              <span className="shrink-0 text-foreground da-label">{p.value_text}</span>
            </div>
            <p className="text-[11px] leading-4 text-muted-foreground">
              hiệu lực {dayMonthYear(p.effective_from)} ·{' '}
              {p.legal_ref.document_id ? (
                <button
                  type="button"
                  className="font-medium text-info hover:underline"
                  onClick={() => navigate('data', 'DOCUMENT', p.legal_ref.document_id ?? undefined)}
                >
                  {p.legal_ref.label}
                </button>
              ) : (
                <span className="font-medium">{p.legal_ref.label}</span>
              )}{' '}
              · {p.certified_by_name}
            </p>
          </li>
        ))}
      </ul>
      <p className="text-muted-foreground da-caption">
        {f.illustrative ? 'Giá trị minh hoạ · ' : ''}người khác người nhập chứng nhận (BR-182)
      </p>
    </Card>
  );
}

function WorldPriceCard({ forecast: f }: { forecast: FuelPriceForecast }) {
  const { known_days: known, total_days: total, simulated_range_text: range } = f.world_price;
  const simulated = total - known;
  return (
    <Card size="sm" title="Giá thế giới bình quân kỳ">
      <div className="flex gap-da-xs" aria-hidden>
        {Array.from({ length: total }, (_, i) => (
          <span
            key={i}
            className={cn(
              'h-2 flex-1 rounded-[2px] bg-info',
              i < known ? 'opacity-90' : 'opacity-25',
            )}
          />
        ))}
      </div>
      <p className="text-foreground da-caption">
        {known}/{total} ngày đã biết · {simulated} ngày mô phỏng ({range})
      </p>
    </Card>
  );
}

const RECENT = 5;

function RecentLedgerCard({ forecast: f }: { forecast: FuelPriceForecast }) {
  const { navigate } = useAppState();
  const ledger = useV1(() => getForecastLedger(f.series_id, RECENT), [f.series_id]);
  return (
    <Card
      size="sm"
      title={`Sổ ${RECENT} kỳ gần nhất`}
      action={
        <CardLink onClick={() => navigate('analysis', 'FORECAST_LEDGER', f.series_id)}>
          Mở sổ
        </CardLink>
      }
    >
      <AsyncRegion state={ledger} what="sổ dự báo">
        {(l) => {
          const c = l.calibration;
          return (
            <>
              <DataTable<ForecastLedgerRow>
                label={`Sổ ${RECENT} kỳ gần nhất`}
                rows={l.rows}
                rowKey={(r) => r.period_date}
                columns={[
                  {
                    head: 'Kỳ',
                    cell: (r) => (
                      <span className={cn(r.missed && 'text-error')}>
                        {dayMonth(r.period_date)}
                      </span>
                    ),
                  },
                  { head: 'Dự báo', cell: (r) => formatWhole(r.median) },
                  {
                    head: 'Công bố',
                    cell: (r) => (r.actual === null ? '—' : formatWhole(r.actual)),
                  },
                  {
                    head: 'Sai',
                    cell: (r) =>
                      r.error === null ? (
                        '—'
                      ) : (
                        <span className={cn(r.missed && 'text-error')}>
                          {formatWholeSigned(r.error)}
                        </span>
                      ),
                  },
                  {
                    head: 'Trong 80%',
                    cell: (r) =>
                      r.inside80 === null ? (
                        <StatusDot tone="idle">Chờ công bố</StatusDot>
                      ) : (
                        <StatusDot tone={r.inside80 ? 'success' : 'error'}>
                          {r.inside80 ? 'Có' : 'Không'}
                        </StatusDot>
                      ),
                  },
                ]}
              />
              <p className="text-muted-foreground da-caption">
                Độ phủ {c.periods} kỳ: 80% → {c.inside80}/{c.periods} · 95% → {c.inside95}/
                {c.periods}
                {l.illustrative ? ' · minh hoạ' : ''}
              </p>
            </>
          );
        }}
      </AsyncRegion>
    </Card>
  );
}

function FundScenarioDialog({
  open,
  forecast: f,
  onClose,
  onDone,
}: {
  open: boolean;
  forecast: FuelPriceForecast;
  onClose: () => void;
  onDone: () => void;
}) {
  const [level, setLevel] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const submit = async () => {
    setBusy(true);
    setError(undefined);
    try {
      await runFundScenario(f.id, level);
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open={open}
      title="Kịch bản Quỹ"
      description={`Giữ cố định một mức Quỹ cho kỳ ${dayMonth(f.period_date)} và xem phân phối giá trần khi đó.`}
      onClose={onClose}
      onSubmit={submit}
      submitLabel="Chạy kịch bản"
      submitting={busy}
      canSubmit={level !== ''}
      error={error}
    >
      <div role="radiogroup" aria-label="Mức Quỹ" className="flex flex-col gap-da-md">
        {f.fund_levels.map((l) => (
          <Radio
            key={l.label}
            name="fund-level"
            value={l.label}
            checked={level === l.label}
            onChange={setLevel}
          >
            {l.label} · xác suất {formatPercent(l.probability)}
          </Radio>
        ))}
      </div>
      <p className="text-muted-foreground da-caption">{FUND_DISCLAIMER}</p>
    </Dialog>
  );
}
