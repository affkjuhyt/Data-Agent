import { pathId, v1Get, v1Send, type Page } from './client';
import type { components } from './schema';

type Schemas = components['schemas'];

/*
 * P15 Dự báo (UC-15-01…08) on the api's forecast group: a forecast question answered by a
 * forecast job (/v1/forecasts), the model versions and their backtest the studio shows, the
 * scenarios a model reruns, the formula models of the fuel prices with their Monte Carlo
 * distribution, and the forecast ledger.
 *
 * The screens keep their view models; the functions below read the api's shapes into them.
 * What the api does not carry (the session a question was asked in, the history of actuals, the
 * names behind user ids, the words of a period's bulletin) is left empty or null and the screens
 * hide that line; the mock sends those fields under `display`, which wins when present.
 *
 * Rules the shapes enforce: a forecast point always carries its 80% and 95% interval (a line
 * without its interval cannot be expressed), every forecast names its model version and data
 * cut-off (BR-157), nothing is answered before backtest (BR-159), the creator never approves
 * their own model (BR-158), and a degraded model labels its forecasts (BR-160).
 */

/* ─── Shared reading ──────────────────────────────────────────────────── */

type DetailWire = Schemas['ForecastDetail'] & {
  display?: {
    job?: Partial<ForecastJob>;
    result?: Partial<Forecast>;
    workbench?: Partial<ForecastWorkbench>;
    /** Per product code. */
    fuel?: Record<string, Partial<FuelPriceForecast>>;
  };
};
type PredictionWire = Schemas['PredictionView'];
type VersionView = Schemas['ModelVersionView'];

const num = (v: number | string | null | undefined) =>
  v === null || v === undefined || v === '' ? null : Number(v);

const record = (v: unknown): Record<string, unknown> =>
  typeof v === 'object' && v !== null && !Array.isArray(v) ? (v as Record<string, unknown>) : {};

const FREQUENCY: Record<string, string> = {
  day: 'ngày',
  week: 'tuần',
  month: 'tháng',
  quarter: 'quý',
};

const ALGORITHM: Record<string, string> = {
  naive: 'Naive',
  seasonal_naive: 'Seasonal naive',
  ets: 'ETS',
  theta: 'Theta',
  arima: 'ARIMA',
  sarimax: 'SARIMAX',
  lightgbm: 'LightGBM',
  ensemble: 'Ensemble',
  formula_mc: 'formula_mc',
};
const algorithmLabel = (code: string) => ALGORITHM[code] ?? code;

/** "2026-11-01" → "T11/2026" for a monthly series, "Q4/2026" quarterly, "01/11/2026" otherwise. */
export function periodLabel(iso: string, frequency: string) {
  const [year, month, day] = iso.slice(0, 10).split('-');
  if (frequency === 'month') return `T${Number(month)}/${year}`;
  if (frequency === 'quarter') return `Q${Math.ceil(Number(month) / 3)}/${year}`;
  return `${day}/${month}/${year}`;
}

const dateVN = (iso: string) => {
  const [year, month, day] = iso.slice(0, 10).split('-');
  return `${day}/${month}/${year}`;
};

/** The clock time in Vietnam, whatever offset the api writes. */
const timeVN = (iso: string) =>
  new Date(iso).toLocaleTimeString('vi-VN', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'Asia/Ho_Chi_Minh',
  });

// Forecast jobs and issued forecasts share the screen model, but are separate resources in the
// deployed API. Keep the job endpoint as a compatibility fallback for older gateways.
const getDetail = (id: string) =>
  v1Get<DetailWire>(`/forecasts/${pathId(id)}`).catch(() =>
    v1Get<DetailWire>(`/forecast-jobs/${pathId(id)}`),
  );

const getVersion = (id: string) => v1Get<VersionView>(`/forecast-model-versions/${pathId(id)}`);

const getBacktest = (versionId: string) =>
  v1Get<Schemas['BacktestView']>(`/forecast-model-versions/${pathId(versionId)}/backtest`);

/* ─── Việc dự báo: the in-progress answer (UC-15-01) ──────────────────── */

export type ForecastJobStatus = 'waiting_human' | 'running' | 'done' | 'failed';

export interface ForecastJobStep {
  title: string;
  /** "query.semantic · cắt tại 30/09/2026"; empty when the api gives no detail. */
  detail: string;
  state: 'done' | 'running' | 'needs_you' | 'pending';
  /** "10:30"; null while it has not started or when the api does not time the step. */
  at_text: string | null;
}

export interface ForecastAssumptionRequest {
  /** The HITL request (kind forecast_assumption), answered with POST /v1/hitl-requests/{id}:resolve. */
  hitl_id: string;
  /** "Lãi suất điều hành là biến cần giá trị tương lai. Chọn giả định cho 3 tháng tới:" */
  question: string;
  options: { id: string; label: string; is_default: boolean }[];
  /** The assumptions already set for the other exogenous variables. */
  other_assumptions: { variable: string; assumption: string }[];
}

export interface ForecastJob {
  id: string;
  status: ForecastJobStatus;
  metric_name: string;
  /** The session the question was asked in; the api's job names the question only. */
  session: { id: string; title: string; owner_name: string; question_count: number } | null;
  /** The question as asked; empty when not known. */
  question: string;
  /** "Chỉ số: …", "Tần suất: tháng", "Tầm: 3 kỳ" — what the job was asked for. */
  intent_chips: string[];
  /** The agent's answer so far: why it is asynchronous and when it should finish. */
  message: string;
  steps: ForecastJobStep[];
  /** An older forecast usable for the trend, with the label it must carry. */
  stale_forecast: { id: string; version_label: string; note: string } | null;
  /** The assumption the agent waits for, when it is known here; otherwise it is in Cần bạn. */
  assumption_request: ForecastAssumptionRequest | null;
  /** Set once the job is done: the forecast it produced (the same resource). */
  forecast_id: string | null;
  code?: string;
  model_id?: string | null;
}

const JOB_STATUS: Record<string, ForecastJobStatus> = {
  queued: 'running',
  preparing: 'running',
  training: 'running',
  backtesting: 'running',
  waiting_human: 'waiting_human',
  ready: 'done',
  failed: 'failed',
  cancelled: 'failed',
};

/** The job's life cycle (forecast_job.status) as the steps of the answer card. */
const JOB_STEPS: [string, string][] = [
  ['preparing', 'Chuẩn bị chuỗi mục tiêu và biến ngoại sinh'],
  ['waiting_human', 'Xác nhận giả định cho biến ngoại sinh'],
  ['training', 'Huấn luyện các mô hình ứng viên'],
  ['backtesting', 'Backtest so seasonal naive'],
  ['ready', 'Dự báo, khoảng 80% và 95%'],
];

function jobSteps(status: string): ForecastJobStep[] {
  const at = status === 'queued' ? -1 : JOB_STEPS.findIndex(([s]) => s === status);
  return JOB_STEPS.map(([step, title], i) => ({
    title,
    detail: '',
    at_text: null,
    state:
      status === 'ready' || (at >= 0 && i < at)
        ? 'done'
        : i === at
          ? step === 'waiting_human'
            ? 'needs_you'
            : 'running'
          : 'pending',
  }));
}

function jobMessage(raw: DetailWire): string {
  const progress = record(raw.progress);
  if (raw.status === 'ready')
    return `Dự báo đã xong${raw.finished_at ? ` lúc ${timeVN(raw.finished_at)}` : ''}; kết quả có khoảng 80% và 95%, kèm mô hình và thời điểm cắt dữ liệu.`;
  if (raw.status === 'waiting_human')
    return 'Agent Dự báo cần bạn xác nhận giả định cho biến ngoại sinh trước khi chạy tiếp.';
  if (raw.status === 'cancelled') return 'Việc dự báo đã bị huỷ.';
  if (raw.status === 'failed')
    return `Việc dự báo không thành công${typeof progress.error === 'string' ? `: ${progress.error}` : '.'}`;
  return `Chưa có dự báo còn hiệu lực cho chỉ số này nên việc dự báo đang chạy${raw.eta_at ? `, dự kiến xong lúc ${timeVN(raw.eta_at)}` : ''}. Kết quả hiện ở đây và được báo cho bạn khi xong.`;
}

