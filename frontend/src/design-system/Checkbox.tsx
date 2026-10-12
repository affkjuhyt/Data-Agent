import type { ReactNode } from 'react';
import { cn } from '../lib/utils';
import { Icon } from './Icon';

/*
 * Figma "Checkbox" (2080:503): State = Off | On | Indeterminate | Disabled; box 16px radius-sm.
 * In a form it applies on save; use Toggle for settings that apply immediately.
 */
export function Checkbox({
  checked,
  indeterminate = false,
  onChange,
  disabled,
  children,
}: {
  checked: boolean;
  indeterminate?: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  children?: ReactNode;
}) {
  const on = checked || indeterminate;
  return (
    <label
      className={cn(
        'inline-flex items-center gap-da-sm da-body-small',
        disabled ? 'cursor-not-allowed text-muted-foreground' : 'cursor-pointer text-foreground',
      )}
    >
      <input
        type="checkbox"
        className="peer sr-only"
        checked={checked}
        disabled={disabled}
        ref={(node) => {
          if (node) node.indeterminate = indeterminate;
        }}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span
        aria-hidden
        className={cn(
          'relative flex size-4 shrink-0 items-center justify-center rounded-sm peer-focus-visible:shadow-[var(--focus-ring)]',
          disabled
            ? 'border border-border-strong bg-subtle'
            : on
              ? 'bg-primary text-primary-foreground'
              : 'border border-border-strong bg-card',
        )}
      >
        {!disabled && checked && !indeterminate && <Icon name="check" />}
        {!disabled && indeterminate && <span className="h-[2px] w-2 bg-primary-foreground" />}
      </span>
      {children}
    </label>
  );
}
