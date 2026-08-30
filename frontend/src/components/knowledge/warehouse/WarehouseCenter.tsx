import React, { useEffect, useMemo, useState } from 'react';
import {
  ServerCog, Plus, Trash2, CheckCircle2, Database, Table2, Calendar, Layers, Eye, X, Loader2,
} from 'lucide-react';
import { cn } from '../../../lib/utils';
import { apiGet, apiMutate } from '../../../lib/apiClient';
import { useCursorPagination } from '../../../lib/useCursorPagination';
import { Pagination } from '../../shared/Pagination';
import { DetailDrawer } from '../../shared/DetailDrawer';
import { KnowledgeDocument } from '../../../types';
import {
  WConfig, getWarehouseConnection, nextVersion, formatRows,
} from '../knowledge-hub/knowledgeHub.data';

/* Map a raw backend config row → WConfig (mirrors the Knowledge Hub normalizer). */
function normalizeWConfig(raw: Record<string, unknown>, idx: number): WConfig {
  const tables = ((raw.tables ?? []) as Array<Record<string, unknown>>).map((t, ti) => ({
    id: (t.id as string) ?? `${String(raw.id)}-t-${ti}`,
    schema: (t.schema as string) ?? '',
    tableName: (t.tableName as string) ?? (t.name as string) ?? '',
    rowCount: typeof t.rowCount === 'number'
      ? t.rowCount
      : parseInt(String(t.rowCount ?? '0').replace(/[^0-9]/g, ''), 10) || 0,
    columns: Array.isArray(t.columns)
      ? t.columns.length
      : (typeof t.columns === 'number' ? t.columns : 0),
  }));
  const rawStatus = (raw.status as string) ?? '';
  return {
    id: raw.id as string,
    name: (raw.name as string) ?? `Config ${idx + 1}`,
    version: (raw.version as string) ?? (raw.version_number as string) ?? 'v1',
    createdAt: (raw.createdAt as string) ?? (raw.created_at as string) ?? '',
    status: rawStatus === 'Active' ? 'Active' : rawStatus === 'Archived' ? 'Archived' : 'Draft',
    syncSchedule: (raw.syncSchedule as string) ?? (raw.sync_schedule as string) ?? 'Manual',
    connection: raw.connection as Record<string, string> | undefined,
    tables,
  };
}

const platformOf = (doc: KnowledgeDocument): string => {
  const p = (doc.metadata?.doc_type ?? '').replace('Warehouse/', '') || 'warehouse';
  return p.charAt(0).toUpperCase() + p.slice(1);
};

interface Props {
  /** Opens the Add-Warehouse wizard (owned by KnowledgeOpsCenter). */
  onAddWarehouse?: () => void;
}

