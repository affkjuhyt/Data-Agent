import React, { useEffect, useRef, useState } from 'react';
import { SlidersHorizontal, ChevronDown } from 'lucide-react';
import { cn } from '../../lib/utils';

export interface FilterGroupOption { id: string; label: string; activeClassName?: string }

export interface FilterGroupDef {
  label: string;
  options: FilterGroupOption[];
  isActive: (id: string) => boolean;
  onToggle: (id: string) => void;
}

/**
 * Collapses one or more chip-row filter groups (tier, source type, status,
 * etc.) behind a single "Filters" button instead of always rendering them
 * as full-width rows on screen — those used to take a fixed, large vertical
 * block above every list (AssetInventory, KnowledgeHub, ConflictWorkspace
 * all had their own copy of this). Active filters still show as small
 * removable chips next to the button so the current state is visible
 * without opening the panel.
 */
export const FilterPopover = ({
  groups, activeCount = 0, activeSummary,
}: {
  groups: FilterGroupDef[];
  /** Shown as a badge on the Filters button when > 0. */
  activeCount?: number;
  /** Optional compact chips shown next to the button (e.g. current tier, deselected sources). */
  activeSummary?: React.ReactNode;
}) => {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onEsc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onEsc);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onEsc);
    };
  }, [open]);

  return (
    <div className="relative inline-block" ref={ref}>
      <div className="flex items-center gap-2 flex-wrap">
        <button
          type="button"
          onClick={() => setOpen(v => !v)}
          aria-expanded={open}
          className={cn(
            'flex items-center gap-1.5 px-3.5 py-2 rounded-xl text-[10px] font-black uppercase tracking-widest transition-all cursor-pointer border',
            open || activeCount > 0
              ? 'bg-[#1E1B15] text-[#D9B86C] border-[#BFA66A] shadow-sm'
              : 'bg-white border-[#BFA66A] text-[#8A5A00] hover:bg-[#FFF9E8]/40'
          )}
        >
          <SlidersHorizontal className="w-3.5 h-3.5" />
          Filters
          {activeCount > 0 && (
            <span className="bg-[#BFA66A] text-[#111111] font-mono text-[9px] w-4.5 h-4.5 flex items-center justify-center rounded-full font-black">
              {activeCount}
            </span>
          )}
          <ChevronDown className={cn('w-3 h-3 transition-transform', open && 'rotate-180')} />
        </button>
        {activeSummary}
      </div>

      {open && (
        <div className="absolute z-30 top-full left-0 mt-2 w-80 max-w-[90vw] bg-white border border-[#BFA66A] rounded-2xl shadow-lg p-4 flex flex-col gap-4">
          {groups.map(group => (
            <div key={group.label} className="flex flex-col gap-2">
              <span className="text-[10px] font-black uppercase tracking-widest text-[#8A5A00] opacity-80 font-mono">
                {group.label}
              </span>
              <div className="flex gap-2 flex-wrap">
                {group.options.map(opt => {
                  const active = group.isActive(opt.id);
                  return (
                    <button
                      key={opt.id}
                      type="button"
                      onClick={() => group.onToggle(opt.id)}
                      aria-pressed={active}
                      className={cn(
                        'px-3 py-1.5 rounded-xl text-[10px] font-black uppercase tracking-widest transition-all cursor-pointer',
                        active
                          ? (opt.activeClassName || 'bg-[#1E1B15] text-[#D9B86C] border border-[#BFA66A] shadow-sm')
                          : 'bg-white border border-[#BFA66A] text-[#8A5A00] opacity-60 hover:opacity-100 hover:bg-[#FFF9E8]/40'
                      )}
                    >
                      {opt.label}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
