import React, { useState } from 'react';
import {
  Key,
  ShieldCheck,
  Lock,
  Eye,
  EyeOff,
  AlertTriangle,
  History,
  Plus,
  Server,
  KeyRound,
  ShieldAlert,
  ArrowRight,
  Loader2,
  Trash2,
  RefreshCw,
  AlertCircle,
  Copy,
  Check,
} from 'lucide-react';
import { OperationalHeader } from '../shared/OperationalHeader';
import { StatusBadge } from '../shared/StatusBadge';
import { DetailDrawer } from '../shared/DetailDrawer';
import { cn } from '../../lib/utils';
import { useSecrets, CreateSecretPayload, Secret, TRANSIT_TYPES } from '../../lib/useSecrets';
import keycloak from '../../lib/keycloak';

const KEY_TYPES = ['BEARER_TOKEN', 'SIGNING_KEY', 'HMAC_KEY', 'ENCRYPTION_KEY', 'MCP_TOKEN', 'KB_API_KEY'];
const ALGORITHMS = ['AES-256', 'RSA-4096', 'HMAC-SHA256', 'BEARER', 'EC-P256', 'ChaCha20'];

function ttlLabel(rotation_due_at: string | null): string {
  if (!rotation_due_at) return '—';
  const diff = new Date(rotation_due_at).getTime() - Date.now();
  if (diff < 0) return 'Expired';
  const days = Math.floor(diff / 86400000);
  if (days === 0) return '< 1 day';
  return `${days} day${days !== 1 ? 's' : ''}`;
}

function ttlColor(rotation_due_at: string | null): string {
  if (!rotation_due_at) return 'text-slate-500';
  const diff = new Date(rotation_due_at).getTime() - Date.now();
  if (diff < 0) return 'text-red-500';
  if (diff < 7 * 86400000) return 'text-amber-500';
  return 'text-emerald-500';
}

