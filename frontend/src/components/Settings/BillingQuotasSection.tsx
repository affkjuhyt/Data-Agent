/**
 * Settings → Billing.
 *
 *   spend   useBillingSpend(30)            GET /api/billing/spend?days=30
 *
 * No balance, no transactions, no top-up: the runtime only knows LLM spend.
 * No token quotas either: nothing in the runtime enforces one, so a quota
 * editor here would configure nothing.
 */
import { useState } from 'react';
import { DollarSign, Zap, BarChart3, Layers, RefreshCw, Coins } from 'lucide-react';
import { Button, Card, EmptyState, ErrorState, LoadingState, SectionTitle, Select, StatTile, Table } from '../ui';
import { useBillingSpend, type SpendByModel, type SpendPoint } from '../../lib/useBillingSpend';

const money = (v: number, currency: string) => {
  try { return new Intl.NumberFormat('en-US', { style: 'currency', currency, maximumFractionDigits: 2 }).format(v); }
  catch { return `${v.toFixed(2)} ${currency}`; }
};
const tokens = (v: number) => v.toLocaleString('en-US');

export const BillingQuotasSection = () => {
  const [days, setDays] = useState(30);
  const { spend, loading, error, reload } = useBillingSpend(days);

  const totalTokens = spend?.series.reduce((a, p) => a + (p.tokens ?? 0), 0) ?? 0;
  const currency = spend?.currency ?? 'USD';

  return (
    <div className="space-y-6">
      <SectionTitle
        title="Billing"
        subtitle="Chi phí LLM theo ngày và theo model từ runtime."
        actions={
          <div className="flex items-center gap-2">
            <Select value={days} onChange={e => setDays(Number(e.target.value))} aria-label="Khoảng thời gian">
              <option value={7}>7 ngày</option>
              <option value={30}>30 ngày</option>
              <option value={90}>90 ngày</option>
            </Select>
            <Button icon={RefreshCw} loading={loading} onClick={reload}>Tải lại</Button>
          </div>
        }
      />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatTile label={`Chi phí ${days} ngày`} value={spend?.available ? money(spend.total, currency) : '—'} icon={DollarSign} loading={loading} />
        <StatTile label="Tokens" value={spend?.available ? tokens(totalTokens) : '—'} icon={Zap} loading={loading} />
        <StatTile label="Models có chi phí" value={spend?.available ? spend.by_model.length : '—'} icon={Layers} loading={loading} />
        <StatTile label="Ngày có dữ liệu" value={spend?.available ? spend.series.filter(p => p.spend > 0 || p.tokens > 0).length : '—'} icon={BarChart3} loading={loading} />
      </div>

      <div className="space-y-6">
        <Card title="Chi phí theo ngày" subtitle={`GET /api/billing/spend?days=${days}`}>
          {error ? <ErrorState message={error} onRetry={reload} /> : loading ? <LoadingState /> : !spend || !spend.available ? (
            <EmptyState icon={Coins} title="Chưa có nguồn chi phí" description={spend?.note ?? 'Runtime chưa cấu hình nguồn dữ liệu chi phí (LiteLLM spend log).'} />
          ) : spend.series.length === 0 ? (
            <EmptyState icon={BarChart3} title="Không có dữ liệu trong khoảng này" description={spend.note ?? undefined} />
          ) : (
            <SpendChart series={spend.series} currency={currency} />
          )}
        </Card>

        <Card title="Chi phí theo model" subtitle="by_model" padding="none">
          {loading ? <LoadingState /> : (
            <Table<SpendByModel>
              rows={spend?.available ? spend.by_model : []}
              rowKey={r => r.model}
              empty={<EmptyState icon={Layers} title="Chưa có chi phí theo model" />}
              columns={[
                { header: 'Model', cell: r => <span className="font-mono font-bold">{r.model}</span> },
                { header: 'Tokens', align: 'right', cell: r => <span className="font-mono">{tokens(r.tokens)}</span> },
                { header: 'Chi phí', align: 'right', cell: r => <span className="font-mono font-bold">{money(r.spend, currency)}</span> },
                { header: 'Tỷ trọng', align: 'right', cell: r => <span className="font-mono">{spend && spend.total > 0 ? `${((r.spend / spend.total) * 100).toFixed(1)}%` : '—'}</span> },
              ]}
            />
          )}
        </Card>
      </div>
    </div>
  );
};

