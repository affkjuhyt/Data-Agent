/**
 * Settings → Billing & Quotas.
 *
 *   spend   useBillingSpend(30)            GET /api/billing/spend?days=30
 *   quotas  usePlatformSetting('quotas')   [{id, name, limit_tokens, period}]
 *
 * No balance, no transactions, no top-up: the runtime only knows LLM spend.
 */
import React, { useState } from 'react';
import { DollarSign, Zap, BarChart3, Layers, Plus, RefreshCw, Trash2, Pencil, Check, X, Coins } from 'lucide-react';
import { Button, Card, EmptyState, ErrorState, Field, Input, LoadingState, SectionTitle, Select, StatTile, Table } from '../ui';
import { useBillingSpend, type SpendByModel, type SpendPoint } from '../../lib/useBillingSpend';
import { usePlatformSetting, newSettingId } from '../../lib/usePlatformSettings';

interface Quota { id: string; name: string; limit_tokens: number; period: 'day' | 'week' | 'month' }
const PERIODS: Quota['period'][] = ['day', 'week', 'month'];
const EMPTY_QUOTA: Omit<Quota, 'id'> = { name: '', limit_tokens: 1_000_000, period: 'month' };

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
        title="Billing & Quotas"
        subtitle="Chi phí LLM theo ngày và theo model từ runtime; hạn mức token do bạn cấu hình."
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

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        <div className="xl:col-span-2 space-y-6">
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

        <div>
          <Quotas />
        </div>
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

/* ─── Quotas (persisted) ─────────────────────────────────── */
const Quotas = () => {
  const setting = usePlatformSetting<Quota[]>('quotas', []);
  const quotas = Array.isArray(setting.value) ? setting.value : [];
  const [editing, setEditing] = useState<Quota | null>(null);
  const [draft, setDraft] = useState<Omit<Quota, 'id'>>(EMPTY_QUOTA);
  const [showForm, setShowForm] = useState(false);

  const startAdd = () => { setEditing(null); setDraft(EMPTY_QUOTA); setShowForm(true); };
  const startEdit = (q: Quota) => { setEditing(q); setDraft({ name: q.name, limit_tokens: q.limit_tokens, period: q.period }); setShowForm(true); };
  const cancel = () => { setShowForm(false); setEditing(null); };
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!draft.name.trim() || draft.limit_tokens <= 0) return;
    const next = editing ? quotas.map(q => (q.id === editing.id ? { ...editing, ...draft } : q)) : [...quotas, { id: newSettingId('quota'), ...draft }];
    await setting.save(next).then(cancel).catch(() => {});
  };
  const remove = (q: Quota) => {
    if (!window.confirm(`Xoá quota "${q.name}"?`)) return;
    setting.save(quotas.filter(x => x.id !== q.id)).catch(() => {});
  };

  return (
    <Card
      title="Token quotas"
      subtitle={<>Lưu tại <code className="font-mono">platform/settings/quotas</code>. Việc thực thi hạn mức chưa được wire ở runtime.</>}
      actions={<Button size="sm" variant="primary" icon={Plus} onClick={startAdd} disabled={setting.loading}>Thêm</Button>}
    >
      {setting.error && <div className="mb-4"><ErrorState message={setting.error} onRetry={setting.reload} /></div>}
      {showForm && (
        <form onSubmit={submit} className="mb-4 p-4 rounded-xl border border-[#BFA66A] bg-[#FFF9E8] space-y-3">
          <Field label="Tên"><Input value={draft.name} onChange={e => setDraft({ ...draft, name: e.target.value })} placeholder="vd. tenant-default" required /></Field>
          <Field label="Giới hạn tokens"><Input type="number" min={1} value={draft.limit_tokens} onChange={e => setDraft({ ...draft, limit_tokens: Number(e.target.value) })} required /></Field>
          <Field label="Chu kỳ">
            <Select value={draft.period} onChange={e => setDraft({ ...draft, period: e.target.value as Quota['period'] })}>
              {PERIODS.map(p => <option key={p} value={p}>{p}</option>)}
            </Select>
          </Field>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" size="sm" icon={X} onClick={cancel}>Huỷ</Button>
            <Button type="submit" variant="primary" size="sm" icon={Check} loading={setting.saving}>{editing ? 'Lưu' : 'Thêm'}</Button>
          </div>
        </form>
      )}
      {setting.loading ? <LoadingState /> : quotas.length === 0 ? (
        <EmptyState icon={Zap} title="Chưa có quota" description="Thêm hạn mức token theo ngày/tuần/tháng." />
      ) : (
        <ul className="space-y-2">
          {quotas.map(q => (
            <li key={q.id} className="flex items-center justify-between gap-3 p-3 rounded-xl border border-[#BFA66A] bg-white">
              <div className="min-w-0">
                <div className="text-sm font-bold text-[#111111] truncate">{q.name}</div>
                <div className="text-[11px] text-[#5F5F5F] font-mono">{tokens(q.limit_tokens)} tokens / {q.period}</div>
              </div>
              <div className="flex gap-1 shrink-0">
                <Button size="sm" variant="ghost" icon={Pencil} onClick={() => startEdit(q)} aria-label="Sửa" />
                <Button size="sm" variant="danger" icon={Trash2} onClick={() => remove(q)} aria-label="Xoá" />
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
};
