import { pathId, V1Error, v1Get, v1Send } from './client';
import type { components } from './schema';

/*
 * "P01 · Biên nhận câu trả lời" (Figma 2420:15098, UC-01-07, SCR-68): the receipt of one answer.
 * Every figure in the answer leads back to the run that produced it — the query run with the
 * metric version and the dataset versions it read, a computation over other figures, or a
 * published period result — together with the models that wrote the sentences and whether one
 * ran outside the organisation. A sealed receipt can be re-run at its original point in time: the
 * figures must come back identical, the words may not (the language model is not deterministic),
 * so by default only the figures re-run.
 *
 * A2: GET /v1/answers/{id}/receipt (ReceiptView), POST /v1/receipts/{id}:replay (202) and
 * GET /v1/receipt-replays/{id} (ReplayView). The screen's shapes below are read from those; what
 * the api does not carry (who asked, the dataset's code, when it was loaded, the signer's name)
 * is not shown.
 */
type Schemas = components['schemas'];
type Uuid = string;
type Timestamp = string;
type IsoDate = string;

export type ReceiptWire = Schemas['ReceiptView'];
export type ReceiptFigureWire = Schemas['ReceiptFigureView'];
export type ReplayWire = Schemas['ReplayView'];

/** A figure read from a governed metric through one query run. */
export interface ReceiptQuerySource {
  kind: 'query_run';
  query_run_id: Uuid;
  /** the certified metric version the figure used; null when it read no metric */
  metric: { version: number | null; certified_at: Timestamp | null } | null;
  /** every dataset version the query read, with its as_of */
  datasets: { version: number | null; as_of: IsoDate | null }[];
  /** sha256 of the compiled SQL and the semantic compiler that produced it */
  query: { sql_sha256: string | null; compiler: string };
}

/** A figure computed deterministically from other figures (a difference, a share). */
export interface ReceiptComputeSource {
  kind: 'compute_run';
  compute_run_id: Uuid | null;
  /** sha256 of the declared computation */
  expr_sha256: string | null;
}

/** A figure read from a period's published result (Kỳ chốt). */
export interface ReceiptPeriodSource {
  kind: 'period_result';
  published_result_id: Uuid | null;
}

/**
 * One figure of the answer, in the order it appears (F1, F2…).
 * `fresh`: data within its update window; `not_applicable`: a reference dataset or a past period,
 * no window applies; `stale`: past its due time, the value is masked (`stale_note` says up to
 * when the data goes); `missing`: no value (period not due yet, or the dataset holds none).
 */
export interface ReceiptFigure {
  key: string;
  /** "Giá trần E5RON92 kỳ 02/07/2026" */
  label: string;
  /** the figure as the answer printed it, with its unit; null when masked or missing */
  value_text: string | null;
  state: 'fresh' | 'not_applicable' | 'stale' | 'missing';
  /** "dữ liệu đến 25/06/2026, đã quá hạn cập nhật"; set when stale */
  stale_note: string | null;
  as_of: IsoDate | null;
  due_at: Timestamp | null;
  source: ReceiptQuerySource | ReceiptComputeSource | ReceiptPeriodSource;
}

/** A language model the turn called. */
export interface ReceiptModel {
  name: string;
  /** "vLLM tại chỗ", "Nhà cung cấp A (API)" */
  provider_label: string;
  /** true when the model runs outside the organisation (a frontier provider) */
  external: boolean;
}

export type RerunMode = 'figures' | 'figures_and_text';

/** The outcome of re-running a receipt at its original point in time. */
export interface ReceiptRerun {
  id: Uuid;
  mode: RerunMode;
  ran_at: Timestamp;
  /** identical, differs_explained, differs, not_reproducible; null while running or failed */
  outcome: string | null;
  failed: boolean;
  figures_total: number;
  figures_matched: number;
  /** the re-computed values, in figure order */
  values_text: string[];
  /** figures whose re-run value differs from the receipt */
  mismatches: { key: string; before: string | null; after: string | null }[];
  /** sha256 of the SQL and of the result rows equal the receipt's */
  hashes_match: boolean;
  /** why the figures differ (a retroactive change, a fix), when the server found out */
  cause_summary: string | null;
  /** the re-written sentences, only for `figures_and_text` */
  answer_text: string | null;
}

export interface AnswerReceipt {
  /** the receipt's own id, which a re-run is asked on */
  id: Uuid;
  answer_id: string;
  /** "BN-20260702-000184" */
  code: string;
  /** a receipt is sealed once the answer is delivered; nothing in it changes afterwards */
  status: 'locked' | 'open';
  created_at: Timestamp;
  /** the product's code and version: the receipt does not carry its display name */
  data_product: { id: string; name: string };
  /** sha256 over the canonical receipt body; null until sealed */
  receipt_sha256: string | null;
  figures: ReceiptFigure[];
  models: ReceiptModel[];
  /** a frontier model was called in this turn */
  frontier_used: boolean;
}

const STATE: Record<string, ReceiptFigure['state']> = {
  masked_stale: 'stale',
  not_available: 'missing',
};

