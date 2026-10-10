/*
 * Figma "SkeletonRow" (2080:736): a 44px row of four 10px bars on subtle.
 * Shown only when loading takes ≥ 300 ms (see useDelayedFlag); keep headers and filters visible.
 */
const BAR_WIDTHS = [160, 240, 80, 100];

function SkeletonRow() {
  return (
    <div className="flex h-11 items-center gap-da-lg bg-card px-da-lg" aria-hidden>
      {BAR_WIDTHS.map((width) => (
        <span
          key={width}
          className="h-2.5 shrink-0 animate-pulse rounded-[4px] bg-subtle"
          style={{ width }}
        />
      ))}
    </div>
  );
}

export function SkeletonRows({
  count = 5,
  label = 'Đang tải…',
}: {
  count?: number;
  label?: string;
}) {
  return (
    <div role="status" aria-label={label} className="flex flex-col">
      {Array.from({ length: count }, (_, i) => (
        <SkeletonRow key={i} />
      ))}
    </div>
  );
}
