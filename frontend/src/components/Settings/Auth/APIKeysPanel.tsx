import React, { useState } from 'react';
import {
  Key, Plus, RefreshCw, XCircle, Eye, EyeOff, Copy,
  AlertCircle, CheckCircle2, Clock, Loader2,
} from 'lucide-react';
import { cn } from '../../../lib/utils';
import { DetailDrawer } from '../../shared/DetailDrawer';
import { useAPIKeys, NewKeyResult } from '../../../lib/useAPIKeys';

const SCOPE_COLOR: Record<string, string> = {
  full_access:    'bg-[#FFF3E0] text-[#E65100] border-[#FFCC80]',
  read_only:      'bg-[#E8F5E9] text-[#2E7D32] border-[#A5D6A7]',
  admin_platform: 'bg-[#EDE7F6] text-[#4527A0] border-[#CE93D8]',
};

function fmtDate(iso: string | null) {
  if (!iso) return '—';
  return iso.slice(0, 10);
}

function fmtRelative(iso: string | null) {
  if (!iso) return '—';
  const diff = Date.now() - new Date(iso).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1)  return 'just now';
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export const APIKeysPanel = () => {
  const { keys, loading, error, createKey, revokeKey, rotateKey, refetch } = useAPIKeys();

  const [showCreate, setShowCreate]   = useState(false);
  const [newName, setNewName]         = useState('');
  const [newScope, setNewScope]       = useState('read_only');
  const [newExpiry, setNewExpiry]     = useState('');
  const [creating, setCreating]       = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const [revealed, setRevealed]       = useState<NewKeyResult | null>(null);

  const [rotateTarget, setRotateTarget] = useState<string | null>(null);
  const [rotating, setRotating]         = useState(false);
  const [rotateResult, setRotateResult] = useState<NewKeyResult | null>(null);

  const [copiedId, setCopiedId] = useState<string | null>(null);
  const [revealId, setRevealId] = useState<string | null>(null);

  const handleCreate = async () => {
    if (!newName.trim()) return;
    setCreating(true);
    setCreateError(null);
    try {
      const result = await createKey(newName.trim(), newScope, newExpiry || undefined);
      setRevealed(result);
      setShowCreate(false);
      setNewName(''); setNewScope('read_only'); setNewExpiry('');
    } catch (e) {
      setCreateError(e instanceof Error ? e.message : 'Failed to create key');
    } finally {
      setCreating(false);
    }
  };

  const handleRotate = async () => {
    if (!rotateTarget) return;
    setRotating(true);
    try {
      const result = await rotateKey(rotateTarget);
      setRotateTarget(null);
      setRotateResult(result);
    } catch (e) {
      // keep drawer open, surface error
    } finally {
      setRotating(false);
    }
  };

  const handleCopy = (text: string, id: string) => {
    navigator.clipboard.writeText(text);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 1800);
  };

  return (
    <>
      <div className="space-y-6">
        <section>
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-base font-bold text-white">API Keys</h3>
              <p className="text-xs text-slate-400 mt-0.5">
                Programmatic access keys — bcrypt-hashed, table <code className="font-mono bg-white/10 px-1 rounded text-[#D9B86C]">api_keys</code>. Raw key shown once on creation.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <button onClick={refetch} className="p-2 text-white/50 hover:text-white transition-colors" title="Refresh">
                <RefreshCw className="w-3.5 h-3.5" />
              </button>
              <button
                onClick={() => setShowCreate(true)}
                className="flex items-center gap-1.5 px-3.5 py-2 bg-white/10 border border-white/20 text-white rounded-xl text-xs font-bold hover:bg-white/15 transition-colors"
              >
                <Plus className="w-3.5 h-3.5" /> Create Key
              </button>
            </div>
          </div>

          {loading && (
            <div className="flex items-center gap-3 p-4 bg-white/5 border border-white/10 rounded-xl">
              <Loader2 className="w-4 h-4 text-white/60 animate-spin" />
              <span className="text-sm text-white/60">Loading keys…</span>
            </div>
          )}

          {error && (
            <div className="p-3 bg-red-500/10 border border-red-400/30 rounded-xl flex items-center gap-2.5">
              <AlertCircle className="w-4 h-4 text-red-400 shrink-0" />
              <span className="text-xs text-red-300">{error}</span>
            </div>
          )}

          {!loading && !error && keys.length === 0 && (
            <div className="p-8 text-center text-white/40 text-sm border border-dashed border-white/10 rounded-2xl">
              No API keys yet. Create one to get started.
            </div>
          )}

          <div className="space-y-2">
            {keys.map(key => {
              const isRevoked  = !!key.revoked_at;
              const isRevealed = revealId === key.id;
              return (
                <div
                  key={key.id}
                  className={cn(
                    'p-4 bg-white border rounded-2xl transition-all',
                    isRevoked ? 'border-red-200 opacity-60' : 'border-[#E8DFC8] hover:border-[#B88719]',
                  )}
                >
                  <div className="flex items-start justify-between gap-4">
                    <div className="flex items-center gap-3 min-w-0">
                      <div className={cn(
                        'w-8 h-8 rounded-xl flex items-center justify-center border shrink-0',
                        isRevoked ? 'bg-red-50 border-red-200 text-red-400' : 'bg-[#F4E8C3] border-[#BFA66A] text-[#B88719]',
                      )}>
                        <Key className="w-4 h-4" />
                      </div>
                      <div className="min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-sm font-bold text-[#111111]">{key.name}</span>
                          <span className={cn('text-[9px] font-bold px-1.5 py-0.5 rounded-full border', SCOPE_COLOR[key.scope] ?? 'bg-gray-100 text-gray-600 border-gray-200')}>
                            {key.scope}
                          </span>
                          {isRevoked && (
                            <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full bg-red-50 text-red-700 border border-red-200">REVOKED</span>
                          )}
                          {key.rotated_from && (
                            <span className="text-[9px] font-bold px-1.5 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200">ROTATED</span>
                          )}
                        </div>
                        <div className="flex items-center gap-3 mt-0.5 flex-wrap">
                          <code className="text-[10px] font-mono text-[#777]">
                            {isRevealed ? key.key_prefix + 'xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx' : key.key_prefix + '...'}
                          </code>
                          <span className="text-[10px] text-[#999]">by {key.created_by}</span>
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center gap-2 shrink-0">
                      <div className="text-right hidden sm:block">
                        <div className="text-[10px] text-[#777]">Last used</div>
                        <div className="text-xs font-bold text-[#111111]">{fmtRelative(key.last_used_at)}</div>
                      </div>
                      {!isRevoked && (
                        <>
                          <button onClick={() => setRevealId(isRevealed ? null : key.id)} className="p-2 text-[#777] hover:text-[#111111] transition-colors">
                            {isRevealed ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                          </button>
                          <button onClick={() => handleCopy(key.key_prefix, key.id)} className="p-2 text-[#777] hover:text-[#B88719] transition-colors">
                            {copiedId === key.id ? <CheckCircle2 className="w-4 h-4 text-emerald-500" /> : <Copy className="w-4 h-4" />}
                          </button>
                          <button onClick={() => setRotateTarget(key.id)} className="p-2 text-[#777] hover:text-[#B88719] transition-colors" title="Rotate key">
                            <RefreshCw className="w-4 h-4" />
                          </button>
                          <button onClick={() => revokeKey(key.id)} className="p-2 text-[#777] hover:text-red-500 transition-colors" title="Revoke">
                            <XCircle className="w-4 h-4" />
                          </button>
                        </>
                      )}
                      {isRevoked && <span className="text-[10px] text-red-400 font-mono">{fmtDate(key.revoked_at)}</span>}
                    </div>
                  </div>

                  {!isRevoked && (
                    <div className="mt-3 pt-3 border-t border-[#F0E8D4] flex items-center gap-4">
                      <div className="flex items-center gap-1.5">
                        <Clock className="w-3 h-3 text-[#999]" />
                        <span className="text-[10px] text-[#777]">Expires {fmtDate(key.expires_at)}</span>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          <div className="mt-3 p-3 bg-[#F8F5EC] border border-[#E8DFC8] rounded-xl">
            <p className="text-[11px] text-[#777] leading-relaxed">
              <strong className="text-[#111111]">Security:</strong> Raw API keys are never stored — only <code className="font-mono bg-[#F4E8C3] px-1 rounded">bcrypt(key)</code> hash is saved in DB.
              The <code className="font-mono bg-[#F4E8C3] px-1 rounded">key_prefix</code> is displayed for identification only.
              Rotation creates a new key and links via <code className="font-mono bg-[#F4E8C3] px-1 rounded">rotated_from</code> for audit chain.
            </p>
          </div>
        </section>
      </div>

      {/* Create Key Drawer */}
      <DetailDrawer
        isOpen={showCreate}
        onClose={() => { setShowCreate(false); setCreateError(null); }}
        title="Create API Key"
        subtitle="Programmatic access credential"
        icon={Key}
        size="sm"
        footer={
          <div className="flex gap-3">
            <button onClick={() => { setShowCreate(false); setCreateError(null); }} className="btn-secondary">Cancel</button>
            <button onClick={handleCreate} disabled={creating || !newName.trim()} className="btn-primary">
              {creating ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : 'Generate Key'}
            </button>
          </div>
        }
      >
        <div className="p-6 space-y-5">
          {createError && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-xl flex items-center gap-2">
              <AlertCircle className="w-4 h-4 text-red-500 shrink-0" />
              <span className="text-xs text-red-700">{createError}</span>
            </div>
          )}
          <div>
            <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">Key Name</label>
            <input value={newName} onChange={e => setNewName(e.target.value)} placeholder="CI/CD Pipeline Key"
              className="w-full px-3 py-2 bg-white border border-[#D6C79F] rounded-xl text-sm text-[#111111] focus:outline-none focus:border-[#B88719]" />
          </div>
          <div>
            <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">Scope</label>
            <select value={newScope} onChange={e => setNewScope(e.target.value)}
              className="w-full px-3 py-2 bg-white border border-[#D6C79F] rounded-xl text-sm text-[#111111] focus:outline-none focus:border-[#B88719]">
              <option value="read_only">read_only</option>
              <option value="full_access">full_access</option>
              <option value="admin_platform">admin_platform</option>
            </select>
          </div>
          <div>
            <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">Expiry Date</label>
            <input type="date" value={newExpiry} onChange={e => setNewExpiry(e.target.value)}
              className="w-full px-3 py-2 bg-white border border-[#D6C79F] rounded-xl text-sm text-[#111111] focus:outline-none focus:border-[#B88719]" />
          </div>
          <div className="p-3 bg-amber-50 border border-amber-200 rounded-xl flex items-start gap-2.5">
            <AlertCircle className="w-4 h-4 text-amber-600 shrink-0 mt-0.5" />
            <p className="text-[11px] text-amber-700">The raw API key is shown only once after creation. Copy and store it securely.</p>
          </div>
        </div>
      </DetailDrawer>

      {/* Raw key reveal after creation */}
      <DetailDrawer
        isOpen={!!revealed}
        onClose={() => setRevealed(null)}
        title="API Key Created"
        subtitle="Copy the key now — it will never be shown again"
        icon={Key}
        size="sm"
        footer={
          <button onClick={() => setRevealed(null)} className="btn-primary w-full">Done</button>
        }
      >
        {revealed && (
          <div className="p-6 space-y-4">
            <div className="p-3 bg-emerald-50 border border-emerald-200 rounded-xl flex items-center gap-2.5">
              <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
              <p className="text-[11px] text-emerald-700">Key <strong>{revealed.name}</strong> created successfully.</p>
            </div>
            <div>
              <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">Raw Key (copy now)</label>
              <div className="flex items-center gap-2">
                <code className="flex-1 px-3 py-2 bg-[#F8F5EC] border border-[#D6C79F] rounded-xl text-xs font-mono text-[#111111] break-all">
                  {revealed.raw_key}
                </code>
                <button
                  onClick={() => handleCopy(revealed.raw_key, 'raw')}
                  className="p-2 text-[#777] hover:text-[#B88719] transition-colors shrink-0"
                >
                  {copiedId === 'raw' ? <CheckCircle2 className="w-4 h-4 text-emerald-500" /> : <Copy className="w-4 h-4" />}
                </button>
              </div>
            </div>
          </div>
        )}
      </DetailDrawer>

      {/* Rotate Confirm Drawer */}
      <DetailDrawer
        isOpen={!!rotateTarget}
        onClose={() => setRotateTarget(null)}
        title="Rotate API Key"
        subtitle="Creates a new key and links it via rotated_from"
        icon={RefreshCw}
        size="sm"
        footer={
          <div className="flex gap-3">
            <button onClick={() => setRotateTarget(null)} className="btn-secondary">Cancel</button>
            <button onClick={handleRotate} disabled={rotating} className="btn-primary">
              {rotating ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : 'Confirm Rotation'}
            </button>
          </div>
        }
      >
        <div className="p-6">
          <div className="p-4 bg-amber-50 border border-amber-200 rounded-xl flex items-start gap-3">
            <AlertCircle className="w-5 h-5 text-amber-600 shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-bold text-amber-800 mb-1">Impact</p>
              <p className="text-[11px] text-amber-700 leading-relaxed">
                A new key will be generated and the old key revoked immediately.
                The rotation chain is preserved via <code className="font-mono bg-amber-100 px-1 rounded">rotated_from</code> FK for audit purposes.
              </p>
            </div>
          </div>
        </div>
      </DetailDrawer>

      {/* Raw key reveal after rotation */}
      <DetailDrawer
        isOpen={!!rotateResult}
        onClose={() => setRotateResult(null)}
        title="Key Rotated"
        subtitle="New key generated — copy it now"
        icon={RefreshCw}
        size="sm"
        footer={<button onClick={() => setRotateResult(null)} className="btn-primary w-full">Done</button>}
      >
        {rotateResult && (
          <div className="p-6 space-y-4">
            <div className="p-3 bg-emerald-50 border border-emerald-200 rounded-xl flex items-center gap-2.5">
              <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
              <p className="text-[11px] text-emerald-700">Key <strong>{rotateResult.name}</strong> rotated successfully.</p>
            </div>
            <div>
              <label className="block text-[10px] font-bold text-[#5A5A5A] uppercase tracking-wider mb-1.5">New Raw Key (copy now)</label>
              <div className="flex items-center gap-2">
                <code className="flex-1 px-3 py-2 bg-[#F8F5EC] border border-[#D6C79F] rounded-xl text-xs font-mono text-[#111111] break-all">
                  {rotateResult.raw_key}
                </code>
                <button
                  onClick={() => handleCopy(rotateResult.raw_key, 'rotated')}
                  className="p-2 text-[#777] hover:text-[#B88719] transition-colors shrink-0"
                >
                  {copiedId === 'rotated' ? <CheckCircle2 className="w-4 h-4 text-emerald-500" /> : <Copy className="w-4 h-4" />}
                </button>
              </div>
            </div>
          </div>
        )}
      </DetailDrawer>
    </>
  );
};