export const WarehouseCenter: React.FC<Props> = ({ onAddWarehouse }) => {
  const {
    items: warehouses, page, totalPages, hasNext, isLoading: whLoading,
    canJump, goTo, reload: reloadWarehouses,
  } = useCursorPagination<KnowledgeDocument>('/api/knowledge/warehouses', {}, []);

  // The warehouse whose config window (drawer) is open. null = closed.
  const [selected, setSelected] = useState<KnowledgeDocument | null>(null);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);

  const [configs, setConfigs] = useState<WConfig[]>([]);
  const [configsLoading, setConfigsLoading] = useState(false);
  const [selectedConfig, setSelectedConfig] = useState<WConfig | null>(null);
  const [pendingDeleteConfig, setPendingDeleteConfig] = useState<string | null>(null);

  // New-config flow: reuse the warehouse account, change DB/schema, re-discover tables, reselect.
  const [showNewConfig, setShowNewConfig] = useState(false);
  const [newCfgStep, setNewCfgStep] = useState<'connection' | 'tables'>('connection');
  const [newCfgSchedule, setNewCfgSchedule] = useState('Manual');
  const [newCfgDatabase, setNewCfgDatabase] = useState('');
  const [newCfgSchema, setNewCfgSchema] = useState('');
  const [newCfgPassword, setNewCfgPassword] = useState('');
  const [newCfgDiscovering, setNewCfgDiscovering] = useState(false);
  const [newCfgConnError, setNewCfgConnError] = useState<string | null>(null);
  const [newCfgTables, setNewCfgTables] = useState<Array<{ name: string; schema: string; rowCount: string; selected: boolean; description: string }>>([]);
  const [savingConfig, setSavingConfig] = useState(false);

  /* ── Load configs for the open warehouse ── */
  useEffect(() => {
    if (!selected) { setConfigs([]); setSelectedConfig(null); return; }
    setConfigsLoading(true);
    setShowNewConfig(false);
    apiGet<Record<string, unknown>[] | { items: Record<string, unknown>[] }>(`/api/knowledge/warehouses/${selected.data_id}/configs`)
      .then(data => {
        const rows = Array.isArray(data) ? data : (data?.items ?? []);
        const list = rows.map((r, i) => normalizeWConfig(r, i));
        setConfigs(list);
        setSelectedConfig(list[0] ?? null);
      })
      .catch(() => { setConfigs([]); setSelectedConfig(null); })
      .finally(() => setConfigsLoading(false));
  }, [selected]);

  const connection = useMemo(
    () => selected ? getWarehouseConnection(selected.metadata?.doc_type ?? '', selectedConfig?.connection) : null,
    [selected, selectedConfig],
  );

  // The account/credentials to reuse for a new version come from the active config
  // (or the first one). Only database/schema are editable in the new-version flow.
  const accountConn = useMemo<Record<string, string>>(() => {
    const active = configs.find(c => c.status === 'Active') ?? configs[0];
    return (active?.connection ?? {}) as Record<string, string>;
  }, [configs]);

  const autoNextVer = useMemo(() => nextVersion(configs.map(c => c.version)), [configs]);

  /* ── Mutations ── */
  const activateConfig = (cfg: WConfig) => {
    if (!selected) return;
    const updated = configs.map(c => ({
      ...c,
      status: (c.id === cfg.id ? 'Active' : c.status === 'Active' ? 'Inactive' : c.status) as WConfig['status'],
    }));
    setConfigs(updated);
    setSelectedConfig(updated.find(c => c.id === cfg.id) ?? null);
    apiMutate('PATCH', `/api/knowledge/warehouses/${selected.data_id}/configs/${cfg.id}/activate`, {}).catch(() => {});
  };

  const deleteConfig = async (configId: string) => {
    if (!selected) return;
    try {
      await apiMutate('DELETE', `/api/knowledge/warehouses/${selected.data_id}/configs/${configId}`);
      const updated = configs.filter(c => c.id !== configId);
      setConfigs(updated);
      if (selectedConfig?.id === configId) setSelectedConfig(updated[0] ?? null);
    } catch { /* ForbiddenToast handles 403 */ } finally { setPendingDeleteConfig(null); }
  };

  const deleteWarehouse = async (e: React.MouseEvent, wh: KnowledgeDocument) => {
    e.stopPropagation();
    try {
      await apiMutate('DELETE', `/api/knowledge/warehouses/${wh.data_id}`);
      if (selected?.data_id === wh.data_id) setSelected(null);
      reloadWarehouses();
    } catch { /* ForbiddenToast handles 403 */ } finally { setPendingDeleteId(null); }
  };

  const refetchConfigs = async () => {
    if (!selected) return;
    const data = await apiGet<Record<string, unknown>[] | { items: Record<string, unknown>[] }>(`/api/knowledge/warehouses/${selected.data_id}/configs`);
    const rows = Array.isArray(data) ? data : (data?.items ?? []);
    const list = rows.map((r, i) => normalizeWConfig(r, i));
    setConfigs(list);
    setSelectedConfig(list[0] ?? null);
  };

  const openNewConfig = () => {
    setShowNewConfig(true);
    setNewCfgStep('connection');
    setNewCfgDatabase(accountConn.database ?? '');
    setNewCfgSchema(accountConn.schema ?? '');
    setNewCfgPassword('');
    setNewCfgTables([]);
    setNewCfgConnError(null);
  };
  const closeNewConfig = () => {
    setShowNewConfig(false);
    setNewCfgStep('connection');
    setNewCfgPassword('');
    setNewCfgTables([]);
    setNewCfgConnError(null);
  };

  // Reuse the warehouse account; re-discover tables for the (possibly changed) database/schema.
  const loadTables = async () => {
    setNewCfgDiscovering(true);
    setNewCfgConnError(null);
    try {
      const res = await apiMutate<{ tables: Array<{ table_name: string; row_count: number }> }>(
        'POST', '/api/knowledge/warehouses/connect', {
          account_identifier: accountConn.account ?? accountConn.host ?? '',
          user: accountConn.username ?? accountConn.user ?? '',
          password: newCfgPassword,
          warehouse: accountConn.warehouse ?? '',
          database: newCfgDatabase,
          schema: newCfgSchema || 'PUBLIC',
          role: accountConn.role || null,
        });
      setNewCfgTables((res?.tables ?? []).map(t => ({
        name: t.table_name,
        schema: newCfgSchema || 'PUBLIC',
        rowCount: t.row_count != null ? t.row_count.toLocaleString() : '—',
        selected: false,
        description: '',
      })));
      setNewCfgStep('tables');
    } catch (e) {
      setNewCfgConnError(e instanceof Error ? e.message : 'Connection failed');
    } finally {
      setNewCfgDiscovering(false);
    }
  };

  const createConfig = async () => {
    if (!selected) return;
    setSavingConfig(true);
    const picked = newCfgTables.filter(t => t.selected);
    try {
      await apiMutate('POST', `/api/knowledge/warehouses/${selected.data_id}/configs`, {
        name: `Config ${autoNextVer}`,
        version: autoNextVer,
        status: 'Draft',
        syncSchedule: newCfgSchedule,
        connection: {
          account: accountConn.account ?? accountConn.host ?? '',
          username: accountConn.username ?? accountConn.user ?? '',
          warehouse: accountConn.warehouse ?? '',
          database: newCfgDatabase,
          schema: newCfgSchema || 'PUBLIC',
          role: accountConn.role ?? '',
        },
        tables: picked.map(t => ({ name: t.name, schema: t.schema, rowCount: t.rowCount, description: t.description, columns: [] })),
      });
      await refetchConfigs();
      closeNewConfig();
    } catch { /* ForbiddenToast handles 403 */ } finally { setSavingConfig(false); }
  };

  const statusChip = (s: WConfig['status']) => (
    <span className={cn(
      'text-[10px] font-mono font-bold px-2 py-0.5 rounded border',
      s === 'Active' ? 'bg-[#E7F5EC] text-[#1B7A43] border-[#1B7A43]/30'
        : s === 'Archived' ? 'bg-[#F1EEE6] text-[#7A6A3A] border-[#7A6A3A]/30'
          : 'bg-[#FFF9E8] text-[#8A5A00] border-[#BFA66A]/40',
    )}>{s.toUpperCase()}</span>
  );

  /* ── Config management window (drawer body) ── */
  const configWindow = (
    <div className="flex gap-5 h-full overflow-hidden p-1">
      {/* config list */}
      <div className="w-[260px] shrink-0 flex flex-col">
        <div className="flex items-center justify-between mb-3">
          <h4 className="text-xs font-bold uppercase tracking-wider text-[#8A5A00]">Config Versions</h4>
          <button onClick={openNewConfig} className="btn-gold !px-2 !py-1 !text-[11px]">
            <Plus className="w-3 h-3" /> New
          </button>
        </div>
        <div className="flex-1 overflow-y-auto space-y-2 custom-scrollbar pr-1">
          {configsLoading ? (
            <div className="flex items-center justify-center py-10">
              <Loader2 className="w-5 h-5 animate-spin text-[#B88719]" />
            </div>
          ) : configs.length === 0 ? (
            <div className="text-center py-10 text-xs text-slate-400 font-mono">No configs yet.</div>
          ) : configs.map(cfg => (
            <div
              key={cfg.id}
              onClick={() => { setSelectedConfig(cfg); setShowNewConfig(false); }}
              className={cn(
                'p-3 rounded-xl border cursor-pointer transition-colors',
                selectedConfig?.id === cfg.id && !showNewConfig
                  ? 'bg-[#FFF9E8] border-[#BFA66A]' : 'bg-[#FCFBF7]/40 border-[#BFA66A]/15 hover:border-[#BFA66A]/40',
              )}
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-bold font-mono text-[#1E1B15]">{cfg.version}</span>
                {statusChip(cfg.status)}
              </div>
              <div className="flex items-center gap-1 mt-1.5 text-[10px] text-[#8A5A00] font-mono">
                <Table2 className="w-3 h-3" /> {cfg.tables.length} tables
                <Calendar className="w-3 h-3 ml-2" /> {cfg.syncSchedule}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* detail / new-config form */}
      <div className="flex-1 overflow-y-auto custom-scrollbar bg-[#FCFBF7]/30 rounded-xl border border-[#BFA66A]/15 p-5">
        {showNewConfig ? (
          <div className="space-y-5">
            <div className="flex items-center gap-2">
              <Plus className="w-4 h-4 text-[#B88719]" />
              <h4 className="text-sm font-bold text-[#1E1B15]">New Config Version <span className="font-mono text-[#8A5A00]">{autoNextVer}</span></h4>
            </div>

            {newCfgStep === 'connection' ? (
              <>
                {/* Reused account (read-only) */}
                <div>
                  <label className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] block mb-1.5">Account (reused)</label>
                  <div className="grid grid-cols-2 gap-2 text-[11px] font-mono">
                    {[
                      ['Account', accountConn.account ?? accountConn.host],
                      ['User', accountConn.username ?? accountConn.user],
                      ['Warehouse', accountConn.warehouse],
                      ['Role', accountConn.role],
                    ].map(([k, v]) => (
                      <div key={k} className="bg-[#F1EEE6] rounded-lg px-3 py-2 border border-[#BFA66A]/20">
                        <span className="text-[9px] uppercase tracking-wider text-[#8A5A00] font-bold block">{k}</span>
                        <span className="text-[#1E1B15] truncate block">{v || '—'}</span>
                      </div>
                    ))}
                  </div>
                </div>
                {/* Editable database + schema */}
                <div className="grid grid-cols-2 gap-3">
                  <div>
                    <label className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] block mb-1.5">Database</label>
                    <input value={newCfgDatabase} onChange={e => setNewCfgDatabase(e.target.value)}
                      className="w-full text-xs border border-[#BFA66A]/30 rounded-lg px-3 py-2 bg-white font-mono" />
                  </div>
                  <div>
                    <label className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] block mb-1.5">Schema</label>
                    <input value={newCfgSchema} onChange={e => setNewCfgSchema(e.target.value)} placeholder="PUBLIC"
                      className="w-full text-xs border border-[#BFA66A]/30 rounded-lg px-3 py-2 bg-white font-mono" />
                  </div>
                </div>
                <div>
                  <label className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] block mb-1.5">Password (account not stored)</label>
                  <input type="password" value={newCfgPassword} onChange={e => setNewCfgPassword(e.target.value)}
                    className="w-full text-xs border border-[#BFA66A]/30 rounded-lg px-3 py-2 bg-white font-mono" />
                </div>
                {newCfgConnError && (
                  <div className="text-[11px] font-mono text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
                    Connection failed — {newCfgConnError}
                  </div>
                )}
                <div className="flex gap-2 pt-2">
                  <button onClick={loadTables} disabled={newCfgDiscovering || !newCfgDatabase.trim() || !newCfgPassword}
                    className="btn-gold !text-xs disabled:opacity-60 inline-flex items-center gap-1.5">
                    {newCfgDiscovering ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading…</> : <>Load Tables</>}
                  </button>
                  <button onClick={closeNewConfig} className="btn-secondary !text-xs">Cancel</button>
                </div>
              </>
            ) : (
              <>
                <div>
                  <label className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] block mb-1.5">Sync Schedule</label>
                  <select value={newCfgSchedule} onChange={e => setNewCfgSchedule(e.target.value)}
                    className="w-full text-xs border border-[#BFA66A]/30 rounded-lg px-3 py-2 bg-white font-mono">
                    {['Manual', 'Hourly', 'Daily', 'Weekly'].map(s => <option key={s} value={s}>{s}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] block mb-1.5">
                    Tables in {newCfgDatabase}.{newCfgSchema || 'PUBLIC'} ({newCfgTables.filter(t => t.selected).length} selected)
                  </label>
                  {newCfgTables.length === 0 ? (
                    <p className="text-[11px] text-slate-400 font-mono py-2">No tables found in this database/schema.</p>
                  ) : (
                    <div className="space-y-1.5 max-h-[280px] overflow-y-auto custom-scrollbar">
                      {newCfgTables.map((t, i) => (
                        <button key={t.name}
                          onClick={() => setNewCfgTables(prev => prev.map((x, j) => j === i ? { ...x, selected: !x.selected } : x))}
                          className={cn(
                            'w-full flex items-center justify-between text-left px-3 py-2 rounded-lg border text-xs transition-colors',
                            t.selected ? 'bg-[#FFF9E8] border-[#BFA66A]' : 'bg-white border-[#BFA66A]/15 hover:border-[#BFA66A]/40',
                          )}>
                          <span className="font-mono text-[#1E1B15]">{t.schema}.{t.name}</span>
                          <span className="text-[10px] text-[#8A5A00] font-mono">{t.rowCount} rows</span>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                <div className="flex gap-2 pt-2">
                  <button onClick={createConfig} disabled={savingConfig || newCfgTables.filter(t => t.selected).length === 0}
                    className="btn-gold !text-xs disabled:opacity-60">
                    {savingConfig ? 'Saving…' : 'Create Version'}
                  </button>
                  <button onClick={() => setNewCfgStep('connection')} className="btn-secondary !text-xs">Back</button>
                </div>
              </>
            )}
          </div>
        ) : !selectedConfig ? (
          <div className="flex items-center justify-center h-full text-xs text-slate-400 font-mono">
            Select a config version.
          </div>
        ) : (
          <div className="space-y-5">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Layers className="w-4 h-4 text-[#B88719]" />
                <h4 className="text-sm font-bold text-[#1E1B15] font-mono">{selectedConfig.version}</h4>
                {statusChip(selectedConfig.status)}
              </div>
              <div className="flex items-center gap-2">
                {selectedConfig.status !== 'Active' && (
                  <button onClick={() => activateConfig(selectedConfig)} className="btn-secondary !px-3 !py-1.5 !text-[11px]">
                    <CheckCircle2 className="w-3.5 h-3.5" /> Activate
                  </button>
                )}
                {pendingDeleteConfig === selectedConfig.id ? (
                  <>
                    <button onClick={() => deleteConfig(selectedConfig.id)} className="btn-danger !px-3 !py-1.5 !text-[11px]">Confirm</button>
                    <button onClick={() => setPendingDeleteConfig(null)} className="btn-secondary !px-3 !py-1.5 !text-[11px]">Cancel</button>
                  </>
                ) : (
                  <button onClick={() => setPendingDeleteConfig(selectedConfig.id)} className="btn-danger !px-3 !py-1.5 !text-[11px]">
                    <Trash2 className="w-3.5 h-3.5" /> Delete
                  </button>
                )}
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3 text-xs">
              <div className="bg-white rounded-lg border border-[#BFA66A]/15 px-3 py-2">
                <span className="text-[10px] uppercase tracking-wider text-[#8A5A00] font-bold block">Sync Schedule</span>
                <span className="font-mono text-[#1E1B15]">{selectedConfig.syncSchedule}</span>
              </div>
              <div className="bg-white rounded-lg border border-[#BFA66A]/15 px-3 py-2">
                <span className="text-[10px] uppercase tracking-wider text-[#8A5A00] font-bold block">Created</span>
                <span className="font-mono text-[#1E1B15]">{selectedConfig.createdAt || '—'}</span>
              </div>
            </div>

            <div>
              <h5 className="text-[11px] font-bold uppercase tracking-wider text-[#8A5A00] mb-2">
                Selected Tables ({selectedConfig.tables.length})
              </h5>
              {selectedConfig.tables.length === 0 ? (
                <p className="text-[11px] text-slate-400 font-mono">No tables in this config.</p>
              ) : (
                <div className="space-y-1.5">
                  {selectedConfig.tables.map(t => (
                    <div key={t.id} className="flex items-center justify-between bg-white rounded-lg border border-[#BFA66A]/15 px-3 py-2 text-xs">
                      <span className="font-mono text-[#1E1B15]">{t.schema}.{t.tableName}</span>
                      <span className="text-[10px] text-[#8A5A00] font-mono">{formatRows(t.rowCount)} rows · {t.columns} cols</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );

  /* ── Render: table + drawer ── */
  return (
    <div className="animate-in fade-in duration-300 space-y-4">
      {/* header row */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ServerCog className="w-4 h-4 text-[#B88719]" />
          <h3 className="text-sm font-bold text-[#1E1B15]">Warehouse Connections</h3>
          <span className="text-[10px] font-mono text-[#8A5A00] font-bold bg-[#FFF9E8] px-2 py-0.5 rounded border border-[#BFA66A]/30">{warehouses.length}</span>
        </div>
        {onAddWarehouse && (
          <button onClick={onAddWarehouse} className="btn-gold !text-xs">
            <Plus className="w-3.5 h-3.5" /> Add Warehouse
          </button>
        )}
      </div>

      {/* table */}
      <div className="bg-white border border-[#BFA66A]/20 rounded-2xl overflow-hidden shadow-sm">
        <table className="w-full text-left">
          <thead>
            <tr className="text-[10px] uppercase tracking-wider text-[#8A5A00] font-black border-b border-[#BFA66A]/20 bg-[#FCFBF7]">
              <th className="px-6 py-5">Warehouse</th>
              <th className="px-6 py-5">Platform</th>
              <th className="px-6 py-5">Added by</th>
              <th className="px-6 py-5">Created</th>
              <th className="px-8 py-5 text-right">Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-[#BFA66A]/10 text-[#111111]">
            {whLoading ? (
              <tr><td colSpan={5} className="px-8 py-12 text-center bg-white">
                <div className="flex items-center justify-center gap-2 text-slate-400 text-xs font-mono">
                  <Loader2 className="w-4 h-4 animate-spin" /> Loading…
                </div>
              </td></tr>
            ) : warehouses.length === 0 ? (
              <tr><td colSpan={5} className="px-8 py-12 text-center text-slate-500 text-xs font-mono bg-white">
                No warehouse connections. Use “Add Warehouse” to connect one.
              </td></tr>
            ) : warehouses.map(wh => (
              <tr
                key={wh.data_id}
                className="group transition-colors cursor-pointer bg-[#FEFDF8]/40 hover:bg-[#FEF9E4]/60"
                onClick={() => setSelected(wh)}
              >
                <td className="px-6 py-6">
                  <div className="flex items-center gap-4">
                    <div className="w-10 h-10 rounded-xl flex items-center justify-center border bg-[#FFFDF8] border-[#BFA66A]/30 text-[#B88719]">
                      <Database className="w-5 h-5" />
                    </div>
                    <div className="text-sm font-bold text-[#111111] group-hover:text-[#B88719] transition-colors">{wh.name}</div>
                  </div>
                </td>
                <td className="px-6 py-6 font-mono text-xs text-slate-600">{platformOf(wh)}</td>
                <td className="px-6 py-6 font-mono text-xs text-slate-500">{wh.added_by || '—'}</td>
                <td className="px-6 py-6 font-mono text-xs text-slate-500">{(wh.added_on ?? '').slice(0, 10) || '—'}</td>
                <td className="px-8 py-6 text-right" onClick={(e) => e.stopPropagation()}>
                  <div className="flex items-center justify-end gap-2.5">
                    <button
                      onClick={() => setSelected(wh)}
                      className="px-2.5 py-1.5 bg-[#FFF9E8] hover:bg-[#B88719] hover:text-white border border-[#BFA66A]/45 rounded-lg text-[#8A5A00] font-bold text-[10px] uppercase tracking-wider transition-all inline-flex items-center gap-1 cursor-pointer"
                      title="Manage configs"
                    >
                      <Eye className="w-3.5 h-3.5" /> Manage
                    </button>
                    {pendingDeleteId === wh.data_id ? (
                      <span className="inline-flex items-center gap-1.5">
                        <button onClick={(e) => deleteWarehouse(e, wh)} className="px-2.5 py-1.5 bg-red-500 hover:bg-red-600 text-white rounded-lg text-[10px] font-bold uppercase tracking-wider transition-all cursor-pointer">Confirm</button>
                        <button onClick={(e) => { e.stopPropagation(); setPendingDeleteId(null); }} className="p-1.5 bg-white border border-slate-200 hover:bg-slate-50 text-slate-500 rounded-lg transition-colors cursor-pointer"><X className="w-3 h-3" /></button>
                      </span>
                    ) : (
                      <button
                        onClick={(e) => { e.stopPropagation(); setPendingDeleteId(wh.data_id); }}
                        className="p-1.5 opacity-0 group-hover:opacity-100 bg-red-50 hover:bg-red-100 border border-red-200 text-red-500 rounded-lg transition-all cursor-pointer"
                        title="Delete warehouse"
                      ><Trash2 className="w-3.5 h-3.5" /></button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {(totalPages > 1 || hasNext) && (
        <div className="flex justify-center pt-2">
          <Pagination page={page} totalPages={totalPages} hasNext={hasNext} canJump={canJump} onJump={goTo} />
        </div>
      )}

      {/* ── Config window (drawer) ── */}
      <DetailDrawer
        isOpen={!!selected}
        onClose={() => { setSelected(null); setPendingDeleteConfig(null); }}
        title={selected?.name || 'Warehouse'}
        subtitle={connection
          ? `${connection.platform}${connection.host ? ` · ${connection.host}` : ''}${connection.database ? ` · ${connection.database}` : ''}`
          : 'Warehouse configuration'}
        icon={ServerCog}
        size="wide"
        persistKey="kb-warehouse-configs"
        fixedHeight
      >
        {configWindow}
      </DetailDrawer>
    </div>
  );
};
