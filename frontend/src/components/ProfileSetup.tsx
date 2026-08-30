import React, { useEffect, useState } from 'react';
import { Building2, Shield, Loader2, AlertCircle } from 'lucide-react';
import keycloak from '../lib/keycloak';
import { fetchAvailableRoles, fetchAvailableTenants, TenantOption } from '../lib/useMicrosoftApprovals';

interface Props {
  email?: string;
  onSubmit: (role: string, tenantId: string) => Promise<void>;
}

const ROLE_LABELS: Record<string, string> = {
  'ai-engineer':       'AI Engineer',
  'executive-viewer':  'Executive Viewer',
  'tenant-admin':      'Tenant Admin',
  'release-manager':   'Release Manager',
  'data-analyst':      'Data Analyst',
};

export const ProfileSetup = ({ email, onSubmit }: Props) => {
  const [roles, setRoles]         = useState<string[]>([]);
  const [tenants, setTenants]     = useState<TenantOption[]>([]);
  const [role, setRole]           = useState('');
  const [tenantId, setTenantId]   = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError]         = useState<string | null>(null);

  useEffect(() => {
    fetchAvailableRoles().then(r => {
      setRoles(r);
      if (r.length > 0) setRole(r[0]);
    });
    fetchAvailableTenants().then(t => {
      setTenants(t);
      if (t.length > 0) setTenantId(t[0].id);
    });
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!role || !tenantId.trim()) return;
    setError(null);
    setSubmitting(true);
    try {
      await onSubmit(role, tenantId.trim());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Submission failed');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="h-screen flex items-center justify-center bg-[#FAF6ED]">
      <div className="w-full max-w-md bg-white border border-[#E8DFC8] rounded-2xl shadow-sm p-8 space-y-6">
        {/* Header */}
        <div className="text-center space-y-2">
          <div className="w-12 h-12 rounded-2xl bg-[#F0F4FF] border border-[#C7D5F8] flex items-center justify-center mx-auto">
            <Shield className="w-6 h-6 text-[#4A6CF7]" />
          </div>
          <h1 className="text-lg font-bold text-[#111]">Complete your profile</h1>
          <p className="text-sm text-[#666]">
            Select your role and team to request access. An admin will review your request.
          </p>
          {email && (
            <p className="text-xs font-mono text-[#888] bg-[#F5F5F5] px-3 py-1 rounded-full inline-block">
              {email}
            </p>
          )}
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Role */}
          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-[#444] uppercase tracking-wide">
              Role
            </label>
            <div className="grid grid-cols-1 gap-2">
              {roles.map(r => (
                <label
                  key={r}
                  className={`flex items-center gap-3 px-4 py-3 rounded-xl border cursor-pointer transition-all ${
                    role === r
                      ? 'border-[#B88719] bg-[#FFFBF0]'
                      : 'border-[#E0D8CC] bg-white hover:border-[#C9A84C]'
                  }`}
                >
                  <input
                    type="radio"
                    name="role"
                    value={r}
                    checked={role === r}
                    onChange={() => setRole(r)}
                    className="accent-[#B88719]"
                  />
                  <span className="text-sm font-medium text-[#222]">
                    {ROLE_LABELS[r] ?? r}
                  </span>
                </label>
              ))}
            </div>
          </div>

          {/* Tenant */}
          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-[#444] uppercase tracking-wide flex items-center gap-1.5">
              <Building2 className="w-3.5 h-3.5" />
              Organization / Tenant
            </label>
            {tenants.length > 0 ? (
              <select
                value={tenantId}
                onChange={e => setTenantId(e.target.value)}
                className="w-full px-4 py-2.5 rounded-xl border border-[#E0D8CC] bg-white text-sm text-[#222] focus:outline-none focus:border-[#B88719] focus:ring-1 focus:ring-[#B88719]"
                required
              >
                {tenants.map(t => (
                  <option key={t.id} value={t.id}>{t.name}</option>
                ))}
              </select>
            ) : (
              <div className="flex items-center gap-2 px-4 py-2.5 rounded-xl border border-[#E0D8CC] bg-[#F9F9F9] text-sm text-[#888]">
                <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading tenants…
              </div>
            )}
          </div>

          {error && (
            <div className="flex items-center gap-2 px-3 py-2.5 rounded-xl bg-red-50 border border-red-200 text-sm text-red-700">
              <AlertCircle className="w-4 h-4 flex-shrink-0" />
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={submitting || !role || !tenantId.trim()}
            className="w-full py-2.5 rounded-xl bg-[#111] hover:bg-[#333] disabled:opacity-50 text-white text-sm font-semibold transition-colors flex items-center justify-center gap-2"
          >
            {submitting && <Loader2 className="w-4 h-4 animate-spin" />}
            Request Access
          </button>
        </form>

        <div className="text-center">
          <button
            onClick={() => keycloak.logout({ redirectUri: window.location.origin })}
            className="text-xs text-[#888] hover:text-[#333] underline"
          >
            Sign out
          </button>
        </div>
      </div>
    </div>
  );
};
