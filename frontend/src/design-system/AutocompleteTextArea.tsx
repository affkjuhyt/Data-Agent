import type { KeyboardEvent, ReactNode, Ref } from 'react';
import { Autocomplete, ListBox, ListBoxItem, TextArea, TextField } from 'react-aria-components';
import { cn } from '../lib/utils';

/*
 * A text area with a suggestion list the caller decides (the "@" mentions and "/" commands of the
 * chat box): React Aria's Autocomplete over a TextArea and a ListBox. Focus stays in the text
 * while ↑/↓ move through the suggestions and Enter or a click picks one; the list is tied to the
 * field by aria-controls and aria-activedescendant. The hand-written list was buttons with
 * role="option" that only the mouse could reach.
 *
 * The caller filters: `suggestions` is what to show now, an empty array closes the list. No
 * suggestion is highlighted until the user moves into the list, so Enter keeps doing what the
 * caller's onKeyDown says (send) while the list is merely open.
 */
export interface Suggestion {
  id: string;
  /** What a screen reader reads for the option. */
  textValue: string;
  content: ReactNode;
}

export function AutocompleteTextArea({
  value,
  onChange,
  label,
  suggestions,
  suggestionsLabel,
  onPick,
  onKeyDown,
  textAreaRef,
  placeholder,
  autoFocus,
  className,
  listClassName,
}: {
  value: string;
  onChange: (value: string) => void;
  /** The text area's accessible name. */
  label: string;
  suggestions: Suggestion[];
  suggestionsLabel: string;
  onPick: (id: string) => void;
  /** Runs for every key except an Enter that picks the highlighted suggestion. */
  onKeyDown?: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  textAreaRef?: Ref<HTMLTextAreaElement>;
  placeholder?: string;
  autoFocus?: boolean;
  className?: string;
  /** Where the list sits, e.g. absolutely above the box that holds the text area. */
  listClassName?: string;
}) {
  return (
    <Autocomplete inputValue={value} onInputChange={onChange} disableAutoFocusFirst>
      <TextField aria-label={label} className="flex min-w-0 flex-1">
        <TextArea
          ref={textAreaRef}
          rows={1}
          autoFocus={autoFocus}
          placeholder={placeholder}
          // Autocomplete turns spell checking off for every field it wraps (it is meant for search
          // boxes); a question is prose, so the browser's check stays.
          spellCheck
          onKeyDown={(event) => {
            // Autocomplete has already picked the highlighted suggestion on this Enter.
            if (event.key === 'Enter' && event.currentTarget.getAttribute('aria-activedescendant'))
              return;
            onKeyDown?.(event);
          }}
          className={className}
        />
      </TextField>
      {suggestions.length > 0 && (
        <ListBox
          aria-label={suggestionsLabel}
          items={suggestions}
          onAction={(id) => onPick(String(id))}
          className={cn(
            'rounded-md border border-border bg-card p-da-xs shadow-md outline-none',
            listClassName,
          )}
        >
          {(item) => (
            <ListBoxItem
              id={item.id}
              textValue={item.textValue}
              className={({ isFocused }) =>
                cn(
                  'flex w-full cursor-pointer items-center gap-da-md rounded-sm px-da-md py-da-sm text-left outline-none hover:bg-subtle',
                  isFocused && 'bg-subtle',
                )
              }
            >
              {item.content}
            </ListBoxItem>
          )}
        </ListBox>
      )}
    </Autocomplete>
  );
}
