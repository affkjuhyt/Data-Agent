import React from 'react';
import { cn } from '../lib/utils';

/*
 * Multi-line counterpart of Input (Figma "Input" 2015:118 states): border, radius-md, 12px side
 * padding, Body Small, the same ring on focus and error colour. Grows with `rows`; resizes
 * vertically only.
 */
interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  error?: string;
  /** Code-like content (SQL, a rule, a key) in the mono face. */
  mono?: boolean;
}

export const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ error, mono = false, className, disabled, rows = 3, ...rest }, ref) => (
    <span className={cn('flex flex-col gap-da-xs', className)}>
      <textarea
        ref={ref}
        rows={rows}
        disabled={disabled}
        aria-invalid={error ? true : undefined}
        className={cn(
          'w-full resize-y rounded-md border px-da-lg py-da-md text-foreground outline-none placeholder:text-muted-foreground',
          'focus:border-ring focus:shadow-[inset_0_0_0_1px_var(--ring)] focus-visible:shadow-[inset_0_0_0_1px_var(--ring)]',
          mono ? 'da-mono' : 'da-body-small',
          disabled ? 'cursor-not-allowed border-border bg-subtle' : 'bg-card',
          error ? 'border-error' : 'border-border',
        )}
        {...rest}
      />
      {error && <span className="text-error da-caption">{error}</span>}
    </span>
  ),
);
Textarea.displayName = 'Textarea';
