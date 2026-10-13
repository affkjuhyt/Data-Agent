import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { Card } from '../../design-system/Card';
import { Checkbox } from '../../design-system/Checkbox';
import { EmptyState } from '../../design-system/EmptyState';
import { Menu } from '../../design-system/Menu';
import { StatusDot } from '../../design-system/StatusDot';
import { csvCell } from '../../lib/csv';
import { cn } from '../../lib/utils';
import {
  formatWhole,
  formatWholeSigned,
  getForecastLedger,
  listForecastSeries,
  type ForecastLedger,
  type ForecastLedgerRow,
  type ForecastStat,
} from '../../lib/v1/forecast';
import { useV1 } from '../../lib/v1/useV1';
import { AccessDenied, forbiddenOf } from '../access/AccessDenied';
import { LedgerChart, LedgerLegend } from './FuelCharts';
import { dayMonthPadded, dayMonthTime, dayMonthYear, FollowScheduleButton } from './fuelParts';
import { DataTable, downloadCsv, Stat, type Column } from './parts';

/*
 * "P15 · Sổ dự báo" (2424:15221, UC-15-08, SCR-71): the post-period bulletin on the web. Every
 * published forecast is written before its period and never edited or deleted; once the
 * ministry publishes the price it is scored. Calibration covers the model's fixed N whatever
 * the window shows, and missed periods stay in the ledger with the Error Book entry they
 * opened (BR-185).
 */

const pct = (count: number, of: number) => (of > 0 ? Math.round((count / of) * 100) : 0);

/** Coverage is on target when within a few points of its nominal level. */
const coverageTone = (count: number, of: number, nominal: number): ForecastStat['tone'] =>
  of > 0 && Math.abs(count / of - nominal) <= (nominal >= 0.95 ? 0.05 : 0.1)
    ? 'success'
    : 'warning';

export function calibrationStats(l: ForecastLedger): ForecastStat[] {
  const c = l.calibration;
  const crps = Math.round(c.crps_vs_naive * 100);
  return [
    {
      label: 'Độ phủ khoảng 80%',
      value: `${pct(c.inside80, c.periods)}%`,
      note: `${c.inside80}/${c.periods} kỳ trong khoảng`,
      tone: coverageTone(c.inside80, c.periods, 0.8),
    },
    {
      label: 'Độ phủ khoảng 95%',
      value: `${pct(c.inside95, c.periods)}%`,
      note: `${c.inside95}/${c.periods} kỳ trong khoảng`,
      tone: coverageTone(c.inside95, c.periods, 0.95),
    },
    {
      label: 'MAE',
      value: `${formatWhole(c.mae)} ${l.unit_label}`,
      note: 'sai tuyệt đối bình quân',
      tone: 'default',
    },
    {
      label: 'CRPS so naive',
      value: `${formatWholeSigned(crps)}%`,
      note: crps < 0 ? 'tốt hơn giữ giá kỳ trước' : 'không tốt hơn giữ giá kỳ trước',
      tone: crps < 0 ? 'success' : 'error',
    },
    {
      label: 'Kỳ trượt',
      value: String(c.missed),
      note: c.miss_rule_text,
      tone: c.missed > 0 ? 'error' : 'default',
    },
  ];
}

function insideText(r: ForecastLedgerRow) {
  const word = (inside: boolean | null) => (inside ? 'Có' : 'Không');
  return `${word(r.inside80)} / ${word(r.inside95)}`;
}

