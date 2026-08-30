import React, { useState, useMemo } from 'react';
import {
  ShieldCheck, LogIn, LogOut, AlertTriangle, Ban, KeyRound,
  RefreshCw, Search, Download, ChevronDown, Filter, Info,
  CheckCircle2, XCircle, Clock, User, Loader2, AlertCircle,
} from 'lucide-react';
import { cn } from '../../../lib/utils';
import { useAuth } from '../../../lib/AuthProvider';
import { useAuthAuditLogs, AuditLogEntry } from '../../../lib/useAuthAuditLogs';

/* ── Event display config ──────────────────────────────────────────────── */
const eventMeta = (event: string): { icon: React.ElementType; color: string; bg: string } => {
  const e = event.toUpperCase();
  if (e.includes('LOGIN_SUCCESS') || e.includes('LOGIN') && !e.includes('FAIL') && !e.includes('ERROR'))
    return { icon: LogIn,        color: 'text-emerald-700', bg: 'bg-emerald-50 border-emerald-200' };
  if (e.includes('LOGIN_FAIL') || e.includes('LOGIN_ERROR') || e.includes('_ERROR') || e.includes('FAILURE'))
    return { icon: XCircle,      color: 'text-red-700',     bg: 'bg-red-50 border-red-200' };
  if (e.includes('LOGOUT') || e.includes('SESSION_EXPIRED'))
    return { icon: LogOut,       color: 'text-slate-600',   bg: 'bg-slate-50 border-slate-200' };
  if (e.includes('TOKEN'))
    return { icon: RefreshCw,    color: 'text-blue-700',    bg: 'bg-blue-50 border-blue-200' };
  if (e.includes('RBAC') || e.includes('ELEVATION') || e.includes('ROLE'))
    return { icon: ShieldCheck,  color: 'text-amber-700',   bg: 'bg-amber-50 border-amber-200' };
  if (e.includes('BLOCK') || e.includes('IP_'))
    return { icon: Ban,          color: 'text-red-700',     bg: 'bg-red-50 border-red-200' };
  if (e.includes('SECRET') || e.includes('PASSWORD') || e.includes('RESET'))
    return { icon: KeyRound,     color: 'text-amber-700',   bg: 'bg-amber-50 border-amber-200' };
  if (e.includes('MFA') || e.includes('TOTP') || e.includes('VERIFY'))
    return { icon: CheckCircle2, color: 'text-emerald-700', bg: 'bg-emerald-50 border-emerald-200' };
  if (e.includes('USER') || e.includes('CREATE') || e.includes('DELETE'))
    return { icon: User,         color: 'text-blue-700',    bg: 'bg-blue-50 border-blue-200' };
  if (e.includes('CLOCK') || e.includes('EXPIRE'))
    return { icon: Clock,        color: 'text-slate-600',   bg: 'bg-slate-50 border-slate-200' };
  return   { icon: AlertTriangle,color: 'text-amber-700',   bg: 'bg-amber-50 border-amber-200' };
};

const OUTCOME_CFG = {
  SUCCESS: { color: 'text-emerald-700', bg: 'bg-emerald-50 border-emerald-200', dot: 'bg-emerald-500' },
  FAILURE: { color: 'text-red-700',     bg: 'bg-red-50 border-red-200',         dot: 'bg-red-500'     },
  BLOCKED: { color: 'text-amber-700',   bg: 'bg-amber-50 border-amber-200',     dot: 'bg-amber-500'   },
};

const fmtTime = (iso: string) =>
  new Date(iso).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

const fmtDate = (iso: string) =>
  new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short' });

