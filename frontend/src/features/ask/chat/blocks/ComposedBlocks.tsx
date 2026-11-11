import { useState, type ReactNode } from 'react';
import { BlockBoundary } from '../../../../design-system/ErrorBoundaries';
import { Icon, type IconName } from '../../../../design-system/Icon';
import { formatNumber } from '../../../../lib/formatVN';
import { MarkdownContent } from '../../components/MarkdownContent';
import { csvFileName, downloadCsv, toCsv } from '../../exportCsv';
import type { AnswerBlockView, ChartSpec, Citation } from '../../types';
import { CaveatsBlock } from './CaveatsBlock';
import { ResultCard } from './ResultCard';

/*
 * An answer the dynamic agent composed (worker_agent/agentic/): its blocks drawn one by one, in
 * the order the agent chose, as they arrive on the stream and the same after reload. Each block
 * type has a renderer in RENDERERS; a type this screen does not know yet is shown as its data,
 * never dropped. The figures of a KPI, chart or table were filled by the server from the turn's
 * datasets, so this only draws them.
 */

const str = (v: unknown) => (typeof v === 'string' ? v : '');
const arr = <T,>(v: unknown) => (Array.isArray(v) ? (v as T[]) : []);
const num = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null);

/** "Tải CSV" on a block downloads that block's rows: the answer's figures are only the first
 *  table or the KPIs, so exporting them from every block gave a table's reader the KPI values. */
const blockCsv = (ctx: Ctx, title: string, columns: string[], rows: Record<string, unknown>[]) =>
  ctx.onExportCsv && rows.length ? () => void downloadCsv(csvFileName(title), toCsv(columns, rows)) : undefined;

/** A block the agent composed carries `composed: true` (blocks.py). */
export const isComposed = (blocks: AnswerBlockView[] | undefined) =>
  !!blocks?.some((b) => (b as Record<string, unknown>).composed === true);

type Ctx = {
  citations?: Citation[];
  onOpenCitation?: (citation: Citation) => void;
  onExportCsv?: () => void;
  onSaveToSession?: () => void;
};
type Block = Record<string, unknown>;

function TrustNote({ block }: { block: Block }) {
  const label = str(block.trust_label);
  if (!label) return null;
  const tone = block.trust === 'certified' ? 'text-success' : 'text-warning';
  return (
    <span className="inline-flex items-center gap-da-xs text-muted-foreground da-caption">
      <Icon name={block.trust === 'certified' ? 'shield' : 'alert'} size={12} className={tone} />
      {label}
    </span>
  );
}

function Kpis({ block }: { block: Block }) {
  const items = arr<Block>(block.items);
  return (
    <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-da-md">
      {items.map((item, i) => {
        const value = num(item.value);
        const change = num(item.change_pct);
        const up = change !== null && change >= 0;
        return (
          <div
            key={`${str(item.label)}-${i}`}
            role="group"
            aria-label={str(item.label)}
            className="flex flex-col gap-[2px] rounded-lg border border-border bg-card px-da-lg py-da-md"
          >
            <span className="text-muted-foreground da-caption">{str(item.label)}</span>
            <span className="text-foreground da-heading-3">
              {value === null ? '—' : formatNumber(value)}
              {str(item.unit) && <span className="ml-da-xs text-muted-foreground da-body-small">{str(item.unit)}</span>}
            </span>
            {change !== null && (
              <span className={`inline-flex items-center gap-da-xs da-caption ${up ? 'text-success' : 'text-error'}`}>
                <Icon name={up ? 'trending-up' : 'trending-down'} size={12} />
                {up ? '+' : ''}
                {formatNumber(change)}% so với {str(item.compare_label) || 'kỳ so sánh'}
              </span>
            )}
            <TrustNote block={item} />
          </div>
        );
      })}
    </div>
  );
}

const CHART_LABEL: Record<string, string> = {
  line: 'Đường',
  bar: 'Cột',
  area: 'Vùng',
  pie: 'Tròn',
  scatter: 'Phân tán',
  composed: 'Kết hợp',
};