/* ─── Single-series bar chart (spend/day) — one hue, one axis, table view below ─── */
const SpendChart = ({ series, currency }: { series: SpendPoint[]; currency: string }) => {
  const [hover, setHover] = useState<number | null>(null);
  const [showTable, setShowTable] = useState(false);
  const W = 720; const H = 200; const padL = 44; const padB = 24; const padT = 8;
  const max = Math.max(...series.map(p => p.spend), 0) || 1;
  const n = series.length;
  const slot = (W - padL) / n;
  const barW = Math.max(2, Math.min(28, slot - 2));
  const y = (v: number) => padT + (H - padT - padB) * (1 - v / max);
  const ticks = [0, 0.5, 1].map(f => f * max);
  const label = (d: string) => { const dt = new Date(d); return Number.isNaN(dt.getTime()) ? d : `${dt.getDate()}/${dt.getMonth() + 1}`; };
  const every = Math.ceil(n / 8);

  return (
    <div>
      <div className="overflow-x-auto">
        <svg viewBox={`0 0 ${W} ${H}`} className="w-full min-w-[480px]" role="img" aria-label={`Chi phí LLM theo ngày, ${n} ngày`}>
          {ticks.map(t => (
            <g key={t}>
              <line x1={padL} x2={W} y1={y(t)} y2={y(t)} stroke="#E4D6AE" strokeWidth={1} />
              <text x={padL - 6} y={y(t) + 3} textAnchor="end" fontSize={10} fill="#5F5F5F">{money(t, currency)}</text>
            </g>
          ))}
          {series.map((p, i) => {
            const x = padL + i * slot + (slot - barW) / 2;
            const h = Math.max(0, (H - padB) - y(p.spend));
            const active = hover === i;
            return (
              <g key={p.date} onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}>
                <rect x={padL + i * slot} y={padT} width={slot} height={H - padT - padB} fill="transparent" />
                <rect x={x} y={y(p.spend)} width={barW} height={h} rx={h > 4 ? 4 : 0} fill={active ? '#8A5A00' : '#B88719'}>
                  <title>{`${p.date}: ${money(p.spend, currency)} · ${tokens(p.tokens)} tokens`}</title>
                </rect>
                {i % every === 0 && <text x={x + barW / 2} y={H - 6} textAnchor="middle" fontSize={10} fill="#5F5F5F">{label(p.date)}</text>}
              </g>
            );
          })}
          <line x1={padL} x2={W} y1={H - padB} y2={H - padB} stroke="#BFA66A" strokeWidth={1} />
        </svg>
      </div>
      <div className="flex items-center justify-between mt-2">
        <p className="text-[11px] text-[#5F5F5F] h-4">
          {hover !== null ? `${series[hover].date} · ${money(series[hover].spend, currency)} · ${tokens(series[hover].tokens)} tokens` : 'Di chuột lên cột để xem chi tiết'}
        </p>
        <Button size="sm" variant="ghost" onClick={() => setShowTable(v => !v)}>{showTable ? 'Ẩn bảng' : 'Xem bảng'}</Button>
      </div>
      {showTable && (
        <div className="mt-3">
          <Table<SpendPoint>
            rows={series}
            rowKey={p => p.date}
            columns={[
              { header: 'Ngày', cell: p => <span className="font-mono">{p.date}</span> },
              { header: 'Tokens', align: 'right', cell: p => <span className="font-mono">{tokens(p.tokens)}</span> },
              { header: 'Chi phí', align: 'right', cell: p => <span className="font-mono">{money(p.spend, currency)}</span> },
            ]}
          />
        </div>
      )}
    </div>
  );
};
