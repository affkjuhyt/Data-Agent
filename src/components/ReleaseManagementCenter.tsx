import React, { useState, useEffect, useCallback } from 'react';
import { motion } from 'motion/react';
import {
  Rocket, History, RotateCcw, CheckCircle2, XCircle,
  Clock, Search, AlertTriangle, Activity, RefreshCw,
  Loader2, GitBranch, GitCommit, ChevronDown, ChevronUp,
  Plus,
} from 'lucide-react';
import { cn } from '../lib/utils';
import { useAuth } from '../lib/AuthProvider';
import {
  fetchPipelines, fetchHistory, fetchRollbackTargets,
  fetchPipelineDetail, triggerPipeline,
  triggerRollback, approveDeployment,
  DeploymentUI, HistoryUI, RollbackTargetUI, PipelineRaw,
  PipelineStepUI, TriggerPipelineParams,
} from '../lib/releaseApi';

type Tab = 'DEPLOYMENTS' | 'HISTORY' | 'ROLLBACK';

/* ─── Status display maps ────────────────────────────────────────────── */

const HISTORY_STATUS: Record<string, { color: string; bg: string; icon: React.ElementType }> = {
  SUCCESS:     { color: 'text-emerald-700', bg: 'bg-emerald-50 border-emerald-200', icon: CheckCircle2 },
  FAILED:      { color: 'text-red-700',     bg: 'bg-red-50 border-red-200',         icon: XCircle      },
  ROLLED_BACK: { color: 'text-blue-700',    bg: 'bg-blue-50 border-blue-200',       icon: RotateCcw    },
};

const DEPLOY_STATUS: Record<string, { label: string; color: string; bg: string }> = {
  SUCCESS:          { label: 'Success',     color: 'text-emerald-700', bg: 'bg-emerald-50 border-emerald-200'  },
  FAILED:           { label: 'Failed',      color: 'text-red-700',     bg: 'bg-red-50 border-red-200'          },
  VALIDATING:       { label: 'Scanning',    color: 'text-amber-700',   bg: 'bg-amber-50 border-amber-200'      },
  PROMOTING:        { label: 'Promoting',   color: 'text-amber-700',   bg: 'bg-amber-50 border-amber-200'      },
  BUILDING:         { label: 'Building',    color: 'text-blue-700',    bg: 'bg-blue-50 border-blue-200'        },
  WAITING_APPROVAL: { label: 'Approval',    color: 'text-purple-700',  bg: 'bg-purple-50 border-purple-200'    },
  ROLLED_BACK:      { label: 'Rolled Back', color: 'text-slate-600',   bg: 'bg-slate-50 border-slate-200'      },
  QUEUED:           { label: 'Queued',      color: 'text-slate-600',   bg: 'bg-slate-50 border-slate-200'      },
};

/* ─── Feature 4: trigger_type badge map ─────────────────────────────── */
const TRIGGER_TYPE: Record<string, { label: string; icon: React.ElementType }> = {
  GIT_PUSH:  { label: 'Push',      icon: GitBranch },
  MANUAL:    { label: 'Manual',    icon: Activity  },
  SCHEDULED: { label: 'Scheduled', icon: Clock     },
};

/* ─── Feature 2: step status map ────────────────────────────────────── */
const STEP_STATUS: Record<string, { color: string; bg: string; border: string }> = {
  SUCCESS:     { color: 'text-emerald-700', bg: 'bg-emerald-50', border: 'border-emerald-200' },
  FAILED:      { color: 'text-red-700',     bg: 'bg-red-50',     border: 'border-red-200'     },
  RUNNING:     { color: 'text-amber-700',   bg: 'bg-amber-50',   border: 'border-amber-200'   },
  IN_PROGRESS: { color: 'text-amber-700',   bg: 'bg-amber-50',   border: 'border-amber-200'   },
  SKIPPED:     { color: 'text-slate-500',   bg: 'bg-slate-50',   border: 'border-slate-200'   },
};
const STEP_STATUS_DEFAULT = { color: 'text-slate-500', bg: 'bg-slate-50', border: 'border-slate-200' };

/* ─── Feature 3: filter constants ───────────────────────────────────── */
const DEPLOYMENTS_ENVS    = ['ALL', 'DEV', 'STAGING', 'UAT', 'PROD'] as const;
const DEPLOYMENTS_STATUSES = [
  { key: 'ALL',              label: 'All'      },
  { key: 'BUILDING',         label: 'Running'  },
  { key: 'WAITING_APPROVAL', label: 'Approval' },
  { key: 'FAILED',           label: 'Failed'   },
] as const;

/* ─── useReleaseData hook ────────────────────────────────────────────── */

