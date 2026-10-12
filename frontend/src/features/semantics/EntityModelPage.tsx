import { useState } from 'react';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { Checkbox } from '../../design-system/Checkbox';
import { Dialog } from '../../design-system/Dialog';
import { Icon } from '../../design-system/Icon';
import { Notice } from '../../design-system/Notice';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { cn } from '../../lib/utils';
import {
  createRelationship,
  getEntity,
  getEntityModel,
  suggestRelationships,
  type EntityModel,
  type RelationshipSuggestion,
} from '../../lib/v1/semanticLayer';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "v3.1 · Semantic · Mô hình thực thể" (2087:8090, UC-07-02): the business concepts of a Data
 * Space and how they connect to real data — the diagram of entities with their key columns and
 * relationship cardinalities, and on the right the selected entity: its key and grain, the sources
 * it maps to, its synonyms and the metrics built on it. "Nhờ agent đề xuất quan hệ" opens
 * "Hộp thoại · suggestRel" (2095:9259): candidates from matching key names, values and frequent
 * queries; ticked ones are saved pending review (BR-100), and one across different codes waits on
 * the other dataset's owner.
 */
const NODE_W = 28; // % of the canvas width
const NODE_H = 18; // % of the canvas height, for the line anchors only

const pct = (n: number) => `${n.toFixed(1).replace('.', ',')}%`;

export default function EntityModelPage() {
  const model = useV1(getEntityModel, []);
  return (
    <AsyncRegion state={model} what="mô hình thực thể">
      {(m) => <ModelBody model={m} />}
    </AsyncRegion>
  );
}

function ModelBody({ model }: { model: EntityModel }) {
  const [focus, setFocus] = useState(model.focus);
  const [suggesting, setSuggesting] = useState(false);
  const detail = useV1(() => getEntity(focus), [focus]);
  const node = (key: string) => model.entities.find((e) => e.key === key);

  return (
    <div className="flex min-h-full max-[1023px]:flex-col">
      <div className="flex min-w-0 flex-1 flex-col gap-da-md px-da-xl pb-da-2xl pt-da-lg">
        <PageHeader
          breadcrumb={['Dữ liệu', 'Ngữ nghĩa', 'Mô hình thực thể']}
          title={`Mô hình thực thể · ${model.title}`}
          actions={
            <>
              <Button kind="ghost" icon="agent" onClick={() => setSuggesting(true)}>
                Nhờ agent đề xuất quan hệ
              </Button>
              <Tooltip content="Trình tạo thực thể đang được làm.">
                <Button kind="primary" disabled>
                  Thêm thực thể
                </Button>
              </Tooltip>
            </>
          }
        />
        <Notice tone="info">
          Thực thể là các khái niệm nghiệp vụ (khách hàng, khoản vay…) và cách chúng nối với dữ liệu
          thật — agent dựa vào đây để hiểu câu hỏi.
        </Notice>
        <div
          role="group"
          aria-label="Sơ đồ thực thể"
          className="relative h-[680px] min-w-[720px] rounded-md border border-border bg-card"
        >
          <svg
            aria-hidden
            className="absolute inset-0 h-full w-full"
            viewBox="0 0 100 100"
            preserveAspectRatio="none"
          >
            {model.relationships.map((r) => {
              const a = node(r.from);
              const b = node(r.to);
              if (!a || !b) return null;
              return (
                <line
                  key={`${r.from}-${r.to}`}
                  x1={a.x + NODE_W / 2}
                  y1={a.y + NODE_H / 2}
                  x2={b.x + NODE_W / 2}
                  y2={b.y + NODE_H / 2}
                  stroke="var(--border-strong)"
                  strokeWidth={1}
                  vectorEffect="non-scaling-stroke"
                />
              );
            })}
          </svg>
          {model.relationships.map((r) => {
            const a = node(r.from);
            const b = node(r.to);
            if (!a || !b) return null;
            return (
              <span
                key={`label-${r.from}-${r.to}`}
                className="absolute -translate-x-1/2 -translate-y-1/2 bg-card px-[4px] text-muted-foreground da-caption"
                style={{
                  left: `${(a.x + b.x) / 2 + NODE_W / 2}%`,
                  top: `${(a.y + b.y) / 2 + NODE_H / 2}%`,
                }}
              >
                {r.cardinality}
              </span>
            );
          })}
          {model.entities.map((e) => (
            <button
              key={e.key}
              type="button"
              aria-pressed={e.key === focus}
              onClick={() => setFocus(e.key)}
              className={cn(
                'absolute flex flex-col overflow-hidden rounded-md border bg-card text-left',
                e.key === focus ? 'border-primary' : 'border-border-strong hover:border-primary',
              )}
              style={{ left: `${e.x}%`, top: `${e.y}%`, width: `${NODE_W}%` }}
            >
              <span className="flex flex-col border-b border-border bg-subtle px-da-sm py-da-xs">
                <span
                  className={cn(
                    'da-body-small',
                    e.key === focus ? 'text-primary' : 'text-foreground',
                  )}
                >
                  {e.name}
                </span>
                <span className="text-muted-foreground da-caption">{e.source_text}</span>
              </span>
              {e.columns.map((c) => (
                <span key={c} className="px-da-sm py-[3px] text-foreground da-mono">
                  {c}
                </span>
              ))}
            </button>
          ))}
        </div>
      </div>
      <aside className="flex w-[360px] shrink-0 flex-col gap-da-md border-l border-border px-da-lg pt-da-lg max-[1023px]:w-full max-[1023px]:border-l-0">
        <AsyncRegion state={detail} what="thực thể">
          {(d) => (
            <>
              <h2 className="text-foreground da-heading-2">{d.name}</h2>
              <p className="text-muted-foreground da-body-small">{d.description}</p>
              <dl className="flex flex-col gap-da-sm da-body-small">
                {d.facts.map((f) => (
                  <div key={f.label} className="flex gap-da-md">
                    <dt className="w-[80px] shrink-0 text-muted-foreground da-caption">
                      {f.label}
                    </dt>
                    <dd className="text-foreground">{f.value}</dd>
                  </div>
                ))}
              </dl>
              <h3 className="text-muted-foreground da-overline">Ánh xạ nguồn</h3>
              <ul className="flex flex-col gap-da-sm da-body-small">
                {d.sources.map((s) => (
                  <li key={s.label} className="flex items-center gap-da-sm">
                    <Icon name="database" className="text-muted-foreground" />
                    <span className="min-w-0 flex-1 text-foreground">{s.label}</span>
                    {s.tone === 'neutral' ? (
                      <Badge tone="neutral">{s.role}</Badge>
                    ) : (
                      <StatusDot tone={s.tone}>{s.role}</StatusDot>
                    )}
                  </li>
                ))}
              </ul>
              {d.synonyms.length > 0 && (
                <>
                  <h3 className="text-muted-foreground da-overline">Từ đồng nghĩa</h3>
                  <p className="flex flex-wrap gap-x-da-md gap-y-da-xs da-body-small">
                    {d.synonyms.map((s) => (
                      <StatusDot key={s} tone="success">
                        {s}
                      </StatusDot>
                    ))}
                  </p>
                </>
              )}
              {d.metrics.length > 0 && (
                <>
                  <h3 className="text-muted-foreground da-overline">
                    Chỉ số dựa trên thực thể này
                  </h3>
                  <p className="flex flex-wrap items-center gap-x-da-md gap-y-da-xs da-body-small">
                    {d.metrics.map((m) => (
                      <StatusDot key={m} tone="success">
                        {m}
                      </StatusDot>
                    ))}
                    {d.more_metrics > 0 && <Badge tone="neutral">+ {d.more_metrics} chỉ số</Badge>}
                  </p>
                </>
              )}
            </>
          )}
        </AsyncRegion>
      </aside>
      {suggesting && <SuggestRelationsDialog onClose={() => setSuggesting(false)} />}
    </div>
  );
}

