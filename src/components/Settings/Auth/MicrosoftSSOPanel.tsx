import React, { useState } from 'react';
import {
  CheckCircle2, XCircle, Clock, RefreshCw, User,
  ShieldCheck, AlertCircle, ChevronDown, ChevronRight,
} from 'lucide-react';
import { cn } from '../../../lib/utils';
import { useAuth } from '../../../lib/AuthProvider';
import { useMicrosoftApprovals, MsApproval, ApprovalStatus } from '../../../lib/useMicrosoftApprovals';

const MS_ICON = () => (
  <svg viewBox="0 0 21 21" className="w-4 h-4" fill="none">
    <rect x="1"  y="1"  width="9" height="9" fill="#F25022" />
    <rect x="11" y="1"  width="9" height="9" fill="#7FBA00" />
    <rect x="1"  y="11" width="9" height="9" fill="#00A4EF" />
    <rect x="11" y="11" width="9" height="9" fill="#FFB900" />
  </svg>
);

const STATUS_CONFIG: Record<ApprovalStatus | 'all', { label: string; color: string }> = {
  pending:  { label: 'Pending',  color: 'text-amber-600 bg-amber-50 border-amber-200' },
  approved: { label: 'Approved', color: 'text-emerald-600 bg-emerald-50 border-emerald-200' },
  rejected: { label: 'Rejected', color: 'text-red-600 bg-red-50 border-red-200' },
  all:      { label: 'All',      color: 'text-[#5A5A5A] bg-[#F5F5F5] border-[#DDD]' },
};

function StatusChip({ status }: { status: ApprovalStatus }) {
  const cfg = STATUS_CONFIG[status];
  return (
    <span className={cn('px-2 py-0.5 rounded-full border text-xs font-semibold', cfg.color)}>
      {cfg.label}
    </span>
  );
}

