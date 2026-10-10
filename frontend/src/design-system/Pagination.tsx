import { cn } from '../lib/utils';

/*
 * Figma "Pagination" (2080:730): "1–8 / 286", then 28px page cells, the current one on accent.
 * Pages are 1-based; shows first, last, and one either side of the current page with "…" gaps.
 */
export function Pagination({
  page,
  pageSize,
  total,
  onPage,
}: {
  page: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const to = Math.min(page * pageSize, total);

  const shown = [...new Set([1, page - 1, page, page + 1, pages])]
    .filter((p) => p >= 1 && p <= pages)
    .sort((a, b) => a - b);
  const cells: (number | 'gap')[] = [];
  shown.forEach((p, i) => {
    if (i > 0 && p - shown[i - 1] > 1) cells.push('gap');
    cells.push(p);
  });

  const cell = 'inline-flex size-7 items-center justify-center rounded-md da-body-small';
  return (
    <nav aria-label="Phân trang" className="flex items-center gap-da-xs">
      <span className="mr-da-xl text-muted-foreground da-caption">
        {from.toLocaleString('vi-VN')}–{to.toLocaleString('vi-VN')} /{' '}
        {total.toLocaleString('vi-VN')}
      </span>
      <button
        type="button"
        aria-label="Trang trước"
        disabled={page <= 1}
        onClick={() => onPage(page - 1)}
        className={cn(cell, 'text-foreground hover:bg-subtle disabled:text-muted-foreground')}
      >
        ‹
      </button>
      {cells.map((c, i) =>
        c === 'gap' ? (
          <span key={`gap-${i}`} className={cn(cell, 'text-foreground')}>
            …
          </span>
        ) : (
          <button
            key={c}
            type="button"
            aria-current={c === page ? 'page' : undefined}
            onClick={() => onPage(c)}
            className={cn(
              cell,
              c === page ? 'bg-accent text-primary' : 'text-foreground hover:bg-subtle',
            )}
          >
            {c}
          </button>
        ),
      )}
      <button
        type="button"
        aria-label="Trang sau"
        disabled={page >= pages}
        onClick={() => onPage(page + 1)}
        className={cn(cell, 'text-foreground hover:bg-subtle disabled:text-muted-foreground')}
      >
        ›
      </button>
    </nav>
  );
}
