import type { ReactNode } from 'react';
import { cn } from '../lib/utils';
import radioOff from './assets/radio-off.svg';
import radioOn from './assets/radio-on.svg';
import radioDisabled from './assets/radio-disabled.svg';

/*
 * Figma "Radio" (2080:550): State = Off | On | Disabled; the circle is the Figma asset of each state.
 * Wrap a set in <fieldset> with a <legend> so the options read as one radio group.
 */
export function Radio({
  name,
  value,
  checked,
  onChange,
  disabled,
  children,
}: {
  name: string;
  value: string;
  checked: boolean;
  onChange: (value: string) => void;
  disabled?: boolean;
  children: ReactNode;
}) {
  const src = disabled ? radioDisabled : checked ? radioOn : radioOff;
  return (
    <label
      className={cn(
        'inline-flex items-center gap-da-sm da-body-small',
        disabled ? 'cursor-not-allowed text-muted-foreground' : 'cursor-pointer text-foreground',
      )}
    >
      <input
        type="radio"
        className="peer sr-only"
        name={name}
        value={value}
        checked={checked}
        disabled={disabled}
        onChange={() => onChange(value)}
      />
      <img
        src={src}
        alt=""
        width={16}
        height={16}
        className="shrink-0 rounded-full peer-focus-visible:shadow-[var(--focus-ring)]"
      />
      {children}
    </label>
  );
}