export const SecretsSection = () => {
  const {
    secrets, governance, auditLog, piiLog, hsmStatus, loading, error,
    createSecret, deleteSecret, rotateSecret, revealSecret,
    updateGovernance, triggerPanic, refetch,
  } = useSecrets();

  const [tab, setTab]               = useState<'active' | 'archived'>('active');
  const [showInject, setShowInject] = useState(false);
  const [showHistory, setShowHistory] = useState<Secret | null>(null);
  const [showRotate, setShowRotate]   = useState<Secret | null>(null);

  // Inject form
  const [form, setForm] = useState<CreateSecretPayload>({
    key_name: '', key_type: 'BEARER_TOKEN', algorithm: 'AES-256', realm: '', value: '', rotation_days: 90,
  });
  const [injecting, setInjecting]   = useState(false);
  const [injectErr, setInjectErr]   = useState<string | null>(null);

  const isAdmin = keycloak.hasRealmRole('platform-admin');

  // Reveal state: id → value (or null = loading)
  const [revealed, setRevealed]     = useState<Record<string, string | null>>({});
  const [revealErr, setRevealErr]   = useState<Record<string, string>>({});
  const [copied, setCopied]         = useState<string | null>(null);

  // Access reason dialog (shown when PII Access Log is enabled)
  const [revealDialogId, setRevealDialogId] = useState<string | null>(null);
  const [revealReason, setRevealReason]     = useState('');

  // Rotate form
  const [rotateVal, setRotateVal]   = useState('');
  const [rotating, setRotating]     = useState(false);
  const [rotateErr, setRotateErr]   = useState<string | null>(null);

  // Governance loading
  const [govLoading, setGovLoading] = useState<string | null>(null);

  const visibleSecrets = secrets.filter(s => tab === 'active' ? s.is_active : !s.is_active);

  const isTransit = (keyType: string) => TRANSIT_TYPES.has(keyType);

  const handleInject = async () => {
    if (!form.key_name.trim()) return;
    if (!isTransit(form.key_type) && !form.value.trim()) return;
    setInjecting(true);
    setInjectErr(null);
    try {
      await createSecret(form);
      setShowInject(false);
      setForm({ key_name: '', key_type: 'BEARER_TOKEN', algorithm: 'AES-256', realm: '', value: '', rotation_days: 90 });
    } catch (e) {
      setInjectErr(e instanceof Error ? e.message : 'Failed to create secret');
    } finally {
      setInjecting(false);
    }
  };

  const handleReveal = async (id: string) => {
    if (revealed[id] !== undefined) {
      setRevealed(prev => { const n = { ...prev }; delete n[id]; return n; });
      return;
    }
    // If PII logging enabled, require access reason before revealing
    if (governance.vault_pii_access_log) {
      setRevealDialogId(id);
      setRevealReason('');
      return;
    }
    await doReveal(id);
  };

  const doReveal = async (id: string, reason?: string) => {
    setRevealErr(prev => { const n = { ...prev }; delete n[id]; return n; });
    setRevealed(prev => ({ ...prev, [id]: null }));
    try {
      const val = await revealSecret(id, reason);
      setRevealed(prev => ({ ...prev, [id]: val }));
      setTimeout(() => setRevealed(prev => { const n = { ...prev }; delete n[id]; return n; }), 30000);
      // Refresh PII log so the new entry (with reason) appears immediately
      if (governance.vault_pii_access_log) refetch();
    } catch (e) {
      setRevealed(prev => { const n = { ...prev }; delete n[id]; return n; });
      setRevealErr(prev => ({ ...prev, [id]: e instanceof Error ? e.message : 'Failed to reveal' }));
    }
  };

  const handleRevealConfirm = async () => {
    if (!revealDialogId || !revealReason.trim()) return;
    const id = revealDialogId;
    setRevealDialogId(null);
    await doReveal(id, revealReason.trim());
    setRevealReason('');
  };

  const handleCopy = (id: string, val: string) => {
    navigator.clipboard.writeText(val);
    setCopied(id);
    setTimeout(() => setCopied(null), 2000);
  };

  const handleRotate = async () => {
    if (!showRotate) return;
    if (!isTransit(showRotate.key_type) && !rotateVal.trim()) return;
    setRotating(true);
    setRotateErr(null);
    try {
      await rotateSecret(showRotate.id, rotateVal);
      setShowRotate(null);
      setRotateVal('');
    } catch (e) {
      setRotateErr(e instanceof Error ? e.message : 'Rotation failed');
    } finally {
      setRotating(false);
    }
  };

  const handleGovToggle = async (key: keyof typeof governance) => {
    setGovLoading(key);
    try {
      await updateGovernance({ [key]: !governance[key] });
    } finally {
      setGovLoading(null);
    }
  };

  return (
    <>
      <div className="space-y-10 lg:space-y-12 pb-20 no-scrollbar">
        <OperationalHeader
          title="Secrets Vault"
          subtitle="Distributed cryptographic storage, API key management, and RSA/HSM signing."
          breadcrumbs={[{ label: 'Settings' }, { label: 'Security' }, { label: 'Secrets Vault' }]}
          status={<StatusBadge status="SECURE" size="lg" />}
          actions={
            <button
              onClick={() => setShowInject(true)}
              className="w-full lg:w-auto flex items-center justify-center gap-2 bg-brand-500 hover:bg-brand-600 text-white px-5 py-2.5 rounded-2xl text-xs font-bold transition-all shadow-lg shadow-brand-500/20 active:scale-95"
            >
              <Plus className="w-4 h-4" />
              INJECT SECRET
            </button>
          }
        />

        <div className="grid grid-cols-1 xl:grid-cols-3 gap-8">
          <div className="xl:col-span-2 space-y-8 lg:space-y-10">

            {/* Active / Archived Secrets */}
            <section className="glass-panel p-6 lg:p-10 rounded-[2.5rem] lg:rounded-[3rem]">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-6 mb-10">
                <div className="flex items-center gap-3">
                  <div className="p-2.5 bg-brand-500/10 rounded-2xl border border-brand-500/20 text-brand-600">
                    <KeyRound className="w-6 h-6" />
                  </div>
                  <div>
                    <h3 className="text-xl lg:text-2xl font-bold text-[#111111] tracking-tight italic">Active Cryptographic Keys</h3>
                    <p className="text-[10px] lg:text-xs text-[#888888] uppercase font-black tracking-widest mt-1">FIPS 140-2 Level 3 Secure Storage</p>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button onClick={refetch} className="p-2 text-[#888888] hover:text-[#111111] transition-colors" title="Refresh">
                    <RefreshCw className="w-3.5 h-3.5" />
                  </button>
                  <div className="flex bg-[#F5EDD5] border border-[#D6C79F] p-1.5 rounded-2xl">
                    <button onClick={() => setTab('active')} className={cn('px-4 py-2 rounded-xl text-[10px] font-black uppercase tracking-widest', tab === 'active' ? 'bg-brand-500 text-white' : 'text-[#888888] hover:text-[#111111]')}>Active</button>
                    <button onClick={() => setTab('archived')} className={cn('px-4 py-2 rounded-xl text-[10px] font-black uppercase tracking-widest', tab === 'archived' ? 'bg-brand-500 text-white' : 'text-[#888888] hover:text-[#111111]')}>Archived</button>
                  </div>
                </div>
              </div>

              {loading && (
                <div className="flex items-center gap-3 p-4 text-[#888888]">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  <span className="text-sm">Loading secrets…</span>
                </div>
              )}
              {error && (
                <div className="p-3 bg-red-50 border border-red-200 rounded-xl flex items-center gap-2">
                  <AlertCircle className="w-4 h-4 text-red-500 shrink-0" />
                  <span className="text-xs text-red-600">{error}</span>
                </div>
              )}
              {!loading && !error && visibleSecrets.length === 0 && (
                <div className="p-8 text-center text-[#888888] text-sm border border-dashed border-[#D6C79F] rounded-2xl">
                  No {tab} secrets. {tab === 'active' && 'Click "INJECT SECRET" to add one.'}
                </div>
              )}

              <div className="space-y-4">
                {visibleSecrets.map(secret => (
                  <div key={secret.id} className="flex flex-col lg:flex-row items-stretch lg:items-center justify-between p-6 bg-[#FAF8F5] border border-[#E8DFC9] rounded-[2rem] hover:bg-[#F5EDD5] transition-all group gap-6 lg:gap-0">
                    <div className="flex items-center gap-5">
                      <div className="w-12 h-12 rounded-2xl bg-[#F0E6CC] border border-[#D6C79F] flex items-center justify-center text-[#888888] group-hover:text-brand-600 transition-colors shadow-inner">
                        <Lock className="w-6 h-6" />
                      </div>
                      <div>
                        <h4 className="text-base lg:text-lg font-bold text-[#111111] tracking-tight font-mono">{secret.key_name}</h4>
                        <div className="flex flex-wrap items-center gap-3 mt-1">
                          {secret.realm && <span className="text-[9px] font-black text-[#888888] uppercase tracking-widest">{secret.realm}</span>}
                          {secret.realm && <div className="w-1 h-1 rounded-full bg-[#D6C79F]" />}
                          <span className="text-[10px] font-mono text-brand-700 uppercase tracking-widest">{secret.algorithm || secret.key_type}</span>
                          {TRANSIT_TYPES.has(secret.key_type) && (
                            <span className="text-[8px] font-black bg-violet-100 text-violet-700 border border-violet-200 px-1.5 py-0.5 rounded-md uppercase tracking-widest">TRANSIT</span>
                          )}
                          <span className="text-[9px] text-[#888888] uppercase">v{secret.version}</span>
                        </div>
                        {revealed[secret.id] !== undefined && (
                          <div className="mt-2 flex items-center gap-2">
                            <code className="text-[10px] font-mono text-emerald-700 bg-emerald-50 border border-emerald-200 px-2 py-1 rounded-lg max-w-[240px] truncate">
                              {revealed[secret.id] === null ? '…' : revealed[secret.id]}
                            </code>
                            {revealed[secret.id] && (
                              <button onClick={() => handleCopy(secret.id, revealed[secret.id]!)} className="text-[#888888] hover:text-[#111111]">
                                {copied === secret.id ? <Check className="w-3 h-3 text-emerald-600" /> : <Copy className="w-3 h-3" />}
                              </button>
                            )}
                            <span className="text-[9px] text-[#888888]">hides in 30s</span>
                          </div>
                        )}
                        {revealErr[secret.id] && (
                          <div className="mt-2 flex items-center gap-1.5">
                            <AlertCircle className="w-3 h-3 text-red-500 shrink-0" />
                            <span className="text-[10px] text-red-600">{revealErr[secret.id]}</span>
                          </div>
                        )}
                      </div>
                    </div>

                    <div className="flex items-center gap-8 justify-between lg:justify-end border-t lg:border-t-0 border-[#E8DFC9] pt-4 lg:pt-0">
                      <div className="text-right">
                        <div className={cn('text-xs font-bold font-mono tracking-tighter', ttlColor(secret.rotation_due_at))}>
                          {ttlLabel(secret.rotation_due_at)}
                        </div>
                        <div className="text-[9px] font-black text-[#888888] uppercase tracking-widest">TTL Remaining</div>
                      </div>
                      <div className="flex gap-2">
                        <button
                          onClick={() => handleReveal(secret.id)}
                          className="p-3 bg-white border border-[#D6C79F] rounded-xl text-[#888888] hover:text-[#111111] transition-all transform hover:scale-110 active:scale-95"
                          title={revealed[secret.id] !== undefined ? 'Hide value' : 'Reveal value'}
                        >
                          {revealed[secret.id] !== undefined ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                        </button>
                        <button
                          onClick={() => { setShowRotate(secret); setRotateVal(''); setRotateErr(null); }}
                          className="p-3 bg-white border border-[#D6C79F] rounded-xl text-[#888888] hover:text-amber-600 transition-all transform hover:scale-110 active:scale-95"
                          title="Rotate secret"
                        >
                          <RefreshCw className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => setShowHistory(secret)}
                          className="p-3 bg-white border border-[#D6C79F] rounded-xl text-[#888888] hover:text-[#111111] transition-all transform hover:scale-110 active:scale-95"
                          title="View rotation history"
                        >
                          <History className="w-4 h-4" />
                        </button>
                        {tab === 'active' && (
                          <button
                            onClick={() => deleteSecret(secret.id)}
                            className="p-3 bg-white border border-[#D6C79F] rounded-xl text-[#888888] hover:text-red-600 transition-all transform hover:scale-110 active:scale-95"
                            title="Deactivate secret"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </section>

            {/* Vault Governance */}
            <section className="glass-panel p-8 lg:p-10 rounded-[2.5rem] lg:rounded-[3rem]">
              <h3 className="text-lg lg:text-xl font-bold text-[#111111] mb-8 italic flex items-center gap-3">
                <ShieldCheck className="w-5 h-5 text-emerald-600" />
                Vault Governance Controls
              </h3>
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                {!isAdmin && (
                  <div className="col-span-full p-3 bg-amber-50 border border-amber-200 rounded-2xl flex items-center gap-2 mb-2">
                    <AlertCircle className="w-3.5 h-3.5 text-amber-500 shrink-0" />
                    <span className="text-[10px] text-amber-700">Governance controls require Platform Admin role.</span>
                  </div>
                )}
                {([
                  { key: 'vault_auto_rotation' as const, label: 'Auto-Rotation', desc: 'Schedules key swap every 90 days' },
                  { key: 'vault_panic_mode' as const,    label: 'Panic Mode',    desc: 'Instant revocation of all non-core keys' },
                  { key: 'vault_pii_access_log' as const, label: 'PII Access Log', desc: 'Record every secret retrieval event + require access reason' },
                ] as const).map(ctl => {
                  const enabled = governance[ctl.key];
                  const isLoading = govLoading === ctl.key;
                  return (
                    <button
                      key={ctl.key}
                      onClick={() => isAdmin && handleGovToggle(ctl.key)}
                      disabled={isLoading || !isAdmin}
                      title={!isAdmin ? 'Requires Platform Admin role' : undefined}
                      className={cn(
                        'p-6 bg-[#FAF8F5] border border-[#E8DFC9] rounded-3xl transition-all flex flex-col justify-between group text-left',
                        isAdmin ? 'hover:border-brand-500 cursor-pointer' : 'cursor-not-allowed opacity-60',
                        'disabled:opacity-50',
                      )}
                    >
                      <div>
                        <h4 className="text-sm font-bold text-[#111111] mb-2 uppercase tracking-tight italic group-hover:text-brand-700">{ctl.label}</h4>
                        <p className="text-[10px] text-[#888888] italic leading-relaxed">{ctl.desc}</p>
                      </div>
                      <div className="mt-6 flex items-center gap-3">
                        {isLoading
                          ? <Loader2 className="w-3 h-3 text-brand-600 animate-spin" />
                          : <div className={cn('w-2 h-2 rounded-full', enabled ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.4)]' : 'bg-[#CCCCCC]')} />
                        }
                        <span className={cn('text-[9px] font-black uppercase tracking-widest', enabled ? 'text-emerald-600' : 'text-[#888888]')}>
                          {enabled ? 'ACTIVE' : 'STANDBY'}
                        </span>
                      </div>
                    </button>
                  );
                })}
              </div>
            </section>

            {/* PII Access Log */}
            <section className="glass-panel p-6 lg:p-8 rounded-[2.5rem] lg:rounded-[3rem]">
              <div className="flex items-center justify-between mb-6">
                <div className="flex items-center gap-3">
                  <div className="p-2 bg-violet-500/10 rounded-xl border border-violet-500/20 text-violet-600">
                    <ShieldCheck className="w-4 h-4" />
                  </div>
                  <div>
                    <h3 className="text-sm font-bold text-[#111111] italic">PII Access Log</h3>
                    <p className="text-[9px] text-[#888888] uppercase font-black tracking-widest mt-0.5">Secret reveal audit trail</p>
                  </div>
                </div>
                <div className={cn('flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[8px] font-black uppercase tracking-widest', governance.vault_pii_access_log ? 'bg-violet-100 text-violet-700' : 'bg-[#F0E6CC] text-[#888888]')}>
                  <div className={cn('w-1.5 h-1.5 rounded-full', governance.vault_pii_access_log ? 'bg-violet-500' : 'bg-[#CCCCCC]')} />
                  {governance.vault_pii_access_log ? 'RECORDING' : 'OFF'}
                </div>
              </div>
              {!governance.vault_pii_access_log && (
                <div className="p-3 bg-amber-50 border border-amber-200 rounded-2xl mb-4 flex items-center gap-2">
                  <AlertCircle className="w-3.5 h-3.5 text-amber-500 shrink-0" />
                  <span className="text-[10px] text-amber-700">PII Access Log is off — enable in Governance Controls above to record reveals.</span>
                </div>
              )}
              {piiLog.length === 0 ? (
                <p className="text-[10px] text-[#888888] text-center py-6 border border-dashed border-[#D6C79F] rounded-2xl">No reveal events recorded yet.</p>
              ) : (
                <div className="space-y-2">
                  {piiLog.slice(0, 10).map(entry => (
                    <div key={entry.id} className="px-4 py-4 bg-[#FAF8F5] border border-[#E8DFC9] rounded-2xl space-y-3">

                      {/* Header row: secret name + version + timestamp */}
                      <div className="flex items-center justify-between gap-3">
                        <div className="flex items-center gap-2.5 min-w-0">
                          <Eye className="w-3.5 h-3.5 text-violet-500 shrink-0" />
                          <span className="text-[11px] font-bold font-mono text-[#111111] truncate">{entry.key_name}</span>
                          <span className="text-[8px] font-black text-violet-600 bg-violet-50 border border-violet-200 px-1.5 py-0.5 rounded-md uppercase shrink-0">v{entry.version}</span>
                        </div>
                        <span className="text-[9px] text-[#888888] shrink-0">{entry.time ? new Date(entry.time).toLocaleString() : '—'}</span>
                      </div>

                      {/* User ID */}
                      <div className="flex items-start gap-2">
                        <span className="text-[8px] font-black text-[#888888] uppercase tracking-widest w-14 shrink-0 mt-0.5">User ID</span>
                        <span className="text-[10px] font-mono text-[#333333] break-all">{entry.actor_id}</span>
                      </div>

                      {/* Reason */}
                      <div className="flex items-start gap-2">
                        <span className="text-[8px] font-black text-[#888888] uppercase tracking-widest w-14 shrink-0 mt-0.5">Reason</span>
                        {entry.access_reason
                          ? <span className="text-[10px] text-violet-700 italic leading-relaxed">"{entry.access_reason}"</span>
                          : <span className="text-[10px] text-[#AAAAAA] italic">—</span>
                        }
                      </div>

                    </div>
                  ))}
                </div>
              )}
            </section>
          </div>

          <div className="space-y-8 lg:space-y-10">
            {/* HSM Infrastructure */}
            <section className="glass-panel p-6 lg:p-10 rounded-[2.5rem] lg:rounded-[3rem]">
              <div className="flex items-center gap-3 mb-8">
                <div className="p-2 bg-brand-500/10 rounded-xl border border-brand-500/20 text-brand-600">
                  <Server className="w-5 h-5" />
                </div>
                <h3 className="text-lg lg:text-xl font-bold text-[#111111] tracking-tight italic">HSM Infrastructure</h3>
              </div>
              <div className="space-y-8">
                {/* Vault status */}
                <div className="p-6 bg-[#FAF8F5] border border-[#E8DFC9] rounded-3xl text-center relative overflow-hidden group">
                  <div className="absolute top-0 right-0 w-32 h-32 bg-brand-500/[0.08] blur-[40px] group-hover:bg-brand-500/[0.15] transition-all" />
                  <div className={cn('text-3xl font-display font-medium mb-2 italic', hsmStatus?.openbao_sealed ? 'text-red-600' : 'text-[#111111]')}>
                    {hsmStatus ? (hsmStatus.openbao_sealed ? 'SEALED' : 'ACTIVE') : '—'}
                  </div>
                  <div className="text-[10px] font-black text-[#888888] uppercase tracking-widest">OpenBao Transit Engine</div>
                </div>
                {/* Transit key count bar */}
                <div className="space-y-6">
                  <div>
                    <div className="flex justify-between text-[10px] font-black mb-2 uppercase tracking-widest text-[#888888]">
                      <span>Transit Keys Managed</span>
                      <span className={cn(hsmStatus?.openbao_sealed ? 'text-red-500' : 'text-emerald-600')}>
                        {hsmStatus ? (hsmStatus.openbao_sealed ? 'SEALED' : 'OPERATIONAL') : '—'}
                      </span>
                    </div>
                    <div className="h-1.5 bg-[#E8DFC9] rounded-full overflow-hidden">
                      <div className={cn('h-full rounded-full shadow-[0_0_10px_rgba(16,185,129,0.3)]', hsmStatus?.openbao_sealed ? 'bg-red-400' : 'bg-emerald-500')}
                        style={{ width: hsmStatus ? `${Math.min(100, (hsmStatus.key_count / 10) * 100)}%` : '0%' }} />
                    </div>
                    <div className="mt-1 text-[9px] text-[#888888] text-right">{hsmStatus?.key_count ?? 0} key{hsmStatus?.key_count !== 1 ? 's' : ''}</div>
                  </div>
                </div>
                {/* Transit key list */}
                <div className="p-6 bg-[#FFF9E8] border border-[#D6C79F] rounded-3xl">
                  <div className="flex items-center gap-3 mb-3 text-brand-700">
                    <ShieldAlert className="w-4 h-4" />
                    <span className="text-[10px] font-black uppercase tracking-widest">Transit Keys</span>
                  </div>
                  {(!hsmStatus || hsmStatus.transit_keys.length === 0) ? (
                    <p className="text-[11px] text-brand-700/60 italic">No Transit keys yet. Create a SIGNING_KEY, ENCRYPTION_KEY, or HMAC_KEY to generate one.</p>
                  ) : (
                    <div className="space-y-2">
                      {hsmStatus.transit_keys.slice(0, 4).map(k => (
                        <div key={k.name} className="flex justify-between items-center">
                          <span className="text-[10px] font-mono text-brand-700 truncate max-w-[140px]">{k.name}</span>
                          <div className="flex items-center gap-2">
                            <span className="text-[9px] text-[#888888] uppercase">{k.type}</span>
                            <span className="text-[9px] font-black text-violet-600">v{k.latest_version}</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            </section>

            {/* Recent Faults / Audit Log */}
            <section className="glass-panel p-6 lg:p-10 rounded-[2.5rem] lg:rounded-[3rem]">
              <h3 className="text-[11px] font-black text-[#5A5A5A] uppercase tracking-[0.3em] mb-8 lg:mb-10 flex items-center gap-3">
                <AlertTriangle className="w-4 h-4 text-amber-500" />
                Recent Faults
              </h3>
              <div className="space-y-4">
                {auditLog.length === 0 && !loading && (
                  <p className="text-[10px] text-[#888888] text-center py-4">No audit events yet.</p>
                )}
                {auditLog.slice(0, 5).map(entry => (
                  <div key={entry.id} className="p-4 bg-[#FAF8F5] border border-[#E8DFC9] rounded-2xl hover:bg-[#F5EDD5] transition-all cursor-pointer">
                    <div className="flex justify-between items-center mb-1">
                      <span className="text-[9px] font-black text-[#5A5A5A] uppercase tracking-widest">{entry.event}</span>
                      <span className="text-[9px] font-mono text-[#888888]">{entry.time ? new Date(entry.time).toLocaleString() : '—'}</span>
                    </div>
                    <div className="text-[10px] font-bold text-[#111111] tracking-tight">{entry.key_name}</div>
                    <div className="text-[9px] text-[#888888] mt-0.5">{entry.actor_id}</div>
                  </div>
                ))}
              </div>
              <div className="mt-8 flex justify-center border-t border-[#E8DFC9] pt-8">
                <button onClick={refetch} className="text-[10px] font-black text-brand-700 uppercase tracking-widest hover:underline flex items-center gap-2">
                  Refresh Audit Stream <ArrowRight size={12} />
                </button>
              </div>
            </section>
          </div>
        </div>
      </div>

      {/* Inject Secret Drawer */}
      <DetailDrawer
        isOpen={showInject}
        onClose={() => { setShowInject(false); setInjectErr(null); }}
        title="Inject Secret"
        subtitle="Store a new secret in OpenBao Vault"
        icon={Key}
        size="sm"
        footer={
          <div className="flex gap-3">
            <button onClick={() => { setShowInject(false); setInjectErr(null); }} className="btn-secondary">Cancel</button>
            <button onClick={handleInject} disabled={injecting || !form.key_name.trim() || (!isTransit(form.key_type) && !form.value.trim())} className="btn-primary">
              {injecting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : 'Inject'}
            </button>
          </div>
        }
      >
        <div className="p-6 space-y-4">
          {injectErr && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-xl flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-red-500 shrink-0" />
              <span className="text-xs text-red-600">{injectErr}</span>
            </div>
          )}
          {([
            { label: 'Key Name', field: 'key_name' as const, type: 'text', placeholder: 'AZURE_PROD_API_KEY', mono: true },
            { label: 'Realm',    field: 'realm' as const,    type: 'text', placeholder: 'FINANCE-INFRA',       mono: false },
          ]).map(({ label, field, placeholder, mono }) => (
            <div key={field}>
              <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">{label}</label>
              <input
                value={form[field] as string}
                onChange={e => setForm(prev => ({ ...prev, [field]: e.target.value }))}
                placeholder={placeholder}
                className={cn('w-full px-3 py-2 bg-white border border-[#D6C79F] rounded-xl text-sm text-[#111111] placeholder:text-[#AAAAAA] focus:outline-none focus:border-brand-500', mono && 'font-mono')}
              />
            </div>
          ))}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">Key Type</label>
              <select value={form.key_type} onChange={e => setForm(prev => ({ ...prev, key_type: e.target.value }))} className="w-full px-3 py-2 bg-white border border-[#D6C79F] rounded-xl text-sm text-[#111111] focus:outline-none focus:border-brand-500">
                {KEY_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">Algorithm</label>
              <select value={form.algorithm} onChange={e => setForm(prev => ({ ...prev, algorithm: e.target.value }))} className="w-full px-3 py-2 bg-white border border-[#D6C79F] rounded-xl text-sm text-[#111111] focus:outline-none focus:border-brand-500">
                {ALGORITHMS.map(a => <option key={a} value={a}>{a}</option>)}
              </select>
            </div>
          </div>
          {isTransit(form.key_type) ? (
            <div className="p-3 bg-violet-50 border border-violet-200 rounded-xl flex items-start gap-2">
              <ShieldCheck className="w-4 h-4 text-violet-600 shrink-0 mt-0.5" />
              <p className="text-[11px] text-violet-700 leading-relaxed">Key auto-generated inside OpenBao Transit — private key never exported from vault.</p>
            </div>
          ) : (
            <div>
              <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">Secret Value</label>
              <textarea
                value={form.value}
                onChange={e => setForm(prev => ({ ...prev, value: e.target.value }))}
                placeholder="Paste secret value here…"
                rows={3}
                className="w-full px-3 py-2 bg-white border border-[#D6C79F] rounded-xl text-sm font-mono text-[#111111] placeholder:text-[#AAAAAA] focus:outline-none focus:border-brand-500 resize-none"
              />
            </div>
          )}
          <div>
            <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">Rotation (days)</label>
            <input
              type="number"
              value={form.rotation_days}
              onChange={e => setForm(prev => ({ ...prev, rotation_days: parseInt(e.target.value) || 90 }))}
              min={1}
              className="w-full px-3 py-2 bg-white border border-[#D6C79F] rounded-xl text-sm text-[#111111] focus:outline-none focus:border-brand-500"
            />
          </div>
        </div>
      </DetailDrawer>

      {/* Rotate Secret Drawer */}
      <DetailDrawer
        isOpen={!!showRotate}
        onClose={() => { setShowRotate(null); setRotateVal(''); setRotateErr(null); }}
        title="Rotate Secret"
        subtitle={showRotate?.key_name ?? ''}
        icon={RefreshCw}
        size="sm"
        footer={
          <div className="flex gap-3">
            <button onClick={() => setShowRotate(null)} className="btn-secondary">Cancel</button>
            <button onClick={handleRotate} disabled={rotating || (!showRotate?.key_type || (!isTransit(showRotate.key_type) && !rotateVal.trim()))} className="btn-primary">
              {rotating ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : 'Rotate'}
            </button>
          </div>
        }
      >
        <div className="p-6 space-y-4">
          {rotateErr && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-xl flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-red-500 shrink-0" />
              <span className="text-xs text-red-600">{rotateErr}</span>
            </div>
          )}
          {showRotate && isTransit(showRotate.key_type) ? (
            <div className="p-3 bg-violet-50 border border-violet-200 rounded-xl flex items-start gap-2">
              <ShieldCheck className="w-4 h-4 text-violet-600 shrink-0 mt-0.5" />
              <p className="text-[11px] text-violet-700 leading-relaxed">Transit rotation generates a new key version inside OpenBao. The old version remains available for signature verification.</p>
            </div>
          ) : (
            <>
              <p className="text-xs text-[#5A5A5A]">Enter the new secret value. The current version will be archived and a new version created in OpenBao.</p>
              <div>
                <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">New Secret Value</label>
                <textarea
                  value={rotateVal}
                  onChange={e => setRotateVal(e.target.value)}
                  placeholder="New secret value…"
                  rows={3}
                  className="w-full px-3 py-2 bg-white border border-[#D6C79F] rounded-xl text-sm font-mono text-[#111111] placeholder:text-[#AAAAAA] focus:outline-none focus:border-brand-500 resize-none"
                />
              </div>
            </>
          )}
        </div>
      </DetailDrawer>

      {/* Access Reason Dialog — shown when PII Access Log is enabled */}
      {revealDialogId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm px-4">
          <div className="w-full max-w-sm bg-white rounded-[2rem] shadow-2xl border border-[#E8DFC9] p-8 space-y-6">
            <div className="flex items-center gap-3">
              <div className="p-2 bg-violet-500/10 rounded-xl border border-violet-500/20 text-violet-600">
                <ShieldCheck className="w-5 h-5" />
              </div>
              <div>
                <h3 className="text-base font-bold text-[#111111] italic">Access Reason Required</h3>
                <p className="text-[10px] text-[#888888] uppercase font-black tracking-widest mt-0.5">PII Access Log is recording</p>
              </div>
            </div>
            <p className="text-xs text-[#5A5A5A] leading-relaxed">
              PII Access Log is enabled. Please provide a reason for accessing this secret — it will be recorded in the audit trail.
            </p>
            <div>
              <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">
                Reason for Access <span className="text-red-500">*</span>
              </label>
              <textarea
                autoFocus
                value={revealReason}
                onChange={e => setRevealReason(e.target.value)}
                placeholder="e.g. Debugging production incident #1234, rotating credentials for deployment…"
                rows={3}
                className="w-full px-3 py-2 bg-[#FAF8F5] border border-[#D6C79F] rounded-xl text-sm text-[#111111] placeholder:text-[#AAAAAA] focus:outline-none focus:border-violet-400 resize-none"
              />
              {revealReason.trim().length > 0 && revealReason.trim().length < 10 && (
                <p className="text-[9px] text-amber-600 mt-1">Please provide a more descriptive reason (min 10 chars).</p>
              )}
            </div>
            <div className="flex gap-3">
              <button
                onClick={() => { setRevealDialogId(null); setRevealReason(''); }}
                className="btn-secondary flex-1"
              >
                Cancel
              </button>
              <button
                onClick={handleRevealConfirm}
                disabled={revealReason.trim().length < 10}
                className="btn-primary flex-1 flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <Eye className="w-3.5 h-3.5" />
                Reveal
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Rotation History Drawer */}
      <DetailDrawer
        isOpen={!!showHistory}
        onClose={() => setShowHistory(null)}
        title="Rotation History"
        subtitle={showHistory?.key_name ?? ''}
        icon={History}
        size="sm"
      >
        <div className="p-6 space-y-3">
          {auditLog.filter(e => e.key_name === showHistory?.key_name).length === 0
            ? <p className="text-xs text-[#888888] text-center py-8">No rotation events recorded.</p>
            : auditLog.filter(e => e.key_name === showHistory?.key_name).map(entry => (
              <div key={entry.id} className="p-4 bg-[#FAF8F5] border border-[#E8DFC9] rounded-2xl">
                <div className="flex justify-between items-center mb-1">
                  <span className={cn('text-[9px] font-black uppercase tracking-widest', entry.event === 'PANIC' ? 'text-red-600' : entry.event === 'MANUAL' ? 'text-amber-600' : 'text-brand-600')}>{entry.event}</span>
                  <span className="text-[9px] font-mono text-[#888888]">{entry.time ? new Date(entry.time).toLocaleString() : '—'}</span>
                </div>
                <div className="text-[10px] text-[#5A5A5A]">v{entry.old_version} → v{entry.new_version} · {entry.actor_id}</div>
                <div className={cn('text-[9px] font-black mt-1', entry.status === 'SUCCESS' ? 'text-emerald-600' : 'text-red-600')}>{entry.status}</div>
              </div>
            ))
          }
        </div>
      </DetailDrawer>
    </>
  );
};