function useReleaseData() {
  const { user } = useAuth();
  const [deployments,      setDeployments]      = useState<DeploymentUI[]>([]);
  const [rawPipelines,     setRawPipelines]      = useState<PipelineRaw[]>([]);
  const [history,          setHistory]           = useState<HistoryUI[]>([]);
  const [rollbackTargets,  setRollbackTargets]   = useState<RollbackTargetUI[]>([]);
  const [loading,          setLoading]           = useState(true);
  const [error,            setError]             = useState<string | null>(null);
  const [lastRefreshed,    setLastRefreshed]      = useState<Date | null>(null);

  const load = useCallback(async () => {
    if (!user?.token) return;
    setLoading(true);
    setError(null);
    try {
      const { deployments: deps, raw } = await fetchPipelines(user.token);
      setDeployments(deps);
      setRawPipelines(raw);

      const [hist, targets] = await Promise.all([
        fetchHistory(user.token, raw),
        fetchRollbackTargets(user.token),
      ]);
      setHistory(hist);
      setRollbackTargets(targets);
      setLastRefreshed(new Date());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load release data');
    } finally {
      setLoading(false);
    }
  }, [user?.token]);

  useEffect(() => { load(); }, [load]);

  return { deployments, rawPipelines, history, rollbackTargets, loading, error, reload: load, lastRefreshed };
}

/* ─── Component ─────────────────────────────────────────────────────── */

