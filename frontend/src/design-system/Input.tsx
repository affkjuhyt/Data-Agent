import React from 'react';
import { cn } from '../lib/utils';
import { Icon, type IconName } from './Icon';

/*
 * Figma "Input" (2015:118): 36px high, State = Default | Focus | Disabled.
 * Focus is a 2px ring-coloured border; it is drawn as 1px border + 1px inset shadow
 * so the field does not shift by a pixel on focus.
 */
interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  icon?: IconName;
  error?: string;
}

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ icon, error, className, disabled, ...rest }, ref) => (
    <span className={cn('inline-flex flex-col gap-da-xs', className)}>
      <span
        className={cn(
          'flex h-9 items-center gap-da-md rounded-md border px-da-lg',
          'focus-within:border-ring focus-within:shadow-[inset_0_0_0_1px_var(--ring)]',
          disabled ? 'bg-subtle border-border' : 'bg-card',
          error ? 'border-error' : 'border-border',
        )}
      >
        {icon && <Icon name={icon} className="text-foreground" />}
        <input
          ref={ref}
          disabled={disabled}
          aria-invalid={error ? true : undefined}
          className="min-w-0 flex-1 bg-transparent text-foreground outline-none da-body-small placeholder:text-muted-foreground focus-visible:shadow-none disabled:cursor-not-allowed"
          {...rest}
        />
      </span>
      {error && <span className="text-error da-caption">{error}</span>}
    </span>
  ),
);
Input.displayName = 'Input';
