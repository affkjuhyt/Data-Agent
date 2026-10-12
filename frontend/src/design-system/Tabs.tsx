import { useRef, type KeyboardEvent } from 'react';
import { cn } from '../lib/utils';

/*
 * Figma "Tab" (2015:123) and "Spec · Component · Tab": a 40px bar with a bottom border,
 * the active tab underlined 2px in primary. role=tablist; ←/→ move, Home/End jump.
 * The caller keeps the value in the URL (?tab=…) so a reload lands on the same tab.
 */
interface TabItem<V extends string> {
  value: V;
  label: string;
  count?: number;
}

export function Tabs<V extends string>({
  items,
  value,
  onChange,
  label,
  className,
}: {
  items: TabItem<V>[];
  value: V;
  onChange: (value: V) => void;
  label: string;
  className?: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const focusAt = (index: number) => {
    const next = (index + items.length) % items.length;
    refs.current[next]?.focus();
    onChange(items[next].value);
  };

  const onKeyDown = (event: KeyboardEvent, index: number) => {
    const moves: Record<string, number> = {
      ArrowRight: index + 1,
      ArrowLeft: index - 1,
      Home: 0,
      End: items.length - 1,
    };
    if (event.key in moves) {
      event.preventDefault();
      focusAt(moves[event.key]);
    }
  };

  return (
    <div
      role="tablist"
      aria-label={label}
      className={cn('flex h-10 items-end border-b border-border', className)}
    >
      {items.map((item, index) => {
        const active = item.value === value;
        return (
          <button
            key={item.value}
            ref={(node) => {
              refs.current[index] = node;
            }}
            type="button"
            role="tab"
            aria-selected={active}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(item.value)}
            onKeyDown={(event) => onKeyDown(event, index)}
            className={cn(
              '-mb-px flex items-center gap-da-xs whitespace-nowrap border-b-2 px-da-lg py-da-md transition-colors duration-[120ms] ease-out',
              active
                ? 'border-primary text-foreground da-body-strong'
                : 'border-transparent text-muted-foreground da-body-small hover:text-foreground',
            )}
          >
            {item.label}
            {item.count !== undefined && (
              <span className="text-muted-foreground da-caption">{item.count}</span>
            )}
          </button>
        );
      })}
    </div>
  );
}