const ReleaseManagementCenter = () => {
  const { user }              = useAuth();
  const {
    deployments, rawPipelines, history, rollbackTargets,
    loading, error, reload, lastRefreshed,
  } = useReleaseData();

  /* ── Existing state ── */
  const [search,      setSearch]      = useState('');
  const [confirmId,   setConfirmId]   = useState<string | null>(null);
  const [actionMsg,   setActionMsg]   = useState<string | null>(null);
  const [expandedRow, setExpandedRow] = useState<string | null>(null);

  /* ── Feature 1: trigger form state ── */
  const [showTriggerForm,   setShowTriggerForm]   = useState(false);
  const [triggerFormData,   setTriggerFormData]   = useState<TriggerPipelineParams & { pipelineName: string; branch: string }>({
    packageVersion:     '',
    pipelineName:       '',
    branch:             '',
    targetEnvironments: [],
  });
  const [triggerSubmitting, setTriggerSubmitting] = useState(false);
  const [triggerError,      setTriggerError]      = useState<string | null>(null);

  /* ── Feature 2: pipeline steps cache ── */
  const [pipelineSteps, setPipelineSteps] = useState<Record<string, PipelineStepUI[] | 'loading' | 'error'>>({});

  /* ── Feature 3: filter state ── */
  const [envFilter,    setEnvFilter]    = useState<string>('ALL');
  const [statusFilter, setStatusFilter] = useState<string>('ALL');

  /* ── Tab helpers (local — independent of the module nav) ── */
  const [activeTab, _setActiveTab] = useState<Tab>('DEPLOYMENTS');
  const setActiveTab = (id: Tab) => {
    _setActiveTab(id);
    if (id !== 'DEPLOYMENTS') {
      setEnvFilter('ALL');
      setStatusFilter('ALL');
    }
  };

  const tabs = [
    { id: 'DEPLOYMENTS', label: 'Deployments',    icon: Rocket    },
    { id: 'HISTORY',     label: 'Release History', icon: History   },
    { id: 'ROLLBACK',    label: 'Rollback Center', icon: RotateCcw },
  ];

  /* ── Derived state ── */
  const activeCount   = deployments.filter(d =>
    ['BUILDING', 'VALIDATING', 'PROMOTING', 'WAITING_APPROVAL'].includes(d.status)
  ).length;
  const successRate   = deployments.length
    ? Math.round(deployments.filter(d => d.status === 'SUCCESS').length / deployments.length * 100)
    : 0;
  const rollbackCount = history.filter(h => h.status === 'ROLLED_BACK').length;
  const lastDeploy    = deployments.find(d => d.status === 'SUCCESS')?.startedAt ?? '--';

  const filteredHistory = history.filter(r =>
    r.name.toLowerCase().includes(search.toLowerCase()) ||
    r.id.toLowerCase().includes(search.toLowerCase())
  );

  /* ── Feature 3: filtered deployments ── */
  const filteredDeployments = deployments.filter(d => {
    const envMatch    = envFilter === 'ALL'    || d.env === envFilter;
    const statusMatch = statusFilter === 'ALL' || d.status === statusFilter;
    return envMatch && statusMatch;
  });

  /* ── Handlers ── */

  const handleApprove = async (dep: DeploymentUI, decision: 'APPROVED' | 'REJECTED') => {
    if (!user?.token) return;
    const rawPipe = rawPipelines.find(p => p.id === dep.id);
    const env = rawPipe?.target_env ?? 'staging';
    try {
      await approveDeployment(user.token, dep.id, env, decision);
      setActionMsg(`${decision === 'APPROVED' ? '✅ Approved' : '❌ Rejected'}: ${dep.name}`);
      setTimeout(() => setActionMsg(null), 4000);
      reload();
    } catch (e) {
      setActionMsg(`Error: ${e instanceof Error ? e.message : 'unknown'}`);
      setTimeout(() => setActionMsg(null), 6000);
    }
    setConfirmId(null);
  };

  const handleRollback = async (target: RollbackTargetUI) => {
    if (!user?.token) return;
    try {
      const { rollback_id } = await triggerRollback(
        user.token, target.current, target.previous,
        target.env.toLowerCase(),
        `Manual rollback from UI — ${target.name}`,
      );
      setActionMsg(`🔄 Rollback initiated: ${rollback_id}`);
      setTimeout(() => setActionMsg(null), 6000);
      reload();
    } catch (e) {
      setActionMsg(`Rollback error: ${e instanceof Error ? e.message : 'unknown'}`);
      setTimeout(() => setActionMsg(null), 6000);
    }
    setConfirmId(null);
  };

  /* ── Feature 1: trigger handler ── */
  const handleTrigger = async () => {
    if (!user?.token) return;
    const { packageVersion, pipelineName, branch, targetEnvironments } = triggerFormData;
    if (!packageVersion.trim()) {
      setTriggerError('Package version is required.');
      return;
    }
    if (targetEnvironments.length === 0) {
      setTriggerError('Select at least one target environment.');
      return;
    }
    setTriggerSubmitting(true);
    setTriggerError(null);
    try {
      const { pipeline_id } = await triggerPipeline(user.token, {
        packageVersion:     packageVersion.trim(),
        pipelineName:       pipelineName.trim() || undefined,
        branch:             branch.trim() || undefined,
        targetEnvironments,
      });
      setActionMsg(`🚀 Pipeline triggered: ${pipeline_id}`);
      setTimeout(() => setActionMsg(null), 6000);
      setShowTriggerForm(false);
      setTriggerFormData({ packageVersion: '', pipelineName: '', branch: '', targetEnvironments: [] });
      reload();
    } catch (e) {
      setTriggerError(e instanceof Error ? e.message : 'Trigger failed');
    } finally {
      setTriggerSubmitting(false);
    }
  };

  /* ── Feature 2: lazy-load steps on expand ── */
  useEffect(() => {
    if (!expandedRow || !user?.token) return;
    if (pipelineSteps[expandedRow] !== undefined) return;
    const id    = expandedRow;
    const token = user.token;
    setPipelineSteps(prev => ({ ...prev, [id]: 'loading' }));
    fetchPipelineDetail(token, id)
      .then(steps => setPipelineSteps(prev => ({ ...prev, [id]: steps })))
      .catch(()   => setPipelineSteps(prev => ({ ...prev, [id]: 'error'  })));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [expandedRow, user?.token]);

  /* ── Render ── */

  if (loading && deployments.length === 0) {
    return (
      <div className="flex items-center justify-center h-64 gap-3 text-[#777]">
        <Loader2 className="w-5 h-5 animate-spin text-[#B88719]" />
        <span className="text-sm font-mono">Loading release data…</span>
      </div>
    );
  }

  return (
    <div className="space-y-6">

      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2 text-[#B88719] text-[10px] font-bold font-mono tracking-widest uppercase mb-2">
            <Rocket className="w-3.5 h-3.5" />
            Control Plane · Release
          </div>
          <h1 className="text-3xl font-display font-medium tracking-tight text-[#111111]">Release Management</h1>
          <p className="text-[#5A5A5A] mt-1 text-sm">Deployment pipeline, release history, and rollback operations.</p>
        </div>
        <div className="flex items-center gap-3 mt-1">
          {lastRefreshed && (
            <span className="text-[9px] font-mono text-[#999]">
              refreshed {lastRefreshed.toLocaleTimeString()}
            </span>
          )}
          <button
            onClick={reload}
            disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 border border-[#BFA66A] rounded-lg text-[10px] font-bold uppercase text-[#5A5A5A] hover:border-[#8A5A00] hover:text-[#8A5A00] transition-all disabled:opacity-40"
          >
            <RefreshCw className={cn('w-3 h-3', loading && 'animate-spin')} />
            Refresh
          </button>
        </div>
      </div>

      {/* Error banner */}
      {error && (
        <div className="p-3 bg-red-50 border border-red-200 rounded-xl text-[11px] text-red-700 font-mono flex items-center gap-2">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          {error}
        </div>
      )}

      {/* Action feedback */}
      {actionMsg && (
        <div className="p-3 bg-emerald-50 border border-emerald-200 rounded-xl text-[11px] text-emerald-700 font-mono">
          {actionMsg}
        </div>
      )}

      {/* Summary Metrics */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          { label: 'Active Deployments', value: String(activeCount),   icon: Rocket,       color: 'text-[#B88719]'    },
          { label: 'Last Release',       value: lastDeploy,            icon: Clock,        color: 'text-blue-600'     },
          { label: 'Success Rate',       value: `${successRate}%`,     icon: CheckCircle2, color: 'text-emerald-600'  },
          { label: 'Rollbacks',          value: String(rollbackCount),  icon: RotateCcw,   color: 'text-amber-600'    },
        ].map(s => (
          <div key={s.label} className="glass-panel p-5 rounded-2xl">
            <s.icon className={cn('w-4 h-4 mb-3', s.color)} />
            <div className="text-2xl font-display font-medium text-[#111111]">{s.value}</div>
            <div className="text-[10px] text-[#5A5A5A] font-bold uppercase tracking-widest mt-1">{s.label}</div>
          </div>
        ))}
      </div>

      {/* Tab Bar */}
      <div className="sub-tab-bar">
        {tabs.map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id as Tab)}
            className={cn('sub-tab', activeTab === tab.id && 'active')}
          >
            <tab.icon className="tab-icon w-3.5 h-3.5" />
            {tab.label}
          </button>
        ))}
      </div>

      <motion.div
        key={activeTab}
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.22 }}
      >

        {/* ── DEPLOYMENTS ─────────────────────────────────── */}
        {activeTab === 'DEPLOYMENTS' && (
          <div className="glass-panel rounded-2xl overflow-hidden">

            {/* Panel header with Trigger button */}
            <div className="px-5 py-4 bg-[#FDFAF2] border-b border-[#E8DFC8] flex items-center justify-between">
              <h3 className="text-sm font-bold text-[#111111] flex items-center gap-2">
                <Activity className="w-4 h-4 text-[#B88719]" />
                Active & Recent Deployments
              </h3>
              <div className="flex items-center gap-3">
                <span className="text-[10px] font-mono text-[#777]">
                  {loading ? 'loading…' : `${filteredDeployments.filter(d => ['BUILDING','VALIDATING','PROMOTING','WAITING_APPROVAL'].includes(d.status)).length} active · ${filteredDeployments.length} shown`}
                </span>
                <button
                  onClick={() => { setShowTriggerForm(v => !v); setTriggerError(null); }}
                  className={cn(
                    'flex items-center gap-1 px-2.5 py-1 rounded-lg text-[10px] font-bold uppercase border transition-all',
                    showTriggerForm
                      ? 'bg-[#B88719] text-white border-[#8A5A00]'
                      : 'bg-white border-[#BFA66A] text-[#5A5A5A] hover:border-[#8A5A00] hover:text-[#8A5A00]',
                  )}
                >
                  <Plus className="w-3 h-3" />
                  {showTriggerForm ? 'Cancel' : 'Trigger'}
                </button>
              </div>
            </div>

            {/* Feature 1: Collapsible trigger form */}
            {showTriggerForm && (
              <div className="border-b border-[#E8DFC8] px-5 py-4 bg-[#FDFAF2] space-y-3">
                {triggerError && (
                  <div className="p-2 bg-red-50 border border-red-200 rounded-lg text-[10px] text-red-700 font-mono flex items-center gap-1.5">
                    <AlertTriangle className="w-3 h-3 shrink-0" />
                    {triggerError}
                  </div>
                )}
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                  <div>
                    <label className="block text-[9px] font-bold text-[#777] uppercase tracking-widest mb-1">
                      Package Version <span className="text-red-500">*</span>
                    </label>
                    <input
                      type="text"
                      placeholder="e.g. v2.4.1"
                      value={triggerFormData.packageVersion}
                      onChange={e => setTriggerFormData(p => ({ ...p, packageVersion: e.target.value }))}
                      className="w-full bg-white border border-[#BFA66A] px-3 py-1.5 rounded-lg text-[11px] font-mono focus:outline-none focus:border-[#8A5A00] transition-colors"
                    />
                  </div>
                  <div>
                    <label className="block text-[9px] font-bold text-[#777] uppercase tracking-widest mb-1">
                      Pipeline Name
                    </label>
                    <input
                      type="text"
                      placeholder="optional"
                      value={triggerFormData.pipelineName}
                      onChange={e => setTriggerFormData(p => ({ ...p, pipelineName: e.target.value }))}
                      className="w-full bg-white border border-[#BFA66A] px-3 py-1.5 rounded-lg text-[11px] font-mono focus:outline-none focus:border-[#8A5A00] transition-colors"
                    />
                  </div>
                  <div>
                    <label className="block text-[9px] font-bold text-[#777] uppercase tracking-widest mb-1">
                      Branch
                    </label>
                    <input
                      type="text"
                      placeholder="optional"
                      value={triggerFormData.branch}
                      onChange={e => setTriggerFormData(p => ({ ...p, branch: e.target.value }))}
                      className="w-full bg-white border border-[#BFA66A] px-3 py-1.5 rounded-lg text-[11px] font-mono focus:outline-none focus:border-[#8A5A00] transition-colors"
                    />
                  </div>
                </div>
                <div>
                  <label className="block text-[9px] font-bold text-[#777] uppercase tracking-widest mb-1.5">
                    Target Environments <span className="text-red-500">*</span>
                  </label>
                  <div className="flex flex-wrap gap-2">
                    {(['dev', 'staging', 'uat', 'prod'] as const).map(env => {
                      const isChecked = triggerFormData.targetEnvironments.includes(env);
                      return (
                        <label
                          key={env}
                          className={cn(
                            'flex items-center gap-1.5 px-3 py-1 rounded-lg border text-[10px] font-bold uppercase cursor-pointer transition-all select-none',
                            isChecked
                              ? 'bg-[#B88719] text-white border-[#8A5A00]'
                              : 'bg-white border-[#BFA66A] text-[#5A5A5A] hover:border-[#8A5A00]',
                          )}
                        >
                          <input
                            type="checkbox"
                            className="sr-only"
                            checked={isChecked}
                            onChange={() => {
                              setTriggerFormData(p => ({
                                ...p,
                                targetEnvironments: isChecked
                                  ? p.targetEnvironments.filter(e => e !== env)
                                  : [...p.targetEnvironments, env],
                              }));
                            }}
                          />
                          {env}
                        </label>
                      );
                    })}
                  </div>
                </div>
                <div className="flex justify-end">
                  <button
                    onClick={handleTrigger}
                    disabled={triggerSubmitting}
                    className="flex items-center gap-1.5 px-4 py-1.5 bg-[#B88719] hover:bg-[#8A5A00] text-white rounded-lg text-[10px] font-bold uppercase transition-all disabled:opacity-50"
                  >
                    {triggerSubmitting
                      ? <Loader2 className="w-3 h-3 animate-spin" />
                      : <Rocket className="w-3 h-3" />}
                    {triggerSubmitting ? 'Triggering…' : 'Trigger Pipeline'}
                  </button>
                </div>
              </div>
            )}

            {/* Feature 3: Filter chips */}
            <div className="px-5 py-2.5 bg-white border-b border-[#E8DFC8] flex flex-wrap gap-x-4 gap-y-2 items-center">
              <div className="flex gap-1.5 flex-wrap">
                {DEPLOYMENTS_ENVS.map(env => (
                  <button
                    key={env}
                    onClick={() => setEnvFilter(env)}
                    className={cn(
                      'px-2.5 py-0.5 rounded-full border text-[9px] font-bold uppercase transition-all',
                      envFilter === env
                        ? 'bg-[#B88719] text-white border-[#8A5A00]'
                        : 'bg-white border-[#BFA66A] text-[#5A5A5A] hover:border-[#8A5A00] hover:text-[#8A5A00]',
                    )}
                  >
                    {env}
                  </button>
                ))}
              </div>
              <div className="w-px h-4 bg-[#E8DFC8] hidden sm:block" />
              <div className="flex gap-1.5 flex-wrap">
                {DEPLOYMENTS_STATUSES.map(s => (
                  <button
                    key={s.key}
                    onClick={() => setStatusFilter(s.key)}
                    className={cn(
                      'px-2.5 py-0.5 rounded-full border text-[9px] font-bold uppercase transition-all',
                      statusFilter === s.key
                        ? 'bg-[#111111] text-white border-[#111111]'
                        : 'bg-white border-[#BFA66A] text-[#5A5A5A] hover:border-[#111111] hover:text-[#111111]',
                    )}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            </div>

            {filteredDeployments.length === 0 && !loading ? (
              <div className="p-10 text-center text-[#777] text-sm font-mono">
                {envFilter !== 'ALL' || statusFilter !== 'ALL'
                  ? 'No deployments match current filters'
                  : 'No deployments found'}
              </div>
            ) : (
              <>
                {/* Desktop */}
                <table className="hidden lg:table w-full text-left">
                  <thead className="bg-[#FDFAF2] border-b border-[#E8DFC8] text-[10px] font-bold text-[#777] uppercase tracking-[0.1em]">
                    <tr>
                      <th className="px-5 py-3">Package</th>
                      <th className="px-4 py-3">Env</th>
                      <th className="px-4 py-3">Owner</th>
                      <th className="px-4 py-3">Started</th>
                      <th className="px-4 py-3">Duration</th>
                      <th className="px-4 py-3 text-center">Risk</th>
                      <th className="px-4 py-3">Status</th>
                      <th className="px-4 py-3"></th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#F0E8D4]">
                    {filteredDeployments.map(dep => {
                      const sc = DEPLOY_STATUS[dep.status] ?? DEPLOY_STATUS['QUEUED'];
                      const isExpanded = expandedRow === dep.id;
                      const canApprove = dep.status === 'WAITING_APPROVAL';
                      const tt = TRIGGER_TYPE[dep.triggerType] ?? { label: dep.triggerType, icon: Activity };
                      const TriggerIcon = tt.icon;
                      return (
                        <React.Fragment key={dep.id}>
                          <tr
                            className={cn('hover:bg-[#FFF9E8] transition-colors cursor-pointer', isExpanded && 'bg-[#FFF9E8]')}
                            onClick={() => setExpandedRow(isExpanded ? null : dep.id)}
                          >
                            {/* Feature 4: trigger_type badge next to name */}
                            <td className="px-5 py-3.5">
                              <div className="flex items-center gap-2">
                                <div className="text-xs font-bold text-[#111111] uppercase">{dep.name}</div>
                                <div className="inline-flex items-center gap-0.5 px-1.5 py-0.5 bg-[#F4E8C3] border border-[#BFA66A] rounded text-[8px] font-bold text-[#5A5A5A] shrink-0">
                                  <TriggerIcon className="w-2.5 h-2.5" />
                                  {tt.label}
                                </div>
                              </div>
                              <div className="text-[9px] font-mono text-[#777] mt-0.5">{dep.id} · {dep.version}</div>
                            </td>
                            <td className="px-4 py-3.5">
                              <span className="px-2 py-0.5 bg-[#F4E8C3] border border-[#BFA66A] rounded text-[9px] font-bold text-[#5A5A5A]">
                                {dep.env}
                              </span>
                            </td>
                            <td className="px-4 py-3.5 text-[11px] font-mono text-[#5A5A5A]">{dep.owner}</td>
                            <td className="px-4 py-3.5 text-[10px] font-mono text-[#777]">{dep.startedAt}</td>
                            <td className="px-4 py-3.5 text-[10px] font-mono text-[#777]">{dep.duration}</td>
                            <td className="px-4 py-3.5 text-center">
                              <span className={cn('text-xs font-bold', dep.riskScore > 50 ? 'text-red-600' : dep.riskScore > 25 ? 'text-amber-600' : 'text-emerald-600')}>
                                {dep.riskScore}
                              </span>
                            </td>
                            <td className="px-4 py-3.5">
                              <div className={cn('inline-flex items-center gap-1 text-[10px] font-bold px-2 py-0.5 rounded-full border', sc.bg, sc.color)}>
                                {['BUILDING', 'VALIDATING'].includes(dep.status) && (
                                  <Loader2 className="w-2.5 h-2.5 animate-spin" />
                                )}
                                {sc.label}
                              </div>
                            </td>
                            <td className="px-4 py-3.5">
                              {isExpanded
                                ? <ChevronUp className="w-3.5 h-3.5 text-[#999]" />
                                : <ChevronDown className="w-3.5 h-3.5 text-[#999]" />}
                            </td>
                          </tr>

                          {/* Expanded detail row — branch/commit/error/approve + Feature 2: steps */}
                          {isExpanded && (
                            <tr className="bg-[#FDFAF2]">
                              <td colSpan={8} className="px-5 py-4 space-y-4">

                                {/* Existing: branch / commit / error / approve */}
                                <div className="flex flex-wrap gap-6 text-[10px] font-mono text-[#5A5A5A]">
                                  {dep.branch && (
                                    <div className="flex items-center gap-1">
                                      <GitBranch className="w-3 h-3 text-[#B88719]" />
                                      {dep.branch}
                                    </div>
                                  )}
                                  {dep.commitSha && (
                                    <div className="flex items-center gap-1">
                                      <GitCommit className="w-3 h-3 text-[#B88719]" />
                                      {dep.commitSha}
                                    </div>
                                  )}
                                  {dep.errorMessage && (
                                    <div className="text-red-600 flex items-center gap-1 flex-1">
                                      <AlertTriangle className="w-3 h-3 shrink-0" />
                                      {dep.errorMessage}
                                    </div>
                                  )}
                                  {canApprove && (
                                    <div className="flex gap-2 ml-auto">
                                      <button
                                        onClick={e => { e.stopPropagation(); handleApprove(dep, 'APPROVED'); }}
                                        className="px-3 py-1 bg-emerald-600 text-white rounded-lg text-[10px] font-bold uppercase hover:bg-emerald-700 transition-colors"
                                      >
                                        ✓ Approve
                                      </button>
                                      <button
                                        onClick={e => { e.stopPropagation(); handleApprove(dep, 'REJECTED'); }}
                                        className="px-3 py-1 bg-red-50 border border-red-200 text-red-700 rounded-lg text-[10px] font-bold uppercase hover:bg-red-100 transition-colors"
                                      >
                                        ✗ Reject
                                      </button>
                                    </div>
                                  )}
                                </div>

                                {/* Feature 2: Pipeline Steps */}
                                <div>
                                  <div className="text-[9px] font-black text-[#777] uppercase tracking-widest mb-2">
                                    Pipeline Steps
                                  </div>
                                  {(() => {
                                    const steps = pipelineSteps[dep.id];
                                    if (steps === undefined || steps === 'loading') {
                                      return (
                                        <div className="flex items-center gap-1.5 text-[10px] font-mono text-[#777]">
                                          <Loader2 className="w-3 h-3 animate-spin text-[#B88719]" />
                                          Loading steps…
                                        </div>
                                      );
                                    }
                                    if (steps === 'error') {
                                      return (
                                        <div className="text-[10px] font-mono text-red-600 flex items-center gap-1">
                                          <AlertTriangle className="w-3 h-3" />
                                          Failed to load step data.
                                        </div>
                                      );
                                    }
                                    if (steps.length === 0) {
                                      return (
                                        <div className="text-[10px] font-mono text-[#777]">
                                          No step data available.
                                        </div>
                                      );
                                    }
                                    return (
                                      <div className="space-y-1">
                                        {steps.map((step, idx) => {
                                          const ss = STEP_STATUS[step.status] ?? STEP_STATUS_DEFAULT;
                                          const isRunning = step.status === 'RUNNING' || step.status === 'IN_PROGRESS';
                                          return (
                                            <div
                                              key={idx}
                                              className={cn(
                                                'flex items-center gap-2.5 px-3 py-1.5 rounded-lg border text-[10px] font-mono',
                                                ss.bg, ss.border,
                                              )}
                                            >
                                              {isRunning
                                                ? <Loader2 className={cn('w-3 h-3 animate-spin shrink-0', ss.color)} />
                                                : <CheckCircle2 className={cn('w-3 h-3 shrink-0', ss.color)} />}
                                              <span className={cn('font-bold', ss.color)}>{step.stepName}</span>
                                              <span className={cn('ml-auto text-[9px]', ss.color)}>{step.status}</span>
                                              <span className="text-[#999] text-[9px]">{step.duration}</span>
                                              {step.error && (
                                                <span
                                                  className="text-red-600 text-[9px] truncate max-w-[200px]"
                                                  title={step.error}
                                                >
                                                  {step.error}
                                                </span>
                                              )}
                                            </div>
                                          );
                                        })}
                                      </div>
                                    );
                                  })()}
                                </div>

                              </td>
                            </tr>
                          )}
                        </React.Fragment>
                      );
                    })}
                  </tbody>
                </table>

                {/* Mobile */}
                <div className="lg:hidden divide-y divide-[#F0E8D4]">
                  {filteredDeployments.map(dep => {
                    const sc = DEPLOY_STATUS[dep.status] ?? DEPLOY_STATUS['QUEUED'];
                    const tt = TRIGGER_TYPE[dep.triggerType] ?? { label: dep.triggerType, icon: Activity };
                    const TriggerIcon = tt.icon;
                    return (
                      <div key={dep.id} className="p-4 space-y-2 hover:bg-[#FFF9E8]">
                        <div className="flex justify-between items-start">
                          <div>
                            {/* Feature 4: trigger_type badge on mobile */}
                            <div className="flex items-center gap-1.5">
                              <div className="text-xs font-bold text-[#111111] uppercase">{dep.name}</div>
                              <div className="inline-flex items-center gap-0.5 px-1 py-0.5 bg-[#F4E8C3] border border-[#BFA66A] rounded text-[8px] font-bold text-[#5A5A5A]">
                                <TriggerIcon className="w-2.5 h-2.5" />
                                {tt.label}
                              </div>
                            </div>
                            <div className="text-[9px] font-mono text-[#777]">{dep.id} · {dep.version}</div>
                          </div>
                          <div className={cn('text-[9px] font-bold px-1.5 py-0.5 rounded-full border', sc.bg, sc.color)}>
                            {sc.label}
                          </div>
                        </div>
                        <div className="flex gap-3 text-[10px] font-mono text-[#777]">
                          <span>{dep.env}</span>
                          <span>{dep.owner}</span>
                          <span>{dep.startedAt}</span>
                        </div>
                        {dep.errorMessage && (
                          <div className="text-[9px] text-red-600 font-mono">{dep.errorMessage}</div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </>
            )}
          </div>
        )}

        {/* ── HISTORY ─────────────────────────────────────── */}
        {activeTab === 'HISTORY' && (
          <div className="space-y-4">
            <div className="flex justify-end">
              <div className="relative">
                <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-[#777]" />
                <input
                  type="text"
                  placeholder="Search releases…"
                  value={search}
                  onChange={e => setSearch(e.target.value)}
                  className="bg-white border border-[#BFA66A] pl-8 pr-4 py-1.5 rounded-lg text-[11px] w-52 focus:outline-none focus:border-[#8A5A00] font-mono transition-colors"
                />
              </div>
            </div>

            <div className="glass-panel rounded-2xl overflow-hidden">
              {filteredHistory.length === 0 && !loading ? (
                <div className="p-10 text-center text-[#777] text-sm font-mono">
                  {search ? `No releases matching "${search}"` : 'No release history yet'}
                </div>
              ) : (
                <>
                  {/* Desktop */}
                  <table className="hidden lg:table w-full text-left">
                    <thead className="bg-[#FDFAF2] border-b border-[#E8DFC8] text-[10px] font-bold text-[#777] uppercase tracking-[0.1em]">
                      <tr>
                        <th className="px-5 py-3">Release</th>
                        <th className="px-4 py-3">Env</th>
                        <th className="px-4 py-3">By</th>
                        <th className="px-4 py-3">Timestamp</th>
                        <th className="px-4 py-3">Duration</th>
                        <th className="px-4 py-3">Status</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#F0E8D4]">
                      {filteredHistory.map((r, idx) => {
                        const sc   = HISTORY_STATUS[r.status] ?? HISTORY_STATUS['SUCCESS'];
                        const Icon = sc.icon;
                        return (
                          <tr key={`${r.id}-${idx}`} className="hover:bg-[#FFF9E8] transition-colors">
                            <td className="px-5 py-3.5">
                              <div className="text-xs font-bold text-[#111111] uppercase">{r.name}</div>
                              <div className="text-[9px] font-mono text-[#777] mt-0.5">{r.id}</div>
                            </td>
                            <td className="px-4 py-3.5">
                              <span className="px-2 py-0.5 bg-[#F4E8C3] border border-[#BFA66A] rounded text-[9px] font-bold text-[#5A5A5A]">
                                {r.env}
                              </span>
                            </td>
                            <td className="px-4 py-3.5 text-[11px] font-mono text-[#5A5A5A]">{r.by}</td>
                            <td className="px-4 py-3.5 text-[10px] font-mono text-[#777]">{r.at}</td>
                            <td className="px-4 py-3.5 text-[10px] font-mono text-[#777]">{r.duration}</td>
                            <td className="px-4 py-3.5">
                              <div className={cn('inline-flex items-center gap-1.5 text-[10px] font-bold px-2 py-0.5 rounded-full border', sc.bg, sc.color)}>
                                <Icon className="w-3 h-3" />
                                {r.status.replace('_', ' ')}
                              </div>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>

                  {/* Mobile */}
                  <div className="lg:hidden divide-y divide-[#F0E8D4]">
                    {filteredHistory.map((r, idx) => {
                      const sc = HISTORY_STATUS[r.status] ?? HISTORY_STATUS['SUCCESS'];
                      return (
                        <div key={`${r.id}-${idx}`} className="p-4 space-y-2">
                          <div className="flex justify-between items-start">
                            <div>
                              <div className="text-xs font-bold text-[#111111]">{r.name}</div>
                              <div className="text-[9px] font-mono text-[#777]">{r.id}</div>
                            </div>
                            <div className={cn('text-[9px] font-bold px-1.5 py-0.5 rounded-full border', sc.bg, sc.color)}>
                              {r.status}
                            </div>
                          </div>
                          <div className="flex gap-3 text-[10px] font-mono text-[#777]">
                            <span>{r.env}</span>
                            <span>{r.by}</span>
                            <span>{r.at}</span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </>
              )}
            </div>
          </div>
        )}

        {/* ── ROLLBACK ─────────────────────────────────────── */}
        {activeTab === 'ROLLBACK' && (
          <div className="space-y-4">
            <div className="p-4 bg-amber-50 border border-amber-200 rounded-2xl flex items-start gap-3">
              <AlertTriangle className="w-4 h-4 text-amber-600 shrink-0 mt-0.5" />
              <p className="text-[11px] text-[#5A5A5A] leading-relaxed">
                Rollback uses a{' '}
                <span className="font-bold text-[#111111]">compensating transaction pattern</span>.
                If any step fails, the operation halts and records{' '}
                <code className="bg-amber-100 border border-amber-200 px-1 rounded font-mono text-amber-700 text-[10px]">
                  PARTIAL_ROLLBACK
                </code>{' '}
                — the release manager is notified immediately.
              </p>
            </div>

            {/* Rollback targets */}
            {rollbackTargets.length === 0 && !loading ? (
              <div className="glass-panel p-10 rounded-2xl text-center text-[#777] text-sm font-mono">
                No rollback candidates — need at least 2 successful deployments per pipeline
              </div>
            ) : (
              <div className="space-y-3">
                {rollbackTargets.map(target => (
                  <div key={target.id} className="glass-panel p-5 rounded-2xl flex items-center gap-4">
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-bold text-[#111111] uppercase">{target.name}</div>
                      <div className="flex items-center gap-2 mt-1.5 text-[10px] font-mono">
                        <span className="text-[#B88719] font-bold">{target.current}</span>
                        <span className="text-[#777]">→ revert to</span>
                        <span className="text-[#5A5A5A]">{target.previous}</span>
                      </div>
                    </div>
                    <span className="px-2 py-0.5 bg-[#F4E8C3] border border-[#BFA66A] rounded text-[9px] font-bold text-[#5A5A5A] shrink-0">
                      {target.env}
                    </span>

                    {confirmId === target.id ? (
                      <div className="flex gap-2 shrink-0">
                        <button
                          onClick={() => handleRollback(target)}
                          className="px-4 py-2 rounded-xl text-[11px] font-bold uppercase tracking-wide bg-amber-500 text-white border border-amber-600 shadow-md hover:bg-amber-600 transition-all"
                        >
                          Confirm
                        </button>
                        <button
                          onClick={() => setConfirmId(null)}
                          className="px-4 py-2 rounded-xl text-[11px] font-bold uppercase tracking-wide bg-white border border-[#BFA66A] text-[#5A5A5A] hover:bg-[#FFF9E8] transition-all"
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => setConfirmId(target.id)}
                        className="px-4 py-2 rounded-xl text-[11px] font-bold uppercase tracking-wide bg-white border border-[#BFA66A] text-[#5A5A5A] hover:border-amber-400 hover:text-amber-700 hover:bg-amber-50 transition-all shrink-0"
                      >
                        Rollback
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* Rollback procedure */}
            <div className="glass-panel p-5 rounded-2xl">
              <h4 className="text-[10px] font-black text-[#777] uppercase tracking-widest mb-4">
                Rollback Procedure
              </h4>
              <div className="space-y-2">
                {[
                  { step: '01', label: 'Revert PostgreSQL release pointer', sub: 'Update version reference to previous release' },
                  { step: '02', label: 'Restore Kong upstream route',       sub: 'Re-route traffic to previous deployment'      },
                  { step: '03', label: 'Redeploy artifact from MinIO',      sub: 'Restore immutable version snapshot'           },
                ].map(s => (
                  <div
                    key={s.step}
                    className="flex items-center gap-4 px-4 py-3 bg-[#FDFAF2] border border-[#E8DFC8] rounded-xl"
                  >
                    <span className="text-[10px] font-black text-[#B88719] font-mono w-6 shrink-0">{s.step}</span>
                    <div>
                      <div className="text-[11px] font-bold text-[#111111]">{s.label}</div>
                      <div className="text-[9px] text-[#777] mt-0.5">{s.sub}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

      </motion.div>
    </div>
  );
};

export default ReleaseManagementCenter;
