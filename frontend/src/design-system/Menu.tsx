import { useEffect, useRef, useState, type ReactNode } from 'react';
import { cn } from '../lib/utils';
import { Icon, type IconName } from './Icon';

/*
 * Figma "Menu" (2080:651), Type = Context: 240px, card, border, radius-md, shadow-md, padding 4;
 * items 13px with an optional icon and a mono shortcut; a separator is a 1px border line.
 * Opened from a "…" button; Esc or a click outside closes it.
 */
type MenuEntry =
  | {
      kind?: 'item';
      label: string;
      icon?: IconName;
      shortcut?: string;
      danger?: boolean;
      onSelect: () => void;
    }
  | { kind: 'separator' };

export function Menu({
  trigger,
  triggerKind = 'icon',
  items,
  label,
}: {
  trigger?: ReactNode;
  /** icon = the 28px "…" button; button = a ghost text button with a chevron ("Chọn bộ sưu tập"). */
  triggerKind?: 'icon' | 'button';
  items: MenuEntry[];
  label: string;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  return (
    <div
      ref={rootRef}
      className="relative inline-flex"
      onKeyDown={(event) => event.key === 'Escape' && setOpen(false)}
    >
      <button
        type="button"
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className={
          triggerKind === 'button'
            ? 'inline-flex h-9 items-center gap-da-sm rounded-md px-da-lg text-foreground da-body-strong hover:bg-subtle'
            : 'inline-flex size-7 items-center justify-center rounded-md text-foreground hover:bg-subtle'
        }
      >
        {triggerKind === 'button' ? (
          <>
            {trigger ?? label}
            <Icon name="chevron-down" />
          </>
        ) : (
          (trigger ?? <Icon name="more" />)
        )}
      </button>
      {open && (
        <div
          role="menu"
          aria-label={label}
          className="absolute right-0 top-full z-[var(--z-popover)] mt-da-xs flex w-60 flex-col rounded-md border border-border bg-card p-da-xs shadow-md"
        >
          {items.map((entry, index) =>
            entry.kind === 'separator' ? (
              <div
                key={`separator-${index}`}
                role="separator"
                className="my-da-xs h-px bg-border"
              />
            ) : (
              <button
                key={entry.label}
                type="button"
                role="menuitem"
                onClick={() => {
                  setOpen(false);
                  entry.onSelect();
                }}
                className={cn(
                  'flex items-center gap-da-sm rounded-sm px-da-md py-da-sm text-left da-body-small hover:bg-subtle focus-visible:bg-subtle',
                  entry.danger ? 'text-error' : 'text-foreground',
                )}
              >
                {entry.icon && <Icon name={entry.icon} />}
                <span className="min-w-0 flex-1">{entry.label}</span>
                {entry.shortcut && (
                  <span className="text-muted-foreground da-mono">{entry.shortcut}</span>
                )}
              </button>
            ),
          )}
        </div>
      )}
    </div>
  );
}
