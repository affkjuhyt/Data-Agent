import React from 'react';
import { cn } from '../lib/utils';
import { Icon, type IconName } from './Icon';

/*
 * Figma "Button" (2015:86): Kind = Primary | Secondary | Ghost | Danger,
 * State = Default | Hover | Focus | Loading | Disabled.
 * At most one Primary per region; Danger only for destructive actions, always behind ConfirmDialog.
 *
 * Every kind carries a 1px border (transparent for Primary/Ghost) so all kinds are the 36px
 * of the spec — in the Figma variants only bordered kinds reach 36, the others are 34.
 */
export type ButtonKind = 'primary' | 'secondary' | 'ghost' | 'danger';

const KIND: Record<ButtonKind, string> = {
  primary: 'bg-primary border-transparent text-primary-foreground hover:bg-primary-hover',
  secondary: 'bg-card border-border-strong text-foreground hover:bg-subtle',
  ghost: 'bg-transparent border-transparent text-foreground hover:bg-subtle',
  danger: 'bg-card border-error text-error hover:bg-subtle',
};

const DISABLED: Record<ButtonKind, string> = {
  primary: 'bg-subtle border-border text-muted-foreground',
  secondary: 'bg-subtle border-border text-muted-foreground',
  danger: 'bg-subtle border-border text-muted-foreground',
  ghost: 'bg-transparent border-transparent text-muted-foreground',
};

/** The look of a Button, for an element that must be something else (a link: LinkButton). */
export const buttonClass = ({
  kind = 'secondary',
  size = 'md',
  disabled = false,
  loading = false,
  className,
}: {
  kind?: ButtonKind;
  size?: 'md' | 'sm';
  disabled?: boolean;
  loading?: boolean;
  className?: string;
}) =>
  cn(
    'inline-flex shrink-0 items-center justify-center gap-da-sm whitespace-nowrap rounded-md border px-da-lg da-body-strong',
    'transition-colors duration-[120ms] ease-out',
    size === 'md' ? 'h-9' : 'h-8',
    disabled ? DISABLED[kind] : KIND[kind],
    loading && 'opacity-85 pointer-events-none',
    (disabled || loading) && 'cursor-not-allowed',
    className,
  );

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  kind?: ButtonKind;
  size?: 'md' | 'sm';
  icon?: IconName;
  loading?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      kind = 'secondary',
      size = 'md',
      icon,
      loading = false,
      disabled,
      className,
      children,
      type = 'button',
      onClick,
      ...rest
    },
    ref,
  ) => {
    const inactive = disabled || loading;
    return (
      <button
        ref={ref}
        type={type}
        // Disabled buttons stay focusable so their tooltip (the reason) can be read.
        aria-disabled={inactive || undefined}
        aria-busy={loading || undefined}
        onClick={inactive ? (event) => event.preventDefault() : onClick}
        className={buttonClass({ kind, size, disabled, loading, className })}
        {...rest}
      >
        {loading ? <Icon name="refresh" className="animate-spin" /> : icon && <Icon name={icon} />}
        {children}
      </button>
    );
  },
);
Button.displayName = 'Button';
