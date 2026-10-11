import { Icon, type IconName } from '../../design-system/Icon';
import { cn } from '../../lib/utils';
import type { AreaId } from './areas';

/*
 * "v3.1 · Mobile · Trang chủ / Hỏi đáp / Cần bạn" (2231:15114, 2231:14983, 2059:1555): under 768px
 * the top bar's areas give way to four tabs at the bottom — Trang chủ, Cần bạn, Agents, Hỏi.
 * 16px icon over a DA/Label word, 72px wide, the current tab in primary; card, top border,
 * 12×8 padding, plus the device's bottom inset.
 */
export type MobileTab = 'home' | 'needs-you' | 'agents' | 'ask';

const TABS: { id: MobileTab; label: string; icon: IconName }[] = [
  { id: 'home', label: 'Trang chủ', icon: 'home' },
  { id: 'needs-you', label: 'Cần bạn', icon: 'approve' },
  { id: 'agents', label: 'Agents', icon: 'agent' },
  { id: 'ask', label: 'Hỏi', icon: 'chat' },
];

/** The tab a page belongs to: Hỏi đáp is "Hỏi", the rest of Phân tích has no tab. */
export const mobileTabOf = (area: AreaId, page: string | undefined): MobileTab | undefined =>
  area === 'analysis' ? (page === 'ASK' ? 'ask' : undefined) : area === 'home' || area === 'needs-you' || area === 'agents' ? area : undefined;

export function MobileTabBar({
  active,
  needsYouCount,
  onSelect,
}: {
  active: MobileTab | undefined;
  needsYouCount?: number;
  onSelect: (tab: MobileTab) => void;
}) {
  return (
    <nav
      aria-label="Điều hướng"
      className="fixed inset-x-0 bottom-0 z-[var(--z-topbar)] flex justify-between border-t border-border bg-card px-da-lg pb-[max(8px,env(safe-area-inset-bottom))] pt-da-md min-[768px]:hidden"
    >
      {TABS.map((tab) => {
        const current = tab.id === active;
        const count = tab.id === 'needs-you' ? needsYouCount : undefined;
        return (
          <button
            key={tab.id}
            type="button"
            aria-current={current ? 'page' : undefined}
            onClick={() => onSelect(tab.id)}
            className={cn(
              'relative flex w-[72px] flex-col items-center gap-da-xs da-label',
              current ? 'text-primary' : 'text-muted-foreground',
            )}
          >
            <Icon name={tab.icon} />
            {tab.label}
            {count !== undefined && count > 0 && (
              <span className="absolute -top-1 left-[calc(50%+4px)] min-w-4 rounded-full bg-warning px-1 text-center text-[10px] leading-4 text-primary-foreground">
                {count}
              </span>
            )}
          </button>
        );
      })}
    </nav>
  );
}