function readJob(raw: DetailWire): ForecastJob {
  const status = JOB_STATUS[raw.status] ?? 'running';
  return {
    id: raw.id,
    status,
    metric_name: raw.metric_code,
    session: null,
    question: '',
    intent_chips: [
      `Chỉ số: ${raw.metric_code}`,
      `Tần suất: ${FREQUENCY[raw.frequency] ?? raw.frequency}`,
      `Tầm: ${raw.horizon} kỳ`,
      `Cắt tại ${dateVN(raw.cutoff_at)}`,
    ],
    message: jobMessage(raw),
    steps: jobSteps(raw.status),
    stale_forecast: null,
    assumption_request: null,
    forecast_id: status === 'done' ? raw.id : null,
    ...raw.display?.job,
  };
}

/** A2: GET /v1/forecasts/{id} — the job, its progress and, once ready, its result. */
export const getForecastJob = async (id: string) => {
  const raw = await getDetail(id);
  if ('intent_chips' in raw && Array.isArray((raw as unknown as ForecastJob).intent_chips))
    return raw as unknown as ForecastJob;
  return readJob(raw);
};

/* ─── Xưởng dự báo (UC-15-02, UC-15-03) ───────────────────────────────── */

export interface ForecastStat {
  label: string;
  value: string;
  note: string;
  /** neutral: a muted note; default: a plain one. */
  tone: 'success' | 'warning' | 'error' | 'neutral' | 'default';
}

export interface ForecastVariable {
  name: string;
  /** "Hợp nhất P14 · tuần → tháng", "sbv.gov.vn" */
  source: string;
  lag_text: string;
  correlation_text: string;
  future_value_text: string;
  /** excluded: published after the cut-off (leakage), removed automatically (BR-157). */
  status: 'used' | 'excluded';
  status_text: string;
}

export interface BacktestRow {
  model: string;
  /** Null when the backtest does not give it. */
  mase: number | null;
  smape_text: string;
  cover80_text: string;
  cover95_text: string;
  role: 'baseline' | 'candidate' | 'selected';
}

export interface ForecastWorkbench {
  job_id: string;
  metric_name: string;
  /** "Chuỗi tháng · cắt tại 30/09/2026 · tầm 3 kỳ · backtest trên 6 điểm cắt" */
  summary_text: string;
  series_stats: ForecastStat[];
  variables: ForecastVariable[];
  variables_note: string;
  backtest: BacktestRow[];
  backtest_note: string;
  /** The job's model version: its card goes for approval (:submit); null before training. */
  version_id: string | null;
  /** The forecast model that version belongs to, retrained by "Chạy lại backtest". */
  forecast_model_id: string | null;
  /** "v7": the version the card is about. */
  next_version_label: string;
  /** No model beat seasonal naive: no card, nothing to publish (UC-15-03). */
  can_create_card: { allowed: boolean; reason: string | null };
  /** Set once the card was sent for approval: the model it is reviewed on. */
  model_id: string | null;
  job_code?: string;
}

const FUTURE_VALUE: Record<string, string> = {
  declared: 'Giả định',
  forecast: 'Dự báo riêng',
  own_forecast: 'Dự báo riêng',
  known: 'Biết trước',
  calendar: 'Biết trước',
};

/** model_version.features: [{name, source, lag, transform, future_assumption}]. */
function variablesOf(features: unknown): ForecastVariable[] {
  return (Array.isArray(features) ? features : []).flatMap((raw) => {
    const f = record(raw);
    if (typeof f.name !== 'string') return [];
    const future = typeof f.future_assumption === 'string' ? f.future_assumption : '';
    return [
      {
        name: f.name,
        source: typeof f.source === 'string' ? f.source : '',
        lag_text: f.lag === undefined || f.lag === null ? '—' : String(f.lag),
        correlation_text: '—',
        future_value_text: future ? (FUTURE_VALUE[future] ?? future) : '—',
        status: 'used' as const,
        status_text: 'Dùng',
      },
    ];
  });
}

/** 0.81 → "81%"; the backtest writes coverage and sMAPE as shares. */
const shareText = (v: unknown) => {
  const n = typeof v === 'number' || typeof v === 'string' ? num(v) : null;
  return n === null || Number.isNaN(n) ? '—' : `${formatOne(n * 100).replace(/,0$/, '')}%`;
};

/**
 * backtest_run.candidates is every candidate's result ([{algorithm, mase, smape, coverage80,
 * coverage95}]), baseline_metrics the same figures for seasonal naive. MASE is scaled by seasonal
 * naive, so the baseline's is 1 when the run does not repeat it.
 */
function backtestRows(backtest: Schemas['BacktestView'], selected: string): BacktestRow[] {
  const base = record(backtest.baseline_metrics);
  const rows: BacktestRow[] = [
    {
      model: 'Seasonal naive',
      mase: num(base.mase as number | string | undefined) ?? 1,
      smape_text: shareText(base.smape),
      cover80_text: shareText(base.coverage80),
      cover95_text: shareText(base.coverage95),
      role: 'baseline',
    },
  ];
  for (const raw of Array.isArray(backtest.candidates) ? backtest.candidates : []) {
    const c = record(raw);
    const code = [c.algorithm, c.model, c.name].find((v) => typeof v === 'string') as
      | string
      | undefined;
    if (!code || code === 'seasonal_naive') continue;
    rows.push({
      model: algorithmLabel(code),
      mase: num(c.mase as number | string | undefined),
      smape_text: shareText(c.smape),
      cover80_text: shareText(c.coverage80),
      cover95_text: shareText(c.coverage95),
      role: code === selected ? 'selected' : 'candidate',
    });
  }
  if (!rows.some((r) => r.role === 'selected'))
    rows.push({
      model: algorithmLabel(selected),
      mase: num(backtest.mase),
      smape_text: '—',
      cover80_text: '—',
      cover95_text: '—',
      role: 'selected',
    });
  return rows;
}

/** A version past draft has been sent for approval: its card is reviewed on the model. */
const SUBMITTED = new Set(['pending_approval', 'published', 'degraded', 'retired', 'rejected']);

/**
 * The studio of one job: the job's model version (its variables, training range and card) and
 * that version's rolling backtest against seasonal naive. Before the job has trained a version
 * there is nothing to show but the job itself.
 */
