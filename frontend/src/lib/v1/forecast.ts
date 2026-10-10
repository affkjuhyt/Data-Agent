import { pathId, v1Get, v1Send, type Page } from './client';

/*
 * P15 Dự báo (UC-15-01…06): a forecast question answered asynchronously, the forecast with its
 * intervals and scenarios, the studio where the forecasting agent prepares data and backtests,
 * and the model card a metric owner approves and then monitors. The spec has the HITL kinds
 * `forecast_assumption` and `forecast_model` but no forecast resources yet, so every endpoint
 * and field here is a PROPOSAL shaped after the Figma frames (section 2392:14840).
 *
 * Rules the shapes enforce: a forecast point always carries its 80% and 95% interval (a line
 * without its interval cannot be expressed), every forecast names its model version and data
 * cut-off (BR-157), nothing is answered before backtest (BR-159), the creator never approves
 * their own model (BR-158), and a degraded model labels its forecasts (BR-160).
 */

/* ─── Việc dự báo: the in-progress answer (UC-15-01) ──────────────────── */

export type ForecastJobStatus = 'waiting_human' | 'running' | 'done' | 'failed';

export interface ForecastJobStep {
  title: string;
  /** "query.semantic · cắt tại 30/09/2026" */
  detail: string;
  state: 'done' | 'running' | 'needs_you' | 'pending';
  /** "10:30"; null while it has not started. */
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
  /** "FJ-2026-1010-03" */
  code: string;
  status: ForecastJobStatus;
  metric_name: string;
  /** The session the question was asked in. */
  session: { id: string; title: string; owner_name: string; question_count: number };
  question: string;
  /** "Ý định: dự báo", "Chỉ số: …", "Tầm: T11/2026 – T1/2027" — the understood intent. */
  intent_chips: string[];
  /** The agent's answer so far: why it is asynchronous and when it should finish. */
  message: string;
  steps: ForecastJobStep[];
  /** An older forecast usable for the trend, with the label it must carry. */
  stale_forecast: { id: string; version_label: string; note: string } | null;
  assumption_request: ForecastAssumptionRequest | null;
  /** Set once the job is done: the forecast it produced. */
  forecast_id: string | null;
  /** The model card this job's backtest became, once created in the studio. */
  model_id: string | null;
}

export const getForecastJob = async (id: string) =>
  v1Get<ForecastJob>(`/forecast-jobs/${pathId(id)}`);

/* ─── Xưởng dự báo (UC-15-02, UC-15-03) ───────────────────────────────── */

export interface ForecastStat {
  label: string;
  value: string;
  note: string;
  tone: 'success' | 'warning' | 'error' | 'neutral';
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
  mase: number;
  smape_text: string;
  cover80_text: string;
  cover95_text: string;
  role: 'baseline' | 'candidate' | 'selected';
}

export interface ForecastWorkbench {
  job_id: string;
  job_code: string;
  metric_name: string;
  /** "Chuỗi tháng · cắt tại 30/09/2026 · tầm 3 tháng · Agent Dự báo đã chạy 6 mô hình…" */
  summary_text: string;
  series_stats: ForecastStat[];
  variables: ForecastVariable[];
  variables_note: string;
  backtest: BacktestRow[];
  backtest_note: string;
  /** "v7": the version a model card would get. */
  next_version_label: string;
  /** No model beat seasonal naive: no card, nothing to publish (UC-15-03). */
  can_create_card: { allowed: boolean; reason: string | null };
  /** Set once the card exists. */
  model_id: string | null;
}

export const getForecastWorkbench = async (jobId: string) =>
  v1Get<ForecastWorkbench>(`/forecast-jobs/${pathId(jobId)}/workbench`);

/** Re-runs the rolling backtest (202); the workbench reloads with the new table. */
export const rerunBacktest = async (jobId: string) =>
  v1Send<ForecastWorkbench>('POST', `/forecast-jobs/${pathId(jobId)}:rerun-backtest`);

/** Turns the selected model into a draft model version with its card. */
export const createModelCard = async (jobId: string) =>
  v1Send<{ model_id: string; version_id: string }>(
    'POST',
    `/forecast-jobs/${pathId(jobId)}:create-model-card`,
  );

