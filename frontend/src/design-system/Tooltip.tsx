import { useRef, type ReactNode, type RefObject } from 'react';
import { mergeProps, Overlay, useOverlayPosition, useTooltip, useTooltipTrigger } from 'react-aria';
import { useTooltipTriggerState, type TooltipTriggerState } from 'react-stately';

/*
 * Figma "Tooltip" (2080:654): foreground background, card-coloured DA/Caption, radius-md.
 * Opens on hover or keyboard focus after 300 ms, 4px above the trigger.
 *
 * React Aria's hooks, not its TooltipTrigger component: that one wants the trigger to be the
 * focusable element itself, while this Tooltip wraps whatever it is given (a Button, a card, a
 * badge) in a span. The bubble is portalled and positioned, so an overflow:hidden parent never
 * clips it and it flips below the trigger at the top of the viewport.
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
  const options = { delay: OPEN_DELAY_MS, closeDelay: 0 };
  const state = useTooltipTriggerState(options);
  const triggerRef = useRef<HTMLSpanElement>(null);
  const { triggerProps, tooltipProps } = useTooltipTrigger(options, state, triggerRef);

  return (
    <span
      ref={triggerRef}
      {...triggerProps}
      className={className ? `relative ${className}` : 'relative inline-flex'}
    >
      {children}
      {state.isOpen && (
        <Bubble state={state} triggerRef={triggerRef} {...tooltipProps}>
          {content}
        </Bubble>
      )}
    </span>
  );
}

function Bubble({
  state,
  triggerRef,
  children,
  ...props
}: {
  state: TooltipTriggerState;
  triggerRef: RefObject<HTMLSpanElement | null>;
  children: ReactNode;
}) {
  const overlayRef = useRef<HTMLSpanElement>(null);
  const { tooltipProps } = useTooltip(props, state);
  const { overlayProps } = useOverlayPosition({
    targetRef: triggerRef,
    overlayRef,
    placement: 'top',
    offset: 4,
    isOpen: state.isOpen,
  });
  return (
    <Overlay>
      <span
        ref={overlayRef}
        {...mergeProps(tooltipProps, overlayProps)}
        className="pointer-events-none z-[var(--z-tooltip)] whitespace-nowrap rounded-md bg-foreground px-da-md py-da-sm text-card da-caption"
      >
        {children}
      </span>
    </Overlay>
  );
}