function ApprovalRow({
  item,
  onApprove,
  onReject,
  busy,
}: {
  item: MsApproval;
  onApprove: (id: string) => Promise<void>;
  onReject:  (id: string) => Promise<void>;
  busy: string | null;
}) {
  const [expanded, setExpanded]   = useState(false);
  const [localErr, setLocalErr]   = useState<string | null>(null);

  const handle = async (fn: (id: string) => Promise<void>) => {
    setLocalErr(null);
    try { await fn(item.keycloak_user_id); }
    catch (e) { setLocalErr(e instanceof Error ? e.message : 'Error'); }
  };

  const isBusy = busy === item.keycloak_user_id;

  return (
    <div className="border border-[#E8DFC8] rounded-xl overflow-hidden">
      <div
        className="flex items-center gap-3 px-4 py-3 bg-white cursor-pointer hover:bg-[#FDFAF2] transition-colors"
        onClick={() => setExpanded(v => !v)}
      >
        <div className="w-8 h-8 rounded-full bg-[#EEE8DB] flex items-center justify-center flex-shrink-0">
          <User className="w-4 h-4 text-[#888]" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-[#111] truncate">
            {item.display_name || item.email}
          </p>
          <p className="text-xs text-[#777] truncate">{item.email}</p>
        </div>
        <StatusChip status={item.status} />
        {expanded ? <ChevronDown className="w-4 h-4 text-[#AAA]" /> : <ChevronRight className="w-4 h-4 text-[#AAA]" />}
      </div>

      {expanded && (
        <div className="px-4 py-3 bg-[#FDFAF2] border-t border-[#EDE6D6] space-y-3">
          <div className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs">
            <span className="text-[#888]">Keycloak ID</span>
            <span className="text-[#333] font-mono truncate">{item.keycloak_user_id}</span>
            {item.requested_role && (
              <>
                <span className="text-[#888]">Requested role</span>
                <span className="text-[#333] font-semibold">{item.requested_role}</span>
              </>
            )}
            {item.requested_tenant_id && (
              <>
                <span className="text-[#888]">Tenant</span>
                <span className="text-[#333] font-semibold">{item.requested_tenant_id}</span>
              </>
            )}
            <span className="text-[#888]">Requested</span>
            <span className="text-[#333]">
              {new Date(item.created_at).toLocaleString('en-GB', {
                day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
              })}
            </span>
            {item.reviewed_by && (
              <>
                <span className="text-[#888]">Reviewed by</span>
                <span className="text-[#333] font-mono truncate">{item.reviewed_by}</span>
              </>
            )}
          </div>

          {localErr && (
            <p className="text-xs text-red-600 flex items-center gap-1">
              <AlertCircle className="w-3 h-3" /> {localErr}
            </p>
          )}

          {item.status === 'pending' && (
            <div className="flex gap-2">
              <button
                disabled={isBusy}
                onClick={e => { e.stopPropagation(); handle(onApprove); }}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-emerald-600 hover:bg-emerald-700 disabled:opacity-50 text-white text-xs font-semibold rounded-lg transition-colors"
              >
                {isBusy ? <RefreshCw className="w-3 h-3 animate-spin" /> : <CheckCircle2 className="w-3 h-3" />}
                Approve
              </button>
              <button
                disabled={isBusy}
                onClick={e => { e.stopPropagation(); handle(onReject); }}
                className="flex items-center gap-1.5 px-3 py-1.5 bg-white hover:bg-red-50 disabled:opacity-50 border border-red-300 text-red-600 text-xs font-semibold rounded-lg transition-colors"
              >
                <XCircle className="w-3 h-3" />
                Reject
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

type FilterTab = ApprovalStatus | 'all';
const FILTER_TABS: FilterTab[] = ['pending', 'approved', 'rejected', 'all'];

export const MicrosoftSSOPanel = () => {
  const { user }                        = useAuth();
  const isAdmin                         = user?.roles.includes('platform-admin') ?? false;
  const [filter, setFilter]             = useState<FilterTab>('pending');
  const [busy, setBusy]                 = useState<string | null>(null);
  const { approvals, loading, error, refetch, approve, reject } =
    useMicrosoftApprovals(isAdmin, filter);

  const handleApprove = async (id: string) => {
    setBusy(id);
    try {
      await approve(id);
      setFilter('all');
      window.dispatchEvent(new Event('sso-approval-changed'));
    } finally { setBusy(null); }
  };
  const handleReject  = async (id: string) => {
    setBusy(id);
    try {
      await reject(id);
      setFilter('all');
      window.dispatchEvent(new Event('sso-approval-changed'));
    } finally { setBusy(null); }
  };

  const pendingCount = approvals.filter(a => a.status === 'pending').length;

  return (
    <div className="space-y-6">
      {/* Header card */}
      <div className="bg-white border border-[#E8DFC8] rounded-2xl p-5 space-y-4">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-[#F0F4FF] border border-[#C7D5F8] flex items-center justify-center">
            <MS_ICON />
          </div>
          <div>
            <p className="text-sm font-bold text-[#111]">Microsoft SSO</p>
            <p className="text-xs text-[#777]">Azure AD / Microsoft Entra ID via Keycloak OIDC IDP</p>
          </div>
          <div className="ml-auto flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-emerald-50 border border-emerald-200">
            <ShieldCheck className="w-3.5 h-3.5 text-emerald-600" />
            <span className="text-xs font-semibold text-emerald-700">Configured</span>
          </div>
        </div>

        <div className="grid grid-cols-3 gap-3 text-xs">
          {[
            { label: 'Protocol',       value: 'OIDC / PKCE S256' },
            { label: 'Alias',          value: 'microsoft' },
            { label: 'First login',    value: 'Admin approval required' },
          ].map(({ label, value }) => (
            <div key={label} className="px-3 py-2 rounded-lg bg-[#FDFAF2] border border-[#EDE6D6]">
              <p className="text-[#888] mb-0.5">{label}</p>
              <p className="font-semibold text-[#333]">{value}</p>
            </div>
          ))}
        </div>

        <div className="flex items-start gap-2 px-3 py-2.5 rounded-xl bg-[#FFF8E1] border border-[#FFE082] text-xs text-[#7A5C00]">
          <Clock className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
          <span>
            New users logging in via Microsoft SSO are automatically held for admin review.
            Approved users can sign in freely on subsequent logins.
          </span>
        </div>
      </div>

      {/* Approval queue — admin only */}
      {isAdmin ? (
        <div className="bg-white border border-[#E8DFC8] rounded-2xl overflow-hidden">
          {/* Sub-header */}
          <div className="flex items-center justify-between px-5 py-4 border-b border-[#EDE6D6]">
            <div className="flex items-center gap-2">
              <Clock className="w-4 h-4 text-[#B88719]" />
              <span className="text-sm font-bold text-[#111]">Approval Queue</span>
              {pendingCount > 0 && filter !== 'pending' && (
                <span className="px-2 py-0.5 rounded-full bg-amber-100 border border-amber-200 text-xs font-bold text-amber-700">
                  {pendingCount} pending
                </span>
              )}
            </div>
            <button
              onClick={refetch}
              disabled={loading}
              className="p-1.5 rounded-lg hover:bg-[#F5F0E8] transition-colors disabled:opacity-50"
            >
              <RefreshCw className={cn('w-4 h-4 text-[#888]', loading && 'animate-spin')} />
            </button>
          </div>

          {/* Filter tabs */}
          <div className="flex gap-1.5 px-5 py-3 border-b border-[#EDE6D6] bg-[#FDFAF2]">
            {FILTER_TABS.map(f => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={cn(
                  'px-3 py-1 rounded-lg text-xs font-semibold border transition-all',
                  filter === f
                    ? 'bg-[#111] text-white border-[#111]'
                    : 'bg-white text-[#555] border-[#D6C79F] hover:border-[#B88719]',
                )}
              >
                {STATUS_CONFIG[f].label}
              </button>
            ))}
          </div>

          {/* List */}
          <div className="p-5 space-y-2">
            {loading && (
              <div className="flex items-center justify-center py-8">
                <RefreshCw className="w-5 h-5 text-[#B88719] animate-spin" />
              </div>
            )}
            {error && (
              <div className="flex items-center gap-2 px-4 py-3 rounded-xl bg-red-50 border border-red-200 text-sm text-red-700">
                <AlertCircle className="w-4 h-4" /> {error}
              </div>
            )}
            {!loading && !error && approvals.length === 0 && (
              <div className="text-center py-10 text-sm text-[#888]">
                No {filter === 'all' ? '' : filter} approval requests.
              </div>
            )}
            {approvals.map(item => (
              <ApprovalRow
                key={item.keycloak_user_id}
                item={item}
                onApprove={handleApprove}
                onReject={handleReject}
                busy={busy}
              />
            ))}
          </div>
        </div>
      ) : (
        <div className="px-4 py-3 rounded-xl bg-[#F5F5F5] border border-[#DDD] text-sm text-[#777]">
          Only platform administrators can manage Microsoft SSO approvals.
        </div>
      )}
    </div>
  );
};
