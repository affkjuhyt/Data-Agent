import React, { useState } from 'react';
import {
  Database, Plus, Check, X, RefreshCw, ChevronLeft, ChevronRight,
  Table2, Zap, CheckCircle2, Lock,
} from 'lucide-react';
import { cn } from '../../../lib/utils';
import { useAppState } from '../../../AppStateContext';
import { mockMutate } from '../../../lib/mockApi';
import {
  WarehouseType, WizardStep, TableRow, FieldDef,
  SNOWFLAKE_FIELDS, DATABRICKS_FIELDS, STEP_LABELS,
} from './warehouse.data';

interface WarehouseWizardProps {
  onCancel: () => void;
  onComplete: () => void;
}

export const WarehouseWizard = ({ onCancel, onComplete }: WarehouseWizardProps) => {
  const { addDocument } = useAppState();

  const [step, setStep] = useState<WizardStep>(1);
  const [warehouseType, setWarehouseType] = useState<WarehouseType | null>(null);
  const [fields, setFields] = useState<Record<string, string>>({});
  const [testingConnection, setTestingConnection] = useState(false);
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [discoveringTables, setDiscoveringTables] = useState(false);
  const [tables, setTables] = useState<TableRow[]>([]);
  const [customTableInput, setCustomTableInput] = useState('');
  const [saving, setSaving] = useState(false);

  const fieldDefs = warehouseType === 'snowflake' ? SNOWFLAKE_FIELDS : DATABRICKS_FIELDS;

  const handleNext2to3 = async () => {
    if (!warehouseType) return;
    setTestingConnection(true);
    setConnectionError(null);

    try {
      const payload = warehouseType === 'snowflake'
        ? {
            account_identifier: fields.account,
            user: fields.username,
            password: fields.password,
            warehouse: fields.warehouse,
            database: fields.database,
            schema: fields.schema || 'PUBLIC',
            role: fields.role || null,
          }
        : {
            account_identifier: fields.host,
            user: fields.username || 'token',
            password: fields.accessToken,
            warehouse: '',
            database: fields.catalog,
            schema: fields.schema || 'default',
            role: null,
          };

      const result = await mockMutate<{ success: boolean; message: string; tables: Array<{ table_name: string; row_count: number }> }>(
        'POST',
        '/api/knowledge/warehouses/connect',
        payload,
      );

      setTestingConnection(false);
      setStep(3);
      setDiscoveringTables(true);

      const discoveredTables: TableRow[] = result.tables.map(t => ({
        name: t.table_name,
        schema: fields.schema || 'PUBLIC',
        rowCount: t.row_count != null ? t.row_count.toLocaleString() : '—',
        selected: false,
        description: '',
      }));

      setTimeout(() => {
        setDiscoveringTables(false);
        setTables(discoveredTables);
      }, 400);
    } catch (err: unknown) {
      setTestingConnection(false);
      const msg = err instanceof Error ? err.message : 'Connection failed';
      setConnectionError(msg);
      // Stay on step 2 — user must fix credentials before proceeding
    }
  };

  const toggleTable = (name: string) =>
    setTables(prev => prev.map(t => t.name === name ? { ...t, selected: !t.selected } : t));

  const setDescription = (name: string, desc: string) =>
    setTables(prev => prev.map(t => t.name === name ? { ...t, description: desc } : t));

  const addCustomTable = () => {
    const raw = customTableInput.trim().toUpperCase();
    if (!raw || tables.some(t => t.name === raw)) { setCustomTableInput(''); return; }
    setTables(prev => [...prev, { name: raw, schema: 'CUSTOM', rowCount: '—', selected: true, description: '' }]);
    setCustomTableInput('');
  };

  const selectedTables = tables.filter(t => t.selected);

  const handleSave = async () => {
    if (!warehouseType) return;
    setSaving(true);
    const { name, ...connectionFields } = fields;
    const displayName = name || `${warehouseType === 'snowflake' ? 'Snowflake' : 'Databricks'} Connection`;
    const now = new Date().toISOString();

    let docId = `wh-${Date.now()}`;

    try {
      const result = await mockMutate<{ warehouse_id: string; connection_name: string; warehouse_type: string }>(
        'POST',
        '/api/knowledge/warehouses/select-tables',
        {
          connection_name: displayName,
          warehouse_type: warehouseType === 'snowflake' ? 'Snowflake' : 'Databricks',
          account_identifier: connectionFields.account || connectionFields.host,
          user: connectionFields.username || 'token',
          password: connectionFields.password || connectionFields.accessToken,
          warehouse: connectionFields.warehouse || '',
          database: connectionFields.database || connectionFields.catalog,
          schema: connectionFields.schema || 'PUBLIC',
          selected_table_ids: selectedTables.map(t => t.name),
          role: connectionFields.role || null,
        },
      );
      docId = result.warehouse_id;
    } catch {
      // Backend unavailable — still add the document client-side with a temporary ID
    }

    addDocument({
      data_id: docId,
      name: displayName,
      source_type: 'warehouse',
      current_tier: 'gold',
      status: 'PUBLISHED',
      added_on: now,
      added_by: 'platform-admin',
      metadata: {
        doc_type: `Warehouse/${warehouseType}`,
        language: null,
        warehouse_type: warehouseType,
      },
    });

    setSaving(false);
    onComplete();
  };

  const canAdvanceStep2 = warehouseType
    ? fieldDefs.filter(f => f.required && f.key !== 'name').every(f => !!fields[f.key]?.trim()) && !!fields.name?.trim()
    : false;

  return (
    <div className="bg-white border border-[#BFA66A]/20 rounded-3xl overflow-hidden shadow-sm">

      {/* Step indicator */}
      <div className="bg-[#FFFDF8] border-b border-[#BFA66A]/20 px-8 py-5 flex items-center gap-2">
        {([1, 2, 3, 4] as WizardStep[]).map((s, idx) => (
          <React.Fragment key={s}>
            <div className={cn(
              'flex items-center gap-2 text-[10px] font-black uppercase tracking-widest',
              s < step ? 'text-green-700' : s === step ? 'text-[#8A5A00]' : 'text-slate-400'
            )}>
              <span className={cn(
                'w-5 h-5 rounded-full flex items-center justify-center text-[9px] font-black border',
                s < step ? 'bg-green-600 border-green-600 text-white' :
                s === step ? 'bg-[#B88719] border-[#B88719] text-white' :
                'bg-white border-slate-200 text-slate-400'
              )}>
                {s < step ? <Check className="w-2.5 h-2.5" /> : s}
              </span>
              <span className="hidden sm:inline">{STEP_LABELS[s]}</span>
            </div>
            {idx < 3 && <ChevronRight className="w-3 h-3 text-slate-300 shrink-0" />}
          </React.Fragment>
        ))}
      </div>

      {/* Step content */}
      <div className="p-8 min-h-[400px] flex flex-col">

        {/* Step 1 — Choose type */}
        {step === 1 && (
          <div className="flex-1 flex flex-col gap-6">
            <div>
              <h3 className="text-base font-black text-[#111111]">Choose Warehouse Type</h3>
              <p className="text-xs text-slate-500 mt-1">Select the data warehouse you want to connect to the knowledge pipeline.</p>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-5 max-w-xl">
              <WarehouseTypeCard type="snowflake" selected={warehouseType === 'snowflake'}
                onSelect={() => { setWarehouseType('snowflake'); setFields({}); }} />
              <WarehouseTypeCard type="databricks" selected={warehouseType === 'databricks'}
                onSelect={() => { setWarehouseType('databricks'); setFields({}); }} />
            </div>
          </div>
        )}

        {/* Step 2 — Credentials */}
        {step === 2 && warehouseType && (
          <div className="flex-1 flex flex-col gap-6">
            <div>
              <h3 className="text-base font-black text-[#111111]">
                {warehouseType === 'snowflake' ? 'Snowflake' : 'Databricks'} Connection Details
              </h3>
              <p className="text-xs text-slate-500 mt-1">These credentials are stored encrypted and used only for data sync.</p>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 max-w-2xl">
              {fieldDefs.map(f => (
                <div key={f.key} className={f.key === 'name' ? 'sm:col-span-2' : ''}>
                  <label className="text-[10px] font-black text-[#5A4209]/70 uppercase tracking-widest block mb-1.5">
                    {f.label} {f.required && <span className="text-red-500">*</span>}
                    {f.secret && <Lock className="w-2.5 h-2.5 inline ml-1 text-slate-400" />}
                  </label>
                  <input
                    type={f.secret ? 'password' : 'text'}
                    placeholder={f.placeholder}
                    value={fields[f.key] ?? ''}
                    onChange={e => setFields(prev => ({ ...prev, [f.key]: e.target.value }))}
                    className="w-full bg-white border border-[#BFA66A]/35 text-[#111111] placeholder:text-slate-300 rounded-xl px-3.5 py-2.5 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-[#B88719] focus:border-[#B88719] transition-all"
                  />
                </div>
              ))}
            </div>
            {testingConnection && (
              <div className="flex items-center gap-2.5 text-[11px] font-mono text-[#8A5A00] font-bold">
                <RefreshCw className="w-3.5 h-3.5 animate-spin text-[#B88719]" />
                Testing connection to {warehouseType === 'snowflake' ? fields.account : fields.host}...
              </div>
            )}
            {connectionError && (
              <div className="flex items-start gap-2 text-[11px] font-mono text-red-600 bg-red-50 border border-red-200 rounded-xl px-3.5 py-2.5">
                <X className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                <span>Connection failed — {connectionError}. Please check your credentials and try again.</span>
              </div>
            )}
          </div>
        )}

        {/* Step 3 — Table selection */}
        {step === 3 && (
          <div className="flex-1 flex flex-col gap-5">
            <div>
              <h3 className="text-base font-black text-[#111111]">Select Tables to Sync</h3>
              <p className="text-xs text-slate-500 mt-1">Choose the tables to include in the knowledge pipeline. Add a description so agents understand the data.</p>
            </div>

            {discoveringTables ? (
              <div className="flex-1 flex flex-col items-center justify-center gap-4 text-[#8A5A00]">
                <RefreshCw className="w-6 h-6 animate-spin text-[#B88719]" />
                <p className="text-xs font-mono font-bold">Discovering tables from {warehouseType === 'snowflake' ? fields.account : fields.host}...</p>
              </div>
            ) : tables.length === 0 ? (
              <div className="flex-1 flex flex-col items-center justify-center gap-3 text-slate-400 py-12">
                <Table2 className="w-8 h-8 text-slate-300" />
                <p className="text-xs font-mono font-bold">No tables discovered</p>
                <p className="text-[10px] font-mono text-center max-w-xs">The connection succeeded but no tables were found. Add tables manually using the field below.</p>
              </div>
            ) : (
              <div className="flex-1 flex flex-col gap-3 overflow-y-auto max-h-[380px] custom-scrollbar pr-1">
                {tables.map(t => (
                  <div key={t.name} className={cn(
                    'border rounded-2xl p-4 transition-all',
                    t.selected ? 'border-[#B88719] bg-[#FFF9E8]' : 'border-[#BFA66A]/25 bg-white hover:border-[#BFA66A]/50'
                  )}>
                    <div className="flex items-start gap-3">
                      <button onClick={() => toggleTable(t.name)} className={cn(
                        'w-5 h-5 rounded border-2 flex items-center justify-center shrink-0 mt-0.5 transition-all cursor-pointer',
                        t.selected ? 'bg-[#B88719] border-[#B88719]' : 'border-[#BFA66A]/40 bg-white hover:border-[#B88719]'
                      )}>
                        {t.selected && <Check className="w-3 h-3 text-white" />}
                      </button>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-xs font-mono font-black text-[#111111]">{t.name}</span>
                          <span className="text-[9px] font-mono text-slate-400 bg-slate-100 px-1.5 py-0.5 rounded">{t.schema}</span>
                          <span className="text-[9px] font-mono text-slate-400">{t.rowCount} rows</span>
                        </div>
                        {t.selected && (
                          <input
                            type="text"
                            placeholder="Describe this table for the AI agents..."
                            value={t.description}
                            onChange={e => setDescription(t.name, e.target.value)}
                            className="mt-2 w-full bg-white border border-[#BFA66A]/30 rounded-xl px-3 py-2 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-[#B88719] focus:border-[#B88719] placeholder:text-slate-300 transition-all"
                          />
                        )}
                      </div>
                    </div>
                  </div>
                ))}

                {/* Add custom table */}
                <div className="flex gap-2 mt-1">
                  <input
                    type="text"
                    placeholder="Add custom table name..."
                    value={customTableInput}
                    onChange={e => setCustomTableInput(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && addCustomTable()}
                    className="flex-1 bg-white border border-[#BFA66A]/25 text-[#111111] placeholder:text-slate-300 rounded-xl px-3.5 py-2 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-[#B88719] focus:border-[#B88719] transition-all"
                  />
                  <button onClick={addCustomTable}
                    className="px-3 py-2 bg-[#FFF9E8] border border-[#BFA66A]/30 text-[#8A5A00] rounded-xl text-xs font-bold hover:bg-[#B88719] hover:text-white transition-all cursor-pointer">
                    <Plus className="w-4 h-4" />
                  </button>
                </div>

                {selectedTables.length > 0 && (
                  <p className="text-[10px] font-mono text-[#8A5A00] font-bold">
                    {selectedTables.length} table{selectedTables.length !== 1 ? 's' : ''} selected
                  </p>
                )}
              </div>
            )}
          </div>
        )}

        {/* Step 4 — Review */}
        {step === 4 && warehouseType && (
          <div className="flex-1 flex flex-col gap-6">
            <div>
              <h3 className="text-base font-black text-[#111111]">Review Connection</h3>
              <p className="text-xs text-slate-500 mt-1">Confirm the configuration before creating the warehouse connection.</p>
            </div>

            <div className="max-w-xl space-y-4">
              {/* Connection summary */}
              <div className="bg-[#FFFDF8] border border-[#BFA66A]/25 rounded-2xl p-5">
                <div className="flex items-center gap-3 mb-4">
                  <div className={cn(
                    'w-10 h-10 rounded-xl flex items-center justify-center border',
                    warehouseType === 'snowflake' ? 'bg-blue-50 border-blue-200' : 'bg-red-50 border-red-200'
                  )}>
                    {warehouseType === 'snowflake'
                      ? <Database className="w-5 h-5 text-blue-500" />
                      : <Zap className="w-5 h-5 text-red-500" />}
                  </div>
                  <div>
                    <p className="text-sm font-black text-[#111111]">{fields.name || 'Unnamed Connection'}</p>
                    <p className="text-[10px] font-mono text-slate-500 uppercase tracking-wide">{warehouseType}</p>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-3 text-[11px] font-mono">
                  {Object.entries(fields)
                    .filter(([k]) => k !== 'name' && k !== 'password' && k !== 'accessToken')
                    .map(([k, v]) => (
                      <div key={k}>
                        <span className="text-[9px] font-black text-slate-400 uppercase tracking-wider block">{k}</span>
                        <span className="text-[#111111] font-bold truncate block">{v || '—'}</span>
                      </div>
                    ))}
                </div>
              </div>

              {/* Selected tables summary */}
              <div className="bg-[#FFFDF8] border border-[#BFA66A]/25 rounded-2xl p-5">
                <h4 className="text-[10px] font-black text-[#5A4209] uppercase tracking-widest mb-3 flex items-center gap-2">
                  <Table2 className="w-3.5 h-3.5 text-[#B88719]" />
                  {selectedTables.length} Tables Selected
                </h4>
                {selectedTables.length === 0 ? (
                  <p className="text-xs text-slate-400 font-mono">No tables selected — you can add them later via the Configs tab.</p>
                ) : (
                  <div className="space-y-2">
                    {selectedTables.map(t => (
                      <div key={t.name} className="flex items-start gap-2 text-[11px] font-mono">
                        <CheckCircle2 className="w-3.5 h-3.5 text-green-600 mt-0.5 shrink-0" />
                        <div>
                          <span className="font-bold text-[#111111]">{t.name}</span>
                          <span className="text-slate-400 ml-1.5">{t.schema}</span>
                          {t.description && <p className="text-[10px] text-slate-500 mt-0.5">{t.description}</p>}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Footer navigation */}
      <div className="bg-[#FFFDF8]/60 border-t border-[#BFA66A]/15 px-8 py-5 flex items-center justify-between">
        <button
          onClick={() => step === 1 ? onCancel() : setStep(prev => (prev - 1) as WizardStep)}
          className="flex items-center gap-1.5 px-4 py-2 bg-white border border-[#BFA66A]/30 text-[#8A5A00] rounded-xl text-[10px] font-black uppercase tracking-wider hover:bg-[#FFF9E8] transition-all cursor-pointer"
        >
          {step === 1 ? <><X className="w-3.5 h-3.5" />Cancel</> : <><ChevronLeft className="w-3.5 h-3.5" />Back</>}
        </button>

        {step < 4 && (
          <button
            onClick={() => step === 2 ? handleNext2to3() : setStep(prev => (prev + 1) as WizardStep)}
            disabled={
              (step === 1 && !warehouseType) ||
              (step === 2 && (!canAdvanceStep2 || testingConnection)) ||
              (step === 3 && (discoveringTables || selectedTables.length === 0))
            }
            className={cn(
              'flex items-center gap-1.5 px-5 py-2 rounded-xl text-[10px] font-black uppercase tracking-wider transition-all cursor-pointer',
              ((step === 1 && !warehouseType) || (step === 2 && (!canAdvanceStep2 || testingConnection)) || (step === 3 && (discoveringTables || selectedTables.length === 0)))
                ? 'bg-slate-100 text-slate-400 cursor-not-allowed'
                : 'bg-[#B88719] text-white hover:bg-[#8A5A00] shadow-md'
            )}
          >
            {step === 2 && testingConnection
              ? <><RefreshCw className="w-3.5 h-3.5 animate-spin" />Testing...</>
              : <>{step === 3 ? 'Review' : 'Next'}<ChevronRight className="w-3.5 h-3.5" /></>}
          </button>
        )}

        {step === 4 && (
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-1.5 px-6 py-2 bg-[#B88719] text-white rounded-xl text-[10px] font-black uppercase tracking-wider hover:bg-[#8A5A00] shadow-md transition-all cursor-pointer disabled:opacity-50"
          >
            {saving
              ? <><RefreshCw className="w-3.5 h-3.5 animate-spin" />Creating...</>
              : <><CheckCircle2 className="w-3.5 h-3.5" />Create Connection</>}
          </button>
        )}
      </div>
    </div>
  );
};

// ── Sub-components ─────────────────────────────────────────────────

function WarehouseTypeCard({ type, selected, onSelect }: {
  type: WarehouseType;
  selected: boolean;
  onSelect: () => void;
}) {
  const isSnow = type === 'snowflake';
  return (
    <button
      onClick={onSelect}
      className={cn(
        'relative p-6 rounded-2xl border-2 text-left transition-all cursor-pointer group',
        selected
          ? isSnow ? 'border-blue-400 bg-blue-50 shadow-md' : 'border-red-400 bg-red-50 shadow-md'
          : 'border-[#BFA66A]/25 bg-white hover:border-[#BFA66A]/50 hover:bg-[#FFFDF8]'
      )}
    >
      {selected && (
        <span className="absolute top-3 right-3 w-5 h-5 rounded-full bg-green-500 flex items-center justify-center">
          <Check className="w-3 h-3 text-white" />
        </span>
      )}
      <div className={cn(
        'w-12 h-12 rounded-xl flex items-center justify-center mb-4 border',
        isSnow ? 'bg-blue-50 border-blue-200' : 'bg-red-50 border-red-200'
      )}>
        {isSnow ? <Database className="w-6 h-6 text-blue-500" /> : <Zap className="w-6 h-6 text-red-500" />}
      </div>
      <p className="text-sm font-black text-[#111111] capitalize">{type}</p>
      <p className="text-[11px] text-slate-500 mt-1 font-mono">
        {isSnow ? 'Cloud data warehouse with SQL interface' : 'Lakehouse platform with Delta tables'}
      </p>
    </button>
  );
}