export async function getForecastWorkbench(jobId: string): Promise<ForecastWorkbench> {
  const job = await getDetail(jobId);
  const versionId = job.model_version_id;
  const [version, backtest] = versionId
    ? await Promise.all([getVersion(versionId), getBacktest(versionId).catch(() => null)])
    : [null, null];
  const mase = backtest ? num(backtest.mase) : null;
  const summary = [
    `Chuỗi ${FREQUENCY[job.frequency] ?? job.frequency}`,
    `cắt tại ${dateVN(job.cutoff_at)}`,
    `tầm ${job.horizon} kỳ`,
    backtest && `backtest trên ${backtest.cutoffs.length} điểm cắt`,
  ];
  const draft = version?.status === 'draft';
  const canCreate =
    !version || !backtest
      ? { allowed: false, reason: 'Việc dự báo chưa có phiên bản mô hình đã qua backtest.' }
      : mase === null || mase >= 1
        ? {
            allowed: false,
            reason: 'Không mô hình nào thắng seasonal naive (MASE < 1), nên không có thẻ để duyệt.',
          }
        : draft
          ? { allowed: true, reason: null }
          : { allowed: false, reason: 'Thẻ mô hình đã được gửi duyệt.' };
  return {
    job_id: job.id,
    metric_name: job.metric_code,
    summary_text: summary.filter(Boolean).join(' · '),
    series_stats: version
      ? [
          {
            label: 'Chuỗi huấn luyện',
            value: version.n_points === null ? '—' : `${version.n_points} kỳ`,
            note: `${dateVN(version.training_from)} – ${dateVN(version.training_to)}`,
            tone: 'default',
          },
          {
            label: 'Biến ngoại sinh',
            value: String(version.n_exog),
            note: 'tối đa min(8, số kỳ / 12) (BR-172)',
            tone: 'neutral',
          },
          {
            label: 'MASE backtest',
            value: mase === null ? '—' : formatTwo(mase),
            note: 'phát hành khi < 1 (BR-158)',
            tone: mase !== null && mase < 1 ? 'success' : 'warning',
          },
        ]
      : [],
    variables: version ? variablesOf(version.features) : [],
    variables_note:
      'Mọi biến căn theo thời điểm ghi nhận; biến chỉ có sau thời điểm cắt bị loại tự động (BR-157).',
    backtest: backtest && version ? backtestRows(backtest, version.algorithm) : [],
    backtest_note:
      mase === null
        ? ''
        : `MASE tổng hợp ${formatTwo(mase)} trên mọi điểm cắt và tầm; khoảng dự báo theo sai số backtest từng bước tầm.`,
    version_id: version?.id ?? null,
    forecast_model_id: version?.model_id ?? null,
    next_version_label: version ? `v${version.version}` : '',
    can_create_card: canCreate,
    model_id: version && SUBMITTED.has(version.status) ? version.model_id : null,
    ...job.display?.workbench,
  };
}

/** A2: retraining makes a new version that goes through backtest again (202). */
export const rerunBacktest = async (forecastModelId: string) =>
  v1Send<Schemas['ForecastAccepted']>(
    'POST',
    `/forecast-models/${pathId(forecastModelId)}:retrain`,
    {
      reason: 'Chạy lại backtest từ Xưởng dự báo',
    } satisfies Schemas['RetrainRequest'],
  );

/** A2: the card is on the version; :submit sends it to the metric owner (kind forecast_model). */
export const createModelCard = async (versionId: string) => {
  const version = await v1Send<VersionView>(
    'POST',
    `/forecast-model-versions/${pathId(versionId)}:submit`,
    {} satisfies Schemas['VersionNote'],
  );
  return { model_id: version.model_id, version_id: version.id };
};

/* ─── Kết quả dự báo và kịch bản (UC-15-01, UC-15-05) ─────────────────── */

export interface ForecastPoint {
  /** ISO date of the period's first day. */
  period: string;
  /** "T11/2026" */
  label: string;
  mean: number;
  lo80: number;
  hi80: number;
  lo95: number;
  hi95: number;
}

export interface HistoryPoint {
  period: string;
  label: string;
  actual: number;
}

export interface ForecastScenario {
  id: string;
  /** The scenario's name, "Lãi suất điều hành +0,5 từ T12/2026". */
  text: string;
  /** The conditional, non-causal reading the screen must show next to it (BR-173). */
  disclaimer: string;
  summary: string;
  points: { period: string; value: number }[];
  /** Legend label. */
  short_label: string;
  saved?: boolean;
}

export interface Forecast {
  id: string;
  metric_name: string;
  frequency: string;
  /** "T11/2026 – T1/2027" */
  horizon_label: string;
  /** "nghìn tỷ đồng"; empty when the api does not say. */
  unit_label: string;
  model: {
    /** The forecast model: scenarios run on it, the model page opens it. */
    id: string;
    version_label: string;
    algorithm_label: string;
    /** Backtest MASE of the version; null without a backtest. */
    mase: number | null;
    status: string;
  };
  /** Point-in-time cut-off of the training data (BR-157), ISO date. */
  data_cutoff: string;
  created_at: string;
  /** approved: from a published model; stale: a degraded model's (BR-160); else experimental. */
  label: 'approved' | 'stale' | 'experimental';
  approved_by_name: string | null;
  added_to_session?: boolean;
  /** Why it is stale, when label is stale. */
  stale_note: string | null;
  /** Actuals before the cut-off; the job does not carry them. */
  history: HistoryPoint[];
  points: ForecastPoint[];
  interval_note: string;
  contributions: { title: string; items: { label: string; value: number }[] };
  assumptions_text: string;
  /** Variables a scenario may move (the version's features). */
  scenario_variables: string[];
  /** The scenario the screen ran; readers leave it null. */
  scenario: ForecastScenario | null;
  /** The session it was asked from, for "Thêm vào phiên"; the job does not name it. */
  session_id: string | null;
}

const INTERVAL_NOTE: Record<string, string> = {
  conformal: 'Khoảng theo conformal trên sai số backtest của chính chuỗi.',
  conformal_pooled: 'Khoảng theo conformal trên sai số gộp của các chuỗi con cùng chỉ số.',
  parametric: 'Khoảng tham số.',
  monte_carlo: 'Khoảng là phân vị của các lần mô phỏng.',
};

const assumptionText = (raw: unknown) =>
  (Array.isArray(raw) ? raw : [])
    .map((a) => {
      const r = record(a);
      return [r.feature, r.change_kind, r.value, r.from_period && `từ ${String(r.from_period)}`]
        .filter((v) => v !== undefined && v !== null && v !== '')
        .join(' ');
    })
    .filter(Boolean)
    .join(' · ');

const pointOf = (p: PredictionWire, frequency: string): ForecastPoint => ({
  period: p.period,
  label: periodLabel(p.period, frequency),
  mean: Number(p.point),
  lo80: Number(p.lo80),
  hi80: Number(p.hi80),
  lo95: Number(p.lo95),
  hi95: Number(p.hi95),
});

/** prediction.contributions: {variable: value} (SARIMAX coefficients or SHAP) of the last period. */
function contributionsOf(predictions: PredictionWire[], frequency: string) {
  const last = predictions[predictions.length - 1];
  const items = Object.entries(record(last?.contributions)).flatMap(([label, v]) =>
    typeof v === 'number' || (typeof v === 'string' && v !== '' && !Number.isNaN(Number(v)))
      ? [{ label, value: Number(v) }]
      : [],
  );
  return {
    title: last ? `Đóng góp vào dự báo ${periodLabel(last.period, frequency)}` : 'Đóng góp',
    items,
  };
}

const VERSION_STATUS_LABEL: Record<string, Forecast['label']> = {
  approved: 'approved',
  degraded: 'stale',
};

function readForecast(
  raw: DetailWire,
  version: VersionView | null,
  backtest: Schemas['BacktestView'] | null,
): Forecast {
  const predictions = raw.predictions ?? [];
  const points = predictions.map((p) => pointOf(p, raw.frequency));
  const labels = raw.labels ?? [];
  const label =
    labels.map((l) => VERSION_STATUS_LABEL[l]).find(Boolean) ??
    (version?.status === 'degraded' ? 'stale' : 'experimental');
  const first = points[0];
  const last = points[points.length - 1];
  return {
    id: raw.id,
    metric_name: raw.metric_code,
    frequency: raw.frequency,
    horizon_label: first ? `${first.label} – ${last.label}` : `${raw.horizon} kỳ`,
    unit_label: '',
    model: {
      id: version?.model_id ?? '',
      version_label: version ? `v${version.version}` : '',
      algorithm_label: version ? algorithmLabel(version.algorithm) : '',
      mase: backtest ? num(backtest.mase) : null,
      status: version?.status ?? '',
    },
    data_cutoff: raw.cutoff_at.slice(0, 10),
    created_at: raw.created_at,
    label,
    approved_by_name: null,
    stale_note: label === 'stale' ? 'mô hình suy giảm' : null,
    history: [],
    points,
    interval_note: INTERVAL_NOTE[predictions[0]?.interval_method ?? ''] ?? '',
    contributions: contributionsOf(predictions, raw.frequency),
    assumptions_text: assumptionText(raw.assumptions),
    scenario_variables: version ? variablesOf(version.features).map((v) => v.name) : [],
    scenario: null,
    session_id: null,
    ...raw.display?.result,
  };
}

