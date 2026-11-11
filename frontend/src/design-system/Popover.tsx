import type { ReactNode } from 'react';
import { Button, Dialog, DialogTrigger, Popover as AriaPopover } from 'react-aria-components';
import { cn } from '../lib/utils';

/*
 * A small non-modal panel opened from a button ("Bộ lọc n" of an answer's result): card, border,
 * radius-md, shadow-md, 12px padding. React Aria's DialogTrigger and Popover: the panel is
 * portalled and placed next to its button (flipped when there is no room), Esc or a click
 * outside closes it and focus goes back to the button. For a list of actions use Menu instead.
 */
export function Popover({
  trigger,
  triggerClassName,
  label,
  placement = 'bottom end',
  className,
  children,
}: {
  /** The button's content; it names the button. */
  trigger: ReactNode;
  triggerClassName?: string;
  /** The panel's accessible name. */
  label: string;
  placement?: 'bottom start' | 'bottom end';
  /** Size of the panel, e.g. w-64. */
  className?: string;
  children: ReactNode;
}) {
  return (
    <DialogTrigger>
      <Button className={triggerClassName}>{trigger}</Button>
      <AriaPopover
        placement={placement}
        offset={4}
        className={cn(
          'z-[var(--z-popover)] rounded-md border border-border bg-card p-da-md shadow-md outline-none',
          className,
        )}
      >
        <Dialog aria-label={label} className="outline-none">
          {children}
        </Dialog>
      </AriaPopover>
    </DialogTrigger>
  );
}
