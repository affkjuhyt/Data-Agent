import type { ReactNode } from 'react';
import { Radio as AriaRadio, RadioGroup as AriaRadioGroup } from 'react-aria-components';
import { cn } from '../lib/utils';
import radioOff from './assets/radio-off.svg';
import radioOn from './assets/radio-on.svg';
import radioDisabled from './assets/radio-disabled.svg';

/*
 * Figma "Radio" (2080:550): State = Off | On | Disabled; the circle is the Figma asset of each state.
 * Wrap a set in <fieldset> with a <legend> so the options read as one radio group. A new set uses
 * RadioGroup below, which names the group and holds the value itself.
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

/*
 * A set of choices as one React Aria RadioGroup: named by `label`, one tab stop, the arrow keys
 * move the choice, the focus ring shows for the keyboard only. Hand-built groups (a div with
 * role="radiogroup" around buttons with aria-checked) were a tab stop per option and ignored the
 * arrow keys. Options are RadioOption (the Figma circle and a label) or RadioCard (a bordered
 * choice with its consequence, "v3.1 · Cần bạn").
 */
export function RadioGroup<V extends string>({
  label,
  value,
  onChange,
  orientation = 'vertical',
  className,
  children,
}: {
  label: string;
  value: V | null;
  onChange: (value: V) => void;
  orientation?: 'vertical' | 'horizontal';
  /** Layout of the options; the default stacks them 8px apart (a row 16px apart when horizontal). */
  className?: string;
  children: ReactNode;
}) {
  return (
    <AriaRadioGroup
      aria-label={label}
      value={value}
      onChange={(next) => onChange(next as V)}
      orientation={orientation}
      className={cn(
        orientation === 'horizontal' ? 'flex gap-da-xl' : 'flex flex-col gap-da-md',
        className,
      )}
    >
      {children}
    </AriaRadioGroup>
  );
}

export function RadioOption({
  value,
  disabled,
  children,
}: {
  value: string;
  disabled?: boolean;
  children: ReactNode;
}) {
  return (
    <AriaRadio
      value={value}
      isDisabled={disabled}
      className={cn(
        'inline-flex w-fit items-center gap-da-sm da-body-small',
        disabled ? 'cursor-not-allowed text-muted-foreground' : 'cursor-pointer text-foreground',
      )}
    >
      {({ isSelected, isFocusVisible }) => (
        <>
          <img
            src={disabled ? radioDisabled : isSelected ? radioOn : radioOff}
            alt=""
            width={16}
            height={16}
            className={cn('shrink-0 rounded-full', isFocusVisible && 'shadow-[var(--focus-ring)]')}
          />
          {children}
        </>
      )}
    </AriaRadio>
  );
}

export function RadioCard({
  value,
  label,
  description,
}: {
  value: string;
  label: string;
  description?: string;
}) {
  return (
    <AriaRadio
      value={value}
      className={({ isSelected, isFocusVisible }) =>
        cn(
          'flex min-w-0 flex-1 cursor-pointer flex-col gap-[2px] rounded-md px-da-md py-da-sm text-left',
          isSelected
            ? 'border-[1.5px] border-primary'
            : 'border border-border hover:border-border-strong',
          isFocusVisible && 'shadow-[var(--focus-ring)]',
        )
      }
    >
      {({ isSelected }) => (
        <>
          <span className={cn('da-body-strong', isSelected ? 'text-primary' : 'text-foreground')}>
            {label}
          </span>
          {description && <span className="text-muted-foreground da-caption">{description}</span>}
        </>
      )}
    </AriaRadio>
  );
}
