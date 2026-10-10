import { cn } from '../lib/utils';

/*
 * Status filter of the run list ("Tất cả · Đang chạy · 3 · …" on "v3.1 · Lượt chạy · danh sách",
 * 2060:1689): radius-full pills, DA/Label; the chosen one inverted (foreground background), the
 * others outlined and muted. One choice at a time.
 */
export function FilterChips<V extends string>({
  items,
  value,
  onChange,
  label,
}: {
  items: { value: V; label: string }[];
  value: V;
  onChange: (value: V) => void;
  label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="flex flex-wrap gap-da-xs">
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
              'whitespace-nowrap rounded-full border px-da-md py-da-xs da-label transition-colors duration-[120ms] ease-out',
              active
                ? 'border-foreground bg-foreground text-primary-foreground'
                : 'border-border text-muted-foreground hover:border-border-strong hover:text-foreground',
            )}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}
