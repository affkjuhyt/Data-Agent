import React from 'react';
import {
  Clock, Plus, Search, Calendar, Bell, MoreVertical,
  Play, GitMerge, RefreshCcw, CheckCircle2, Settings2, CalendarDays
} from 'lucide-react';
import { motion } from 'motion/react';
import { cn } from '../../../lib/utils';

const SCHEDULES = [
  { id: 'sc-1', name: 'Global Finance Sync', workflow: 'Multi-Agent Banking Support', type: 'CRON', pattern: '0 */4 * * *', nextRun: '2h 12m', status: 'ACTIVE' },
  { id: 'sc-2', name: 'Knowledge Graph Maintenance', workflow: 'GraphRAG Intelligence Sync', type: 'EVENT', pattern: 'KB_UPDATE_V2', nextRun: 'On Change', status: 'WAITING' },
  { id: 'sc-3', name: 'Dev Weekly Audit', workflow: 'Gov-Compliance Review', type: 'SCHEDULED', pattern: 'Mon @ 09:00', nextRun: '3d 14h', status: 'PAUSED' },
];

const STATUS_STYLE: Record<string, string> = {
  ACTIVE:  'bg-emerald-50 border-emerald-200 text-emerald-700',
  WAITING: 'bg-amber-50 border-amber-200 text-amber-700',
  PAUSED:  'bg-gray-100 border-gray-200 text-gray-500',
};

const SchedulingCenter = () => {
  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="bg-white border border-[#ECE7DA] rounded-2xl p-6">
        <div className="flex items-center justify-between mb-5">
          <div className="flex items-center gap-3">
            <div className="p-2 bg-[#F3E2A7] rounded-xl border border-[#BFA66A]/50">
              <Clock className="w-5 h-5 text-[#7C5A0E]" />
            </div>
            <div>
              <h2 className="text-lg font-display font-bold text-[#111111]">Scheduling Center</h2>
              <p className="text-xs text-[#8A8A7A] mt-0.5">Configure CRON, event-based, and scheduled workflow triggers</p>
            </div>
          </div>
          <button className="btn-primary">
            <Plus className="w-4 h-4" /> New Trigger
          </button>
        </div>

        <div className="flex items-center gap-3">
          <div className="flex-1 relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#8A8A7A]" />
            <input
              type="text"
              placeholder="Search schedules, cron patterns, or event IDs..."
              className="w-full bg-[#FAFAF5] border border-[#ECE7DA] rounded-xl py-2 pl-10 pr-4 text-sm text-[#111111] placeholder-[#8A8A7A] focus:outline-none focus:border-[#BFA66A]"
            />
          </div>
          <button className="p-2.5 bg-[#F8F5EE] hover:bg-[#F3E2A7] border border-[#ECE7DA] rounded-xl transition-colors text-[#5A5A5A]">
            <CalendarDays className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-5">
        {/* Schedule list */}
        <div className="lg:col-span-3 space-y-3">
          <div className="text-[10px] font-bold text-[#8A8A7A] uppercase tracking-widest mb-2">Active Triggers</div>
          {SCHEDULES.map((sc) => (
            <motion.div
              key={sc.id}
              initial={{ opacity: 0, x: -8 }}
              animate={{ opacity: 1, x: 0 }}
              className="group bg-white border border-[#ECE7DA] hover:border-[#BFA66A] rounded-2xl p-5 transition-all flex items-center justify-between"
            >
              <div className="flex items-center gap-5">
                <div className={cn(
                  'p-3 rounded-xl border',
                  sc.status === 'ACTIVE' ? 'bg-emerald-50 border-emerald-200 text-emerald-600' :
                  sc.status === 'WAITING' ? 'bg-amber-50 border-amber-200 text-amber-600' :
                  'bg-[#F8F5EE] border-[#ECE7DA] text-[#8A8A7A]'
                )}>
                  <RefreshCcw className={cn('w-4 h-4', sc.status === 'ACTIVE' && 'animate-spin')} style={{ animationDuration: '8s' }} />
                </div>
                <div>
                  <h4 className="text-sm font-bold text-[#111111] mb-1.5 group-hover:text-[#B88719] transition-colors">{sc.name}</h4>
                  <div className="flex items-center gap-2">
                    <div className="flex items-center gap-1 px-2 py-0.5 bg-[#F3E2A7] rounded border border-[#BFA66A]/40">
                      <GitMerge className="w-3 h-3 text-[#B88719]" />
                      <span className="text-[10px] font-semibold text-[#7C5A0E]">{sc.workflow}</span>
                    </div>
                    <span className="text-[10px] font-mono text-[#8A8A7A] italic uppercase">{sc.pattern}</span>
                    <span className={cn('text-[9px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full border', STATUS_STYLE[sc.status])}>
                      {sc.status}
                    </span>
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-8">
                <div className="text-right">
                  <div className="text-sm font-bold text-[#111111]">In {sc.nextRun}</div>
                  <div className="text-[9px] font-semibold text-[#8A8A7A] uppercase tracking-widest">Next Execution</div>
                </div>
                <div className="flex items-center gap-1">
                  <button className="p-1.5 hover:bg-[#F3E2A7] rounded-lg text-[#5A5A5A] hover:text-emerald-600 transition-colors"><Play className="w-4 h-4" /></button>
                  <button className="p-1.5 hover:bg-[#F3E2A7] rounded-lg text-[#5A5A5A] hover:text-[#111111] transition-colors"><Settings2 className="w-4 h-4" /></button>
                  <button className="p-1.5 hover:bg-[#F3E2A7] rounded-lg text-[#5A5A5A] hover:text-[#111111] transition-colors"><MoreVertical className="w-4 h-4" /></button>
                </div>
              </div>
            </motion.div>
          ))}
        </div>

        {/* Right metrics */}
        <div className="space-y-4">
          <div className="text-[10px] font-bold text-[#8A8A7A] uppercase tracking-widest mb-2">Scheduling Health</div>

          <div className="p-5 bg-white border border-[#ECE7DA] rounded-2xl">
            <div className="flex items-center justify-between mb-4">
              <Calendar className="w-5 h-5 text-[#B88719]" />
              <span className="text-[10px] font-bold text-[#B88719] uppercase tracking-widest">SLA: 99.9%</span>
            </div>
            <div className="text-3xl font-display font-bold text-[#111111] mb-1">1,242</div>
            <div className="text-xs text-[#8A8A7A]">Executions scheduled this month</div>
            <div className="mt-5 pt-5 border-t border-[#ECE7DA] space-y-3">
              {[
                { label: 'Total Cron Jobs', value: '12', color: 'text-[#111111]' },
                { label: 'Event Triggers', value: '156', color: 'text-[#111111]' },
                { label: 'Missed Windows', value: '0', color: 'text-emerald-600' },
              ].map(row => (
                <div key={row.label} className="flex items-center justify-between text-xs">
                  <span className="text-[#5A5A5A]">{row.label}</span>
                  <span className={cn('font-bold', row.color)}>{row.value}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="p-5 bg-white border border-[#ECE7DA] rounded-2xl">
            <h4 className="text-xs font-bold text-[#111111] mb-3">Conflict Alerts</h4>
            <div className="flex items-center gap-2.5 p-3 bg-red-50 border border-red-200 rounded-xl text-red-600">
              <Bell className="w-4 h-4 shrink-0" />
              <p className="text-[10px] font-medium leading-relaxed">2 workflows overlap on Wednesday 09:00 window. Resource bottleneck detected.</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default SchedulingCenter;