function ChartBlock({ block, ctx }: { block: Block; ctx: Ctx }) {
  const spec = block.spec as ChartSpec | undefined;
  // The types the same rows fit, checked by the server (blocks.fitting_charts): changing one
  // redraws the chart from its own data, nothing is asked again (WrenAI's chart adjustment).
  const alternatives = arr<string>(block.alternatives).filter((t) => t in CHART_LABEL);
  const [chosen, setChosen] = useState<string | null>(null);
  if (!spec || !Array.isArray(spec.data)) return null;
  const type = (chosen ?? spec.chart_type) as ChartSpec['chart_type'];
  const shown: ChartSpec = {
    ...spec,
    chart_type: type,
    ...(type === 'composed' && {
      series: spec.y_keys.map((key, i) => ({ key, type: i === 0 ? ('bar' as const) : ('line' as const) })),
    }),
  };
  return (
    <div className="flex flex-col gap-da-xs">
      {alternatives.length > 1 && (
        <div role="group" aria-label="Kiểu biểu đồ" className="flex flex-wrap gap-da-xs">
          {alternatives.map((t) => (
            <button
              key={t}
              type="button"
              aria-pressed={t === type}
              onClick={() => setChosen(t)}
              className={`rounded-md border px-da-sm py-[2px] da-caption ${
                t === type ? 'border-primary bg-primary/10 text-foreground' : 'border-border text-muted-foreground hover:bg-subtle'
              }`}
            >
              {CHART_LABEL[t]}
            </button>
          ))}
        </div>
      )}
      <ResultCard
        key={type}
        title={spec.title || str(block.title) || 'Biểu đồ'}
        chart={shown}
        columns={[spec.x_key, ...spec.y_keys]}
        rows={spec.data}
        rowCount={spec.data.length}
        reading={[]}
        queryRunId={arr<string>(block.query_run_ids)[0] ?? null}
        onExportCsv={blockCsv(ctx, spec.title || str(block.title) || 'bieu-do', [spec.x_key, ...spec.y_keys], spec.data)}
        onSaveToSession={ctx.onSaveToSession}
      />
      <TrustNote block={block} />
    </div>
  );
}

function TableBlock({ block, ctx }: { block: Block; ctx: Ctx }) {
  return (
    <div className="flex flex-col gap-da-xs">
      <ResultCard
        title={str(block.title) || 'Bảng dữ liệu'}
        chart={null}
        columns={arr<string>(block.columns)}
        rows={arr<Record<string, unknown>>(block.rows)}
        rowCount={num(block.row_count)}
        reading={[]}
        queryRunId={arr<string>(block.query_run_ids)[0] ?? null}
        onExportCsv={blockCsv(
          ctx,
          str(block.title) || 'bang-du-lieu',
          arr<string>(block.columns),
          arr<Record<string, unknown>>(block.rows),
        )}
        onSaveToSession={ctx.onSaveToSession}
      />
      <TrustNote block={block} />
    </div>
  );
}

const TONE_ICON: Record<string, { icon: IconName; className: string }> = {
  positive: { icon: 'trending-up', className: 'text-success' },
  negative: { icon: 'trending-down', className: 'text-error' },
  neutral: { icon: 'sparkles', className: 'text-primary' },
};