/**
 * A2 has no list of forecast jobs. The issued forecasts are the ledger's lines, each pointing at
 * the job that made it; a fuel price's line (scope.product_code) belongs to its own screen.
 */
export async function listForecasts(): Promise<Page<{ id: string }>> {
  const ledger = await v1Get<Page<Schemas['LedgerEntryView']>>('/forecast-ledger', {
    page_size: 50,
  });
  const ids = new Set<string>();
  for (const line of ledger.items) if (line.job_id && !productOf(line)) ids.add(line.job_id);
  if (ids.size) return { items: [...ids].map((id) => ({ id })), next_page_token: '' };
  const legacy = await v1Get<Page<Forecast>>('/forecasts', { page_size: 50 }).catch(() => null);
  return legacy
    ? {
        items: legacy.items.map((item) => ({ id: item.id })),
        next_page_token: legacy.next_page_token,
      }
    : { items: [], next_page_token: '' };
}

/** A2: GET /v1/forecasts/{id}, its model version and that version's backtest. */
export async function getForecast(id: string): Promise<Forecast> {
  const raw = await getDetail(id);
  if ('metric_name' in raw && Array.isArray((raw as unknown as Forecast).points))
    return raw as unknown as Forecast;
  const versionId = raw.model_version_id;
  const [version, backtest] = versionId
    ? await Promise.all([getVersion(versionId), getBacktest(versionId).catch(() => null)])
    : [null, null];
  return readForecast(raw, version, backtest);
}

export interface ScenarioInput {
  name: string;
  feature: string;
  /** "tăng", "giảm", "giữ nguyên" */
  change_kind: string;
  value: number | null;
  /** ISO date of the first period it applies from. */
  from_period: string;
}

/** Polls of a queued scenario before the screen offers to load it later. */
const SCENARIO_POLLS = 6;
const MAX_POLL_MS = 5_000;

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function scenarioPoints(modelId: string, scenarioId: string) {
  const page = await v1Get<Schemas['PredictionPage']>(
    `/forecast-models/${pathId(modelId)}/predictions`,
    { scenario_id: scenarioId, page_size: 50 },
  );
  return {
    points: page.items.map((p) => ({ period: p.period, value: Number(p.point) })),
    notice: page.notice ?? '',
  };
}

function scenarioSummary(forecast: Forecast, points: { period: string; value: number }[]) {
  if (points.length === 0) return 'Kịch bản đang chạy; tải lại sau ít phút để xem kết quả.';
  const base = new Map(forecast.points.map((p) => [p.period, p]));
  const last = [...points].reverse().find((p) => base.has(p.period));
  if (!last) return '';
  const point = base.get(last.period) as ForecastPoint;
  const delta = Math.round((last.value - point.mean) * 10) / 10;
  return `${point.label}: ${formatOne(last.value)}, ${formatSigned(delta)} so dự báo gốc.`;
}

/**
 * A2: POST /v1/forecast-models/{id}/scenarios queues the trained model with one assumption fixed
 * (202, saved under its name) and its predictions are read with ?scenario_id once ml-forecast has
 * written them. A variable the model does not use is refused with 422 (UC-15-05 E1).
 */
export async function runScenario(forecast: Forecast, input: ScenarioInput) {
  const accepted = await v1Send<Schemas['ScenarioAccepted']>(
    'POST',
    `/forecast-models/${pathId(forecast.model.id)}/scenarios`,
    {
      name: input.name,
      assumptions: [
        {
          feature: input.feature,
          change_kind: input.change_kind,
          value: input.value,
          from_period: input.from_period,
        },
      ],
    } satisfies Schemas['ScenarioCreate'],
  );
  const scenario: ForecastScenario = {
    id: accepted.id,
    text: accepted.name,
    disclaimer: accepted.notice,
    summary: '',
    points: [],
    short_label: accepted.name,
  };
  for (let i = 0; i < SCENARIO_POLLS && scenario.points.length === 0; i++) {
    await wait(Math.min(accepted.poll_after_ms, MAX_POLL_MS));
    scenario.points = (await scenarioPoints(forecast.model.id, accepted.id)).points;
  }
  return { ...scenario, summary: scenarioSummary(forecast, scenario.points) };
}

/** Reads a queued scenario's predictions again. */
export async function reloadScenario(forecast: Forecast, scenario: ForecastScenario) {
  const { points, notice } = await scenarioPoints(forecast.model.id, scenario.id);
  return {
    ...scenario,
    points,
    disclaimer: notice || scenario.disclaimer,
    summary: scenarioSummary(forecast, points),
  };
}

/**
 * A2: POST /v1/workspaces/{id}/tiles. TileCreate has no forecast kind, so the forecast goes in as
 * an agent result whose spec names the job.
 */
export const addForecastToSession = async (workspaceId: string, forecast: Forecast) =>
  v1Send<Schemas['TileView']>('POST', `/workspaces/${pathId(workspaceId)}/tiles`, {
    kind: 'agent_result',
    title: `Dự báo ${forecast.metric_name} · ${forecast.horizon_label}`,
    spec: { forecast_id: forecast.id },
    // spec is a free object; the generated type reads it as Record<string, never>.
  } satisfies Omit<Schemas['TileCreate'], 'spec'> & { spec: Record<string, unknown> });

/* ─── Mô hình dự báo: duyệt và theo dõi (UC-15-04, UC-15-06) ──────────── */

export interface ForecastModel {
  id: string;
  metric_name: string;
  /** "tháng" */
  frequency_label: string;
  /** "v6 đang phát hành · suy giảm từ 06/10 · v7 nháp chờ duyệt · người tạo không tự duyệt (BR-158)" */
  status_text: string;
  candidate: {
    version_id: string;
    version_label: string;
    status: 'pending_review' | 'published' | 'rejected';
    /** Mục tiêu, Dữ liệu huấn luyện, Biến và giả định, Thuật toán, Người tạo, Người duyệt. */
    card: { label: string; value: string }[];
    horizons: {
      horizon_label: string;
      mase_text: string;
      baseline_text: string;
      cover80_text: string;
      cover95_text: string;
    }[];
    limitation: string;
    rejection_reason: string | null;
  } | null;
  /** Whether the caller may decide: the metric owner, never the creator (BR-158). */
  can_approve: { allowed: boolean; reason: string | null };
  monitoring: {
    version_label: string;
    status: 'published' | 'degraded' | 'retired';
    stats: ForecastStat[];
    rows: {
      label: string;
      forecast_text: string;
      actual_text: string;
      error_text: string;
      inside95: boolean;
    }[];
    note: string;
  } | null;
}

type ModelWire = (Schemas['ForecastModelView'] | Schemas['ForecastModelDetail']) & {
  display?: ForecastModel;
};

/** model_card is jsonb: its scalar entries become the card's lines, as written. */
const cardOf = (version: VersionView): { label: string; value: string }[] => {
  const entries =
    typeof version.model_card === 'object' && version.model_card !== null
      ? Object.entries(version.model_card as Record<string, unknown>).filter(
          ([, v]) => typeof v === 'string' || typeof v === 'number',
        )
      : [];
  return [
    { label: 'Thuật toán', value: version.algorithm },
    { label: 'Dữ liệu huấn luyện', value: `${version.training_from} – ${version.training_to}` },
    ...entries.map(([label, value]) => ({ label, value: String(value) })),
  ];
};

const CANDIDATE_STATUS: Record<string, 'pending_review' | 'published' | 'rejected'> = {
  pending_approval: 'pending_review',
  published: 'published',
  degraded: 'published',
  rejected: 'rejected',
};