function SuggestRelationsDialog({ onClose }: { onClose: () => void }) {
  const toast = useToast();
  const suggestions = useV1(suggestRelationships, []);
  const [skipped, setSkipped] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const picked = (suggestions.data?.items ?? []).filter((s) => !skipped.has(s.id));

  const save = async () => {
    if (picked.length === 0) {
      setError('Chọn ít nhất một quan hệ.');
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      for (const s of picked) await createRelationship(s.body);
      toast({ tone: 'success', message: `Đã gửi ${picked.length} quan hệ chờ duyệt` });
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open
      size="md"
      title="Agent đề xuất quan hệ"
      description="Dựa trên khoá trùng tên, giá trị khớp và truy vấn thường dùng."
      submitLabel="Thêm quan hệ đã chọn"
      submitting={busy}
      canSubmit={!!suggestions.data}
      error={error}
      onClose={onClose}
      onSubmit={save}
    >
      <div className="flex flex-col gap-da-md">
        <AsyncRegion state={suggestions} what="đề xuất quan hệ">
          {(s) => (
            <>
              <ul className="flex flex-col gap-da-sm">
                {s.items.map((r: RelationshipSuggestion) => (
                  <li key={r.id} className="rounded-md border border-border px-da-md py-da-sm">
                    <Checkbox
                      checked={!skipped.has(r.id)}
                      onChange={(on) => {
                        const next = new Set(skipped);
                        if (on) next.delete(r.id);
                        else next.add(r.id);
                        setSkipped(next);
                      }}
                    >
                      Đề xuất · {r.from_label} {r.arrow} {r.to_label} · khớp {pct(r.match_pct)}
                    </Checkbox>
                  </li>
                ))}
              </ul>
              {s.note && <p className="text-muted-foreground da-caption">{s.note}</p>}
            </>
          )}
        </AsyncRegion>
      </div>
    </Dialog>
  );
}
