import React, { useState } from 'react';
import {
  ShieldCheck,
  ShieldAlert,
  Settings2,
  TrendingUp,
  Globe,
  ScanLine,
  Loader2,
  AlertCircle,
} from 'lucide-react';
import { motion } from 'motion/react';
import { useCompliance, PII_TYPES, type ComplianceSettings, type PiiScanResult, type ReprocessResult } from '../../lib/useCompliance';

// Residency options: the persisted value comes from useCompliance().settings.residency_region;
// this list only seeds the picker and is extended with the stored value.
const REGION_OPTIONS = ['Asia-SE1', 'EU-West-3'];

// Which governance policy card is wired to which persisted setting toggle.
const POLICY_TOGGLE: Record<string, keyof ComplianceSettings> = {
  'pol-pii': 'pii_masking',
  'pol-zt': 'zero_trust',
  'pol-ret': 'retention_lock',
  'pol-aud': 'audit_residency',
};

const Toggle = ({ on, disabled, onClick }: { on: boolean; disabled?: boolean; onClick: () => void }) => (
  <button
    onClick={onClick}
    disabled={disabled}
    className={`relative w-12 h-6 rounded-full transition-all disabled:opacity-40 ${on ? 'bg-brand-500' : 'bg-[#F4E8C3]'}`}
    aria-pressed={on}
  >
    <span className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform ${on ? 'translate-x-6' : ''}`} />
  </button>
);

export const SecurityComplianceSection = () => {
  const { settings, score, loading, saving, error, updateSettings, scanPii, reprocessPii } = useCompliance();

  const [scanText, setScanText] = useState('');
  const [scanResult, setScanResult] = useState<PiiScanResult | null>(null);
  const [scanning, setScanning] = useState(false);
  const [scanErr, setScanErr] = useState<string | null>(null);

  const [reprocessing, setReprocessing] = useState(false);
  const [reprocessResult, setReprocessResult] = useState<ReprocessResult | null>(null);

  const runReprocess = async () => {
    setReprocessing(true);
    setReprocessResult(null);
    try {
      setReprocessResult(await reprocessPii());
    } catch {
      setReprocessResult(null);
    } finally {
      setReprocessing(false);
    }
  };

  const runScan = async () => {
    if (!scanText.trim()) return;
    setScanning(true);
    setScanErr(null);
    try {
      setScanResult(await scanPii(scanText, settings?.pii_types));
    } catch (e) {
      setScanErr(e instanceof Error ? e.message : 'Scan failed');
    } finally {
      setScanning(false);
    }
  };

  const toggle = (key: keyof ComplianceSettings) => {
    if (!settings) return;
    updateSettings({ [key]: !settings[key] } as Partial<ComplianceSettings>).catch(() => {});
  };

  const togglePiiType = (t: string) => {
    if (!settings) return;
    const has = settings.pii_types.includes(t);
    const next = has ? settings.pii_types.filter(x => x !== t) : [...settings.pii_types, t];
    updateSettings({ pii_types: next }).catch(() => {});
  };

  return (
    <div className="space-y-12">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold text-[#111111] tracking-tight mb-2">Security &amp; Compliance Center</h1>
          <p className="text-[#5F5F5F] font-medium leading-relaxed max-w-xl">
            Zero-trust access, data residency, retention, and PII masking. These controls are enforced —
            enabling PII masking redacts personal data at ingestion before it is embedded or stored.
          </p>
        </div>
        <div className="flex items-center gap-3">
          {saving && <Loader2 className="w-4 h-4 text-[#B88719] animate-spin" />}
          {settings?.updated_at && (
            <span className="text-[10px] font-black text-[#5F5F5F] uppercase tracking-widest">
              Saved {new Date(settings.updated_at).toLocaleString()}
            </span>
          )}
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-3 p-4 bg-red-50 border border-red-200 rounded-2xl text-red-500 text-sm">
          <AlertCircle className="w-4 h-4" /> {error}
        </div>
      )}

      {loading || !settings || !score ? (
        <div className="flex items-center gap-3 text-[#5F5F5F] py-20 justify-center">
          <Loader2 className="w-5 h-5 animate-spin" /> Loading compliance posture…
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
          <div className="lg:col-span-2 space-y-8">
            {/* Governance policies — each wired to a real, persisted toggle */}
            <div className="p-8 bg-white border border-[#BFA66A] rounded-[40px]">
              <div className="flex items-center justify-between mb-8">
                <h3 className="text-xl font-bold text-[#111111] tracking-tight">Enterprise Governance Policies</h3>
                <Settings2 className="w-4 h-4 text-[#5F5F5F]" />
              </div>

              <div className="space-y-4">
                {score.policies.map((policy) => {
                  const key = POLICY_TOGGLE[policy.id];
                  const on = key ? Boolean(settings[key]) : false;
                  return (
                    <div key={policy.id} className="flex items-center justify-between p-6 bg-[#FDFAF2] border border-[#BFA66A] rounded-3xl">
                      <div className="flex items-center gap-6">
                        <div className={`p-4 rounded-2xl bg-[#F4F4F4] border border-[#D6C79F] ${on ? 'text-emerald-600' : 'text-[#5F5F5F]'}`}>
                          {on ? <ShieldCheck className="w-5 h-5" /> : <ShieldAlert className="w-5 h-5" />}
                        </div>
                        <div>
                          <div className="text-[#111111] font-bold text-sm tracking-tight mb-1">{policy.name}</div>
                          <div className="flex items-center gap-3">
                            <span className="text-[9px] font-black text-[#5F5F5F] uppercase tracking-widest">{policy.module}</span>
                            <div className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${
                              policy.severity === 'CRITICAL' ? 'bg-red-50 border-red-200 text-red-600' :
                              policy.severity === 'HIGH' ? 'bg-amber-50 border-amber-200 text-amber-600' :
                              'bg-blue-50 border-blue-200 text-blue-600'
                            }`}>{policy.severity}</div>
                          </div>
                        </div>
                      </div>
                      <div className="flex items-center gap-6">
                        <div className={`text-xs font-bold ${on ? 'text-emerald-600' : 'text-[#5F5F5F]'}`}>{policy.status}</div>
                        {key && <Toggle on={on} disabled={saving} onClick={() => toggle(key)} />}
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* PII categories to redact — only meaningful when masking is on */}
              <div className={`mt-6 p-6 bg-white border border-[#BFA66A] rounded-3xl ${settings.pii_masking ? '' : 'opacity-50'}`}>
                <div className="text-[10px] font-black text-[#5F5F5F] uppercase tracking-widest mb-4">PII categories redacted at ingestion</div>
                <div className="flex flex-wrap gap-2">
                  {PII_TYPES.map(t => {
                    const active = settings.pii_types.includes(t);
                    return (
                      <button
                        key={t}
                        disabled={!settings.pii_masking || saving}
                        onClick={() => togglePiiType(t)}
                        className={`px-3 py-1.5 rounded-full text-[10px] font-black uppercase tracking-widest border transition-all disabled:cursor-not-allowed ${
                          active ? 'bg-[#F4E8C3] border-[#BFA66A] text-[#B88719]' : 'bg-[#FDFAF2] border-[#D6C79F] text-[#5F5F5F]'
                        }`}
                      >{t}</button>
                    );
                  })}
                </div>
              </div>

              {/* Apply the current masking policy to already-ingested documents */}
              <div className="mt-4 flex items-center justify-between gap-4 p-6 bg-white border border-[#BFA66A] rounded-3xl">
                <div>
                  <div className="text-[#111111] font-bold text-sm">Reprocess existing documents</div>
                  <div className="text-[11px] text-[#5F5F5F] mt-1">
                    Re-runs the Gold pipeline on already-ingested content so the current masking policy is applied retroactively.
                  </div>
                  {reprocessResult && (
                    <div className="text-[11px] mt-2 text-emerald-600">
                      Re-queued {reprocessResult.enqueued}/{reprocessResult.total} document(s)
                      {reprocessResult.failed > 0 && <span className="text-amber-600"> · {reprocessResult.failed} failed</span>}
                    </div>
                  )}
                </div>
                <button
                  onClick={runReprocess}
                  disabled={reprocessing}
                  className="shrink-0 px-5 py-3 bg-[#FDFAF2] hover:bg-[#F4E8C3] disabled:opacity-40 border border-[#D6C79F] text-[#111111] rounded-2xl text-[11px] font-black uppercase tracking-widest transition-all flex items-center gap-2"
                >
                  {reprocessing ? <Loader2 className="w-4 h-4 animate-spin" /> : <ShieldCheck className="w-4 h-4" />}
                  Reprocess
                </button>
              </div>
            </div>

            {/* Data residency + retention — editable and persisted */}
            <div className="p-8 bg-white border border-[#BFA66A] rounded-[40px] relative overflow-hidden">
              <div className="absolute top-0 right-0 p-8">
                <Globe className="w-24 h-24 text-[#D9B86C] rotate-12" />
              </div>
              <h3 className="text-xl font-bold text-[#111111] mb-8">Data Residency &amp; Retention</h3>
              <div className="grid grid-cols-1 md:grid-cols-3 gap-6 relative">
                <div>
                  <label className="text-[9px] font-black text-[#5F5F5F] uppercase tracking-widest mb-2 block">Residency Region</label>
                  <select
                    value={settings.residency_region}
                    onChange={e => updateSettings({ residency_region: e.target.value }).catch(() => {})}
                    className="w-full bg-[#FDFAF2] border border-[#D6C79F] rounded-2xl px-4 py-3 text-[#111111] text-sm"
                  >
                    {[...new Set([settings.residency_region, ...REGION_OPTIONS].filter(Boolean))].map(r => <option key={r} value={r}>{r}</option>)}
                  </select>
                </div>
                <div>
                  <label className="text-[9px] font-black text-[#5F5F5F] uppercase tracking-widest mb-2 block">Standards</label>
                  <input
                    defaultValue={settings.residency_standards}
                    onBlur={e => { if (e.target.value !== settings.residency_standards) updateSettings({ residency_standards: e.target.value }).catch(() => {}); }}
                    className="w-full bg-[#FDFAF2] border border-[#D6C79F] rounded-2xl px-4 py-3 text-[#111111] text-sm"
                    placeholder="GDPR, SOC2…"
                  />
                </div>
                <div>
                  <label className="text-[9px] font-black text-[#5F5F5F] uppercase tracking-widest mb-2 block">Retention (days)</label>
                  <input
                    type="number"
                    min={0}
                    defaultValue={settings.retention_days}
                    onBlur={e => { const v = Number(e.target.value); if (v >= 0 && v !== settings.retention_days) updateSettings({ retention_days: v }).catch(() => {}); }}
                    className="w-full bg-[#FDFAF2] border border-[#D6C79F] rounded-2xl px-4 py-3 text-[#111111] text-sm"
                  />
                </div>
              </div>
            </div>
          </div>

          <div className="space-y-8">
            {/* Compliance score — computed from the real controls above */}
            <div className="p-8 bg-white border border-[#BFA66A] rounded-[40px]">
              <div className="flex items-center justify-between mb-8">
                <h3 className="text-lg font-bold text-[#111111]">Compliance Score</h3>
                <TrendingUp className="w-5 h-5 text-emerald-600" />
              </div>
              <div className="flex items-end gap-3 mb-8">
                <motion.span
                  key={score.score}
                  initial={{ opacity: 0.3 }}
                  animate={{ opacity: 1 }}
                  className="text-5xl font-bold text-[#111111] leading-none"
                >{score.score}</motion.span>
                <span className="text-[#5F5F5F] font-bold mb-1">/ 100</span>
              </div>
              <div className="space-y-4">
                {score.breakdown.map(m => (
                  <div key={m.label} className="space-y-1.5">
                    <div className="flex justify-between text-[10px] font-black text-[#5F5F5F] uppercase tracking-widest">
                      <span>{m.label}</span><span>{m.val}%</span>
                    </div>
                    <div className="h-1 bg-[#FDFAF2] rounded-full overflow-hidden">
                      <div className="h-full bg-brand-500 rounded-full transition-all" style={{ width: `${Math.min(m.val, 100)}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            </div>

            {/* PII scan tool — live preview of what masking would redact */}
            <div className="p-8 bg-[#F4F4F4] border border-[#BFA66A] rounded-[40px]">
              <div className="flex items-center gap-3 mb-6">
                <ScanLine className="w-5 h-5 text-[#B88719]" />
                <h3 className="text-lg font-bold text-[#111111]">PII Scanner</h3>
              </div>
              <textarea
                value={scanText}
                onChange={e => setScanText(e.target.value)}
                rows={4}
                placeholder="Paste text to detect PII (email, phone, CCCD/CMND, card…)"
                className="w-full bg-[#FDFAF2] border border-[#D6C79F] rounded-2xl px-4 py-3 text-[#111111] text-sm resize-none"
              />
              <button
                onClick={runScan}
                disabled={scanning || !scanText.trim()}
                className="w-full mt-4 py-3 bg-brand-500 hover:bg-brand-400 disabled:opacity-40 text-white rounded-[20px] font-bold transition-all flex items-center justify-center gap-2"
              >
                {scanning ? <Loader2 className="w-4 h-4 animate-spin" /> : <ScanLine className="w-4 h-4" />}
                Scan for PII
              </button>
              {scanErr && <div className="mt-3 text-xs text-red-600">{scanErr}</div>}
              {scanResult && (
                <div className="mt-5 space-y-3">
                  <div className="flex items-center justify-between text-[10px] font-black uppercase tracking-widest">
                    <span className="text-[#5F5F5F]">Detected</span>
                    <span className={scanResult.total ? 'text-amber-600' : 'text-emerald-600'}>{scanResult.total} item(s)</span>
                  </div>
                  {Object.entries(scanResult.counts).length > 0 && (
                    <div className="flex flex-wrap gap-2">
                      {Object.entries(scanResult.counts).map(([t, n]) => (
                        <span key={t} className="px-2 py-0.5 rounded-full bg-amber-50 border border-amber-200 text-amber-600 text-[10px] font-bold">{t}: {n}</span>
                      ))}
                    </div>
                  )}
                  <div className="p-4 bg-[#FFF9E8] border border-[#BFA66A] rounded-2xl text-[11px] text-[#3F3F3F] font-mono whitespace-pre-wrap break-words">
                    {scanResult.preview || '(empty)'}
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