/*
 * The model as "Mô hình dự báo" reads it. The candidate is the current version when it waits for
 * approval or was decided; its horizons and the monitoring rows are PROPOSAL (A2 serves them as
 * backtest and accuracy reads), so the figures are the model's rolling and backtest MASE.
 */
function readModel(raw: ModelWire): ForecastModel {
  if (raw.display) return raw.display;
  const version = 'current_version' in raw ? (raw.current_version ?? null) : null;
  const candidateStatus = version ? CANDIDATE_STATUS[version.status] : undefined;
  const degraded = 'degraded' in raw && raw.degraded === true;
  const mase = (v: number | string | null | undefined) =>
    v === null || v === undefined ? '—' : String(Number(v).toFixed(2)).replace('.', ',');
  return {
    id: raw.id,
    metric_name: raw.metric_code,
    frequency_label: FREQUENCY[raw.frequency] ?? raw.frequency,
    status_text: [
      version &&
        `v${version.version} ${candidateStatus === 'pending_review' ? 'chờ duyệt' : 'đang dùng'}`,
      degraded && 'suy giảm',
    ]
      .filter(Boolean)
      .join(' · '),
    candidate:
      version && candidateStatus
        ? {
            version_id: version.id,
            version_label: `v${version.version}`,
            status: candidateStatus,
            card: cardOf(version),
            horizons: [],
            limitation: '',
            rejection_reason: null,
          }
        : null,
    // BR-158 is the server's: the creator cannot decide, the call says so with 403.
    can_approve: { allowed: candidateStatus === 'pending_review', reason: null },
    monitoring: version
      ? {
          version_label: `v${version.version}`,
          status: degraded ? 'degraded' : raw.status === 'retired' ? 'retired' : 'published',
          stats: [
            {
              label: 'MASE gần đây',
              value: mase('rolling_mase' in raw ? raw.rolling_mase : null),
              note: degraded ? 'đã suy giảm (BR-160)' : '',
              tone: degraded ? 'warning' : 'default',
            },
            {
              label: 'MASE kiểm định',
              value: mase('backtest_mase' in raw ? raw.backtest_mase : null),
              note: '',
              tone: 'default',
            },
          ],
          rows: [],
          note: '',
        }
      : null,
  };
}

export const listForecastModels = async () => {
  const page = await v1Get<Page<ModelWire>>('/forecast-models', { page_size: 50 });
  return { ...page, items: page.items.map(readModel) };
};

export const getForecastModel = async (id: string) =>
  readModel(await v1Get<ModelWire>(`/forecast-models/${pathId(id)}`));

/** Publishes the candidate; the published version before it is retired. */
export const approveModelVersion = async (versionId: string) =>
  v1Send<VersionView>('POST', `/forecast-model-versions/${pathId(versionId)}:approve`, {});

export const rejectModelVersion = async (versionId: string, reason: string) =>
  v1Send<VersionView>('POST', `/forecast-model-versions/${pathId(versionId)}:reject`, {
    reason,
  } satisfies Schemas['VersionReject']);

/* ─── Giá xăng dầu: phân phối, phân rã và sổ dự báo (UC-15-07, UC-15-08) ─ */

/*
 * The retail ceiling price of one fuel for the next adjustment period, computed by the price
 * formula (world price, exchange rate, taxes and fees, the stabilisation Fund) under Monte Carlo
 * simulation: a distribution, never a single number (BR-183). The Fund decision belongs to the
 * ministries, so the model only estimates a probability per Fund level (BR-184). Every published
 * forecast is written to the ledger before its period, is never edited, and is scored when the
 * Ministry of Industry and Trade publishes the price; missed periods stay visible (BR-185).
 *
 * A fuel forecast is one product of a formula run (a forecast job, POST /formula-models/{id}/runs),
 * so its id here is `<job id>~<product code>`.
 */

/** One sample of the simulated density; `density` is relative (the chart only needs its shape). */
export interface DensityPoint {
  price: number;
  density: number;
}

export interface FuelPriceDistribution {
  median: number;
  lo80: number;
  hi80: number;
  lo95: number;
  hi95: number;
  /** P(ceiling above the previous period), 0–1; null before the forecast is issued. */
  p_increase: number | null;
  density: DensityPoint[];
}

export interface FuelFundLevel {
  /** "Trích 200 đ/lít" */
  label: string;
  /** 0–1 */
  probability: number;
  /** The Fund amount per unit: contributions positive, uses negative. */
  value: number;
}

/** A formula parameter in force, certified by someone other than who entered it (BR-182). */
export interface FuelFormulaParameter {
  /** "thue_bvmt_xang" */
  key: string;
  /** "2.000 đ/lít" */
  value_text: string;
  /** ISO date the value takes effect. */
  effective_from: string;
  /** The legal document it comes from, opened in the document viewer. */
  legal_ref: { label: string; document_id: string | null };
  /** Empty when only the certifier's id is known. */
  certified_by_name: string;
  certified: boolean;
}

export interface FuelForecastProduct {
  forecast_id: string;
  /** "E5RON92", "Diesel 0,05S" */
  label: string;
}

export interface FuelFundScenario {
  fund_level_label: string;
  median: number;
  lo80: number;
  hi80: number;
  lo95: number;
  hi95: number;
}

export interface FuelPriceForecast {
  id: string;
  job_id: string;
  product_code: string;
  /** The formula model of this product: "Chạy lại" runs it. */
  formula_model_id: string;
  /** The forecast model behind it: Fund scenarios run on it. */
  model_id: string;
  /** The ledger series of this fuel (UC-15-08). */
  series_id: string;
  product_label: string;
  following_schedule?: boolean;
  /** The run's fuels, in tab order (this one included). */
  products: FuelForecastProduct[];
  /** ISO date of the adjustment period, "2026-07-09". */
  period_date: string;
  /** "dự kiến 15:00, theo nhịp thường lệ"; empty when not known. */
  period_note: string;
  /** ISO time the simulation ran. */
  run_at: string;
  model: { version_label: string; simulations: number };
  /** "không dùng Platts: …"; empty when not known. */
  source_note: string;
  /** The figures are illustrative (demo data); the screen says so in the title badge. */
  illustrative: boolean;
  unit_label: string;
  /** The previous period's ceiling: known once the forecast is issued to the ledger. */
  previous: { period_date: string | null; ceiling: number | null; source_text: string };
  distribution: FuelPriceDistribution;
  /** Expected change from the previous ceiling, by formula component. */
  decomposition: {
    steps: { label: string; value: number }[];
    /** The expected ceiling the steps add up to. */
    expected: number;
    /** The value axis starts here so small steps stay visible ("trục cắt từ 19.680"). */
    axis_from: number;
    source_note: string;
  };
  fund_levels: FuelFundLevel[];
  parameters: FuelFormulaParameter[];
  /** Days of the averaging window already known vs simulated; null when not known. */
  world_price: { known_days: number; total_days: number; simulated_range_text: string } | null;
  /** The Fund scenario the screen ran; readers leave it null. */
  fund_scenario: FuelFundScenario | null;
}

// Composite ids are used as route params by the list screen, so keep the separator inside the
// same safe alphabet as UUIDs/business codes accepted by pathId().
const FUEL_SEP = '__';
const fuelId = (jobId: string, product: string) => `${jobId}${FUEL_SEP}${product}`;
const splitFuelId = (id: string) => {
  const at = id.lastIndexOf(FUEL_SEP);
  return at < 0 ? [id, ''] : [id.slice(0, at), id.slice(at + 1)];
};

type DistributionItem = Schemas['DistributionItem'];
type FormulaModelWire = Schemas['FormulaModelView'];

