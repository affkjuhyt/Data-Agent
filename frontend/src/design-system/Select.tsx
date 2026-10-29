import { useId } from 'react';
import {
  Select as AriaSelect,
  Autocomplete,
  Button,
  Input,
  ListBox,
  ListBoxItem,
  Popover,
  SearchField,
  SelectValue,
  useFilter,
} from 'react-aria-components';
import { cn } from '../lib/utils';
import { Icon } from './Icon';

/*
 * Figma "Select" (2080:616): State = Closed | Open | Error, and "Spec · Component · Select":
 * 36px field, list radius-md + shadow-md, options 32px; more than 8 options adds a search box.
 * React Aria's Select: a button that opens a listbox in a popover (portalled, so a scrolling
 * Drawer or Dialog never clips it); ↑/↓ and typeahead move, Enter picks, Esc closes and keeps the
 * old value without closing a dialog around it.
 */
interface SelectOption<V extends string> {
  value: V;
  label: string;
}

const SEARCH_THRESHOLD = 8;

export function Select<V extends string>({
  options,
  value,
  onChange,
  label,
  placeholder = 'Chọn…',
  error,
  disabled,
  size = 'md',
  className,
}: {
  options: SelectOption<V>[];
  value: V | null;
  onChange: (value: V) => void;
  /** Accessible name; render a visible label with <Field> around the Select. */
  label: string;
  placeholder?: string;
  error?: string;
  disabled?: boolean;
  /** sm = the 28px filter pills of page headers ("Kỳ: tuần 41"); md = the 36px form field. */
  size?: 'md' | 'sm';
  className?: string;
}) {
  // React Aria names the button "<value> <label>"; the field is named by its label alone, as the
  // Field around it shows, and the value is the button's text.
  const nameId = useId();
  const errorId = useId();
  const { contains } = useFilter({ sensitivity: 'base' });
  const searchable = options.length > SEARCH_THRESHOLD;

  const list = (
    <ListBox
      items={options.map((o) => ({ id: o.value, label: o.label }))}
      aria-label={label}
      // React Aria wraps this in role=option, so it is only for a search that matched nothing; an
      // empty list (options still loading) says so outside the listbox instead.
      renderEmptyState={searchable ? () => <Empty /> : undefined}
      className="max-h-64 overflow-y-auto outline-none"
    >
      {(item) => (
        <ListBoxItem
          id={item.id}
          textValue={item.label}
          className={({ isSelected, isFocused }) =>
            cn(
              'flex h-8 cursor-pointer items-center rounded-sm px-da-md da-body-small outline-none',
              isSelected
                ? 'bg-accent text-primary'
                : isFocused
                  ? 'bg-subtle text-foreground'
                  : 'text-foreground',
            )
          }
        >
          {({ isSelected }) => (
            <>
              <span className="min-w-0 flex-1 truncate">{item.label}</span>
              {isSelected && <Icon name="check" />}
            </>
          )}
        </ListBoxItem>
      )}
    </ListBox>
  );

  return (
    <AriaSelect
      selectedKey={value}
      onSelectionChange={(key) => key !== null && onChange(key as V)}
      isDisabled={disabled}
      isInvalid={Boolean(error)}
      // Options often arrive after the page draws; the list opens anyway and shows them when they do.
      allowsEmptyCollection
      aria-label={label}
      placeholder={placeholder}
      className={cn('relative flex flex-col gap-da-xs', className)}
    >
      <span id={nameId} hidden>
        {label}
      </span>
      <Button
        aria-labelledby={nameId}
        aria-describedby={error ? errorId : undefined}
        // React Aria holds the button pressed while the list is open.
        className={({ isPressed }) =>
          cn(
            'flex w-full items-center rounded-md border bg-card px-da-md text-left da-body-small',
            size === 'sm' ? 'h-7' : 'h-9',
            isPressed
              ? 'border-ring shadow-[inset_0_0_0_1px_var(--ring)]'
              : error
                ? 'border-error'
                : 'border-border-strong',
            disabled && 'cursor-not-allowed bg-subtle text-muted-foreground',
          )
        }
      >
        <SelectValue
          className={({ isPlaceholder }) =>
            cn(
              'min-w-0 flex-1 truncate',
              isPlaceholder ? 'text-muted-foreground' : 'text-foreground',
            )
          }
        />
        <Icon name="chevron-down" className="text-foreground" />
      </Button>
      {error && (
        <span id={errorId} className="text-error da-caption">
          {error}
        </span>
      )}
      <Popover
        offset={4}
        className="z-[var(--z-popover)] flex w-[var(--trigger-width)] flex-col rounded-md border border-border bg-card p-da-xs shadow-md outline-none"
      >
        {options.length === 0 && <Empty />}
        {searchable ? (
          <Autocomplete filter={contains}>
            <SearchField aria-label={`Tìm trong ${label}`} autoFocus className="mb-da-xs">
              <Input
                placeholder="Tìm…"
                className="h-8 w-full rounded-sm border border-border px-da-md da-body-small outline-none focus-visible:border-ring"
              />
            </SearchField>
            {list}
          </Autocomplete>
        ) : (
          list
        )}
      </Popover>
    </AriaSelect>
  );
}

function Empty() {
  return (
    <span className="block px-da-md py-da-sm text-muted-foreground da-caption">
      Không có mục khớp
    </span>
  );
}
