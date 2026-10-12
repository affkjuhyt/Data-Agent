import { useEffect, type KeyboardEvent, type RefObject } from 'react';

// The first field gets focus, not the × of the header; a surface without fields focuses its first button.
const FIELDS =
  'input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [role="combobox"]';
const FOCUSABLE =
  'input:not([disabled]), textarea:not([disabled]), select:not([disabled]), button:not([aria-disabled="true"]):not([disabled]), [role="combobox"], a[href]';

/*
 * Shared by Dialog and Drawer: focus moves inside on open and back to the opener on close,
 * Tab stays inside, Esc closes. A control inside that handles Esc itself (a Select closing its
 * list) calls preventDefault, and then the surface stays open.
 */
export function useModalFocus(
  open: boolean,
  ref: RefObject<HTMLElement | null>,
  onClose: () => void,
) {
  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    let first = ref.current?.querySelector<HTMLElement>(FIELDS);
    // In a radio group the checked option is the natural first stop, not the group's first radio.
    if (first instanceof HTMLInputElement && first.type === 'radio' && !first.checked) {
      const group = first.name;
      first =
        Array.from(
          ref.current?.querySelectorAll<HTMLInputElement>('input[type="radio"]:checked') ?? [],
        ).find((radio) => radio.name === group) ?? first;
    }
    (first ?? ref.current?.querySelector<HTMLElement>(FOCUSABLE))?.focus();
    return () => previous?.focus();
  }, [open, ref]);

  return (event: KeyboardEvent) => {
    if (event.key === 'Escape') {
      if (event.defaultPrevented) return;
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== 'Tab') return;
    const focusable = ref.current?.querySelectorAll<HTMLElement>(FOCUSABLE);
    if (!focusable || focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };
}