function readSource(raw: ReceiptFigureWire): ReceiptFigure['source'] {
  if (raw.source_kind === 'compute_run')
    return {
      kind: 'compute_run',
      compute_run_id: raw.compute_run_id,
      expr_sha256: raw.expr_sha256,
    };
  if (raw.source_kind === 'period_result')
    return { kind: 'period_result', published_result_id: raw.published_result_id };
  const versions = Array.isArray(raw.dataset_versions)
    ? (raw.dataset_versions as { version?: unknown; as_of?: unknown }[])
    : [];
  return {
    kind: 'query_run',
    query_run_id: raw.query_run_id ?? '',
    metric:
      raw.metric_version_id || raw.metric_version_no != null
        ? { version: raw.metric_version_no, certified_at: raw.metric_certified_at }
        : null,
    datasets: versions.map((d) => ({
      version: typeof d.version === 'number' ? d.version : null,
      as_of: typeof d.as_of === 'string' ? d.as_of : null,
    })),
    query: { sql_sha256: raw.compiled_sql_sha256, compiler: '' },
  };
}

export const readFigure = (raw: ReceiptFigureWire, compiler = ''): ReceiptFigure => {
  const source = readSource(raw);
  if (source.kind === 'query_run') source.query.compiler = compiler;
  return {
    key: raw.figure_key,
    label: raw.label,
    value_text: raw.value === null ? null : [raw.value, raw.unit].filter(Boolean).join(' '),
    state:
      STATE[raw.display_state] ?? (raw.freshness_state === 'fresh' ? 'fresh' : 'not_applicable'),
    stale_note: raw.stale_note,
    as_of: raw.as_of,
    due_at: raw.due_at,
    source,
  };
};

/** `models` holds one row per model call: {task, provider_kind, provider_display_name, model_name}. */
function readModels(raw: unknown): ReceiptModel[] {
  const seen = new Set<string>();
  return (Array.isArray(raw) ? raw : []).flatMap((m) => {
    if (typeof m !== 'object' || m === null || typeof m.model_name !== 'string') return [];
    const label = String(m.provider_display_name ?? m.provider_code ?? '');
    const key = `${m.model_name}|${label}`;
    if (seen.has(key)) return [];
    seen.add(key);
    return [
      { name: m.model_name, provider_label: label, external: m.provider_kind === 'frontier' },
    ];
  });
}

export const readReceipt = (raw: ReceiptWire): AnswerReceipt => ({
  id: raw.id,
  answer_id: raw.answer_id,
  code: raw.receipt_no,
  status: raw.sealed_at ? 'locked' : 'open',
  created_at: raw.answered_at,
  data_product: {
    id: raw.data_product_id,
    name: `${raw.data_product_code} v${raw.data_product_version}`,
  },
  receipt_sha256: raw.receipt_sha256,
  figures: (raw.figures ?? []).map((f) => readFigure(f, raw.compiler_version)),
  models: readModels(raw.models),
  frontier_used: raw.frontier_used,
});

export const getAnswerReceipt = async (answerId: string) =>
  readReceipt(await v1Get<ReceiptWire>(`/answers/${pathId(answerId)}/receipt`));

/** figure_results: {figure_key, original_value, replay_value, sql_sha256_match, result_sha256_match}. */
interface FigureResult {
  figure_key: string;
  original_value: string | null;
  replay_value: string | null;
  sql_sha256_match: boolean | null;
  result_sha256_match: boolean | null;
}

const figureResults = (raw: unknown): FigureResult[] =>
  (Array.isArray(raw) ? raw : []).filter(
    (r): r is FigureResult =>
      typeof r === 'object' && r !== null && typeof r.figure_key === 'string',
  );

const asText = (v: unknown) => (v === null || v === undefined ? null : String(v));

export function readReplay(raw: ReplayWire): ReceiptRerun {
  const results = figureResults(raw.figure_results);
  const mismatches = results
    .filter((r) => asText(r.original_value) !== asText(r.replay_value))
    .map((r) => ({
      key: r.figure_key,
      before: asText(r.original_value),
      after: asText(r.replay_value),
    }));
  return {
    id: raw.id,
    mode: raw.mode as RerunMode,
    ran_at: raw.finished_at ?? raw.started_at,
    outcome: raw.outcome,
    failed: raw.status === 'failed',
    figures_total: results.length,
    figures_matched: results.length - mismatches.length,
    values_text: results.map((r) => asText(r.replay_value) ?? '—'),
    mismatches,
    hashes_match: results.every(
      (r) => r.sql_sha256_match !== false && r.result_sha256_match !== false,
    ),
    cause_summary: raw.cause_summary,
    answer_text: raw.replay_text,
  };
}

const POLL_LIMIT = 30;
const pause = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * A2: POST /v1/receipts/{id}:replay {mode} (202) re-runs the stored plan at the receipt's
 * answered_at; the replay is then read until it is no longer running.
 */
export async function rerunReceipt(receiptId: string, mode: RerunMode): Promise<ReceiptRerun> {
  const job = await v1Send<Schemas['JobAccepted']>(
    'POST',
    `/receipts/${pathId(receiptId)}:replay`,
    {
      mode,
    } satisfies Schemas['ReplayCreate'],
  );
  const wait = Math.min(job.poll_after_ms ?? 2000, 2000);
  for (let i = 0; i < POLL_LIMIT; i += 1) {
    const replay = await v1Get<ReplayWire>(`/receipt-replays/${pathId(job.id)}`);
    if (replay.status !== 'running') return readReplay(replay);
    await pause(wait);
  }
  throw new V1Error(504, 'timeout', 'Lượt chạy lại chưa xong; mở lại biên nhận sau ít phút.');
}
