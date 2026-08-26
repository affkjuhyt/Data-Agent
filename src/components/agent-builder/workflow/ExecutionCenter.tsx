import React, { useState } from 'react';
import {
  Search,
  Filter,
  Play,
  Clock,
  CheckCircle2,
  AlertCircle,
  History,
  ChevronDown,
  ArrowRight,
  Activity,
  X,
  Cpu,
  Database,
  Bot,
  ShieldCheck,
  Zap,
  Terminal,
  Layers,
  Code
} from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { ExecutionStatus, WorkflowExecution } from '../../../types/workflow';
import { cn } from '../../../lib/utils';
import SandboxQueuePanel from './SandboxQueuePanel';

const MOCK_EXECUTIONS: WorkflowExecution[] = [
  {
    id: 'exec-8921',
    workflowId: 'wf-1',
    workflowName: 'Multi-Agent Banking Support',
    trigger: 'WEBHOOK',
    tenant: 'Global-Finance',
    environment: 'PROD',
    status: ExecutionStatus.COMPLETED,
    startedAt: '12m ago',
    duration: '45s',
    cost: 0.12,
  },
  {
    id: 'exec-8920',
    workflowId: 'wf-3',
    workflowName: 'Gov-Compliance Review',
    trigger: 'UI',
    tenant: 'Gov-Tech-Hanoi',
    environment: 'DEV',
    status: ExecutionStatus.WAITING_APPROVAL,
    startedAt: '45m ago',
    duration: '3h 12m',
    cost: 0.05,
    waitingReason: 'Legal Director Signature Required'
  },
  {
    id: 'exec-8919',
    workflowId: 'wf-2',
    workflowName: 'GraphRAG Intelligence Sync',
    trigger: 'SCHEDULE',
    tenant: 'Smart-City-Nexus',
    environment: 'STAGING',
    status: ExecutionStatus.RUNNING,
    startedAt: 'Just now',
    duration: '1m 12s',
    cost: 0.85,
    currentNodeId: 'node-sync-graph'
  },
  {
    id: 'exec-8918',
    workflowId: 'wf-1',
    workflowName: 'Multi-Agent Banking Support',
    trigger: 'WEBHOOK',
    tenant: 'Global-Finance',
    environment: 'PROD',
    status: ExecutionStatus.FAILED,
    startedAt: '2h ago',
    duration: '12s',
    cost: 0.02,
    waitingReason: 'Gateway Timeout (504)'
  }
];

const STATUS_STYLE: Record<string, string> = {
  [ExecutionStatus.COMPLETED]:        'bg-emerald-50 border-emerald-200 text-emerald-700',
  [ExecutionStatus.RUNNING]:          'bg-blue-50 border-blue-200 text-blue-700',
  [ExecutionStatus.FAILED]:           'bg-red-50 border-red-200 text-red-700',
  [ExecutionStatus.WAITING_APPROVAL]: 'bg-amber-50 border-amber-200 text-amber-700',
};

const STATUS_DOT: Record<string, string> = {
  [ExecutionStatus.COMPLETED]:        'bg-emerald-500',
  [ExecutionStatus.RUNNING]:          'bg-blue-500 animate-pulse',
  [ExecutionStatus.FAILED]:           'bg-red-500',
  [ExecutionStatus.WAITING_APPROVAL]: 'bg-amber-500',
};

interface ExecutionCenterProps {
  initialSearch?: string;
}

type CenterTab = 'HISTORY' | 'QUEUE' | 'SCHEDULES';