const PRODUCT_LABEL: Record<string, string> = {
  xang: 'E5RON92',
  e5ron92: 'E5RON92',
  ron95: 'RON95-III',
  ron95iii: 'RON95-III',
  'ron95-iii': 'RON95-III',
  do: 'Diesel 0,05S',
  do005s: 'Diesel 0,05S',
  'diesel-005s': 'Diesel 0,05S',
  dau_hoa: 'Dầu hoả',
  'dau-hoa': 'Dầu hoả',
  mazut: 'Mazut',
  mazut180: 'Mazut',
};
const productLabel = (code: string) => PRODUCT_LABEL[code] ?? (code.toUpperCase() || 'Mô hình');

// The ceiling prices are per litre, mazut's per kilogram; the api's scope carries no unit.
const unitOf = (product: string) => (product.startsWith('mazut') ? 'đ/kg' : product ? 'đ/lít' : '');

/**
 * contributions.histogram, 50 bins of the simulated ceiling: [{price, density}], [{lo, hi,
 * count}] or {edges, counts}. Densities are scaled to the tallest bin.
 */
function densityOf(histogram: unknown): DensityPoint[] {
  let points: DensityPoint[] = [];
  if (Array.isArray(histogram)) {
    points = histogram.flatMap((raw) => {
      const h = record(raw);
      const lo = num(h.lo as number | undefined);
      const hi = num(h.hi as number | undefined);
      const price = num((h.price ?? h.x ?? h.mid) as number | undefined);
      const at = price ?? (lo !== null && hi !== null ? (lo + hi) / 2 : null);
      const weight = num((h.density ?? h.p ?? h.count) as number | undefined);
      return at === null || weight === null ? [] : [{ price: at, density: weight }];
    });
  } else {
    const h = record(histogram);
    const edges = Array.isArray(h.edges) ? h.edges.map(Number) : [];
    const counts = Array.isArray(h.counts) ? h.counts.map(Number) : [];
    points = counts.flatMap((count, i) =>
      edges[i + 1] === undefined ? [] : [{ price: (edges[i] + edges[i + 1]) / 2, density: count }],
    );
  }
  const top = Math.max(...points.map((p) => p.density), 0);
  return top > 0 ? points.map((p) => ({ ...p, density: p.density / top })) : [];
}

/** fund_probs: {contribute: {"200": 0.7}, use: {"300": 0.08}} or a flat {"-500": 0.2}. */
function fundLevelsOf(raw: unknown, unit: string): FuelFundLevel[] {
  const probs = record(raw);
  const level = (value: number, probability: number): FuelFundLevel => ({
    label: `${value < 0 ? 'Chi' : 'Trích'} ${formatWhole(Math.abs(value))} ${unit}`.trim(),
    probability,
    value,
  });
  const entries = (side: unknown, sign: number) =>
    Object.entries(record(side)).flatMap(([amount, p]) =>
      Number.isFinite(Number(amount)) && Number.isFinite(Number(p))
        ? [level(sign * Number(amount), Number(p))]
        : [],
    );
  if ('contribute' in probs || 'use' in probs) {
    // "Chi 0" is the same as "Trích 0": keep the one the contribution side states.
    const uses = entries(probs.use, -1).filter((l) => l.value !== 0);
    return [...entries(probs.contribute, 1), ...uses];
  }
  return entries(probs, 1);
}

const COMPONENT_STEP: [string, string][] = [
  ['world', 'Thế giới'],
  ['fx', 'Tỷ giá'],
  ['tax', 'Thuế phí'],
  ['fund', 'Quỹ'],
  ['proxy_error', 'Sai số proxy'],
];

/** contributions: {world, fx, tax, fund, proxy_error}, each {vnd_per_litre, source_refs}. */
function decompositionOf(raw: unknown, previous: number | null, point: number) {
  const parts = record(raw);
  const sources = new Set<string>();
  const steps = COMPONENT_STEP.flatMap(([key, label]) => {
    const part = parts[key];
    const value =
      typeof part === 'number' ? part : num(record(part).vnd_per_litre as number | undefined);
    for (const ref of Array.isArray(record(part).source_refs)
      ? (record(part).source_refs as unknown[])
      : []) {
      const text = typeof ref === 'string' ? ref : record(ref).label;
      if (typeof text === 'string') sources.add(text);
    }
    return value === null || Number.isNaN(value) ? [] : [{ label, value }];
  });
  const start = previous ?? point;
  const expected = previous === null ? point : steps.reduce((sum, s) => sum + s.value, previous);
  // The axis is cut just below the lowest running total so small steps stay visible.
  let running = start;
  let low = Math.min(start, expected);
  for (const s of steps) {
    running += s.value;
    low = Math.min(low, running);
  }
  return {
    steps,
    expected,
    axis_from: Math.floor((low - 50) / 10) * 10,
    source_note: sources.size ? `Nguồn: ${[...sources].join(' · ')}.` : '',
  };
}

const VN_VALUE = new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 4 });

function parameterOf(c: Schemas['ComponentView']): FuelFormulaParameter {
  const value = Number(c.value);
  const shown = Number.isFinite(value) ? VN_VALUE.format(value) : String(c.value);
  return {
    key: c.code,
    value_text: c.unit === '%' ? `${shown}%` : `${shown} ${c.unit}`.trim(),
    effective_from: c.valid_from.slice(0, 10),
    legal_ref: { label: c.legal_doc_number, document_id: c.document_id },
    certified_by_name: '',
    certified: c.certified_at !== null,
  };
}

/** The formula model of one product of a run: progress names them in scope.product_codes order. */
async function formulaOf(job: DetailWire, product: string): Promise<FormulaModelWire> {
  const ids = record(job.progress).formula_model_ids;
  const codes = record(job.scope).product_codes;
  if (Array.isArray(ids) && Array.isArray(codes)) {
    const id = ids[codes.indexOf(product)];
    if (typeof id === 'string') return v1Get<FormulaModelWire>(`/formula-models/${pathId(id)}`);
  }
  const page = await v1Get<Page<FormulaModelWire>>('/formula-models', {
    product_code: product,
    page_size: 1,
  });
  if (!page.items[0]) throw new Error(`Không có mô hình công thức cho ${productLabel(product)}.`);
  return page.items[0];
}

function readFuel(
  job: DetailWire,
  item: DistributionItem,
  all: DistributionItem[],
  formula: FormulaModelWire,
  version: VersionView | null,
  components: Schemas['ComponentView'][],
): FuelPriceForecast {
  const product = item.product_code;
  const unit = unitOf(product);
  const point = Number(item.point);
  const previous = num(item.baseline_value);
  const q = record(item.quantiles);
  const at = (key: string, fallback: number | string) => Number(q[key] ?? fallback);
  return {
    id: fuelId(job.id, product),
    job_id: job.id,
    product_code: product,
    formula_model_id: formula.id,
    model_id: formula.model_id,
    series_id: ledgerSeriesId(formula.model_id, product),
    product_label: productLabel(product),
    products: [...new Set(all.map((i) => i.product_code))].map((code) => ({
      forecast_id: fuelId(job.id, code),
      label: productLabel(code),
    })),
    period_date: item.period,
    period_note: '',
    run_at: job.finished_at ?? job.created_at,
    model: {
      version_label: version ? `${version.algorithm} v${version.version}` : 'formula_mc',
      simulations: formula.n_draws,
    },
    source_note: '',
    illustrative: false,
    unit_label: unit,
    previous: { period_date: null, ceiling: previous, source_text: '' },
    distribution: {
      median: at('p50', item.point),
      lo80: at('p10', item.lo80),
      hi80: at('p90', item.hi80),
      lo95: at('p02_5', item.lo95),
      hi95: at('p97_5', item.hi95),
      p_increase: num(item.prob_up),
      density: densityOf(item.histogram),
    },
    decomposition: decompositionOf(item.contributions, previous, point),
    fund_levels: fundLevelsOf(item.fund_probs, unit),
    parameters: components.map(parameterOf),
    world_price: null,
    fund_scenario: null,
    ...job.display?.fuel?.[product],
  };
}

