import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { cn } from '../lib/utils';
import { Icon } from './Icon';

/*
 * Figma "Select" (2080:616): State = Closed | Open | Error, and "Spec · Component · Select":
 * 36px field, list radius-md + shadow-md, options 32px; more than 8 options adds a search box.
 * ARIA combobox: ↑/↓ move, Enter picks, Esc closes and keeps the old value.
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
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const listId = useId();

  const searchable = options.length > SEARCH_THRESHOLD;
  const visible = useMemo(
    () =>
      query ? options.filter((o) => o.label.toLowerCase().includes(query.toLowerCase())) : options,
    [options, query],
  );
  const selected = options.find((o) => o.value === value);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  const openList = () => {
    if (disabled) return;
    setQuery('');
    setActive(
      Math.max(
        0,
        options.findIndex((o) => o.value === value),
      ),
    );
    setOpen(true);
  };

  const pick = (option: SelectOption<V> | undefined) => {
    if (!option) return;
    onChange(option.value);
    setOpen(false);
  };

  const onKeyDown = (event: KeyboardEvent) => {
    if (!open) {
      if (['ArrowDown', 'Enter', ' '].includes(event.key)) {
        event.preventDefault();
        openList();
      }
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      setOpen(false);
    } else if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActive((i) => Math.min(i + 1, visible.length - 1));
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActive((i) => Math.max(i - 1, 0));
    } else if (event.key === 'Enter') {
      event.preventDefault();
      pick(visible[active]);
    }
  };

  return (
    <div
      ref={rootRef}
      className={cn('relative flex flex-col gap-da-xs', className)}
      onKeyDown={onKeyDown}
    >
      <button
        type="button"
        role="combobox"
        aria-label={label}
        aria-expanded={open}
        aria-controls={listId}
        aria-invalid={error ? true : undefined}
        disabled={disabled}
        onClick={() => (open ? setOpen(false) : openList())}
        className={cn(
          'flex w-full items-center rounded-md border bg-card px-da-md text-left da-body-small',
          size === 'sm' ? 'h-7' : 'h-9',
          open
            ? 'border-ring shadow-[inset_0_0_0_1px_var(--ring)]'
            : error
              ? 'border-error'
              : 'border-border-strong',
          disabled && 'cursor-not-allowed bg-subtle text-muted-foreground',
        )}
      >
        <span
          className={cn(
            'min-w-0 flex-1 truncate',
            selected ? 'text-foreground' : 'text-muted-foreground',
          )}
        >
          {selected?.label ?? placeholder}
        </span>
        <Icon name="chevron-down" className="text-foreground" />
      </button>
      {error && <span className="text-error da-caption">{error}</span>}
      {open && (
        <div className="absolute top-full z-[var(--z-popover)] mt-da-xs flex w-full flex-col rounded-md border border-border bg-card p-da-xs shadow-md">
          {searchable && (
            <input
              autoFocus
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setActive(0);
              }}
              placeholder="Tìm…"
              aria-label={`Tìm trong ${label}`}
              className="mb-da-xs h-8 rounded-sm border border-border px-da-md da-body-small outline-none focus-visible:border-ring focus-visible:shadow-none"
            />
          )}
          <ul id={listId} role="listbox" aria-label={label} className="max-h-64 overflow-y-auto">
            {visible.map((option, index) => {
              const isSelected = option.value === value;
              return (
                <li
                  key={option.value}
                  role="option"
                  aria-selected={isSelected}
                  onMouseEnter={() => setActive(index)}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => pick(option)}
                  className={cn(
                    'flex h-8 cursor-pointer items-center rounded-sm px-da-md da-body-small',
                    isSelected
                      ? 'bg-accent text-primary'
                      : index === active
                        ? 'bg-subtle text-foreground'
                        : 'text-foreground',
                  )}
                >
                  <span className="min-w-0 flex-1 truncate">{option.label}</span>
                  {isSelected && <Icon name="check" />}
                </li>
              );
            })}
            {visible.length === 0 && (
              <li className="px-da-md py-da-sm text-muted-foreground da-caption">
                Không có mục khớp
              </li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
