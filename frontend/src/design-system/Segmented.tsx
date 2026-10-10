import { cn } from '../lib/utils';

/*
 * Segmented filter of the v3.1 lists ("Đang chờ · 4 | Sắp hết hạn · 1 | …" on "v3.1 · Cần bạn",
 * 2149:15816). The spec: list filters use this, not Tab. A subtle track with 4px padding and
 * 2px gaps; the chosen segment is a card with a border (radius-sm), the rest muted text.
 * `outline` is the view switch of "Lượt chạy · Đang làm gì" (2310:20090): a bordered strip
 * (radius-sm) split by lines, the chosen view on subtle in Body Strong.
 */
interface Segment<V extends string> {
  value: V;
  label: string;
}

export function Segmented<V extends string>({
  items,
  value,
  onChange,
  label,
  variant = 'track',
}: {
  items: Segment<V>[];
  value: V;
  onChange: (value: V) => void;
  label: string;
  variant?: 'track' | 'outline';
}) {
  if (variant === 'outline') {
    return (
      <div
        role="radiogroup"
        aria-label={label}
        className="inline-flex w-fit overflow-hidden rounded-sm border border-border"
      >
        {items.map((item) => {
          const active = item.value === value;
          return (
            <button
              key={item.value}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => onChange(item.value)}
              className={cn(
                'whitespace-nowrap border-r border-border px-da-lg py-da-sm',
                active
                  ? 'bg-subtle text-foreground da-body-strong'
                  : 'text-muted-foreground da-body-small hover:text-foreground',
              )}
            >
              {item.label}
            </button>
          );
        })}
      </div>
    );
  }
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className="inline-flex w-fit gap-[2px] rounded-md bg-subtle p-da-xs"
    >
      {items.map((item) => {
        const active = item.value === value;
        return (
          <button
            key={item.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(item.value)}
            className={cn(
              'whitespace-nowrap rounded-sm border px-da-md py-da-xs da-body-strong transition-colors duration-[120ms] ease-out',
              active
                ? 'border-border bg-card text-foreground'
                : 'border-transparent text-muted-foreground hover:text-foreground',
            )}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}
