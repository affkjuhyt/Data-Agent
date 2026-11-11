import {
  parseDate,
  parseDateTime,
  type CalendarDate,
  type CalendarDateTime,
} from '@internationalized/date';
import {
  DateField as AriaDateField,
  DateInput,
  DateSegment,
  I18nProvider,
} from 'react-aria-components';
import { cn } from '../lib/utils';
import { Icon } from './Icon';

/*
 * A date (or date and time) typed day / month / year in that order, whatever the browser's
 * language: the native <input type="date"> drew the browser's order (mm/dd/yyyy in an English
 * browser) on a Vietnamese screen. React Aria's DateField: segments typed or stepped with the arrow
 * keys, min and max held, read by screen readers part by part. The value stays the ISO string the
 * native input gave ("2026-10-20", "2026-10-20T08:30"), '' when empty, so callers did not change.
 * Drawn as the design system's Input: 36px, radius-md, the ring on focus.
 */

type Granular = CalendarDate | CalendarDateTime;

const read = (value: string, withTime: boolean): Granular | null => {
  if (!value) return null;
  try {
    return withTime ? parseDateTime(value) : parseDate(value.slice(0, 10));
  } catch {
    return null;
  }
};

export function DateField({
  value,
  onChange,
  label,
  min,
  max,
  withTime = false,
  clearable = false,
  error,
  className,
}: {
  /** ISO day ("2026-10-20"), or day and minute with `withTime` ("2026-10-20T08:30"); '' = none. */
  value: string;
  onChange: (value: string) => void;
  /** Its accessible name; the visible label is the surrounding Field's. */
  label: string;
  min?: string;
  max?: string;
  withTime?: boolean;
  /** An optional date (a filter): a button empties it, rather than erasing each part. */
  clearable?: boolean;
  error?: string;
  className?: string;
}) {
  return (
    <span className={cn('inline-flex flex-col gap-da-xs', className)}>
      <I18nProvider locale="vi-VN">
        <AriaDateField<Granular>
          aria-label={label}
          value={read(value, withTime)}
          minValue={min ? (read(min, withTime) ?? undefined) : undefined}
          maxValue={max ? (read(max, withTime) ?? undefined) : undefined}
          granularity={withTime ? 'minute' : 'day'}
          hourCycle={24}
          shouldForceLeadingZeros
          isInvalid={Boolean(error)}
          onChange={(next) => onChange(next ? next.toString().slice(0, withTime ? 16 : 10) : '')}
        >
          <span
            className={cn(
              'flex h-9 items-center rounded-md border bg-card px-da-lg text-foreground da-body-small',
              'focus-within:border-ring focus-within:shadow-[inset_0_0_0_1px_var(--ring)]',
              error ? 'border-error' : 'border-border',
            )}
          >
            <DateInput className="flex flex-1 items-center">
              {(segment) => (
                <DateSegment
                  segment={segment}
                  className={({ isPlaceholder, isFocused }) =>
                    cn(
                      'rounded-[2px] px-[1px] tabular-nums outline-none',
                      isPlaceholder && 'text-muted-foreground',
                      isFocused && 'bg-accent text-primary',
                    )
                  }
                />
              )}
            </DateInput>
            {clearable && value && (
              <button
                type="button"
                aria-label={`Xoá ${label.toLowerCase()}`}
                onClick={() => onChange('')}
                className="ml-da-sm text-muted-foreground hover:text-foreground"
              >
                <Icon name="x" size={14} />
              </button>
            )}
          </span>
        </AriaDateField>
      </I18nProvider>
      {error && <span className="text-error da-caption">{error}</span>}
    </span>
  );
}
