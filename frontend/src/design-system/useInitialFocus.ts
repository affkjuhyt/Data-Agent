import { useEffect, type RefObject } from 'react';

// The first field gets focus, not the × of the header; a surface without fields focuses its first button.
const FIELDS =
  'input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [aria-haspopup="listbox"]';
const FOCUSABLE =
  'input:not([disabled]), textarea:not([disabled]), select:not([disabled]), button:not([aria-disabled="true"]):not([disabled]), [aria-haspopup="listbox"], a[href]';

/*
 * Shared by Dialog and Drawer. React Aria traps Tab, closes on Esc and gives focus back to the
 * opener; where focus starts is ours. It runs before React Aria's own effect, which then finds
 * focus already inside and leaves it there.
 */
export function useInitialFocus(ref: RefObject<HTMLElement | null>) {
  useEffect(() => {
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
  }, [ref]);
}