/* ─── Kết quả dự báo và kịch bản (UC-15-01, UC-15-05) ─────────────────── */

export interface ForecastPoint {
  /** "2026-11" */
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
  /** "Lãi suất điều hành +0,5 điểm từ T12/2026" */
  text: string;
  saved: boolean;
  /** The conditional, non-causal reading the screen must show next to it. */
  disclaimer: string;
  summary: string;
  points: { period: string; value: number }[];
  /** Legend label, "Kịch bản +0,5 điểm". */
  short_label: string;
}

export interface Forecast {
  id: string;
  metric_name: string;
  /** "T11/2026 – T1/2027" */
  horizon_label: string;
  /** "nghìn tỷ đồng" */
  unit_label: string;
  model: {
    id: string;
    version_label: string;
    algorithm_label: string;
    mase: number;
    status: 'published' | 'degraded' | 'draft' | 'retired';
  };
  /** Point-in-time cut-off of the training data (BR-157), ISO date. */
  data_cutoff: string;
  created_at: string;
  /** approved: from a published model; stale: an older forecast kept for the trend. */
  label: 'approved' | 'stale' | 'experimental';
  approved_by_name: string | null;
  /** "dự báo cũ, dữ liệu đến 31/08, mô hình suy giảm" when label is stale. */
  stale_note: string | null;
  history: HistoryPoint[];
  points: ForecastPoint[];
  /** "Khoảng đã gồm sai số của dự báo lãi suất huy động 12 tháng thị trường." */
  interval_note: string;
  contributions: { title: string; items: { label: string; value: number }[] };
  assumptions_text: string;
  /** Variables a scenario may move; any other gets "kịch bản không áp dụng". */
  scenario_variables: string[];
  scenario: ForecastScenario | null;
  /** The session it was asked from, for "Thêm vào phiên". */
  session_id: string | null;
  added_to_session: boolean;
}

export const listForecasts = () => v1Get<Page<Forecast>>('/forecasts', { page_size: 50 });

export const getForecast = async (id: string) => v1Get<Forecast>(`/forecasts/${pathId(id)}`);

/** Recomputes with one exogenous assumption changed; returns the forecast with `scenario` set. */
export const runScenario = async (id: string, text: string) =>
  v1Send<Forecast>('POST', `/forecasts/${pathId(id)}:run-scenario`, { text });

export const saveScenario = async (id: string) =>
  v1Send<Forecast>('POST', `/forecasts/${pathId(id)}:save-scenario`);

export const clearScenario = async (id: string) =>
  v1Send<Forecast>('POST', `/forecasts/${pathId(id)}:clear-scenario`);

export const addForecastToSession = async (id: string) =>
  v1Send<Forecast>('POST', `/forecasts/${pathId(id)}:add-to-session`);

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

export const listForecastModels = () =>
  v1Get<Page<ForecastModel>>('/forecast-models', { page_size: 50 });

export const getForecastModel = async (id: string) =>
  v1Get<ForecastModel>(`/forecast-models/${pathId(id)}`);

/** Publishes the candidate; the published version before it is retired. */
export const approveModelVersion = async (versionId: string) =>
  v1Send<ForecastModel>('POST', `/forecast-model-versions/${pathId(versionId)}:approve`);

export const rejectModelVersion = async (versionId: string, reason: string) =>
  v1Send<ForecastModel>('POST', `/forecast-model-versions/${pathId(versionId)}:reject`, {
    reason,
  });

/* ─── Formatting ──────────────────────────────────────────────────────── */

const VN1 = new Intl.NumberFormat('vi-VN', { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const VN2 = new Intl.NumberFormat('vi-VN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** 428.6 → "428,6"; negatives with a real minus sign, as the Figma tables write them. */
export const formatOne = (value: number) => VN1.format(value).replace('-', '−');

export const formatTwo = (value: number) => VN2.format(value).replace('-', '−');

/** +1,8 / −2,4 / 0,0 */
export const formatSigned = (value: number) =>
  value > 0 ? `+${formatOne(value)}` : formatOne(value);