function csvOf(l: ForecastLedger): string {
  const head = [
    'Kỳ',
    'Phát hành lúc',
    'Trung vị',
    'Thấp 80%',
    'Cao 80%',
    'Thấp 95%',
    'Cao 95%',
    'Công bố',
    'Sai số',
    'Trong 80%',
    'Trong 95%',
    'Kỳ trượt',
    'Lệch chính',
    'Error Book',
  ];
  const yesNo = (v: boolean | null) => (v === null ? '' : v ? 'Có' : 'Không');
  const rows = l.rows.map((r) => [
    dayMonthYear(r.period_date),
    r.published_at,
    r.median,
    r.lo80,
    r.hi80,
    r.lo95,
    r.hi95,
    r.actual ?? '',
    r.error ?? '',
    yesNo(r.inside80),
    yesNo(r.inside95),
    r.missed ? 'Có' : 'Không',
    r.main_driver ?? '',
    r.error_book_ref ?? '',
  ]);
  const meta = [
    [`Sổ dự báo giá trần ${l.product_label} · ${l.model_version_label} · ${l.unit_label}`],
  ];
  return [...meta, head, ...rows].map((row) => row.map(csvCell).join(',')).join('\n');
}

export default function ForecastLedgerPage() {
  const { routeId } = useAppState();
  const [periods, setPeriods] = useState<number>();
  // From the rail without an id: the first fuel's ledger.
  const series = useV1(routeId ? null : listForecastSeries, [routeId]);
  const id = routeId || series.data?.items[0]?.id;
  const ledger = useV1(id ? () => getForecastLedger(id, periods) : null, [id, periods]);
  const forbidden = forbiddenOf(ledger.error);
  if (forbidden) return <AccessDenied access={forbidden} />;
  if (!routeId && series.error) {
    return (
      <AsyncRegion state={series} what="sổ dự báo">
        {() => null}
      </AsyncRegion>
    );
  }
  if (!routeId && series.data && series.data.items.length === 0) {
    return (
      <div className="px-da-3xl pt-da-xl">
        <EmptyState
          icon="chart"
          title="Chưa có sổ dự báo nào"
          description="Mỗi dự báo giá được phát hành sẽ được ghi vào sổ trước kỳ và chấm khi có giá công bố."
        />
      </div>
    );
  }
  return (
    <AsyncRegion state={ledger} what="sổ dự báo">
      {(l) => <LedgerBody ledger={l} onPeriods={setPeriods} />}
    </AsyncRegion>
  );
}