/**
 * The fuels with a formula model, each opened on its latest issued run. A2's formula models do
 * not name their latest run, so it is read from the ledger (the newest line of that product).
 */
export async function listFuelPriceForecasts(): Promise<Page<{ id: string; label: string }>> {
  const formulas = await v1Get<Page<FormulaModelWire>>('/formula-models', { page_size: 20 }).catch(
    () => null,
  );
  if (!formulas)
    return v1Get<Page<FuelPriceForecast>>('/fuel-price-forecasts', { page_size: 20 }).then(
      (page) => ({
        items: page.items.map((item) => ({ id: item.id, label: item.product_label })),
        next_page_token: page.next_page_token,
      }),
    );
  if (formulas.items.length === 0) return { items: [], next_page_token: '' };
  const products = new Set(formulas.items.map((f) => f.product_code));
  const ledger = await v1Get<Page<Schemas['LedgerEntryView']>>('/forecast-ledger', {
    page_size: 50,
  });
  const seen = new Map<string, string>();
  for (const line of ledger.items) {
    const product = productOf(line);
    if (line.job_id && products.has(product) && !seen.has(product))
      seen.set(product, fuelId(line.job_id, product));
  }
  return {
    items: [...seen].map(([product, id]) => ({ id, label: productLabel(product) })),
    next_page_token: '',
  };
}

/**
 * A2: GET /v1/forecasts/{id} and its /distribution (SCR-70), the product's formula model, its
 * model version and the parameters in force at the period (BR-182).
 */
export async function getFuelPriceForecast(id: string): Promise<FuelPriceForecast> {
  const legacy = await v1Get<FuelPriceForecast>(`/fuel-price-forecasts/${pathId(id)}`).catch(
    () => null,
  );
  if (legacy) return legacy;
  const [jobId, product] = splitFuelId(id);
  const [job, distribution] = await Promise.all([
    getDetail(jobId),
    v1Get<Schemas['DistributionView']>(`/forecasts/${pathId(jobId)}/distribution`),
  ]);
  const item = distribution.items.find((i) => i.product_code === product) ?? distribution.items[0];
  if (!item) throw new Error('Lượt chạy này chưa có phân phối giá.');
  const formula = await formulaOf(job, item.product_code);
  const [version, components] = await Promise.all([
    getVersion(item.model_version_id).catch(() => null),
    v1Get<Page<Schemas['ComponentView']>>(`/formula-models/${pathId(formula.id)}/components`, {
      at: `${item.period}T00:00:00Z`,
      page_size: 50,
    }),
  ]);
  return readFuel(job, item, distribution.items, formula, version, components.items);
}

/**
 * A2: POST /v1/formula-models/{id}/runs (202) — a new run of the period with the latest known
 * world prices, for every fuel of this run; not issued, the bulletin issues its own.
 */
export const rerunFuelPriceForecast = async (f: FuelPriceForecast) =>
  v1Send<Schemas['ForecastAccepted']>(
    'POST',
    `/formula-models/${pathId(f.formula_model_id)}/runs`,
    {
      period: f.period_date,
      issue: 'none',
      product_codes: f.products.map((p) => splitFuelId(p.forecast_id)[1]),
    } satisfies Schemas['FormulaRunCreate'],
  );

/** The Fund as a scenario variable of the formula's model version. */
const FUND_FEATURE = 'fund';

/**
 * The distribution conditional on one Fund level, never a forecast of the Fund decision (BR-184):
 * a scenario of the forecast model with the Fund fixed, read back by scenario_id.
 */
export async function runFundScenario(
  f: FuelPriceForecast,
  level: FuelFundLevel,
): Promise<FuelFundScenario | null> {
  const accepted = await v1Send<Schemas['ScenarioAccepted']>(
    'POST',
    `/forecast-models/${pathId(f.model_id)}/scenarios`,
    {
      // A scenario's name is unique per model and author: the time keeps reruns apart.
      name: `Quỹ ${level.label} · kỳ ${f.period_date} · ${new Date().toISOString()}`,
      assumptions: [
        {
          feature: FUND_FEATURE,
          change_kind: 'cố định',
          value: level.value,
          from_period: f.period_date,
        },
      ],
    } satisfies Schemas['ScenarioCreate'],
  );
  for (let i = 0; i < SCENARIO_POLLS; i++) {
    await wait(Math.min(accepted.poll_after_ms, MAX_POLL_MS));
    const page = await v1Get<Schemas['PredictionPage']>(
      `/forecast-models/${pathId(f.model_id)}/predictions`,
      { scenario_id: accepted.id, from: f.period_date, to: f.period_date },
    );
    const p = page.items[0];
    if (p)
      return {
        fund_level_label: level.label,
        median: Number(p.point),
        lo80: Number(p.lo80),
        hi80: Number(p.hi80),
        lo95: Number(p.lo95),
        hi95: Number(p.hi95),
      };
  }
  return null;
}

/* Sổ dự báo (UC-15-08) */

export interface ForecastLedgerRow {
  /** ISO date of the period. */
  period_date: string;
  /** ISO time it was published, always before the period. */
  published_at: string;
  /** "bản tin" — the channel it was published through. */
  channel_label: string;
  median: number;
  lo80: number;
  hi80: number;
  lo95: number;
  hi95: number;
  /** Null until the ministry publishes the price. */
  actual: number | null;
  /** actual − median; null while unscored. */
  error: number | null;
  inside80: boolean | null;
  inside95: boolean | null;
  /** Outside 95% or error above the miss threshold. */
  missed: boolean;
  /** "Giá thế giới khác dự kiến"; null when nothing stands out. */
  main_driver: string | null;
  /** The Error Book entry a missed period opened. */
  error_book_ref: string | null;
}

export interface ForecastLedger {
  series_id: string;
  product_label: string;
  /** The other fuels' ledgers. */
  products: { series_id: string; label: string }[];
  model_version_label: string;
  illustrative: boolean;
  unit_label: string;
  /** Calibration over a fixed N set by the model, whatever the window shows. */
  calibration: {
    periods: number;
    inside80: number;
    inside95: number;
    /** Mean absolute error in the unit. */
    mae: number;
    /** CRPS relative to the naive "keep last price" forecast; −0.38 = 38% better. */
    crps_vs_naive: number;
    missed: number;
    /** "ngoài 95% hoặc sai > 300 đ/lít"; empty when the rule is not served. */
    miss_rule_text: string;
  };
  /** Windows the table can show: "20 kỳ gần nhất". */
  windows: { periods: number; label: string }[];
  window_periods: number;
  /** Newest first. */
  rows: ForecastLedgerRow[];
  /**
   * The ledger has no schedule to follow: null. Following is the subscription to the external
   * calendar of the series' model (getScheduleCalendar).
   */
  following_schedule: boolean | null;
}

/*
 * A2's ledger is GET /v1/forecast-ledger, one line per issued forecast, filtered by model and
 * scope.product_code: a series here is that pair, its id `ledger~<model>~<product>`.
 */
const LEDGER = 'ledger~';
type LedgerLine = Schemas['LedgerEntryView'];
type LedgerPage = Page<LedgerLine> & { calibration?: Schemas['Calibration'] | null };

const productOf = (line: LedgerLine) => {
  const scope = (line.scope ?? {}) as Record<string, unknown>;
  return typeof scope.product_code === 'string' ? scope.product_code : '';
};
const ledgerSeriesId = (modelId: string, product: string) => `${LEDGER}${modelId}~${product}`;
const splitSeriesId = (seriesId: string) => {
  const [modelId = '', product = ''] = seriesId.slice(LEDGER.length).split('~');
  return { modelId, product };
};

/**
 * The fuel series of the ledger: one per model and product. Lines without a product code are
 * forecasts answered to a question; the ledger screen is the fuel prices' (UC-15-08).
 */
