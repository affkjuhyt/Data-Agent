import { useMemo, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Field } from '../../design-system/Field';
import { Select } from '../../design-system/Select';
import { cn } from '../../lib/utils';
import { getLineage, listLineageTargets, type LineageGraph } from '../../lib/v1/dataPrep';
import { useV1 } from '../../lib/v1/useV1';
import { FormDialog } from '../sessions/FormDialog';
import { NODE_H, NODE_W, layoutLineage, lineageToSvg, type LineageLayout } from './lineageLayout';
import { AccessDenied, forbiddenOf } from '../access/AccessDenied';

/*
 * "P06 · Dòng dữ liệu" (2290:17979, UC-06-05): what a table, metric or report is built from and
 * what uses it. The graph is laid out from the API's nodes and edges (lineageLayout.ts); edges
 * leaving a late source are drawn in warning so the affected path stands out, and the panel on
 * the right explains what the delay touches. Only objects the viewer may see come back from the
 * API. A node is a button: it opens its details in the panel.
 */
export default function LineagePage() {
  const { navigate, routeId } = useAppState();
  const graph = useV1(() => getLineage(routeId), [routeId]);
  const [picking, setPicking] = useState(false);

  // A graph in a Data Space the viewer may not read: the designed "Thiếu quyền" state.
  const forbidden = forbiddenOf(graph.error);
  if (forbidden) return <AccessDenied access={forbidden} />;
  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Dòng dữ liệu']}
        title={graph.data ? `${graph.data.title} đi từ đâu` : 'Dòng dữ liệu'}
        description="Chọn một bảng, chỉ số hoặc báo cáo để xem nó được tạo từ những nguồn và bước nào"
        actions={
          <>
            <Button onClick={() => setPicking(true)}>Đổi đối tượng</Button>
            <Button
              kind="ghost"
              disabled={!graph.data}
              onClick={() => graph.data && exportSvg(graph.data)}
            >
              Xuất sơ đồ
            </Button>
          </>
        }
      />
      <AsyncRegion state={graph} what="dòng dữ liệu" skeletonRows={6}>
        {(g) => <LineageView key={g.focus_id} graph={g} />}
      </AsyncRegion>
      <TargetDialog
        open={picking}
        current={graph.data?.focus_id}
        onClose={() => setPicking(false)}
        onPick={(id) => {
          setPicking(false);
          navigate('data', 'LINEAGE', id);
        }}
      />
    </div>
  );
}

function LineageView({ graph }: { graph: LineageGraph }) {
  const layout = useMemo<LineageLayout>(() => layoutLineage(graph), [graph]);
  const [selected, setSelected] = useState(graph.focus_id);
  const node = layout.nodes.find((n) => n.id === selected) ?? layout.nodes.find((n) => n.focus);

  return (
    <div className="flex min-h-0 flex-1 max-[1023px]:flex-col">
      <div className="min-w-0 flex-1 overflow-auto bg-background">
        <div
          role="group"
          aria-label="Sơ đồ dòng dữ liệu"
          className="relative"
          style={{ width: layout.width, height: layout.height }}
        >
          <svg aria-hidden width={layout.width} height={layout.height} className="absolute inset-0">
            {layout.edges.map((e) => (
              <path
                key={`${e.from}-${e.to}`}
                d={e.d}
                fill="none"
                strokeWidth={1.5}
                style={{ stroke: e.late ? 'var(--warning)' : 'var(--border-strong)' }}
              />
            ))}
          </svg>
          {layout.nodes.map((n) => (
            <button
              key={n.id}
              type="button"
              aria-pressed={n.id === node?.id}
              onClick={() => setSelected(n.id)}
              style={{ left: n.x, top: n.y, width: NODE_W, height: NODE_H }}
              className={cn(
                'absolute flex flex-col items-start gap-[2px] overflow-hidden rounded-sm border bg-card px-da-lg py-da-md text-left',
                n.focus
                  ? 'border-foreground shadow-[inset_0_0_0_0.5px_var(--foreground)]'
                  : n.late
                    ? 'border-warning'
                    : 'border-border',
                n.id === node?.id && !n.focus && 'ring-2 ring-ring',
              )}
            >
              <span className="w-full truncate text-foreground da-body-strong">{n.label}</span>
              <span
                className={cn(
                  'w-full truncate da-caption',
                  n.late ? 'text-warning' : 'text-muted-foreground',
                )}
              >
                {n.detail_text}
              </span>
            </button>
          ))}
        </div>
      </div>
      <aside
        aria-label="Chi tiết"
        className="flex w-[340px] shrink-0 flex-col gap-[10px] border-l border-border px-da-2xl pt-[20px] max-[1023px]:w-full"
      >
        {node && (
          <>
            <h2 className="text-foreground da-heading-3">{node.label}</h2>
            {node.facts.length === 0 && (
              <p className="text-muted-foreground da-body-small">{node.detail_text}</p>
            )}
            <dl className="flex flex-col gap-[10px]">
              {node.facts.map((f) => (
                <div key={f.label} className="flex flex-col gap-[2px] pb-da-sm">
                  <dt className="text-muted-foreground da-caption">{f.label}</dt>
                  <dd className="text-foreground da-body-small">{f.text}</dd>
                </div>
              ))}
            </dl>
          </>
        )}
        <p className="text-muted-foreground da-caption">
          Bấm một nút để xem chi tiết; đường đi từ nguồn trễ tô màu cảnh báo.
        </p>
      </aside>
    </div>
  );
}

function exportSvg(graph: LineageGraph) {
  const css = getComputedStyle(document.documentElement);
  const token = (name: string) => css.getPropertyValue(name).trim() || 'currentColor';
  const svg = lineageToSvg(layoutLineage(graph), {
    card: token('--card'),
    border: token('--border-strong'),
    foreground: token('--foreground'),
    muted: token('--muted-foreground'),
    warning: token('--warning'),
    background: token('--background'),
  });
  const url = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = `dong-du-lieu-${graph.focus_id}.svg`;
  a.click();
  URL.revokeObjectURL(url);
}

function TargetDialog({
  open,
  current,
  onClose,
  onPick,
}: {
  open: boolean;
  current: string | undefined;
  onClose: () => void;
  onPick: (id: string) => void;
}) {
  const targets = useV1(open ? listLineageTargets : null, [open]);
  const [value, setValue] = useState<string | null>(null);
  const chosen = value ?? current ?? null;

  return (
    <FormDialog
      open={open}
      title="Đổi đối tượng"
      onClose={onClose}
      footer={
        <>
          <span className="flex-1" />
          <Button kind="ghost" onClick={onClose}>
            Huỷ
          </Button>
          <Button kind="primary" disabled={!chosen} onClick={() => chosen && onPick(chosen)}>
            Xem dòng dữ liệu
          </Button>
        </>
      }
    >
      <Field label="Bảng, chỉ số hoặc báo cáo">
        <Select
          label="Bảng, chỉ số hoặc báo cáo"
          options={(targets.data?.items ?? []).map((t) => ({ value: t.id, label: t.label }))}
          value={chosen}
          onChange={setValue}
          placeholder={targets.loading ? 'Đang tải…' : 'Chọn đối tượng'}
        />
      </Field>
    </FormDialog>
  );
}