const exportCSV = (logs: AuditLogEntry[]) => {
  const rows = [
    ['Time', 'Event', 'Actor', 'IP', 'Outcome', 'Detail'].join(','),
    ...logs.map(e => [
      new Date(e.time).toISOString(),
      e.event,
      `"${e.actor}"`,
      e.ip,
      e.outcome,
      `"${e.detail ?? ''}"`,
    ].join(',')),
  ].join('\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([rows], { type: 'text/csv' }));
  a.download = `auth-audit-${Date.now()}.csv`;
  a.click();
};

/* ── Component ─────────────────────────────────────────────────────────── */
export const AuthAuditPanel = () => {
  const { user } = useAuth();
  const isAdmin  = user?.roles.some(r => r.toUpperCase().replace(/-/g, '_') === 'PLATFORM_ADMIN') ?? false;

  const { logs, loading, error, refetch } = useAuthAuditLogs(isAdmin);

  const [search, setSearch]               = useState('');
  const [outcomeFilter, setOutcomeFilter] = useState<'ALL' | 'SUCCESS' | 'FAILURE' | 'BLOCKED'>('ALL');
  const [selected, setSelected]           = useState<AuditLogEntry | null>(null);
  const [showFilters, setShowFilters]     = useState(false);

  const uniqueEvents = useMemo(() => [...new Set(logs.map(l => l.event))].sort(), [logs]);
  const [eventFilter, setEventFilter] = useState<string>('ALL');

  const filtered = useMemo(() => logs.filter(ev => {
    const q = search.toLowerCase();
    const matchSearch  = !q || ev.actor.toLowerCase().includes(q) || ev.event.toLowerCase().includes(q) || ev.ip.includes(q);
    const matchEvent   = eventFilter   === 'ALL' || ev.event   === eventFilter;
    const matchOutcome = outcomeFilter === 'ALL' || ev.outcome === outcomeFilter;
    return matchSearch && matchEvent && matchOutcome;
  }), [logs, search, eventFilter, outcomeFilter]);

  const stats = useMemo(() => ({
    total:    logs.length,
    failures: logs.filter(e => e.outcome === 'FAILURE').length,
    blocked:  logs.filter(e => e.outcome === 'BLOCKED').length,
    elevated: logs.filter(e => e.event.toUpperCase().includes('ROLE') || e.event.toUpperCase().includes('RBAC')).length,
  }), [logs]);

  if (!isAdmin) {
    return (
      <div className="flex items-center gap-3 p-5 bg-[#FDFAF2] border border-[#E8DFC8] rounded-2xl">
        <ShieldCheck className="w-5 h-5 text-[#B88719] shrink-0" />
        <div>
          <p className="text-sm font-bold text-[#111111]">Admin access required</p>
          <p className="text-xs text-[#777] mt-0.5">Only Platform Admins can view the full auth audit stream.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-5">

      {/* Stats */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        {[
          { label: 'Total Events',     value: stats.total,    sub: 'from Keycloak',      color: 'slate'  },
          { label: 'Failures',         value: stats.failures, sub: 'login / token error', color: 'red'   },
          { label: 'Blocked',          value: stats.blocked,  sub: 'IP / rate-limit',     color: 'amber' },
          { label: 'Role Changes',     value: stats.elevated, sub: 'RBAC / role ops',     color: 'purple'},
        ].map(s => (
          <div key={s.label} className="p-4 bg-white border border-[#E8DFC8] rounded-2xl">
            <div className={cn(
              'text-2xl font-black mb-0.5',
              loading ? 'text-[#CCC]' :
              s.color === 'red'    ? 'text-red-600' :
              s.color === 'amber'  ? 'text-amber-600' :
              s.color === 'purple' ? 'text-purple-600' :
              'text-[#111111]',
            )}>
              {loading ? '…' : s.value}
            </div>
            <div className="text-[10px] font-bold text-[#111111] uppercase tracking-wide">{s.label}</div>
            <div className="text-[9px] text-[#777] mt-0.5">{s.sub}</div>
          </div>
        ))}
      </div>

      {/* Loading */}
      {loading && (
        <div className="flex items-center gap-3 p-4 bg-[#FDFAF2] border border-[#E8DFC8] rounded-xl">
          <Loader2 className="w-4 h-4 text-[#B88719] animate-spin" />
          <span className="text-sm text-[#5A5A5A]">Fetching events from Keycloak…</span>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="p-4 bg-red-50 border border-red-200 rounded-xl flex items-start gap-3">
          <AlertCircle className="w-4 h-4 text-red-500 shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-bold text-red-700">Cannot load audit logs</p>
            <p className="text-xs text-red-600 font-mono mt-0.5">{error}</p>
          </div>
        </div>
      )}

      {/* Toolbar */}
      {!loading && !error && (
        <>
          <div className="flex flex-col sm:flex-row gap-2 sm:items-center sm:justify-between">
            <div className="flex items-center gap-2">
              <div className="relative">
                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[#777]" />
                <input
                  type="text"
                  placeholder="Search actor, event, IP…"
                  value={search}
                  onChange={e => setSearch(e.target.value)}
                  className="bg-white border border-[#BFA66A] pl-8 pr-4 py-1.5 rounded-lg text-[11px] w-52 focus:outline-none focus:border-[#8A5A00] font-mono transition-colors"
                />
              </div>
              <button
                onClick={() => setShowFilters(f => !f)}
                className={cn(
                  'flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-[11px] font-bold transition-all',
                  showFilters
                    ? 'bg-[#111111] text-white border-[#111111]'
                    : 'bg-white text-[#5A5A5A] border-[#D6C79F] hover:border-[#B88719]',
                )}
              >
                <Filter className="w-3.5 h-3.5" />
                Filters
              </button>
            </div>

            <div className="flex items-center gap-2">
              <span className="text-[10px] text-[#777] font-mono">{filtered.length} / {logs.length} events</span>
              <button
                onClick={refetch}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-white border border-[#D6C79F] hover:border-[#B88719] rounded-lg text-[11px] font-bold text-[#5A5A5A] hover:text-[#111111] transition-all"
              >
                <RefreshCw className="w-3.5 h-3.5" />
                Refresh
              </button>
              <button
                onClick={() => exportCSV(filtered)}
                disabled={filtered.length === 0}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-white border border-[#D6C79F] hover:border-[#B88719] rounded-lg text-[11px] font-bold text-[#5A5A5A] hover:text-[#111111] transition-all disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <Download className="w-3.5 h-3.5" />
                Export CSV
              </button>
            </div>
          </div>

          {/* Filters */}
          {showFilters && (
            <div className="flex flex-wrap gap-3 p-4 bg-[#FDFAF2] border border-[#E8DFC8] rounded-xl">
              <div className="flex flex-col gap-1">
                <label className="text-[9px] font-bold text-[#777] uppercase tracking-widest">Event Type</label>
                <div className="relative">
                  <select
                    value={eventFilter}
                    onChange={e => setEventFilter(e.target.value)}
                    className="appearance-none bg-white border border-[#BFA66A] px-3 pr-7 py-1.5 rounded-lg text-[11px] font-mono text-[#111111] focus:outline-none focus:border-[#8A5A00] cursor-pointer"
                  >
                    <option value="ALL">All Events</option>
                    {uniqueEvents.map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                  <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 w-3 h-3 text-[#777] pointer-events-none" />
                </div>
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-[9px] font-bold text-[#777] uppercase tracking-widest">Outcome</label>
                <div className="relative">
                  <select
                    value={outcomeFilter}
                    onChange={e => setOutcomeFilter(e.target.value as any)}
                    className="appearance-none bg-white border border-[#BFA66A] px-3 pr-7 py-1.5 rounded-lg text-[11px] font-mono text-[#111111] focus:outline-none focus:border-[#8A5A00] cursor-pointer"
                  >
                    <option value="ALL">All Outcomes</option>
                    <option value="SUCCESS">SUCCESS</option>
                    <option value="FAILURE">FAILURE</option>
                    <option value="BLOCKED">BLOCKED</option>
                  </select>
                  <ChevronDown className="absolute right-2 top-1/2 -translate-y-1/2 w-3 h-3 text-[#777] pointer-events-none" />
                </div>
              </div>
              {(eventFilter !== 'ALL' || outcomeFilter !== 'ALL' || search) && (
                <button
                  onClick={() => { setEventFilter('ALL'); setOutcomeFilter('ALL'); setSearch(''); }}
                  className="self-end text-[10px] font-bold text-red-600 hover:underline"
                >
                  Clear filters
                </button>
              )}
            </div>
          )}

          {/* Table */}
          <div className="border border-[#E8DFC8] rounded-2xl overflow-hidden bg-white">
            {/* Desktop */}
            <table className="hidden lg:table w-full text-left">
              <thead className="bg-[#FDFAF2] border-b border-[#E8DFC8] text-[10px] font-bold text-[#777] uppercase tracking-[0.1em]">
                <tr>
                  <th className="px-4 py-3">Time</th>
                  <th className="px-4 py-3">Event</th>
                  <th className="px-4 py-3">Actor</th>
                  <th className="px-4 py-3">IP Address</th>
                  <th className="px-4 py-3">Outcome</th>
                  <th className="px-4 py-3 w-8" />
                </tr>
              </thead>
              <tbody className="divide-y divide-[#F0E8D4]">
                {filtered.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-4 py-8 text-center text-sm text-[#777]">
                      No events match current filters.
                    </td>
                  </tr>
                )}
                {filtered.map(ev => {
                  const em = eventMeta(ev.event);
                  const oc = OUTCOME_CFG[ev.outcome];
                  const Icon = em.icon;
                  return (
                    <tr
                      key={ev.id}
                      onClick={() => setSelected(selected?.id === ev.id ? null : ev)}
                      className={cn(
                        'group cursor-pointer transition-colors',
                        selected?.id === ev.id ? 'bg-[#FFF9E8]' : 'hover:bg-[#FDFAF2]',
                      )}
                    >
                      <td className="px-4 py-3 text-[10px] font-mono text-[#777] whitespace-nowrap">
                        <div>{fmtTime(ev.time)}</div>
                        <div className="text-[9px] text-[#AAA]">{fmtDate(ev.time)}</div>
                      </td>
                      <td className="px-4 py-3">
                        <div className={cn('inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-[10px] font-bold', em.bg, em.color)}>
                          <Icon className="w-3 h-3" />
                          {ev.event}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-xs font-mono text-[#111111] max-w-[160px] truncate">{ev.actor}</td>
                      <td className="px-4 py-3 text-[10px] font-mono text-[#555]">{ev.ip}</td>
                      <td className="px-4 py-3">
                        <div className={cn('inline-flex items-center gap-1.5 text-[10px] font-bold px-2 py-0.5 rounded-full border', oc.bg, oc.color)}>
                          <span className={cn('w-1.5 h-1.5 rounded-full', oc.dot)} />
                          {ev.outcome}
                        </div>
                      </td>
                      <td className="px-4 py-3">
                        <Info className={cn('w-3.5 h-3.5 transition-all', selected?.id === ev.id ? 'text-[#B88719]' : 'text-[#DDD] group-hover:text-[#BFA66A]')} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

            {/* Mobile */}
            <div className="lg:hidden divide-y divide-[#F0E8D4]">
              {filtered.map(ev => {
                const em = eventMeta(ev.event);
                const oc = OUTCOME_CFG[ev.outcome];
                const Icon = em.icon;
                return (
                  <div
                    key={ev.id}
                    onClick={() => setSelected(selected?.id === ev.id ? null : ev)}
                    className={cn('p-4 space-y-2 cursor-pointer', selected?.id === ev.id ? 'bg-[#FFF9E8]' : 'hover:bg-[#FDFAF2]')}
                  >
                    <div className="flex justify-between items-start gap-2">
                      <div className={cn('inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-[10px] font-bold', em.bg, em.color)}>
                        <Icon className="w-3 h-3" />
                        {ev.event}
                      </div>
                      <div className={cn('text-[9px] font-bold px-1.5 py-0.5 rounded-full border', oc.bg, oc.color)}>{ev.outcome}</div>
                    </div>
                    <div className="flex gap-3 text-[10px] font-mono text-[#777]">
                      <span>{fmtTime(ev.time)}</span>
                      <span className="text-[#111111] truncate max-w-[120px]">{ev.actor}</span>
                      <span>{ev.ip}</span>
                    </div>
                  </div>
                );
              })}
            </div>

            {/* Inline detail row */}
            {selected && (
              <div className="border-t border-[#E8DFC8] bg-[#FDFAF2] px-5 py-4">
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 text-[11px]">
                  <div>
                    <div className="text-[9px] font-bold text-[#777] uppercase tracking-widest mb-1">Realm</div>
                    <code className="font-mono text-[#111111]">{selected.realm}</code>
                  </div>
                  {selected.sessionId && (
                    <div>
                      <div className="text-[9px] font-bold text-[#777] uppercase tracking-widest mb-1">Session ID</div>
                      <code className="font-mono text-[#111111]">{selected.sessionId}</code>
                    </div>
                  )}
                  {selected.detail && (
                    <div className="sm:col-span-2 lg:col-span-3">
                      <div className="text-[9px] font-bold text-[#777] uppercase tracking-widest mb-1">Detail</div>
                      <p className="text-[#333]">{selected.detail}</p>
                    </div>
                  )}
                  <div className="sm:col-span-2 lg:col-span-3">
                    <div className="text-[9px] font-bold text-[#777] uppercase tracking-widest mb-1">Raw Event</div>
                    <pre className="font-mono text-[10px] text-[#555] bg-white border border-[#E8DFC8] rounded-lg p-3 overflow-auto max-h-40">
                      {JSON.stringify(selected.raw, null, 2)}
                    </pre>
                  </div>
                </div>
              </div>
            )}
          </div>
        </>
      )}

      {/* Pipeline note */}
      <div className="p-3.5 bg-[#FDFAF2] border border-[#E8DFC8] rounded-xl flex items-start gap-2.5">
        <Info className="w-3.5 h-3.5 text-[#B88719] shrink-0 mt-0.5" />
        <p className="text-[11px] text-[#777] leading-relaxed">
          Events sourced live from <strong>Keycloak Admin API</strong> ({KC_URL}/admin/realms/{KC_REALM}/events).
          Persistent copy: Keycloak SPI → Kafka <code className="font-mono bg-[#F4E8C3] px-1 rounded">audit.auth.events</code> → Consumer → PostgreSQL <code className="font-mono bg-[#F4E8C3] px-1 rounded">audit_logs</code>.
        </p>
      </div>
    </div>
  );
};

const KC_URL   = import.meta.env.VITE_KEYCLOAK_URL   ?? 'http://localhost:8080';
const KC_REALM = import.meta.env.VITE_KEYCLOAK_REALM ?? 'aeroflow';
