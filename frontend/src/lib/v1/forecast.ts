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

/* ─── Giá xăng dầu: phân phối, phân rã và sổ dự báo (UC-15-07, UC-15-08) ─ */

/*
 * The retail ceiling price of one fuel for the next adjustment period, computed by the price
 * formula (world price, exchange rate, taxes and fees, the stabilisation Fund) under Monte Carlo
 * simulation: a distribution, never a single number (BR-183). The Fund decision belongs to the
 * ministries, so the model only estimates a probability per Fund level (BR-184). Every published
 * forecast is written to the ledger before its period, is never edited, and is scored when the
 * Ministry of Industry and Trade publishes the price; missed periods stay visible (BR-185).
 * PROPOSAL: no fuel forecast resource exists in the spec yet.
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
  /** P(ceiling above the previous period), 0–1. */
  p_increase: number;
  density: DensityPoint[];
}

export interface FuelFundLevel {
  /** "Trích 200 đ/lít" */
  label: string;
  /** 0–1 */
  probability: number;
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
  certified_by_name: string;
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
  /** The ledger series of this fuel (UC-15-08). */
  series_id: string;
  product_label: string;
  /** The run's other fuels, in tab order (this one included). */
  products: FuelForecastProduct[];
  /** ISO date of the adjustment period, "2026-07-09". */
  period_date: string;
  /** "dự kiến 15:00, theo nhịp thường lệ" */
  period_note: string;
  /** ISO time the simulation ran. */
  run_at: string;
  model: { version_label: string; simulations: number };
  /** "không dùng Platts: số bình quân Bộ Công Thương công bố và chuỗi giá công khai" */
  source_note: string;
  /** The figures are illustrative (demo data); the screen says so in the title badge. */
  illustrative: boolean;
  unit_label: string;
  previous: { period_date: string; ceiling: number; source_text: string };
  distribution: FuelPriceDistribution;
  /** Expected change from the previous ceiling, by formula component. */
  decomposition: {
    steps: { label: string; value: number }[];
    /** The expected (mean) ceiling the steps add up to. */
    expected: number;
    /** The value axis starts here so small steps stay visible ("trục cắt từ 19.680"). */
    axis_from: number;
    source_note: string;
  };
  fund_levels: FuelFundLevel[];
  parameters: FuelFormulaParameter[];
  /** Days of the averaging window already known vs simulated. */
  world_price: { known_days: number; total_days: number; simulated_range_text: string };
  fund_scenario: FuelFundScenario | null;
  /** Whether the viewer follows the publication schedule of this fuel. */
  following_schedule: boolean;
}

export const listFuelPriceForecasts = () =>
  v1Get<Page<FuelPriceForecast>>('/fuel-price-forecasts', { page_size: 20 });

export const getFuelPriceForecast = async (id: string) =>
  v1Get<FuelPriceForecast>(`/fuel-price-forecasts/${pathId(id)}`);

/** Re-runs the simulation with the latest known world prices; returns the new run. */
export const rerunFuelPriceForecast = async (id: string) =>
  v1Send<FuelPriceForecast>('POST', `/fuel-price-forecasts/${pathId(id)}:rerun`);

/** The distribution conditional on one Fund level, never a forecast of the Fund decision. */
export const runFundScenario = async (id: string, fundLevel: string) =>
  v1Send<FuelPriceForecast>('POST', `/fuel-price-forecasts/${pathId(id)}:run-fund-scenario`, {
    fund_level: fundLevel,
  });

export const clearFundScenario = async (id: string) =>
  v1Send<FuelPriceForecast>('POST', `/fuel-price-forecasts/${pathId(id)}:clear-fund-scenario`);

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
  /** "Giá thế giới thấp hơn dự kiến"; null when nothing stands out. */
  main_driver: string | null;
  /** The Error Book entry a missed period opened, "EB-0031". */
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
    /** "ngoài 95% hoặc sai > 300 đ/lít" */
    miss_rule_text: string;
  };
  /** Windows the table can show: "20 kỳ gần nhất". */
  windows: { periods: number; label: string }[];
  window_periods: number;
  /** Newest first. */
  rows: ForecastLedgerRow[];
  following_schedule: boolean;
}

export const listForecastSeries = () =>
  v1Get<Page<{ id: string; label: string }>>('/forecast-series', { page_size: 20 });

/** The newest `periods` rows of the ledger; the calibration always covers the model's fixed N. */
export const getForecastLedger = async (seriesId: string, periods?: number) =>
  v1Get<ForecastLedger>(`/forecast-series/${pathId(seriesId)}/ledger`, { periods });

/** Subscribe to (or leave) the pre- and post-period bulletins of one fuel. */
export const followForecastSchedule = async (seriesId: string, follow: boolean) =>
  v1Send<{ following: boolean }>(
    'POST',
    `/forecast-series/${pathId(seriesId)}:${follow ? 'follow' : 'unfollow'}`,
  );

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