function LedgerBody({
  ledger: l,
  onPeriods,
}: {
  ledger: ForecastLedger;
  onPeriods: (periods: number) => void;
}) {
  const { navigate } = useAppState();
  const [missedOnly, setMissedOnly] = useState(false);
  const rows = missedOnly ? l.rows.filter((r) => r.missed) : l.rows;
  const newest = l.rows[0];
  const oldest = l.rows[l.rows.length - 1];
  const windowLabel =
    l.windows.find((w) => w.periods === l.window_periods)?.label ?? `${l.window_periods} kỳ`;
  const range =
    newest && oldest
      ? ` · ${dayMonthPadded(oldest.period_date)} – ${dayMonthPadded(newest.period_date)}/${newest.period_date.slice(0, 4)}`
      : '';

  const columns: Column<ForecastLedgerRow>[] = [
    {
      head: 'Kỳ',
      cell: (r) => (
        <span className={cn(r.missed && 'font-medium text-error')}>
          {dayMonthYear(r.period_date)}
        </span>
      ),
    },
    { head: 'Phát hành lúc', cell: (r) => `${dayMonthTime(r.published_at)} · ${r.channel_label}` },
    { head: 'Trung vị', cell: (r) => formatWhole(r.median) },
    { head: 'Khoảng 80%', cell: (r) => `${formatWhole(r.lo80)} – ${formatWhole(r.hi80)}` },
    { head: 'Công bố', cell: (r) => (r.actual === null ? '—' : formatWhole(r.actual)) },
    {
      head: 'Sai số',
      cell: (r) =>
        r.error === null ? (
          '—'
        ) : (
          <span className={cn(r.missed && 'font-semibold text-error')}>
            {formatWholeSigned(r.error)}
          </span>
        ),
    },
    {
      head: 'Trong 80 / 95%',
      cell: (r) =>
        r.inside80 === null || r.inside95 === null ? (
          <StatusDot tone="idle">Chờ công bố</StatusDot>
        ) : (
          <StatusDot tone={r.inside95 ? (r.inside80 ? 'success' : 'warning') : 'error'}>
            {insideText(r)}
          </StatusDot>
        ),
    },
    {
      head: 'Lệch chính',
      cell: (r) => r.main_driver ?? <span className="text-muted-foreground">—</span>,
    },
    {
      head: 'Error Book',
      cell: (r) =>
        r.error_book_ref ? (
          // Error Book ids are the code in lower case (EB-0031 → eb-0031), P07 SCR-69.
          <button
            type="button"
            className="font-medium text-info hover:underline"
            onClick={() => navigate('data', 'ERROR_BOOK', r.error_book_ref?.toLowerCase())}
          >
            {r.error_book_ref}
          </button>
        ) : (
          <span className="text-muted-foreground">—</span>
        ),
    },
  ];

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Phân tích', 'Dự báo', 'Giá xăng dầu trong nước', 'Sổ dự báo']}
        title={
          <span className="flex flex-wrap items-center gap-[10px]">
            Sổ dự báo · Giá trần {l.product_label}
            {l.illustrative && <Badge tone="warning">Số minh hoạ</Badge>}
          </span>
        }
        description={`Mỗi dự báo đã phát hành ghi trước kỳ, không sửa, không xoá; kỳ trượt vẫn hiện (BR-185) · ${l.model_version_label} · chấm khi Bộ Công Thương công bố`}
        actions={
          <>
            <Button onClick={() => downloadCsv(`so-du-bao-${l.series_id}.csv`, csvOf(l))}>
              Xuất
            </Button>
            {l.following_schedule !== null && (
              <FollowScheduleButton
                seriesId={l.series_id}
                following={l.following_schedule}
                primary
              />
            )}
          </>
        }
      />
      <div className="flex flex-col gap-da-xl px-da-3xl pb-da-2xl pt-da-xl">
        <div className="flex flex-wrap items-center gap-da-md">
          <Menu
            triggerKind="button"
            label="Mặt hàng"
            trigger={`Mặt hàng: ${l.product_label}`}
            items={l.products.map((p) => ({
              label: p.label,
              onSelect: () => navigate('analysis', 'FORECAST_LEDGER', p.series_id),
            }))}
          />
          <Menu
            triggerKind="button"
            label="Kỳ"
            trigger={`Kỳ: ${windowLabel}${range}`}
            items={l.windows.map((w) => ({ label: w.label, onSelect: () => onPeriods(w.periods) }))}
          />
          <Checkbox checked={missedOnly} onChange={setMissedOnly}>
            Chỉ kỳ trượt
          </Checkbox>
          <span className="flex-1" />
          <span className="text-muted-foreground da-caption">
            N = {l.calibration.periods} kỳ · cố định theo mô hình
          </span>
        </div>
        <div className="flex flex-wrap gap-da-lg">
          {calibrationStats(l).map((s) => (
            <Stat key={s.label} stat={s} />
          ))}
        </div>
        <Card size="sm" title={`Trung vị, khoảng 80/95% và giá công bố · ${l.rows.length} kỳ`}>
          <LedgerLegend />
          <LedgerChart rows={l.rows} productLabel={l.product_label} />
        </Card>
        {rows.length === 0 ? (
          <EmptyState
            icon="check"
            title="Không có kỳ trượt"
            description={`Trong ${windowLabel}, mọi kỳ đều trong khoảng 95% và sai không quá ngưỡng.`}
          />
        ) : (
          <DataTable
            label="Sổ dự báo · mới nhất trước"
            rows={rows}
            rowKey={(r) => r.period_date}
            rowClassName={(r) => (r.missed ? 'bg-accent' : undefined)}
            columns={columns}
          />
        )}
      </div>
    </div>
  );
}
