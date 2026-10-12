import { useId, useRef, useState, type ReactNode } from 'react';

/*
 * Figma "Tooltip" (2080:654): foreground background, card-coloured DA/Caption, radius-md.
 * Opens on hover or focus after 300 ms, 4px above the trigger.
 */
const OPEN_DELAY_MS = 300;

export function Tooltip({
  content,
  children,
  className,
}: {
  content: ReactNode;
  children: ReactNode;
  /** Layout of the wrapper, e.g. to let a card inside it fill its column. */
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const timer = useRef<number | undefined>(undefined);
  const id = useId();

  const show = () => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setOpen(true), OPEN_DELAY_MS);
  };
  const hide = () => {
    window.clearTimeout(timer.current);
    setOpen(false);
  };

  return (
    <span
      className={className ? `relative ${className}` : 'relative inline-flex'}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
      onKeyDown={(event) => event.key === 'Escape' && hide()}
      aria-describedby={open ? id : undefined}
    >
      {children}
      {open && (
        <span
          id={id}
          role="tooltip"
          className="pointer-events-none absolute bottom-full left-1/2 z-[var(--z-tooltip)] mb-da-xs -translate-x-1/2 whitespace-nowrap rounded-md bg-foreground px-da-md py-da-sm text-card da-caption"
        >
          {content}
        </span>
      )}
    </span>
  );
}