function Insights({ block, ctx }: { block: Block; ctx: Ctx }) {
  return (
    <section aria-label={str(block.title) || 'Nhận định'} className="flex flex-col gap-da-sm">
      <span className="text-foreground da-label">{str(block.title) || 'Nhận định'}</span>
      <ul className="m-0 flex list-none flex-col gap-da-sm p-0">
        {arr<Block>(block.items).map((item, i) => {
          const tone = TONE_ICON[str(item.tone)] ?? TONE_ICON.neutral;
          return (
            <li key={i} className="flex items-start gap-da-sm">
              <Icon name={tone.icon} size={14} className={`mt-[3px] shrink-0 ${tone.className}`} />
              <MarkdownContent text={str(item.text)} citations={ctx.citations} onOpenCitation={ctx.onOpenCitation} />
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function Callout({ block, ctx }: { block: Block; ctx: Ctx }) {
  const warning = block.tone === 'warning';
  return (
    <aside
      role="note"
      className={`flex items-start gap-da-sm rounded-md border px-da-md py-da-sm ${
        warning ? 'border-warning/30 bg-warning-subtle' : 'border-border bg-subtle'
      }`}
    >
      <Icon name={warning ? 'alert' : 'info'} size={14} className={`mt-[3px] shrink-0 ${warning ? 'text-warning' : 'text-primary'}`} />
      <div className="flex flex-col gap-da-xs">
        {str(block.title) && <span className="text-foreground da-label">{str(block.title)}</span>}
        <MarkdownContent text={str(block.content)} citations={ctx.citations} onOpenCitation={ctx.onOpenCitation} />
      </div>
    </aside>
  );
}

function CodeBlock({ block }: { block: Block }) {
  const [open, setOpen] = useState(false);
  const code = str(block.content);
  return (
    <section className="rounded-md border border-border">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center gap-da-sm px-da-md py-da-sm text-left text-foreground da-label hover:bg-subtle"
      >
        <Icon name={open ? 'chevron-down' : 'chevron-right'} size={14} />
        {str(block.title) || 'Cách tính (Python)'}
      </button>
      {open && (
        <div className="relative border-t border-border">
          <button
            type="button"
            aria-label="Sao chép code"
            onClick={() => void navigator.clipboard?.writeText(code)}
            className="absolute right-da-sm top-da-sm rounded p-da-xs text-muted-foreground hover:bg-subtle"
          >
            <Icon name="copy" size={14} />
          </button>
          <pre className="m-0 overflow-x-auto p-da-md da-caption">
            <code>{code}</code>
          </pre>
        </div>
      )}
    </section>
  );
}

function Unknown({ block }: { block: Block }) {
  return (
    <pre className="m-0 overflow-x-auto rounded-md border border-border p-da-md text-muted-foreground da-caption">
      {JSON.stringify(block, null, 2)}
    </pre>
  );
}

const RENDERERS: Record<string, (block: Block, ctx: Ctx) => ReactNode> = {
  markdown: (b, ctx) => <MarkdownContent text={str(b.content)} citations={ctx.citations} onOpenCitation={ctx.onOpenCitation} />,
  kpi: (b) => <Kpis block={b} />,
  chart: (b, ctx) => <ChartBlock block={b} ctx={ctx} />,
  table: (b, ctx) => <TableBlock block={b} ctx={ctx} />,
  insights: (b, ctx) => <Insights block={b} ctx={ctx} />,
  callout: (b, ctx) => <Callout block={b} ctx={ctx} />,
  code: (b) => <CodeBlock block={b} />,
  caveats: (b) => <CaveatsBlock items={arr<unknown>(b.items).map(String)} />,
  // The next questions are drawn under the answer from the answer's follow_ups.
  follow_ups: () => null,
};

/** One block, drawn as its own component: a renderer that throws does so inside BlockBoundary,
 *  not in ComposedBlocks, so the other blocks of the answer stay. */
function ComposedBlock({ block, index, ctx }: { block: Block; index: number; ctx: Ctx }) {
  const type = str(block.type);
  const render = RENDERERS[type];
  const node = render ? render(block, ctx) : <Unknown block={block} />;
  if (node === null) return null;
  return (
    <div key={str(block.id) || `${type}-${index}`} data-testid="answer-block" data-type={type}>
      {node}
    </div>
  );
}

export function ComposedBlocks({ blocks, ...ctx }: { blocks: AnswerBlockView[] } & Ctx) {
  return (
    <div className="flex flex-col gap-da-lg">
      {blocks.map((raw, i) => {
        const block = raw as Block;
        return (
          <BlockBoundary key={str(block.id) || `${str(block.type)}-${i}`}>
            <ComposedBlock block={block} index={i} ctx={ctx} />
          </BlockBoundary>
        );
      })}
    </div>
  );
}