const ExecutionCenter = ({ initialSearch }: ExecutionCenterProps = {}) => {
  const [tab, setTab] = useState<CenterTab>('HISTORY');
  const [selectedExecution, setSelectedExecution] = useState<WorkflowExecution | null>(null);
  const [search, setSearch] = useState(initialSearch ?? '');

  const displayedExecutions = MOCK_EXECUTIONS.filter(e =>
    !search.trim()
      ? true
      : e.workflowName.toLowerCase().includes(search.toLowerCase()) || e.id.includes(search)
  );

  const renderTrace = () => {
    if (!selectedExecution) return null;

    return (
      <motion.div
        initial={{ opacity: 0, x: 20 }}
        animate={{ opacity: 1, x: 0 }}
        className="flex flex-col h-full bg-white border-l border-[#ECE7DA]"
      >
        <div className="p-5 border-b border-[#ECE7DA] bg-[#FAFAF5] flex items-center justify-between">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="text-[10px] font-black text-[#B88719] uppercase tracking-widest">TRACE ID</span>
              <span className="text-xs font-mono text-[#5A5A5A]">#{selectedExecution.id}</span>
            </div>
            <h3 className="text-base font-bold text-[#111111]">{selectedExecution.workflowName}</h3>
          </div>
          <div className="flex items-center gap-2">
            <button className="flex items-center gap-1.5 px-3 py-1.5 bg-[#F3E2A7] hover:bg-[#EDD98A] border border-[#BFA66A]/50 text-[#7C5A0E] rounded-lg text-xs font-bold transition-all">
              <Play className="w-3.5 h-3.5" /> Replay
            </button>
            <button className="flex items-center gap-1.5 px-3 py-1.5 bg-[#F8F5EE] hover:bg-[#F3E2A7] border border-[#ECE7DA] text-[#5A5A5A] rounded-lg text-xs font-bold transition-all">
              <Code className="w-3.5 h-3.5" /> JSON Trace
            </button>
            <button
              onClick={() => setSelectedExecution(null)}
              className="p-2 hover:bg-[#F3E2A7] rounded-lg transition-all text-[#8A8A7A] hover:text-[#111111]"
              title="Close"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        <div className="flex-1 overflow-auto p-6 space-y-6">
          {/* Trace Statistics */}
          <div className="grid grid-cols-2 gap-3">
            {[
              { label: 'Total Duration', value: selectedExecution.duration, icon: Clock },
              { label: 'Infrastructure Cost', value: `$${selectedExecution.cost}`, icon: Zap },
              { label: 'Tokens Processed', value: '1,242k', icon: Layers },
              { label: 'Worker Affinity', value: 'US-EAST-1A', icon: Cpu },
            ].map((stat) => (
              <div key={stat.label} className="p-3.5 bg-[#FAFAF5] border border-[#ECE7DA] rounded-xl">
                <div className="flex items-center gap-1.5 text-[#8A8A7A] mb-2">
                  <stat.icon className="w-3.5 h-3.5 text-[#B88719]" />
                  <span className="text-[9px] font-black uppercase tracking-widest">{stat.label}</span>
                </div>
                <div className="text-base font-bold text-[#111111]">{stat.value}</div>
              </div>
            ))}
          </div>

          {/* Trace Steps Tree */}
          <div className="space-y-3 relative">
            <div className="absolute left-[23px] top-6 bottom-6 w-px bg-[#ECE7DA]" />

            {[
              { title: 'Workflow Triggered', type: 'SYSTEM', status: 'SUCCESS', icon: Zap, time: 'T+0s' },
              { title: 'Metadata Extraction', type: 'KNOWLEDGE', status: 'SUCCESS', icon: Database, time: 'T+12s', logs: 'Extracted 12 PII fields, masked 4.' },
              { title: 'Agent Reasoning Stage', type: 'AI', status: 'SUCCESS', icon: Bot, time: 'T+24s', tokens: 420 },
              { title: 'Human Approval Request', type: 'HUMAN', status: selectedExecution.status === ExecutionStatus.WAITING_APPROVAL ? 'PENDING' : 'SUCCESS', icon: ShieldCheck, time: 'T+32s' },
              { title: 'External Tool Call', type: 'TOOL', status: 'IDLE', icon: Activity, time: '--' },
            ].map((step, i) => (
              <div key={i} className="group relative pl-14">
                <div className={cn(
                  'absolute left-4 top-1/2 -translate-y-1/2 w-5 h-5 rounded-full border-2 bg-white flex items-center justify-center z-10',
                  step.status === 'SUCCESS' ? 'border-emerald-400' :
                  step.status === 'PENDING' ? 'border-amber-400' : 'border-[#ECE7DA]'
                )}>
                  <div className={cn(
                    'w-1.5 h-1.5 rounded-full',
                    step.status === 'SUCCESS' ? 'bg-emerald-500' :
                    step.status === 'PENDING' ? 'bg-amber-500 animate-pulse' : 'bg-[#D0CCBE]'
                  )} />
                </div>

                <div className="p-3.5 bg-[#FAFAF5] hover:bg-[#F3E2A7]/30 border border-[#ECE7DA] hover:border-[#BFA66A] rounded-xl transition-all flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <div className="p-2 bg-white border border-[#ECE7DA] rounded-lg group-hover:border-[#BFA66A] transition-all">
                      <step.icon className="w-4 h-4 text-[#8A8A7A] group-hover:text-[#B88719] transition-colors" />
                    </div>
                    <div>
                      <div className="text-[#111111] font-bold text-sm">{step.title}</div>
                      <div className="flex items-center gap-2 mt-0.5">
                        <span className="text-[9px] font-black text-[#8A8A7A] uppercase tracking-widest">{step.type}</span>
                        {step.time !== '--' && <span className="text-[9px] text-[#B88719] font-mono italic">{step.time}</span>}
                      </div>
                    </div>
                  </div>

                  <div className="flex items-center gap-2">
                    {step.tokens && (
                      <div className="text-[9px] px-1.5 py-0.5 bg-[#F3E2A7] text-[#7C5A0E] border border-[#BFA66A]/40 rounded font-mono">
                        {step.tokens} TOKENS
                      </div>
                    )}
                    <button className="p-1.5 hover:bg-[#F3E2A7] rounded-lg text-[#8A8A7A] transition-all">
                      <ChevronDown className="w-4 h-4" />
                    </button>
                  </div>
                </div>

                {step.logs && (
                  <div className="mt-2 ml-4 p-3 bg-[#F8F5EE] rounded-lg text-[10px] font-mono text-[#5A5A5A] italic border-l-2 border-[#BFA66A]/40">
                    LOGGER OUT: {step.logs}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </motion.div>
    );
  };

  const headerCopy: Record<CenterTab, { title: string; subtitle: string }> = {
    HISTORY:   { title: 'Recent Executions', subtitle: 'Live execution history and trace viewer' },
    QUEUE:     { title: 'Sandbox Queue', subtitle: 'Live container pool health and execute_python/execute_sql jobs (docs/1.md mục h)' },
    SCHEDULES: { title: 'Schedules', subtitle: 'Recurring workflow triggers' },
  };

  return (
    <div className={cn('flex bg-[#FAFAF5] overflow-hidden rounded-2xl border border-[#ECE7DA]', (selectedExecution || tab !== 'HISTORY') ? 'h-[680px]' : '')}>
      <div className={`flex-1 flex flex-col transition-all duration-500 min-w-0`}>
        {/* Header */}
        <div className="p-6 border-b border-[#ECE7DA] bg-white">
          <div className={cn('flex items-center justify-between', tab === 'HISTORY' ? 'mb-5' : '')}>
            <div className="flex items-center gap-3">
              <div className="p-2 bg-[#F3E2A7] rounded-xl border border-[#BFA66A]/50">
                <History className="w-5 h-5 text-[#7C5A0E]" />
              </div>
              <div>
                <h2 className="text-lg font-display font-bold text-[#111111]">{headerCopy[tab].title}</h2>
                <p className="text-xs text-[#8A8A7A] mt-0.5">{headerCopy[tab].subtitle}</p>
              </div>
            </div>
            <div className="flex items-center gap-1 p-1 bg-[#F8F5EE] rounded-xl border border-[#ECE7DA]">
              {(['HISTORY', 'QUEUE', 'SCHEDULES'] as CenterTab[]).map((t) => (
                <button
                  key={t}
                  onClick={() => setTab(t)}
                  className={cn(
                    'px-4 py-1.5 rounded-lg text-xs font-bold transition-all',
                    tab === t ? 'bg-[#B88719] text-white shadow-sm' : 'text-[#8A8A7A] hover:text-[#111111]',
                  )}
                >
                  {t === 'HISTORY' ? 'History' : t === 'QUEUE' ? 'Queue' : 'Schedules'}
                </button>
              ))}
            </div>
          </div>

          {tab === 'HISTORY' && (
            <div className="flex items-center gap-3">
              <div className="flex-1 relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#8A8A7A]" />
                <input
                  type="text"
                  value={search}
                  onChange={e => setSearch(e.target.value)}
                  placeholder="Search executions by trace ID, workflow name, or environment..."
                  className="w-full bg-[#FAFAF5] border border-[#ECE7DA] rounded-xl py-2 pl-10 pr-4 text-sm text-[#111111] placeholder-[#8A8A7A] focus:outline-none focus:border-[#BFA66A]"
                />
              </div>
              <button className="p-2.5 bg-[#F8F5EE] hover:bg-[#F3E2A7] border border-[#ECE7DA] rounded-xl transition-colors text-[#5A5A5A]">
                <Filter className="w-4 h-4" />
              </button>
            </div>
          )}
        </div>

        {tab === 'QUEUE' && <SandboxQueuePanel />}

        {tab === 'SCHEDULES' && (
          <div className="flex-1 flex items-center justify-center text-sm text-[#8A8A7A]">
            Scheduled triggers are not part of this view yet.
          </div>
        )}

        {tab === 'HISTORY' && (
        <>
        {/* Table */}
        <div className="flex-1 overflow-auto">
          <table className="w-full">
            <thead>
              <tr className="text-[10px] font-black text-[#8A8A7A] uppercase tracking-widest border-b border-[#ECE7DA] bg-[#FAFAF5]">
                <th className="px-6 py-3 text-left">Execution ID / Workflow</th>
                <th className="px-6 py-3 text-left">Trigger / Env</th>
                <th className="px-6 py-3 text-left">Status</th>
                <th className="px-6 py-3 text-left">Metrics</th>
                <th className="px-6 py-3 text-right">Activity</th>
              </tr>
            </thead>
            <tbody>
              {displayedExecutions.map((exec) => (
                <tr
                  key={exec.id}
                  onClick={() => setSelectedExecution(exec)}
                  className={cn(
                    'group border-b border-[#ECE7DA] hover:bg-[#FBF8F0] transition-all cursor-pointer',
                    selectedExecution?.id === exec.id ? 'bg-[#F3E2A7]/30 border-[#BFA66A]/40' : 'bg-white'
                  )}
                >
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-3">
                      <div className="w-9 h-9 rounded-xl bg-[#F8F5EE] border border-[#ECE7DA] group-hover:border-[#BFA66A] flex items-center justify-center text-[#8A8A7A] group-hover:text-[#B88719] transition-all">
                        <Terminal className="w-4 h-4" />
                      </div>
                      <div>
                        <div className="text-sm font-bold text-[#111111] leading-none mb-1">#{exec.id}</div>
                        <div className="text-xs text-[#5A5A5A]">{exec.workflowName}</div>
                      </div>
                    </div>
                  </td>
                  <td className="px-6 py-4">
                    <div className="flex flex-col gap-0.5">
                      <span className="text-[10px] font-black text-[#8A8A7A] uppercase tracking-widest">{exec.trigger}</span>
                      <span className="text-[10px] text-[#B88719] font-bold">{exec.tenant} / {exec.environment}</span>
                    </div>
                  </td>
                  <td className="px-6 py-4">
                    <div className={cn(
                      'inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-black uppercase tracking-widest border',
                      STATUS_STYLE[exec.status]
                    )}>
                      <div className={cn('w-1 h-1 rounded-full', STATUS_DOT[exec.status])} />
                      {exec.status.replace('_', ' ')}
                    </div>
                    {exec.waitingReason && (
                      <div className="text-[9px] text-[#8A8A7A] mt-1 italic">Wait: {exec.waitingReason}</div>
                    )}
                  </td>
                  <td className="px-6 py-4">
                    <div className="flex items-center gap-4">
                      <div>
                        <div className="text-xs font-bold text-[#111111]">{exec.duration}</div>
                        <div className="text-[9px] text-[#8A8A7A] uppercase font-black tracking-widest">Duration</div>
                      </div>
                      <div className="w-px h-6 bg-[#ECE7DA]" />
                      <div>
                        <div className="text-xs font-bold text-[#111111]">${exec.cost}</div>
                        <div className="text-[9px] text-[#8A8A7A] uppercase font-black tracking-widest">Cost</div>
                      </div>
                    </div>
                  </td>
                  <td className="px-6 py-4 text-right">
                    <div className="flex items-center justify-end gap-2 text-[#8A8A7A]">
                      <div className="text-xs font-medium text-[#5A5A5A] italic mr-2">{exec.startedAt}</div>
                      <button className="p-1.5 hover:bg-[#F3E2A7] rounded-lg transition-all text-[#8A8A7A] hover:text-[#7C5A0E]">
                        <ArrowRight className="w-4 h-4" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        </>
        )}
      </div>

      {tab === 'HISTORY' && (
        <AnimatePresence>
          {selectedExecution && (
            <div className="w-[420px] shrink-0 border-l border-[#ECE7DA]">
              {renderTrace()}
            </div>
          )}
        </AnimatePresence>
      )}
    </div>
  );
};

export default ExecutionCenter;
