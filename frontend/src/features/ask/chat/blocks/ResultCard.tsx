import { useState } from 'react';
import { Dialog } from '../../../../design-system/Dialog';
import { Icon } from '../../../../design-system/Icon';
import { Menu } from '../../../../design-system/Menu';
import { cn } from '../../../../lib/utils';
import { ChartRenderer } from '../../components/ChartRenderer';
import { DataGrid } from '../../components/DataGrid';
import type { ReadingChip } from '../../interpretation';
import type { ChartSpec } from '../../types';
import { RunSql } from '../RunSql';

/*
 * The answer's result as one card: the chart or its table (a toggle), and a toolbar —
 * "Khám phá" opens the SQL that ran (design C8), "Xem" the card full size, "Bộ lọc n" the
 * filters the answer applied, read-only (C7: changing one is a follow-up question, so no
 * un-governed figure appears inside an answer), and "…" for CSV and saving to a session.
 */

type View = 'chart' | 'table';

export function ResultCard({
  title,
  chart,
  columns,
  rows,
  rowCount,
  reading,
  queryRunId,
  onExportCsv,
  onSaveToSession,
  onPick,
}: {
  title: string;
  chart: ChartSpec | null;
  columns: string[];
  rows: Record<string, unknown>[];
  rowCount: number | null;
  reading: ReadingChip[];
  queryRunId: string | null;
  onExportCsv?: () => void;
  onSaveToSession?: () => void;
  /** A bar or point picked on the chart: the member to drill into (DrillPicker). */
  onPick?: (category: string) => void;
}) {
  const [view, setView] = useState<View>(chart ? 'chart' : 'table');
  const [full, setFull] = useState(false);
  const [explore, setExplore] = useState(false);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const filters = reading.filter((chip) => chip.label !== 'Chỉ số');
  const menu = [
    ...(onExportCsv ? [{ label: 'Tải CSV', icon: 'download' as const, onSelect: onExportCsv }] : []),
    ...(onSaveToSession ? [{ label: 'Lưu vào phiên', icon: 'bookmark' as const, onSelect: onSaveToSession }] : []),
  ];

  const body = (big: boolean) =>
    view === 'chart' && chart ? (
      <div className={big ? 'h-[480px]' : 'h-[280px]'}>
        <ChartRenderer spec={chart} onPick={onPick} />
      </div>
    ) : (
      <DataGrid columns={columns} rows={rows} rowCount={rowCount} title={title} pageSize={big ? 20 : 8} />
    );

  const toolButton = 'inline-flex items-center gap-[4px] rounded-sm px-da-sm py-[3px] text-muted-foreground da-body-small hover:bg-subtle hover:text-foreground';

  return (
    <section aria-label={`Kết quả: ${title}`} className="flex flex-col gap-da-sm rounded-lg border border-border bg-card p-da-md">
      <header className="flex flex-wrap items-center gap-da-sm">
        <h3 className="m-0 min-w-0 flex-1 truncate text-foreground da-body-strong">{title}</h3>
        {chart && (
          <div role="tablist" aria-label="Cách hiển thị" className="inline-flex rounded-md border border-border p-[2px]">
            {(['chart', 'table'] as const).map((v) => (
              <button
                key={v}
                type="button"
                role="tab"
                aria-selected={view === v}
                onClick={() => setView(v)}
                className={cn('rounded-sm px-da-sm py-[2px] da-caption', view === v ? 'bg-accent text-primary' : 'text-muted-foreground')}
              >
                {v === 'chart' ? 'Biểu đồ' : 'Bảng'}
              </button>
            ))}
          </div>
        )}
        {queryRunId && (
          <button type="button" className={toolButton} onClick={() => setExplore(true)}>
            <Icon name="search" size={14} /> Khám phá
          </button>
        )}
        <button type="button" className={toolButton} onClick={() => setFull(true)}>
          <Icon name="external-link" size={14} /> Xem
        </button>
        {filters.length > 0 && (
          <div className="relative">
            <button
              type="button"
              className={toolButton}
              aria-expanded={filtersOpen}
              onClick={() => setFiltersOpen((v) => !v)}
            >
              <Icon name="filter" size={14} /> Bộ lọc {filters.length}
            </button>
            {filtersOpen && (
              <div
                role="dialog"
                aria-label="Bộ lọc của câu trả lời"
                className="absolute right-0 z-[var(--z-popover)] mt-da-xs w-64 rounded-md border border-border bg-card p-da-md shadow-md"
              >
                <ul className="m-0 flex list-none flex-col gap-da-xs p-0 da-body-small">
                  {filters.map((chip) => (
                    <li key={chip.key} className="flex justify-between gap-da-md">
                      <span className="text-muted-foreground">{chip.label}</span>
                      <span className="text-foreground">{chip.value}</span>
                    </li>
                  ))}
                </ul>
                <p className="m-0 mt-da-sm text-muted-foreground da-caption">Muốn đổi bộ lọc, hãy hỏi tiếp.</p>
              </div>
            )}
          </div>
        )}
        {menu.length > 0 && <Menu label="Thêm thao tác với kết quả" items={menu} />}
      </header>
      {body(false)}
      <Dialog open={full} title={title} size="lg" onClose={() => setFull(false)}>
        {body(true)}
      </Dialog>
      {queryRunId && (
        <Dialog open={explore} title="Truy vấn của câu trả lời" size="lg" onClose={() => setExplore(false)}>
          {explore && <RunSql queryRunId={queryRunId} />}
        </Dialog>
      )}
    </section>
  );
}
