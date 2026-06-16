import React from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { cn } from '../../lib/utils';

interface PaginationProps {
  page: number;
  totalPages: number;
  hasNext: boolean;
  /** Whether a given page can be navigated to (keyset reachability). Defaults to true. */
  canJump?: (page: number) => boolean;
  onJump: (page: number) => void;
  className?: string;
}

type Slot = number | 'ellipsis';

/** Windowed page list: first · … · [page-1 page page+1] · … — matches `1 … 34 35 36 … >`. */
function buildSlots(page: number, totalPages: number): Slot[] {
  const pages = new Set<number>([1]);
  for (let p = page - 1; p <= page + 1; p++) if (p >= 1 && p <= totalPages) pages.add(p);
  if (totalPages > 1) pages.add(totalPages);
  const sorted = [...pages].sort((a, b) => a - b);
  const slots: Slot[] = [];
  let prev = 0;
  for (const p of sorted) {
    if (prev && p - prev > 1) slots.push('ellipsis');
    slots.push(p);
    prev = p;
  }
  return slots;
}

export const Pagination = ({ page, totalPages, hasNext, canJump, onJump, className }: PaginationProps) => {
  if (totalPages <= 1 && !hasNext) return null;
  const slots = buildSlots(page, totalPages);
  const reachable = (p: number) => (canJump ? canJump(p) : true);

  const arrow = 'w-8 h-8 flex items-center justify-center rounded-lg border border-[#BFA66A] text-[#3F3F3F] transition-all';
  const numBtn = 'min-w-8 h-8 px-2 flex items-center justify-center rounded-lg text-[12px] font-bold transition-all border';

  return (
    <div className={cn('flex items-center gap-1.5', className)}>
      <button
        onClick={() => onJump(page - 1)}
        disabled={page <= 1}
        aria-label="Previous page"
        className={cn(arrow, page <= 1 ? 'opacity-40 cursor-not-allowed' : 'hover:bg-[#FFF9E8] hover:border-[#8A5A00] cursor-pointer')}
      >
        <ChevronLeft className="w-4 h-4" />
      </button>

      {slots.map((slot, i) =>
        slot === 'ellipsis' ? (
          <span key={`e${i}`} className="px-1 text-[#8A8A7A] select-none">…</span>
        ) : (
          <button
            key={slot}
            onClick={() => slot !== page && onJump(slot)}
            disabled={slot !== page && !reachable(slot)}
            aria-current={slot === page ? 'page' : undefined}
            title={slot !== page && !reachable(slot) ? 'Not directly reachable — use Next/Prev' : undefined}
            className={cn(
              numBtn,
              slot === page
                ? 'bg-[#F3E2A7] border-[#8A5A00] text-[#111111] cursor-default'
                : reachable(slot)
                  ? 'bg-white border-[#BFA66A] text-[#3F3F3F] hover:bg-[#FFF9E8] hover:border-[#8A5A00] cursor-pointer'
                  : 'bg-white border-[#E5E0D2] text-[#B8AA91] cursor-not-allowed',
            )}
          >
            {slot}
          </button>
        ),
      )}

      <button
        onClick={() => onJump(page + 1)}
        disabled={!hasNext}
        aria-label="Next page"
        className={cn(arrow, !hasNext ? 'opacity-40 cursor-not-allowed' : 'hover:bg-[#FFF9E8] hover:border-[#8A5A00] cursor-pointer')}
      >
        <ChevronRight className="w-4 h-4" />
      </button>
    </div>
  );
};
