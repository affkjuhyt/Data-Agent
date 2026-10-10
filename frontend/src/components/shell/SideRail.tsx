import { cn } from '../../lib/utils';
import type { RailGroup } from './areas';

/*
 * The secondary rail of detail screens, as drawn on "P13 · Đội agent" (2309:19811):
 * 208px on --background with a right border, 12px padding. Group headers are DA/Overline in
 * capitals; items are 13/18 with 5×8 padding and radius 6, muted; the active item is a card
 * with a border in the default text colour. No icons (v3.2 visual rules).
 */
export function SideRail({
  groups,
  active,
  onSelect,
  counts,
  label,
}: {
  groups: RailGroup[];
  active: string;
  onSelect: (id: string) => void;
  /** Right-aligned caption per item: "7", "3 đang chạy". */
  counts?: Record<string, string | number | undefined>;
  label: string;
}) {
  return (
    <nav
      aria-label={label}
      className="flex w-[208px] shrink-0 flex-col gap-px overflow-y-auto border-r border-border bg-background px-da-lg pt-da-lg max-[1023px]:hidden"
    >
      {groups.map((group) => (
        <div key={group.label} className="flex flex-col gap-px">
          <span className="pb-da-xs pl-da-md pt-da-lg uppercase text-muted-foreground da-overline">
            {group.label}
          </span>
          {group.items.map((item) => {
            const isActive = item.id === active;
            const count = counts?.[item.id];
            return (
              <a
                key={item.id}
                href="#"
                aria-current={isActive ? 'page' : undefined}
                onClick={(event) => {
                  event.preventDefault();
                  onSelect(item.id);
                }}
                className={cn(
                  'flex items-center gap-da-md rounded-sm border px-da-md py-[5px] da-body-small transition-colors duration-[120ms] ease-out',
                  isActive
                    ? 'border-border bg-card text-foreground'
                    : 'border-transparent text-muted-foreground hover:text-foreground',
                )}
              >
                <span className="min-w-0 flex-1 truncate">{item.label}</span>
                {count !== undefined && <span className="shrink-0 da-caption">{count}</span>}
              </a>
            );
          })}
        </div>
      ))}
    </nav>
  );
}