const seriesOf = (lines: LedgerLine[]) => {
  const seen = new Map<string, { id: string; label: string; model: string }>();
  for (const line of lines) {
    const product = productOf(line);
    const id = ledgerSeriesId(line.forecast_model_id, product);
    if (product && !seen.has(id))
      seen.set(id, { id, label: productLabel(product), model: line.forecast_model_id });
  }
  return [...seen.values()];
};

/** The series with issued lines; until there are any, one per formula model and product. */
export const listForecastSeries = async (): Promise<Page<{ id: string; label: string }>> => {
  const lines = await v1Get<LedgerPage>('/forecast-ledger', { page_size: 500 });
  const series = seriesOf(lines.items);
  if (series.length)
    return { items: series.map(({ id, label }) => ({ id, label })), next_page_token: '' };
  const formulas = await v1Get<Page<FormulaModelWire>>('/formula-models', { page_size: 20 }).catch(
    () => null,
  );
  if (!formulas)
    return v1Get<Page<{ id: string; label: string }>>('/forecast-series', { page_size: 500 });
  return {
    items: formulas.items.map((f) => ({
      id: ledgerSeriesId(f.model_id, f.product_code),
      label: productLabel(f.product_code),
    })),
    next_page_token: '',
  };
};

const ISSUED_VIA: Record<string, string> = {
  bulletin: 'bản tin',
  answer: 'câu trả lời',
  monitor: 'theo dõi',
  manual: 'thủ công',
};
const COMPONENT: Record<string, string> = {
  fund: 'Quỹ bình ổn khác dự kiến',
  world_price: 'Giá thế giới khác dự kiến',
  world: 'Giá thế giới khác dự kiến',
  fx: 'Tỷ giá khác dự kiến',
  tax: 'Thuế, phí khác dự kiến',
};

function readLedgerRow(line: LedgerLine): ForecastLedgerRow {
  const q = (line.quantiles ?? {}) as Record<string, number | string>;
  const median = Number(line.point);
  const at = (key: string) => (q[key] === undefined ? median : Number(q[key]));
  const actual = num(line.actual);
  const attribution = (line.miss_attribution ?? null) as { component?: string } | null;
  return {
    period_date: line.period,
    published_at: line.issued_at,
    channel_label: ISSUED_VIA[line.issued_via] ?? line.issued_via,
    median,
    lo80: at('p10'),
    hi80: at('p90'),
    lo95: at('p02_5'),
    hi95: at('p97_5'),
    actual,
    error: actual === null ? null : actual - median,
    inside80: line.in80,
    inside95: line.in95,
    missed: line.miss === true,
    main_driver: attribution?.component
      ? (COMPONENT[attribution.component] ?? attribution.component)
      : null,
    error_book_ref: line.error_book_entry_id,
  };
}

const WINDOWS = [
  { periods: 20, label: '20 kỳ gần nhất' },
  { periods: 10, label: '10 kỳ gần nhất' },
];

/*
 * One series of A2's ledger. The calibration comes with the page (BR-185: the model's last N
 * scored periods); coverage is a share of the scored ones, so the counts are read back from it.
 * The rule a miss is judged by is the formula model's and is not on the ledger, nor is the
 * version label; the version shown is the model's current one.
 */
async function servedLedger(seriesId: string, periods: number): Promise<ForecastLedger> {
  const { modelId, product } = splitSeriesId(seriesId);
  const [page, all, model] = await Promise.all([
    v1Get<LedgerPage>('/forecast-ledger', {
      forecast_model_id: modelId,
      ...(product ? { product_code: product } : {}),
      page_size: periods,
    }),
    // The other fuels: a formula model per product may sit on its own forecast model.
    v1Get<LedgerPage>('/forecast-ledger', { page_size: 500 }),
    v1Get<Schemas['ForecastModelDetail']>(`/forecast-models/${pathId(modelId)}`),
  ]);
  const c = page.calibration;
  const n = c?.n_scored ?? 0;
  const share = (v: number | string | null | undefined) => Math.round((num(v) ?? 0) * n);
  const crps = num(c?.crps);
  const naive = num(c?.crps_naive);
  return {
    series_id: seriesId,
    product_label: productLabel(product),
    products: seriesOf(all.items).map((s) => ({ series_id: s.id, label: s.label })),
    model_version_label: model.current_version
      ? `${model.metric_code} v${model.current_version.version}`
      : model.metric_code,
    illustrative: false,
    unit_label: unitOf(product),
    calibration: {
      periods: n,
      inside80: share(c?.coverage80),
      inside95: share(c?.coverage95),
      mae: num(c?.mae) ?? 0,
      crps_vs_naive: crps !== null && naive ? crps / naive - 1 : 0,
      missed: c?.miss_count ?? 0,
      miss_rule_text: c?.insufficient ? 'chưa đủ 10 kỳ đã chấm để kết luận' : '',
    },
    windows: WINDOWS,
    window_periods: periods,
    rows: page.items.map(readLedgerRow),
    following_schedule: null,
  };
}

/** The newest `periods` rows of the ledger; the calibration always covers the model's fixed N. */
export const getForecastLedger = async (seriesId: string, periods?: number) => {
  try {
    return await servedLedger(seriesId, periods ?? 20);
  } catch {
    return v1Get<ForecastLedger>(`/forecast-series/${pathId(seriesId)}/ledger`, {
      periods: periods ?? 20,
    });
  }
};

/* Theo dõi lịch: the pre- and post-period bulletins of a series (UC-08-08) */

export interface ScheduleCalendar {
  id: string;
  name: string;
  /** The api's calendar does not say whether the caller is subscribed: false until toggled. */
  following: boolean;
}

/** The external calendar whose events the series' model forecasts, if any. */
export async function getScheduleCalendar(seriesId: string): Promise<ScheduleCalendar | null> {
  const { modelId } = splitSeriesId(seriesId);
  if (!modelId) return null;
  const page = await v1Get<Page<Schemas['CalendarSummary']>>('/external-calendars', {
    status: 'active',
    page_size: 50,
  });
  const calendar = page.items.find((c) => c.forecast_model_ids.includes(modelId));
  return calendar ? { id: calendar.id, name: calendar.name, following: false } : null;
}

/** A2: PUT /v1/external-calendars/{id}/subscription — both bulletins in the app, or paused. */
export async function followForecastSchedule(calendarId: string, follow: boolean) {
  const subscription = await v1Send<Schemas['SubscriptionView']>(
    'PUT',
    `/external-calendars/${pathId(calendarId)}/subscription`,
    {
      kinds: ['pre', 'post'],
      channels: ['in_app'],
      status: follow ? 'active' : 'paused',
    } satisfies Schemas['SubscriptionPut'],
  );
  return { following: subscription.status === 'active' };
}

/* ─── Formatting ──────────────────────────────────────────────────────── */

const VN1 = new Intl.NumberFormat('vi-VN', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const VN2 = new Intl.NumberFormat('vi-VN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** 428.6 → "428,6"; negatives with a real minus sign, as the Figma tables write them. */
export const formatOne = (value: number) => VN1.format(value).replace('-', '−');

export const formatTwo = (value: number) => VN2.format(value).replace('-', '−');

/** +1,8 / −2,4 / 0,0 */
export const formatSigned = (value: number) =>
  value > 0 ? `+${formatOne(value)}` : formatOne(value);

const VN0 = new Intl.NumberFormat('vi-VN', { maximumFractionDigits: 0 });

/** 19730 → "19.730"; −120 → "−120". */
export const formatWhole = (value: number) => VN0.format(Math.round(value)).replace('-', '−');

/** +180 / −24 / 0 */
export const formatWholeSigned = (value: number) =>
  value > 0 ? `+${formatWhole(value)}` : formatWhole(value);

/** 0.72 → "72%" */
export const formatPercent = (share: number) => `${Math.round(share * 100)}%`;
