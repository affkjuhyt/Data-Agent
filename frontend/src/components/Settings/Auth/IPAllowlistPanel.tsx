import React, { useState } from 'react';
import { Globe, Plus, Trash2, AlertCircle, Loader2, RefreshCw, ShieldOff, ShieldCheck } from 'lucide-react';
import { cn } from '../../../lib/utils';
import { DetailDrawer } from '../../shared/DetailDrawer';
import { useIPAllowlist, IPMode } from '../../../lib/useIPAllowlist';

export const IPAllowlistPanel = () => {
  const { rules, mode, loading, error, addRule, deleteRule, toggleRule, setMode, refetch } = useIPAllowlist();
  const [showAdd, setShowAdd]     = useState(false);
  const [newCidr, setNewCidr]     = useState('');
  const [newLabel, setNewLabel]   = useState('');
  const [saving, setSaving]       = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [modeLoading, setModeLoading] = useState(false);

  const handleAdd = async () => {
    if (!newCidr.trim()) return;
    setSaving(true);
    setSaveError(null);
    try {
      await addRule(newCidr.trim(), newLabel.trim());
      setShowAdd(false);
      setNewCidr('');
      setNewLabel('');
    } catch (e) {
      setSaveError(e instanceof Error ? e.message : 'Failed to add rule');
    } finally {
      setSaving(false);
    }
  };

  const handleModeToggle = async () => {
    setModeLoading(true);
    try {
      await setMode(mode === 'allow_all' ? 'allowlist' : 'allow_all');
    } finally {
      setModeLoading(false);
    }
  };

  return (
    <>
      <div className="space-y-6">
        <section>
          {/* Header */}
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-base font-bold text-[#111111]">IP / CIDR Allowlist</h3>
              <p className="text-xs text-[#5A5A5A] mt-0.5">
                Enforced at Kong Gateway — table <code className="font-mono bg-[#F4E8C3] px-1 rounded">ip_allowlists</code>
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button onClick={refetch} className="p-2 text-[#777] hover:text-[#111111] transition-colors" title="Refresh">
                <RefreshCw className="w-3.5 h-3.5" />
              </button>
              <button
                onClick={() => setShowAdd(true)}
                disabled={mode === 'allow_all'}
                className="flex items-center gap-1.5 px-3.5 py-2 bg-[#111111] text-white rounded-xl text-xs font-bold hover:bg-[#2a2a2a] transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <Plus className="w-3.5 h-3.5" /> Add CIDR
              </button>
            </div>
          </div>

          {/* Allow All toggle */}
          <div className={cn(
            'p-4 mb-4 rounded-2xl border flex items-center justify-between gap-4 transition-all',
            mode === 'allow_all'
              ? 'bg-red-50 border-red-300'
              : 'bg-[#F4F9F4] border-emerald-200',
          )}>
            <div className="flex items-center gap-3">
              {mode === 'allow_all'
                ? <ShieldOff className="w-5 h-5 text-red-500 shrink-0" />
                : <ShieldCheck className="w-5 h-5 text-emerald-600 shrink-0" />}
              <div>
                <p className={cn('text-sm font-bold', mode === 'allow_all' ? 'text-red-700' : 'text-emerald-800')}>
                  {mode === 'allow_all' ? 'Allow All IPs (restriction disabled)' : 'Restricted — allowlist enforced'}
                </p>
                <p className={cn('text-[11px] mt-0.5', mode === 'allow_all' ? 'text-red-500' : 'text-emerald-600')}>
                  {mode === 'allow_all'
                    ? 'Any IP can access the platform. Only use for local development.'
                    : 'Only IPs in the list below can access the platform via Kong.'}
                </p>
              </div>
            </div>
            <button
              onClick={handleModeToggle}
              disabled={modeLoading}
              className={cn(
                'relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors focus:outline-none disabled:opacity-50',
                mode === 'allow_all' ? 'bg-red-500' : 'bg-emerald-500',
              )}
              title={mode === 'allow_all' ? 'Switch to allowlist mode' : 'Allow all IPs'}
            >
              {modeLoading
                ? <Loader2 className="w-3 h-3 text-white animate-spin m-auto" />
                : <span className={cn(
                    'pointer-events-none inline-block h-5 w-5 rounded-full bg-white shadow transform transition-transform',
                    mode === 'allow_all' ? 'translate-x-5' : 'translate-x-0',
                  )} />}
            </button>
          </div>

          {/* Kong enforcement note */}
          <div className="p-3 mb-4 bg-amber-50 border border-amber-200 rounded-xl flex items-start gap-2.5">
            <AlertCircle className="w-4 h-4 text-amber-600 shrink-0 mt-0.5" />
            <p className="text-[11px] text-amber-700">
              Changes sync to <strong>Kong ip-restriction plugin</strong> automatically after each action.
            </p>
          </div>

          {/* Loading / Error */}
          {loading && (
            <div className="flex items-center gap-3 p-4 bg-[#FDFAF2] border border-[#E8DFC8] rounded-xl">
              <Loader2 className="w-4 h-4 text-[#B88719] animate-spin" />
              <span className="text-sm text-[#5A5A5A]">Loading rules…</span>
            </div>
          )}
          {error && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-xl flex items-center gap-2.5">
              <AlertCircle className="w-4 h-4 text-red-500 shrink-0" />
              <span className="text-xs text-red-700">{error}</span>
            </div>
          )}

          {/* Empty state */}
          {!loading && !error && rules.length === 0 && mode !== 'allow_all' && (
            <div className="p-8 text-center text-[#999] text-sm border border-dashed border-[#E0D5C0] rounded-2xl">
              No IP rules yet. Add a CIDR to restrict access.
            </div>
          )}

          {/* Rules list */}
          <div className={cn('space-y-2', mode === 'allow_all' && 'opacity-40 pointer-events-none')}>
            {rules.map(rule => (
              <div
                key={rule.id}
                className={cn(
                  'flex items-center justify-between p-4 bg-white border rounded-2xl transition-all',
                  rule.is_active ? 'border-[#E8DFC8] hover:border-[#B88719]' : 'border-[#EEEEEE] opacity-60',
                )}
              >
                <div className="flex items-center gap-4">
                  <button
                    onClick={() => toggleRule(rule.id)}
                    className={cn(
                      'w-8 h-8 rounded-xl flex items-center justify-center border shrink-0 transition-colors',
                      rule.is_active
                        ? 'bg-emerald-50 border-emerald-200 text-emerald-600 hover:bg-emerald-100'
                        : 'bg-[#F4F4F4] border-[#DDD] text-[#999] hover:bg-[#EAEAEA]',
                    )}
                    title={rule.is_active ? 'Disable rule' : 'Enable rule'}
                  >
                    <Globe className="w-4 h-4" />
                  </button>
                  <div>
                    <div className="flex items-center gap-2">
                      <code className="text-sm font-mono font-bold text-[#111111]">{rule.cidr}</code>
                      <span className={cn(
                        'text-[9px] font-bold px-1.5 py-0.5 rounded-full',
                        rule.is_active ? 'bg-emerald-50 text-emerald-700' : 'bg-[#F4F4F4] text-[#999]',
                      )}>
                        {rule.is_active ? 'ACTIVE' : 'DISABLED'}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 mt-0.5">
                      {rule.label && <span className="text-[10px] text-[#777]">{rule.label}</span>}
                      <span className="text-[10px] text-[#999]">by {rule.created_by}</span>
                      <span className="text-[10px] text-[#999]">{rule.created_at?.slice(0, 10)}</span>
                    </div>
                  </div>
                </div>
                <button
                  onClick={() => deleteRule(rule.id)}
                  className="p-2 text-[#DDD] hover:text-red-500 transition-colors"
                  title="Delete rule"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            ))}
          </div>
        </section>
      </div>

      {/* Add CIDR Drawer */}
      <DetailDrawer
        isOpen={showAdd}
        onClose={() => { setShowAdd(false); setSaveError(null); }}
        title="Add IP / CIDR Rule"
        subtitle="Network allowlist entry"
        icon={Globe}
        size="sm"
        footer={
          <div className="flex gap-3">
            <button onClick={() => { setShowAdd(false); setSaveError(null); }} className="btn-secondary">Cancel</button>
            <button onClick={handleAdd} disabled={saving || !newCidr.trim()} className="btn-primary">
              {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : 'Add Rule'}
            </button>
          </div>
        }
      >
        <div className="p-6 space-y-5">
          {saveError && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-xl flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-red-500 shrink-0" />
              <span className="text-xs text-red-700">{saveError}</span>
            </div>
          )}
          <div>
            <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">CIDR</label>
            <input
              value={newCidr}
              onChange={e => setNewCidr(e.target.value)}
              placeholder="10.0.0.0/24"
              className="w-full px-3 py-2 bg-white border border-[#D6C79F] rounded-xl text-sm font-mono text-[#111111] focus:outline-none focus:border-[#B88719]"
            />
          </div>
          <div>
            <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">Label</label>
            <input
              value={newLabel}
              onChange={e => setNewLabel(e.target.value)}
              placeholder="Corporate VPN – HCM"
              className="w-full px-3 py-2 bg-white border border-[#D6C79F] rounded-xl text-sm text-[#111111] focus:outline-none focus:border-[#B88719]"
            />
          </div>
        </div>
      </DetailDrawer>
    </>
  );
};
