import { useState, useEffect } from 'react';
import { Activity, ChevronRight, CheckCircle2, XCircle, Loader2, X } from 'lucide-react';
import { cn } from '../../../lib/utils';

export interface PromotionEntry {
  data_id: string;
  name: string;
  from_tier: 'source' | 'bronze' | 'silver';
  to_tier: 'bronze' | 'silver' | 'gold';
  progress: number;
  status: 'queued' | 'processing' | 'done' | 'failed';
  /** Overrides the default status line (e.g. "Fetching source…" for a crawl). */
  detail?: string;
}

interface Props {
  queue: PromotionEntry[];
  onDismiss: (data_id: string) => void;
}

const TIER_STYLE: Record<string, string> = {
  source: 'bg-white/10 text-[#D9B86C]',
  bronze: 'bg-[#92620A]/40 text-[#D9B86C]',
  silver: 'bg-slate-700/50 text-[#5F5F5F]',
  gold:   'bg-[#B88719]/40 text-[#FFD97A]',
};

export function PromotionProgressOverlay({ queue, onDismiss }: Props) {
  const [open, setOpen] = useState(false);

  // Auto-open whenever a new item lands
  useEffect(() => {
    if (queue.length > 0) setOpen(true);
  }, [queue.length]);

  if (queue.length === 0) return null;

  const activeCount = queue.filter(e => e.status !== 'done' && e.status !== 'failed').length;

  return (
    <div className="fixed bottom-6 right-6 z-[300] flex flex-col items-end gap-2.5 select-none">

      {/* ── Expanded panel ── */}
      {open && (
        <div className="w-80 bg-[#13110E] border border-[#BFA66A] rounded-2xl shadow-2xl overflow-hidden">

          <div className="flex items-center justify-between px-4 py-3 border-b border-[#BFA66A]">
            <div className="flex items-center gap-2">
              <Activity className="w-3.5 h-3.5 text-[#B88719]" />
              <span className="text-[11px] font-black text-[#D9B86C] uppercase tracking-wider">
                Pipeline Activity
              </span>
            </div>
            <button
              onClick={() => setOpen(false)}
              className="btn-ghost btn-sm"
            >
              Hide
            </button>
          </div>

          <div className="max-h-72 overflow-y-auto custom-scrollbar p-3 space-y-2">
            {queue.map((entry) => (
              <div
                key={entry.data_id}
                className="bg-white/[0.04] border border-white/15 rounded-xl p-3"
              >
                {/* Name + tier arrow */}
                <div className="flex items-center justify-between mb-2 gap-2">
                  <span className="text-[11px] font-bold text-slate-100 truncate">{entry.name}</span>
                  <div className="flex items-center gap-1 shrink-0">
                    <span className={cn('text-[9px] font-mono font-black px-1.5 py-0.5 rounded uppercase', TIER_STYLE[entry.from_tier])}>
                      {entry.from_tier}
                    </span>
                    <ChevronRight className="w-3 h-3 text-[#3F3F3F]" />
                    <span className={cn('text-[9px] font-mono font-black px-1.5 py-0.5 rounded uppercase', TIER_STYLE[entry.to_tier])}>
                      {entry.to_tier}
                    </span>
                  </div>
                </div>

                {/* Progress bar */}
                <div className="h-1.5 bg-white/10 rounded-full overflow-hidden mb-1.5">
                  <div
                    className={cn(
                      'h-full rounded-full transition-all duration-700 ease-out',
                      entry.status === 'done'   ? 'bg-green-500' :
                      entry.status === 'failed' ? 'bg-red-500' :
                      'bg-gradient-to-r from-[#8A5A00] to-[#B88719]'
                    )}
                    style={{ width: `${entry.progress}%` }}
                  />
                </div>

                {/* Status line + % */}
                <div className="flex items-center justify-between">
                  <span className="text-[10px] font-mono text-[#5F5F5F]">
                    {entry.detail ??
                     (entry.status === 'done'       ? 'Complete' :
                      entry.status === 'failed'     ? 'Failed' :
                      entry.status === 'processing' ? 'Processing pipeline…' :
                      'Queued')}
                  </span>
                  <div className="flex items-center gap-1.5">
                    {entry.status === 'done' ? (
                      <CheckCircle2 className="w-3.5 h-3.5 text-green-500" />
                    ) : entry.status === 'failed' ? (
                      <XCircle className="w-3.5 h-3.5 text-red-500" />
                    ) : (
                      <Loader2 className="w-3 h-3 text-[#B88719] animate-spin" />
                    )}
                    <span className="text-[10px] font-mono font-black text-[#D9B86C]">
                      {entry.progress}%
                    </span>
                    {(entry.status === 'done' || entry.status === 'failed') && (
                      <button
                        onClick={() => onDismiss(entry.data_id)}
                        className="ml-0.5 text-[#5F5F5F] hover:text-[#5F5F5F] transition-colors cursor-pointer"
                      >
                        <X className="w-3 h-3" />
                      </button>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Floating trigger button ── */}
      <button
        onClick={() => setOpen((s) => !s)}
        className="relative w-11 h-11 rounded-full bg-[#1E1B15] border border-[#BFA66A] shadow-lg flex items-center justify-center hover:border-[#BFA66A] transition-all cursor-pointer"
        title="Pipeline activity"
      >
        <Activity
          className={cn(
            'w-5 h-5',
            activeCount > 0 ? 'text-[#B88719] animate-pulse' : 'text-[#D9B86C]',
          )}
        />
        {activeCount > 0 && (
          <span className="absolute -top-1 -right-1 w-4 h-4 bg-[#B88719] rounded-full text-[9px] font-black text-white flex items-center justify-center leading-none">
            {activeCount}
          </span>
        )}
      </button>
    </div>
  );
}
