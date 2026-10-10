import { useEffect, useMemo, useRef, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { Icon } from '../../design-system/Icon';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { Segmented } from '../../design-system/Segmented';
import { Select } from '../../design-system/Select';
import { SkeletonRows } from '../../design-system/SkeletonRow';
import { useToast } from '../../design-system/Toast';
import { csvCell } from '../../lib/csv';
import { requestAccess } from '../../lib/v1/adminAccess';
import { V1Error } from '../../lib/v1/client';
import { listDashboards } from '../../lib/v1/dashboards';
import {
  getExploreExplanation,
  getExploreModel,
  getExploreRows,
  interpretQuestion,
  listExploreDatasets,
  runExploreQuery,
  saveQueryToDashboard,
  type ExploreModel,
  type ExploreResult,
  type ExploreRow,
  type ExploreRows,
  type NextStep,
} from '../../lib/v1/explore';
import { useV1 } from '../../lib/v1/useV1';
import { cn } from '../../lib/utils';
import { Dialog } from '../../design-system/Dialog';
import { ExplainPanel } from './ExplainPanel';
import { FieldList, type FieldRef } from './FieldList';
import { ResultChart, ValueTable, type ChartKind } from './ResultChart';
import { Shelves, type ShelfState } from './Shelves';

/*
 * "v3.1 · Khám phá" (2140:15461, UC-01-04): self-service exploration over the certified semantic
 * layer. Left the dataset and its fields (280px), right "Agent giải thích" (340px), in between
 * the mode switch, the shelves, the chart card and the value table. States: "Khám phá · Đang
 * tải" (2165:17536), "Lỗi truy vấn" (2165:17822), "Thiếu quyền" (2165:18122); interactions as
 * "Tương tác · Khám phá" (2168:18414) and its "Đặc tả" (2168:18624).
 *
 * Under 1280px ("Khám phá · 1024", 2167:18020) both side columns fold away: the dataset name
 * opens the field list and "Agent giải thích" opens the explanation, each as an overlay.
 * Every endpoint is a PROPOSAL (lib/v1/explore.ts).
 */
type Mode = 'drag' | 'ask';
const EMPTY: ShelfState = { metric_ids: [], dimension_ids: [], filters: [] };

export default function ExplorePage() {
  const datasets = useV1(listExploreDatasets, []);
  const [datasetId, setDatasetId] = useState<string | null>(null);
  const firstId = datasets.data?.items.find((d) => d.certified)?.id ?? null;
  const currentId = datasetId ?? firstId;
  const model = useV1(currentId ? () => getExploreModel(currentId) : null, [currentId]);

  return (
    <div className="flex h-full min-h-0">
      {datasets.data?.items.length === 0 ? (
        <div className="flex-1 p-da-xl">
          <EmptyState
            icon="database"
            title="Chưa có bộ dữ liệu nào để khám phá"
            description="Khám phá chỉ dùng bộ dữ liệu đã chứng nhận mà bạn có quyền. Khi người phụ trách chứng nhận một bộ, nó sẽ hiện ở đây."
          />
        </div>
      ) : (
        <AsyncRegion state={model} what="bộ dữ liệu" skeletonRows={6}>
          {(m) => (
            <Explorer
              key={m.dataset.id}
              model={m}
              datasets={datasets.data?.items ?? [m.dataset]}
              onDataset={setDatasetId}
            />
          )}
        </AsyncRegion>
      )}
      {datasets.error && !datasets.data && (
        <div className="flex-1 p-da-xl">
          <AsyncRegion state={datasets} what="danh sách bộ dữ liệu">
            {() => null}
          </AsyncRegion>
        </div>
      )}
    </div>
  );
}

type View =
  | { kind: 'chart' }
  | { kind: 'rows'; rows: ExploreRows }
  | { kind: 'locked'; reason: string };

function Explorer({
  model,
  datasets,
  onDataset,
}: {
  model: ExploreModel;
  datasets: { id: string; display_name: string; certified: boolean; data_until_text: string }[];
  onDataset: (id: string) => void;
}) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [mode, setMode] = useState<Mode>('drag');
  const [question, setQuestion] = useState('');
  const [interpreting, setInterpreting] = useState(false);
  const [shelves, setShelves] = useState<ShelfState>(() => {
    const d = model.default_query;
    return d
      ? { metric_ids: d.metric_ids, dimension_ids: d.dimension_ids, filters: d.filters }
      : EMPTY;
  });
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
    runExploreQuery({ dataset_id: model.dataset.id, ...shelves })
      .then((r) => {
        if (mine !== seq.current) return;
        setResult(r);
        setError(undefined);
      })
      .catch((e: Error) => mine === seq.current && setError(e))
      .finally(() => mine === seq.current && setRunning(false));
    // shelves are captured through queryKey.
  }, [queryKey, model.dataset.id, ready, tick]);

  const explanation = useV1(result && !running ? () => getExploreExplanation(result.id) : null, [
    result?.id,
    running,
  ]);

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

  const categoryId = (r: ExploreResult) =>
    model.dimensions.find((d) => d.label === r.category_label)?.id ?? shelves.dimension_ids[0];

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
      const filled = await interpretQuestion(model.dataset.id, text);
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
        reason: `Xem dòng gốc của “${result.title}” trong Khám phá. ${reason}`,
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
        Xem {result.detail_row_count.toLocaleString('vi-VN')} dòng gốc
      </LinkButton>
      <LinkButton onClick={whyThisNumber}>Vì sao số này?</LinkButton>
      <LinkButton onClick={downloadCsv}>Tải CSV</LinkButton>
    </>
  );

  const queryError = error instanceof V1Error && error.status === 422;

  return (
    <div className="relative flex min-h-0 w-full flex-1">
      <aside
        aria-label="Bộ dữ liệu và trường"
        className={cn(
          'w-[280px] shrink-0 overflow-y-auto border-r border-border bg-card p-da-lg',
          fieldsOpen
            ? 'max-[1279px]:absolute max-[1279px]:inset-y-0 max-[1279px]:left-0 max-[1279px]:z-[var(--z-popover)] max-[1279px]:shadow-lg'
            : 'max-[1279px]:hidden',
        )}
      >
        <FieldList
          datasets={datasets}
          model={model}
          selected={selected}
          metricIds={shelves.metric_ids}
          onDataset={onDataset}
          onAdd={add}
        />
      </aside>

      <main className="flex min-w-0 flex-1 flex-col gap-da-lg overflow-y-auto px-da-xl py-da-lg">
        <div className="flex items-center gap-da-lg">
          <button
            type="button"
            onClick={() => setFieldsOpen((o) => !o)}
            aria-expanded={fieldsOpen}
            className="flex shrink-0 items-center gap-da-xs rounded-md border border-border px-da-lg py-da-md text-foreground da-body-strong min-[1280px]:hidden"
          >
            {model.dataset.display_name}
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
            Lưu vào dashboard
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
            description={`Bộ dữ liệu này có chi tiết từng khoản vay; ${lowerFirst(view.reason).replace(/\.$/, '')}. Biểu đồ theo ${(result?.category_label ?? 'vùng').toLowerCase()} vẫn dùng được.`}
            action={
              <div className="flex gap-da-md">
                <Button kind="primary" onClick={() => void requestDetail(view.reason)}>
                  Yêu cầu quyền
                </Button>
                <Button onClick={() => setView({ kind: 'chart' })}>
                  Xem theo {(result?.category_label ?? 'vùng').toLowerCase()}
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
        <SaveDialog
          queryId={result.id}
          onClose={() => setSaving(false)}
          onSaved={(dashboardId, title) => {
            setSaving(false);
            toast({
              tone: 'success',
              message: `Đã lưu vào ${title}`,
              action: {
                label: 'Mở dashboard',
                onClick: () => navigate('analysis', 'DASHBOARD', dashboardId),
              },
            });
          }}
        />
      )}
    </div>
  );
}

