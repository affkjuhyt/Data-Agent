import { useEffect, useMemo, useRef, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { Icon } from '../../design-system/Icon';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { Segmented } from '../../design-system/Segmented';
import { SkeletonRows } from '../../design-system/SkeletonRow';
import { useToast } from '../../design-system/Toast';
import { csvCell } from '../../lib/csv';
import { requestAccess } from '../../lib/v1/adminAccess';
import { V1Error } from '../../lib/v1/client';
import {
  getExploreExplanation,
  getExploreModel,
  getExploreRows,
  interpretQuestion,
  runExploreQuery,
  saveExploreToSession,
  type ExploreModel,
  type ExploreResult,
  type ExploreRow,
  type ExploreRows,
  type NextStep,
} from '../../lib/v1/explore';
import { useV1 } from '../../lib/v1/useV1';
import { cn } from '../../lib/utils';
import { ExplainPanel } from './ExplainPanel';
import { FieldList, type FieldRef } from './FieldList';
import { ResultChart, ValueTable, type ChartKind } from './ResultChart';
import { SaveToSessionDialog } from './SaveToSessionDialog';
import { Shelves, type ShelfState } from './Shelves';

/*
 * "v3.1 · Khám phá" (2140:15461, UC-01-04): self-service exploration over the certified semantic
 * layer. Left the certified metrics and their dimensions (280px), right "Agent giải thích"
 * (340px), in between
 * the mode switch, the shelves, the chart card and the value table. States: "Khám phá · Đang
 * tải" (2165:17536), "Lỗi truy vấn" (2165:17822), "Thiếu quyền" (2165:18122); interactions as
 * "Tương tác · Khám phá" (2168:18414) and its "Đặc tả" (2168:18624).
 *
 * Under 1280px ("Khám phá · 1024", 2167:18020) both side columns fold away: "Chỉ số và chiều"
 * opens the field list and "Agent giải thích" opens the explanation, each as an overlay.
 * A result is kept as a tile of a session ("Lưu vào phiên"): the design has no dashboards.
 */
type Mode = 'drag' | 'ask';
const EMPTY: ShelfState = { metric_ids: [], dimension_ids: [], filters: [] };

export default function ExplorePage() {
  const model = useV1(getExploreModel, []);

  return (
    <div className="flex h-full min-h-0">
      {model.data?.metrics.length === 0 ? (
        <div className="flex-1 p-da-xl">
          <EmptyState
            icon="database"
            title="Chưa có chỉ số nào để khám phá"
            description="Khám phá chỉ dùng chỉ số đã chứng nhận mà bạn có quyền. Khi người phụ trách chứng nhận một chỉ số, nó sẽ hiện ở đây."
          />
        </div>
      ) : (
        <AsyncRegion state={model} what="chỉ số và chiều" skeletonRows={6}>
          {(m) => <Explorer model={m} />}
        </AsyncRegion>
      )}
    </div>
  );
}

type View =
  | { kind: 'chart' }
  | { kind: 'rows'; rows: ExploreRows }
  | { kind: 'locked'; reason: string };

function Explorer({ model: base }: { model: ExploreModel }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [mode, setMode] = useState<Mode>('drag');
  const [question, setQuestion] = useState('');
  const [interpreting, setInterpreting] = useState(false);
  const [shelves, setShelves] = useState<ShelfState>(EMPTY);
  // A3 lists no values of a dimension: the filter offers the values results have shown so far.
  const [seen, setSeen] = useState<Record<string, string[]>>({});
  const model = useMemo(
    () => ({
      ...base,
      dimensions: base.dimensions.map((d) => ({
        ...d,
        values: (seen[d.id] ?? []).map((value) => ({ value, label: value })),
      })),
    }),
    [base, seen],
  );
  const [kind, setKind] = useState<ChartKind>('bar');
  const [result, setResult] = useState<ExploreResult>();
  const [error, setError] = useState<Error>();
  const [running, setRunning] = useState(false);
  const [tick, setTick] = useState(0);
  const [view, setView] = useState<View>({ kind: 'chart' });
  const [saving, setSaving] = useState(false);
  const [fieldsOpen, setFieldsOpen] = useState(false);
  const [explainOpen, setExplainOpen] = useState(false);
  const askRef = useRef<HTMLInputElement>(null);
  const seq = useRef(0);

  const ready = shelves.metric_ids.length > 0 && shelves.dimension_ids.length > 0;
  const queryKey = JSON.stringify(shelves);

  useEffect(() => {
    if (!ready) {
      setResult(undefined);
      setError(undefined);
      return;
    }
    const mine = ++seq.current;
    setRunning(true);
    setView({ kind: 'chart' });
    runExploreQuery(shelves, base)
      .then((r) => {
        if (mine !== seq.current) return;
        setResult(r);
        setError(undefined);
        const category = r.category_dimension_id;
        if (category)
          setSeen((s) => ({
            ...s,
            [category]: [...new Set([...(s[category] ?? []), ...r.rows.map((row) => row.key)])],
          }));
      })
      .catch((e: Error) => mine === seq.current && setError(e))
      .finally(() => mine === seq.current && setRunning(false));
    // shelves are captured through queryKey.
  }, [queryKey, base, ready, tick]);

  const explanation = useV1(
    result && !running ? () => getExploreExplanation(result, shelves.metric_ids) : null,
    [result?.id, running],
  );

  const selected = useMemo(
    () => new Set([...shelves.metric_ids, ...shelves.dimension_ids]),
    [shelves],
  );

  const add = (field: FieldRef) => {
    setShelves((s) =>
      field.kind === 'metric'
        ? s.metric_ids.includes(field.id)
          ? s
          : { ...s, metric_ids: [...s.metric_ids, field.id] }
        : s.dimension_ids.includes(field.id)
          ? s
          : { ...s, dimension_ids: [...s.dimension_ids, field.id] },
    );
    setFieldsOpen(false);
  };

  const drill = (categoryDimension: string, value: string, dimensionIds: string[]) =>
    setShelves((s) => ({
      ...s,
      dimension_ids: dimensionIds,
      filters: [
        ...s.filters.filter((f) => f.dimension_id !== categoryDimension),
        { dimension_id: categoryDimension, op: 'eq', values: [value] },
      ],
    }));

  const categoryId = (r: ExploreResult) => r.category_dimension_id ?? shelves.dimension_ids[0];

  const drillColumn = (row: ExploreRow) => {
    if (!result?.drill_dimension_id) return;
    const category = categoryId(result);
    drill(category, row.key, [
      result.drill_dimension_id,
      ...shelves.dimension_ids.filter((d) => d !== category),
    ]);
  };

  const openRows = async () => {
    if (!result) return;
    try {
      setView({ kind: 'rows', rows: await getExploreRows(result.id) });
    } catch (e) {
      if (e instanceof V1Error && (e.status === 403 || e.code === 'aggregate_only'))
        setView({ kind: 'locked', reason: e.message });
      else toast({ tone: 'error', message: `Không mở được dòng gốc: ${(e as Error).message}` });
    }
  };

  const whyThisNumber = () => navigate('data', 'METRIC', shelves.metric_ids[0]);

  const watch = () => navigate('agents', 'BUILDER');

  const onStep = (step: NextStep) => {
    setExplainOpen(false);
    if (step.kind === 'drill')
      drill(step.filter.dimension_id, step.filter.values[0], step.dimension_ids);
    else if (step.kind === 'rows') void openRows();
    else if (step.kind === 'lineage') whyThisNumber();
    else watch();
  };

  const downloadCsv = () => {
    if (!result) return;
    const header = [result.category_label, ...result.series.map((s) => s.label)];
    if (result.series.length > 1) header.push('Thay đổi');
    const lines = [
      header.map(csvCell).join(','),
      ...result.rows.map((r) =>
        [r.label, ...r.values, ...(result.series.length > 1 ? [r.delta] : [])]
          .map(csvCell)
          .join(','),
      ),
    ];
    const blob = new Blob([`﻿${lines.join('\r\n')}`], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${result.title}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const ask = async () => {
    const text = question.trim();
    if (!text) return;
    setInterpreting(true);
    try {
      const filled = await interpretQuestion(text, base);
      setShelves({
        metric_ids: filled.metric_ids,
        dimension_ids: filled.dimension_ids,
        filters: filled.filters,
      });
      setMode('drag');
      toast({
        tone: 'success',
        message: 'Agent đã điền kệ từ câu hỏi — kiểm tra trước khi dùng số.',
      });
    } catch (e) {
      toast({ tone: 'error', message: `Agent chưa hiểu câu hỏi: ${(e as Error).message}` });
    } finally {
      setInterpreting(false);
    }
  };

  const requestDetail = async (reason: string) => {
    if (!result) return;
    const until = new Date(Date.now() + 30 * 86_400_000).toISOString().slice(0, 10);
    try {
      await requestAccess({
        data_space_id: result.data_space_id,
        capability: 'view_detail',
        reason: `Xem dòng kết quả của “${result.title}” trong Khám phá. ${reason}`,
        requested_until: until,
      });
      toast({ tone: 'success', message: 'Đã gửi yêu cầu quyền tới người phụ trách.' });
    } catch (e) {
      toast({ tone: 'error', message: `Không gửi được yêu cầu: ${(e as Error).message}` });
    }
  };

  const links = result && (
    <>
      <LinkButton onClick={() => void openRows()}>
        {result.row_count === null
          ? 'Xem dòng kết quả'
          : `Xem ${result.row_count.toLocaleString('vi-VN')} dòng kết quả`}
      </LinkButton>
      <LinkButton onClick={whyThisNumber}>Vì sao số này?</LinkButton>
      <LinkButton onClick={downloadCsv}>Tải CSV</LinkButton>
    </>
  );

  const queryError = error instanceof V1Error && error.status === 422;

  return (
    <div className="relative flex min-h-0 w-full flex-1">
      <aside
        aria-label="Chỉ số và chiều"
        className={cn(
          'w-[280px] shrink-0 overflow-y-auto border-r border-border bg-card p-da-lg',
          fieldsOpen
            ? 'max-[1279px]:absolute max-[1279px]:inset-y-0 max-[1279px]:left-0 max-[1279px]:z-[var(--z-popover)] max-[1279px]:shadow-lg'
            : 'max-[1279px]:hidden',
        )}
      >
        <FieldList model={model} selected={selected} metricIds={shelves.metric_ids} onAdd={add} />
      </aside>

      <main className="flex min-w-0 flex-1 flex-col gap-da-lg overflow-y-auto px-da-xl py-da-lg">
        <div className="flex items-center gap-da-lg">
          <button
            type="button"
            onClick={() => setFieldsOpen((o) => !o)}
            aria-expanded={fieldsOpen}
            className="flex shrink-0 items-center gap-da-xs rounded-md border border-border px-da-lg py-da-md text-foreground da-body-strong min-[1280px]:hidden"
          >
            Chỉ số và chiều
            <Icon name="chevron-down" size={12} />
          </button>
          <Segmented<Mode>
            label="Cách khám phá"
            value={mode}
            onChange={(m) => {
              setMode(m);
              if (m === 'ask') window.setTimeout(() => askRef.current?.focus(), 0);
            }}
            items={[
              { value: 'drag', label: 'Kéo thả' },
              { value: 'ask', label: 'Hỏi bằng lời' },
            ]}
          />
          {mode === 'drag' ? (
            <button
              type="button"
              onClick={() => {
                setMode('ask');
                window.setTimeout(() => askRef.current?.focus(), 0);
              }}
              className="flex min-w-0 flex-1 items-center gap-da-md rounded-md border border-border bg-card px-da-md py-da-sm text-left text-muted-foreground da-body-small"
            >
              <Icon name="agent" className="text-foreground" />
              <span className="truncate">
                hoặc hỏi: “so sánh nợ xấu bán lẻ theo vùng Q3 với Q2”
              </span>
            </button>
          ) : (
            <form
              className="flex min-w-0 flex-1 items-center gap-da-md"
              onSubmit={(e) => {
                e.preventDefault();
                void ask();
              }}
            >
              <div className="min-w-0 flex-1">
                <Input
                  ref={askRef}
                  icon="agent"
                  aria-label="Hỏi bằng lời"
                  placeholder="so sánh nợ xấu bán lẻ theo vùng Q3 với Q2"
                  value={question}
                  onChange={(e) => setQuestion(e.target.value)}
                />
              </div>
              <Button type="submit" loading={interpreting} disabled={!question.trim()}>
                Điền kệ
              </Button>
            </form>
          )}
          <Button
            icon="bookmark"
            disabled={!result || running}
            onClick={() => setSaving(true)}
            className="shrink-0"
          >
            Lưu vào phiên
          </Button>
          <Button
            kind="secondary"
            onClick={() => setExplainOpen((o) => !o)}
            aria-expanded={explainOpen}
            className="shrink-0 text-primary min-[1280px]:hidden"
          >
            Agent giải thích
          </Button>
        </div>

        <Shelves model={model} state={shelves} onChange={setShelves} />

        {!ready ? (
          <p className="rounded-lg border border-dashed border-border-strong px-da-xl py-da-2xl text-center text-muted-foreground da-body">
            Kéo một chỉ số và một chiều vào kệ, hoặc hỏi bằng lời.
          </p>
        ) : view.kind === 'locked' ? (
          <EmptyState
            icon="lock"
            title="Bạn chỉ xem được số tổng hợp"
            description={`${view.reason.replace(/\.$/, '')}. Biểu đồ theo ${(result?.category_label ?? 'chiều đã chọn').toLowerCase()} vẫn dùng được.`}
            action={
              <div className="flex gap-da-md">
                <Button kind="primary" onClick={() => void requestDetail(view.reason)}>
                  Yêu cầu quyền
                </Button>
                <Button onClick={() => setView({ kind: 'chart' })}>
                  Xem theo {(result?.category_label ?? 'chiều đã chọn').toLowerCase()}
                </Button>
              </div>
            }
          />
        ) : view.kind === 'rows' ? (
          <DetailRows rows={view.rows} onBack={() => setView({ kind: 'chart' })} />
        ) : error && !running ? (
          <Notice
            tone="error"
            className="items-start bg-card px-da-xl py-da-lg"
            action={<Button onClick={() => setTick((t) => t + 1)}>Thử lại</Button>}
          >
            <span className="block text-error da-body-strong">
              {queryError ? 'Không chạy được truy vấn này' : 'Không tải được kết quả'}
            </span>
            {error.message}
          </Notice>
        ) : result ? (
          <>
            <ResultChart
              result={result}
              kind={kind}
              onKind={setKind}
              loading={running}
              onDrill={result.drill_dimension_id ? drillColumn : undefined}
              links={links}
            />
            {kind !== 'table' && !running && <ValueTable result={result} />}
          </>
        ) : (
          <SkeletonRows count={6} label="Đang chạy truy vấn…" />
        )}
      </main>

      <aside
        aria-label="Agent giải thích"
        className={cn(
          'w-[340px] shrink-0 overflow-y-auto border-l border-border bg-card px-da-xl py-da-lg',
          explainOpen
            ? 'max-[1279px]:absolute max-[1279px]:inset-y-0 max-[1279px]:right-0 max-[1279px]:z-[var(--z-popover)] max-[1279px]:shadow-lg'
            : 'max-[1279px]:hidden',
        )}
      >
        <ExplainPanel result={result} explanation={explanation} onStep={onStep} />
      </aside>

      {saving && result && (
        <SaveToSessionDialog
          onClose={() => setSaving(false)}
          onSave={(workspaceId) => saveExploreToSession(workspaceId, result, shelves)}
          onSaved={(workspaceId, title) => {
            setSaving(false);
            toast({
              tone: 'success',
              message: `Đã lưu vào ${title}`,
              action: {
                label: 'Mở phiên',
                onClick: () => navigate('analysis', 'SESSIONS', workspaceId),
              },
            });
          }}
        />
      )}
    </div>
  );
}

function LinkButton({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" onClick={onClick} className="text-primary da-body-strong hover:underline">
      {children}
    </button>
  );
}

function DetailRows({ rows, onBack }: { rows: ExploreRows; onBack: () => void }) {
  return (
    <section aria-label="Dòng gốc" className="flex flex-col gap-da-md">
      <div className="flex items-center gap-da-md">
        <p className="flex-1 text-muted-foreground da-caption">
          {rows.total === null
            ? `${rows.rows.length.toLocaleString('vi-VN')} dòng đầu`
            : `${rows.total.toLocaleString('vi-VN')} dòng`}
        </p>
        <Button size="sm" onClick={onBack}>
          Quay lại biểu đồ
        </Button>
      </div>
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="w-full border-separate border-spacing-0">
          <thead>
            <tr className="bg-subtle text-left text-muted-foreground da-label">
              {rows.columns.map((c) => (
                <th key={c} className="px-da-lg py-da-sm font-medium">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.rows.map((r, i) => (
              <tr key={i}>
                {r.map((cell, j) => (
                  <td
                    key={j}
                    className="border-t border-border px-da-lg py-da-sm text-foreground da-body-small"
                  >
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
