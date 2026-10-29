import { type ReactNode } from 'react';
import {
  Button,
  Menu as AriaMenu,
  MenuItem,
  MenuTrigger,
  Popover,
  Separator,
} from 'react-aria-components';
import { cn } from '../lib/utils';
import { Icon, type IconName } from './Icon';

/*
 * Figma "Menu" (2080:651), Type = Context: 240px, card, border, radius-md, shadow-md, padding 4;
 * items 13px with an optional icon and a mono shortcut; a separator is a 1px border line.
 * Opened from a "…" button; Esc or a click outside closes it. React Aria's MenuTrigger: the list is
 * portalled and positioned, ↑/↓ and typeahead move, Enter or a click runs an item and closes.
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
  return (
    <MenuTrigger>
      <Button
        aria-label={label}
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
      </Button>
      <Popover
        placement="bottom end"
        offset={4}
        className="z-[var(--z-popover)] w-60 rounded-md border border-border bg-card p-da-xs shadow-md outline-none"
      >
        <AriaMenu
          aria-label={label}
          onAction={(key) => {
            const entry = items[Number(key)];
            if (entry && entry.kind !== 'separator') entry.onSelect();
          }}
          className="flex flex-col outline-none"
        >
          {items.map((entry, index) =>
            entry.kind === 'separator' ? (
              <Separator key={`separator-${index}`} className="my-da-xs h-px bg-border" />
            ) : (
              <MenuItem
                key={index}
                id={index}
                textValue={entry.label}
                className={({ isFocused }) =>
                  cn(
                    'flex cursor-pointer items-center gap-da-sm rounded-sm px-da-md py-da-sm text-left da-body-small outline-none',
                    isFocused && 'bg-subtle',
                    entry.danger ? 'text-error' : 'text-foreground',
                  )
                }
              >
                {entry.icon && <Icon name={entry.icon} />}
                <span className="min-w-0 flex-1">{entry.label}</span>
                {entry.shortcut && (
                  <span className="text-muted-foreground da-mono">{entry.shortcut}</span>
                )}
              </MenuItem>
            ),
          )}
        </AriaMenu>
      </Popover>
    </MenuTrigger>
  );
}