const lowerFirst = (text: string) => text.charAt(0).toLowerCase() + text.slice(1);

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
          {rows.rows.length.toLocaleString('vi-VN')} / {rows.total.toLocaleString('vi-VN')} dòng
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

function SaveDialog({
  queryId,
  onClose,
  onSaved,
}: {
  queryId: string;
  onClose: () => void;
  onSaved: (dashboardId: string, title: string) => void;
}) {
  const dashboards = useV1(listDashboards, []);
  const [target, setTarget] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string>();
  const save = async () => {
    if (!target) return;
    setBusy(true);
    try {
      await saveQueryToDashboard(queryId, target);
      onSaved(target, dashboards.data?.items.find((d) => d.id === target)?.title ?? 'dashboard');
    } catch (e) {
      setFailure((e as Error).message);
      setBusy(false);
    }
  };
  return (
    <Dialog
      open
      title="Lưu vào dashboard"
      onClose={onClose}
      footer={
        <>
          <Button kind="ghost" onClick={onClose}>
            Huỷ
          </Button>
          <Button kind="primary" disabled={!target} loading={busy} onClick={() => void save()}>
            Lưu
          </Button>
        </>
      }
    >
      <AsyncRegion state={dashboards} what="danh sách dashboard" skeletonRows={2}>
        {(page) => (
          <Select
            label="Dashboard đích"
            placeholder="Chọn dashboard…"
            value={target}
            onChange={setTarget}
            options={page.items.map((d) => ({ value: d.id, label: d.title }))}
          />
        )}
      </AsyncRegion>
      {failure && <p className="text-error da-caption">Không lưu được: {failure}</p>}
    </Dialog>
  );
}
