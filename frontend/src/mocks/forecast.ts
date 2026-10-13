import { http, HttpResponse } from 'msw';
import type {
  Forecast,
  ForecastJob,
  ForecastLedger,
  ForecastLedgerRow,
  ForecastModel,
  ForecastWorkbench,
  FuelPriceForecast,
  HistoryPoint,
} from '../lib/v1/forecast';

/*
 * P15 Dự báo (section 2392:14840): one forecast question about retail deposits. The job waits on
 * an assumption for the policy rate (answered through the ordinary HITL :resolve, handled here
 * for this mock's request only), then runs backtest and finishes after two polls. The forecast,
 * the studio's workbench and the model card (v7 waiting for review, v6 degrading) are the
 * figures of the Figma frames.
 */
const JOB_ID = 'fj-2026-1010-03';
const HITL_ID = 'hitl-forecast-policy-rate';
const FORECAST_ID = 'fc-deposit-2026-11';
const STALE_ID = 'fc-deposit-2026-09-v6';
const MODEL_ID = 'fm-deposit-retail';
const V7_ID = 'fmv-deposit-retail-v7';
/** Polls of a running job before it finishes. */
const POLLS_TO_FINISH = 2;

const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    { title: code, status, code, detail },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

const MONTHS: [string, string, number][] = [
  ['2024-10', 'T10/2024', 371.2],
  ['2024-11', 'T11/2024', 373.0],
  ['2024-12', 'T12/2024', 369.4],
  ['2025-01', 'T1/2025', 372.1],
  ['2025-02', 'T2/2025', 375.6],
  ['2025-03', 'T3/2025', 378.2],
  ['2025-04', 'T4/2025', 379.9],
  ['2025-05', 'T5/2025', 382.4],
  ['2025-06', 'T6/2025', 383.7],
  ['2025-07', 'T7/2025', 381.9],
  ['2025-08', 'T8/2025', 386.3],
  ['2025-09', 'T9/2025', 389.0],
  ['2025-10', 'T10/2025', 396.0],
  ['2025-11', 'T11/2025', 399.1],
  ['2025-12', 'T12/2025', 401.2],
  ['2026-01', 'T1/2026', 397.0],
  ['2026-02', 'T2/2026', 400.3],
  ['2026-03', 'T3/2026', 404.6],
  ['2026-04', 'T4/2026', 407.9],
  ['2026-05', 'T5/2026', 411.2],
  ['2026-06', 'T6/2026', 414.0],
  ['2026-07', 'T7/2026', 412.3],
  ['2026-08', 'T8/2026', 416.8],
  ['2026-09', 'T9/2026', 420.5],
];

const history = (until: string): HistoryPoint[] =>
  MONTHS.filter(([period]) => period <= until).map(([period, label, actual]) => ({
    period,
    label,
    actual,
  }));

const SCENARIO_DISCLAIMER =
  'Kịch bản là phép tính có điều kiện, không phải ước lượng tác động nhân quả. Nếu lãi suất điều hành tăng 0,5 điểm từ T12/2026 và các giả định khác giữ nguyên, mô hình cho kết quả dưới đây. Mô hình học từ tương quan trong dữ liệu quá khứ, nên chênh lệch này không chứng minh lãi suất điều hành gây ra thay đổi của số dư huy động. Khoảng của kịch bản không gồm độ bất định của giả định.';

function scenario(text: string): Forecast['scenario'] {
  return {
    id: `scenario-${text.length}`,
    text,
    saved: false,
    disclaimer: SCENARIO_DISCLAIMER,
    summary:
      'T1/2027: 435,0 nghìn tỷ, cao hơn kịch bản gốc 3,6 nghìn tỷ (+0,8%). Tác động chủ yếu qua biến lãi suất 12T thị trường với độ trễ 1–2 tháng.',
    short_label: 'Kịch bản +0,5 điểm',
    points: [
      { period: '2026-11', value: 428.6 },
      { period: '2026-12', value: 437.9 },
      { period: '2027-01', value: 435.0 },
    ],
  };
}

const SCENARIO_VARIABLES = ['lãi suất điều hành', 'lãi suất huy động 12t', 'cpi'];

function forecast(): Forecast {
  return {
    id: FORECAST_ID,
    metric_name: 'Số dư huy động bán lẻ',
    frequency: 'month',
    horizon_label: 'T11/2026 – T1/2027',
    unit_label: 'nghìn tỷ đồng',
    model: {
      id: MODEL_ID,
      version_label: 'v7',
      algorithm_label: 'Ensemble',
      mase: 0.64,
      status: 'published',
    },
    data_cutoff: '2026-09-30',
    created_at: '2026-10-10T10:42:00+07:00',
    label: 'approved',
    approved_by_name: 'Đỗ Hải Yến',
    stale_note: null,
    history: history('2026-09'),
    points: [
      {
        period: '2026-11',
        label: 'T11/2026',
        mean: 428.6,
        lo80: 421.9,
        hi80: 435.3,
        lo95: 418.2,
        hi95: 439.0,
      },
      {
        period: '2026-12',
        label: 'T12/2026',
        mean: 436.1,
        lo80: 426.8,
        hi80: 445.4,
        lo95: 421.7,
        hi95: 450.5,
      },
      {
        period: '2027-01',
        label: 'T1/2027',
        mean: 431.4,
        lo80: 419.6,
        hi80: 443.2,
        lo95: 413.3,
        hi95: 449.5,
      },
    ],
    interval_note: 'Khoảng đã gồm sai số của dự báo lãi suất huy động 12 tháng thị trường.',
    contributions: {
      title: 'Đóng góp vào thay đổi T1/2027 so T9/2026',
      items: [
        { label: 'Xu hướng và mùa vụ', value: 5.8 },
        { label: 'Lãi suất 12T thị trường', value: 3.1 },
        { label: 'Lãi suất điều hành', value: 0.9 },
        { label: 'Lịch Tết (rút trước Tết)', value: -2.4 },
      ],
    },
    assumptions_text:
      'Lãi suất điều hành giữ 4,50% (kịch bản gốc) · lãi suất 12T thị trường theo dự báo ETS riêng · CPI theo công bố gần nhất · Tết 17/02/2027.',
    scenario_variables: SCENARIO_VARIABLES,
    scenario: scenario('Lãi suất điều hành +0,5 điểm từ T12/2026'),
    session_id: 'ws-huy-dong-q4',
    added_to_session: false,
  };
}

// The v6 forecast the in-progress answer offers for the trend, labelled as stale.
function staleForecast(): Forecast {
  return {
    ...forecast(),
    id: STALE_ID,
    horizon_label: 'T9/2026 – T11/2026',
    model: {
      id: MODEL_ID,
      version_label: 'v6',
      algorithm_label: 'SARIMAX',
      mase: 0.7,
      status: 'degraded',
    },
    data_cutoff: '2026-08-31',
    created_at: '2026-09-05T07:10:00+07:00',
    label: 'stale',
    approved_by_name: null,
    stale_note: 'dự báo cũ, dữ liệu đến 31/08, mô hình suy giảm',
    history: history('2026-08'),
    points: [
      {
        period: '2026-09',
        label: 'T9/2026',
        mean: 418.9,
        lo80: 413.0,
        hi80: 424.8,
        lo95: 409.6,
        hi95: 428.2,
      },
      {
        period: '2026-10',
        label: 'T10/2026',
        mean: 421.7,
        lo80: 414.2,
        hi80: 429.2,
        lo95: 409.9,
        hi95: 433.5,
      },
      {
        period: '2026-11',
        label: 'T11/2026',
        mean: 424.0,
        lo80: 415.0,
        hi80: 433.0,
        lo95: 409.8,
        hi95: 438.2,
      },
    ],
    interval_note: 'Khoảng theo sai số backtest của v6 tại thời điểm phát hành.',
    contributions: {
      title: 'Đóng góp vào thay đổi T11/2026 so T8/2026',
      items: [
        { label: 'Xu hướng và mùa vụ', value: 5.1 },
        { label: 'Lãi suất 12T thị trường', value: 2.1 },
      ],
    },
    assumptions_text:
      'Lãi suất điều hành giữ 4,50% · lãi suất 12T thị trường theo dự báo ETS riêng.',
    scenario: null,
    session_id: null,
  };
}

function job(): ForecastJob {
  return {
    id: JOB_ID,
    code: 'FJ-2026-1010-03',
    status: 'waiting_human',
    metric_name: 'Số dư huy động bán lẻ',
    session: {
      id: 'ws-huy-dong-q4',
      title: 'Huy động bán lẻ Q4',
      owner_name: 'Đỗ Hải Yến',
      question_count: 2,
    },
    question: 'Dự báo số dư huy động bán lẻ 3 tháng tới, có tính tác động của lãi suất?',
    intent_chips: [
      'Ý định: dự báo',
      'Chỉ số: Số dư huy động bán lẻ',
      'Phạm vi: toàn hàng',
      'Tần suất: tháng',
      'Tầm: T11/2026 – T1/2027',
    ],
    message:
      'Chưa có dự báo còn hiệu lực cho chỉ số này (mô hình v6 đang suy giảm, dữ liệu mới nhất 30/09). Mình đã tạo việc dự báo mới, dự kiến xong lúc 10:42, khoảng 12 phút nữa. Kết quả sẽ hiện ngay trong hội thoại này và gửi thông báo cho bạn.',
    steps: [
      {
        title: 'Lấy chuỗi mục tiêu 69 tháng',
        detail: 'query.semantic · cắt tại 30/09/2026',
        state: 'done',
        at_text: '10:30',
      },
      {
        title: 'Đề xuất 5 biến ngoại sinh, loại 1 biến rò rỉ',
        detail: 'dữ liệu hợp nhất P14 · lịch Tết',
        state: 'done',
        at_text: '10:31',
      },
      {
        title: 'Xác nhận giả định cho lãi suất điều hành',
        detail: 'cần bạn · bên phải',
        state: 'needs_you',
        at_text: '10:31',
      },
      {
        title: 'Backtest 6 mô hình, 6 điểm cắt',
        detail: 'chọn theo MASE',
        state: 'pending',
        at_text: null,
      },
      {
        title: 'Dự báo, khoảng 80% và 95%, đóng góp biến',
        detail: 'conformal trên sai số backtest',
        state: 'pending',
        at_text: null,
      },
    ],
    stale_forecast: {
      id: STALE_ID,
      version_label: 'v6',
      note: 'dự báo cũ, dữ liệu đến 31/08, mô hình suy giảm',
    },
    assumption_request: {
      hitl_id: HITL_ID,
      question: 'Lãi suất điều hành là biến cần giá trị tương lai. Chọn giả định cho 3 tháng tới:',
      options: [
        { id: 'hold', label: 'Giữ nguyên 4,50% (mặc định)', is_default: true },
        { id: 'own_forecast', label: 'Theo dự báo riêng của biến', is_default: false },
        { id: 'scenario_plus_50', label: 'Theo kịch bản: +0,5 điểm từ T12', is_default: false },
      ],
      other_assumptions: [
        { variable: 'Lãi suất huy động 12T thị trường', assumption: 'Theo dự báo riêng (ETS)' },
        { variable: 'CPI so cùng kỳ', assumption: 'Theo công bố gần nhất' },
        { variable: 'Lịch Tết', assumption: 'Biết trước · 17/02/2027' },
      ],
    },
    forecast_id: null,
    model_id: null,
  };
}

function workbench(): ForecastWorkbench {
  return {
    job_id: JOB_ID,
    job_code: 'FJ-2026-1010-03',
    metric_name: 'Số dư huy động bán lẻ',
    summary_text:
      'Chuỗi tháng · cắt tại 30/09/2026 · tầm 3 tháng · Agent Dự báo đã chạy 6 mô hình trên 6 điểm cắt cuốn chiếu',
    series_stats: [
      { label: 'Chuỗi mục tiêu', value: '69 kỳ', note: 'T1/2021 – T9/2026', tone: 'success' },
      { label: 'Khoảng trống', value: '0', note: 'không cần nội suy', tone: 'success' },
      { label: 'Ngắt cấu trúc', value: '1', note: 'T4/2023 · gộp chi nhánh', tone: 'warning' },
      { label: 'Ngoại lai', value: '2', note: 'đánh dấu, không xoá', tone: 'warning' },
    ],
    variables: [
      {
        name: 'Lãi suất huy động 12T thị trường',
        source: 'Hợp nhất P14 · tuần → tháng',
        lag_text: '1 tháng',
        correlation_text: '0,62',
        future_value_text: 'Dự báo riêng',
        status: 'used',
        status_text: 'Dùng',
      },
      {
        name: 'Lãi suất điều hành NHNN',
        source: 'sbv.gov.vn',
        lag_text: '2 tháng',
        correlation_text: '0,48',
        future_value_text: 'Giả định 4,50%',
        status: 'used',
        status_text: 'Dùng',
      },
      {
        name: 'CPI so cùng kỳ',
        source: 'gso.gov.vn',
        lag_text: '1 tháng',
        correlation_text: '−0,31',
        future_value_text: 'Công bố gần nhất',
        status: 'used',
        status_text: 'Dùng',
      },
      {
        name: 'Lịch Tết',
        source: 'Lịch làm việc',
        lag_text: '—',
        correlation_text: '—',
        future_value_text: 'Biết trước',
        status: 'used',
        status_text: 'Dùng',
      },
      {
        name: 'Số dư cuối tháng toàn hệ thống',
        source: 'công bố T+25 · sau thời điểm cắt',
        lag_text: '0',
        correlation_text: '0,97',
        future_value_text: '—',
        status: 'excluded',
        status_text: 'Loại · rò rỉ',
      },
    ],
    variables_note:
      'Mọi biến căn theo thời điểm ghi nhận; biến chỉ có sau thời điểm cắt bị loại tự động (BR-157).',
    backtest: [
      {
        model: 'Seasonal naive',
        mase: 1.0,
        smape_text: '3,9%',
        cover80_text: '—',
        cover95_text: '—',
        role: 'baseline',
      },
      {
        model: 'Theta',
        mase: 0.88,
        smape_text: '3,4%',
        cover80_text: '76%',
        cover95_text: '92%',
        role: 'candidate',
      },
      {
        model: 'ETS',
        mase: 0.82,
        smape_text: '3,2%',
        cover80_text: '78%',
        cover95_text: '93%',
        role: 'candidate',
      },
      {
        model: 'SARIMAX',
        mase: 0.71,
        smape_text: '2,8%',
        cover80_text: '80%',
        cover95_text: '94%',
        role: 'candidate',
      },
      {
        model: 'LightGBM',
        mase: 0.69,
        smape_text: '2,7%',
        cover80_text: '79%',
        cover95_text: '95%',
        role: 'candidate',
      },
      {
        model: 'Ensemble 3',
        mase: 0.64,
        smape_text: '2,5%',
        cover80_text: '81%',
        cover95_text: '95%',
        role: 'selected',
      },
    ],
    backtest_note:
      'Ensemble trung bình LightGBM, SARIMAX, ETS giảm MASE 7% so mô hình tốt nhất (ngưỡng 5%). Khoảng dự báo theo conformal trên sai số backtest từng bước tầm.',
    next_version_label: 'v7',
    can_create_card: { allowed: true, reason: null },
    model_id: null,
    version_id: null,
    forecast_model_id: null,
  };
}

function model(): ForecastModel {
  return {
    id: MODEL_ID,
    metric_name: 'Số dư huy động bán lẻ',
    frequency_label: 'tháng',
    status_text:
      'v6 đang phát hành · suy giảm từ 06/10 · v7 nháp chờ duyệt · người tạo không tự duyệt (BR-158)',
    candidate: {
      version_id: V7_ID,
      version_label: 'v7',
      status: 'pending_review',
      card: [
        { label: 'Mục tiêu', value: 'Số dư huy động bán lẻ cuối tháng, toàn hàng' },
        { label: 'Dữ liệu huấn luyện', value: 'T1/2021 – T9/2026 · 69 kỳ · cắt 30/09/2026' },
        {
          label: 'Biến và giả định',
          value:
            'Lãi suất 12T thị trường (dự báo riêng), lãi suất điều hành (giả định), CPI, lịch Tết',
        },
        { label: 'Thuật toán', value: 'Ensemble trung bình LightGBM, SARIMAX, ETS' },
        { label: 'Người tạo', value: 'Agent Dự báo · trình bởi Trần Minh Quân' },
        { label: 'Người duyệt', value: 'Đỗ Hải Yến · phụ trách chỉ số' },
      ],
      horizons: [
        {
          horizon_label: '1 tháng',
          mase_text: '0,52',
          baseline_text: '1,00',
          cover80_text: '82%',
          cover95_text: '96%',
        },
        {
          horizon_label: '2 tháng',
          mase_text: '0,63',
          baseline_text: '1,00',
          cover80_text: '81%',
          cover95_text: '95%',
        },
        {
          horizon_label: '3 tháng',
          mase_text: '0,77',
          baseline_text: '1,00',
          cover80_text: '80%',
          cover95_text: '94%',
        },
      ],
      limitation:
        'Giới hạn: chưa có dữ liệu khuyến mại của đối thủ; ngắt cấu trúc T4/2023 xử lý bằng biến giả.',
      rejection_reason: null,
    },
    can_approve: { allowed: true, reason: null },
    monitoring: {
      version_label: 'v6',
      status: 'published',
      stats: [
        {
          label: 'MASE cuốn chiếu 6 kỳ',
          value: '0,94',
          note: 'ngưỡng 0,84 = 1,2 × 0,70',
          tone: 'error',
        },
        { label: 'Trong khoảng 95%', value: '4 / 6', note: 'T8, T9 nằm ngoài', tone: 'error' },
      ],
      rows: [
        {
          label: 'T4/2026',
          forecast_text: '405,2',
          actual_text: '407,9',
          error_text: '+0,7%',
          inside95: true,
        },
        {
          label: 'T5/2026',
          forecast_text: '409,8',
          actual_text: '411,2',
          error_text: '+0,3%',
          inside95: true,
        },
        {
          label: 'T6/2026',
          forecast_text: '415,1',
          actual_text: '414,0',
          error_text: '−0,3%',
          inside95: true,
        },
        {
          label: 'T7/2026',
          forecast_text: '417,4',
          actual_text: '412,3',
          error_text: '−1,2%',
          inside95: true,
        },
        {
          label: 'T8/2026',
          forecast_text: '410,6',
          actual_text: '416,8',
          error_text: '+1,5%',
          inside95: false,
        },
        {
          label: 'T9/2026',
          forecast_text: '413,9',
          actual_text: '420,5',
          error_text: '+1,6%',
          inside95: false,
        },
      ],
      note: 'Suy giảm theo BR-160: dự báo của v6 gắn nhãn “suy giảm” cho tới khi v7 được duyệt. Huấn luyện lại mỗi kỳ dữ liệu mới (ngày 5 hằng tháng) hoặc khi suy giảm.',
    },
  };
}

/*
 * P15 Giá xăng dầu (2422:15221, 2424:15221): the ceiling price of the 09/7/2026 period for five
 * fuels and the ledger of the twenty periods before it. E5RON92 carries the figures of the Figma
 * frames (medians and published prices read off the ledger chart); the other fuels are the same
 * shapes shifted to their own previous ceiling. Every figure is illustrative.
 */
interface FuelProduct {
  code: string;
  label: string;
  /** Replaces "xang" in the formula parameter keys. */
  key: string;
  previous: number;
  unit: string;
}

const FUELS: FuelProduct[] = [
  { code: 'e5ron92', label: 'E5RON92', key: 'xang', previous: 19730, unit: 'đ/lít' },
  { code: 'ron95-iii', label: 'RON95-III', key: 'ron95', previous: 20370, unit: 'đ/lít' },
  { code: 'diesel-005s', label: 'Diesel 0,05S', key: 'diesel', previous: 18910, unit: 'đ/lít' },
  { code: 'dau-hoa', label: 'Dầu hoả', key: 'dau_hoa', previous: 18460, unit: 'đ/lít' },
  { code: 'mazut', label: 'Mazut', key: 'mazut', previous: 15720, unit: 'đ/kg' },
];

const fuelForecastId = (f: FuelProduct) => `fpf-2026-07-09-${f.code}`;
const seriesId = (f: FuelProduct) => `fs-ceiling-${f.code}`;
const FUEL_BASE = FUELS[0].previous;

/** A two-sided normal density whose 10th/50th/90th percentiles are the forecast's. */
function density(median: number, lo80: number, hi80: number, from: number, to: number) {
  const z80 = 1.2816;
  const left = (median - lo80) / z80;
  const right = (hi80 - median) / z80;
  const prices = new Set<number>();
  for (let p = from; p <= to; p += 20) prices.add(p);
  return [...prices]
    .sort((a, b) => a - b)
    .map((price) => {
      const s = price < median ? left : right;
      const z = (price - median) / s;
      return { price, density: Math.round(Math.exp(-0.5 * z * z) * 1000) / 1000 };
    });
}

function fuelForecast(fuel: FuelProduct): FuelPriceForecast {
  const shift = fuel.previous - FUEL_BASE;
  const at = (v: number) => v + shift;
  const median = at(19910);
  const lo80 = at(19640);
  const hi80 = at(20190);
  return {
    id: fuelForecastId(fuel),
    job_id: JOB_ID,
    product_code: fuel.key,
    formula_model_id: 'formula-mc-v2',
    model_id: MODEL_ID,
    series_id: seriesId(fuel),
    product_label: fuel.label,
    products: FUELS.map((f) => ({ forecast_id: fuelForecastId(f), label: f.label })),
    period_date: '2026-07-09',
    period_note: 'dự kiến 15:00, theo nhịp thường lệ',
    run_at: '2026-07-08T18:02:00+07:00',
    model: { version_label: 'formula_mc v2', simulations: 10000 },
    source_note: 'không dùng Platts: số bình quân Bộ Công Thương công bố và chuỗi giá công khai',
    illustrative: true,
    unit_label: fuel.unit,
    previous: {
      period_date: '2026-07-02',
      ceiling: fuel.previous,
      source_text: 'Bộ Công Thương công bố',
    },
    distribution: {
      median,
      lo80,
      hi80,
      lo95: at(19480),
      hi95: at(20350),
      p_increase: 0.72,
      density: density(median, lo80, hi80, at(19400), at(20400)),
    },
    decomposition: {
      steps: [
        { label: 'Thế giới', value: 195 },
        { label: 'Tỷ giá', value: 14 },
        { label: 'Thuế phí', value: 0 },
        { label: 'Quỹ', value: -24 },
      ],
      expected: at(19915),
      axis_from: at(19680),
      source_note:
        'Nguồn: giá thế giới bình quân Bộ Công Thương 25/6–02/7 + chuỗi công khai · tỷ giá trung tâm NHNN · thuế phí theo NQ 34/2026/NQ-CP · Quỹ theo mô hình xác suất.',
    },
    fund_levels: [
      { label: `Trích 0 ${fuel.unit}`, probability: 0.12, value: 0 },
      { label: `Trích 200 ${fuel.unit}`, probability: 0.7, value: 200 },
      { label: `Trích 300 ${fuel.unit}`, probability: 0.18, value: 300 },
      { label: `Chi 300 ${fuel.unit}`, probability: 0.08, value: -300 },
    ],
    parameters: [
      {
        key: `thue_bvmt_${fuel.key}`,
        value_text: `2.000 ${fuel.unit}`,
        effective_from: '2026-01-01',
        legal_ref: { label: 'NQ 34/2026/NQ-CP', document_id: 'doc-nq-34-2026' },
        certified_by_name: 'Lê Thu Hà',
        certified: true,
      },
      {
        key: fuel.key === 'xang' ? 'thue_ttdb_xang_e5' : `thue_ttdb_${fuel.key}`,
        value_text: '7%',
        effective_from: '2026-01-01',
        legal_ref: { label: 'Luật 66/2025/QH15', document_id: 'doc-luat-66-2025' },
        certified_by_name: 'Lê Thu Hà',
        certified: true,
      },
      {
        key: `thue_nk_bq_${fuel.key}`,
        value_text: '9,3%',
        effective_from: '2026-06-26',
        legal_ref: { label: 'CV 9017/BTC-QLG', document_id: 'doc-cv-9017-btc' },
        certified_by_name: 'Nguyễn Thị Hạnh',
        certified: true,
      },
      {
        key: `chi_phi_dinh_muc_${fuel.key}`,
        value_text: `1.150 ${fuel.unit}`,
        effective_from: '2026-06-26',
        legal_ref: { label: 'CV 9149/BTC-QLG', document_id: 'doc-cv-9149-btc' },
        certified_by_name: 'Nguyễn Thị Hạnh',
        certified: true,
      },
    ],
    world_price: { known_days: 5, total_days: 7, simulated_range_text: '03/7–08/7' },
    fund_scenario: null,
    following_schedule: false,
  };
}

/** [period, median, published] — read off "Biểu đồ sổ dự báo" (2424:15479), oldest first. */
const LEDGER_E5: [string, number, number][] = [
  ['2026-02-19', 20040, 20100],
  ['2026-02-26', 20340, 20250],
  ['2026-03-05', 20330, 20480],
  ['2026-03-12', 20420, 20390],
  ['2026-03-19', 20430, 20720],
  ['2026-03-26', 20740, 20540],
  ['2026-04-02', 20590, 20610],
  ['2026-04-09', 20640, 20950],
  ['2026-04-16', 20990, 21080],
  ['2026-04-23', 20930, 20870],
  ['2026-04-29', 20820, 20520],
  ['2026-05-07', 20470, 20460],
  ['2026-05-14', 20520, 20640],
  ['2026-05-21', 20540, 20390],
  ['2026-05-28', 20150, 20200],
  ['2026-06-04', 19800, 19840],
  ['2026-06-11', 20480, 20020],
  ['2026-06-18', 20420, 20380],
  ['2026-06-25', 20700, 20785],
  ['2026-07-02', 19850, 19730],
];

const DRIVERS: Record<string, [string | null, string | null]> = {
  '2026-07-02': ['Giá thế giới thấp hơn dự kiến', null],
  '2026-06-25': ['Tỷ giá', null],
  '2026-06-11': ['Quỹ chi không dự kiến 300 đ/lít', 'EB-0031'],
  '2026-04-09': ['Giá thế giới tăng mạnh cuối kỳ', 'EB-0027'],
};

/** A miss: outside 95% or an error above this many units. */
const MISS_THRESHOLD = 300;

function ledgerRows(fuel: FuelProduct): ForecastLedgerRow[] {
  const shift = fuel.previous - FUEL_BASE;
  return LEDGER_E5.map(([period, m, actualE5]) => {
    const median = m + shift;
    const actual = actualE5 + shift;
    const [lo80, hi80, lo95, hi95] = [median - 290, median + 270, median - 440, median + 420];
    const error = actual - median;
    const inside95 = actual >= lo95 && actual <= hi95;
    const day = new Date(`${period}T00:00:00Z`);
    day.setUTCDate(day.getUTCDate() - 1);
    const [driver, errorBook] = DRIVERS[period] ?? [null, null];
    return {
      period_date: period,
      published_at: `${day.toISOString().slice(0, 10)}T20:00:00+07:00`,
      channel_label: 'bản tin',
      median,
      lo80,
      hi80,
      lo95,
      hi95,
      actual,
      error,
      inside80: actual >= lo80 && actual <= hi80,
      inside95,
      missed: !inside95 || Math.abs(error) > MISS_THRESHOLD,
      main_driver: driver,
      error_book_ref: errorBook,
    };
  }).reverse();
}

function ledger(fuel: FuelProduct, periods: number): ForecastLedger {
  const all = ledgerRows(fuel);
  const scored = all.filter((r) => r.error !== null);
  return {
    series_id: seriesId(fuel),
    product_label: fuel.label,
    products: FUELS.map((f) => ({ series_id: seriesId(f), label: f.label })),
    model_version_label: 'formula_mc v2',
    illustrative: true,
    unit_label: fuel.unit,
    calibration: {
      periods: scored.length,
      inside80: scored.filter((r) => r.inside80).length,
      inside95: scored.filter((r) => r.inside95).length,
      mae: scored.reduce((sum, r) => sum + Math.abs(r.error ?? 0), 0) / scored.length,
      crps_vs_naive: -0.38,
      missed: scored.filter((r) => r.missed).length,
      miss_rule_text: `ngoài 95% hoặc sai > ${MISS_THRESHOLD} ${fuel.unit}`,
    },
    windows: [
      { periods: 20, label: '20 kỳ gần nhất' },
      { periods: 10, label: '10 kỳ gần nhất' },
    ],
    window_periods: periods,
    rows: all.slice(0, periods),
    following_schedule: false,
  };
}

/** The Fund scenario shifts the distribution by the Fund's deviation from its expected level. */
const FUND_SHIFT: Record<string, number> = {
  'Trích 0': -170,
  'Trích 200': 30,
  'Trích 300': 130,
  'Chi 300': -470,
};

function fuelHandlers() {
  let fuelForecasts = FUELS.map(fuelForecast);
  // Following is per fuel, shared by the forecast and the ledger of that fuel.
  const following = new Set<string>();
  const findFuel = (id: string) => fuelForecasts.find((f) => f.id === id);
  const withFollow = (f: FuelPriceForecast) => ({
    ...f,
    following_schedule: following.has(f.series_id),
  });
  const saveFuel = (next: FuelPriceForecast) => {
    fuelForecasts = fuelForecasts.map((f) => (f.id === next.id ? next : f));
    return HttpResponse.json(withFollow(next));
  };

  return [
    http.get('*/v1/fuel-price-forecasts', () =>
      HttpResponse.json({ items: fuelForecasts.map(withFollow), next_page_token: '' }),
    ),
    http.get('*/v1/fuel-price-forecasts/:id', ({ params }) => {
      const found = findFuel(String(params.id));
      return found
        ? HttpResponse.json(withFollow(found))
        : problem(404, 'not_found', 'Không tìm thấy dự báo giá.');
    }),
    http.post('*/v1/fuel-price-forecasts/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      const found = findFuel(id);
      if (!found) return problem(404, 'not_found', 'Không tìm thấy dự báo giá.');
      if (verb === 'rerun')
        return saveFuel({ ...found, run_at: '2026-07-08T19:15:00+07:00', fund_scenario: null });
      if (verb === 'run-fund-scenario') {
        const { fund_level = '' } = (await request.json()) as { fund_level?: string };
        const level = found.fund_levels.find((l) => l.label === fund_level);
        if (!level) return problem(422, 'validation_failed', 'Chọn một mức Quỹ.');
        const key = Object.keys(FUND_SHIFT).find((k) => level.label.startsWith(k));
        const delta = key ? FUND_SHIFT[key] : 0;
        const d = found.distribution;
        return saveFuel({
          ...found,
          fund_scenario: {
            fund_level_label: level.label,
            // Fixing the Fund removes its share of the spread.
            median: d.median + delta,
            lo80: d.lo80 + delta + 40,
            hi80: d.hi80 + delta - 40,
            lo95: d.lo95 + delta + 60,
            hi95: d.hi95 + delta - 60,
          },
        });
      }
      if (verb === 'clear-fund-scenario') return saveFuel({ ...found, fund_scenario: null });
      return problem(404, 'not_found', 'Không có thao tác này.');
    }),

    // A2's ledger has no line yet in the demo; the screens fall back to the PROPOSAL series below.
    http.get('*/v1/forecast-ledger', () => HttpResponse.json({ items: [], next_page_token: '' })),
    http.get('*/v1/forecast-series', () =>
      HttpResponse.json({
        items: FUELS.map((f) => ({ id: seriesId(f), label: f.label })),
        next_page_token: '',
      }),
    ),
    http.get('*/v1/forecast-series/:id/ledger', ({ params, request }) => {
      const fuel = FUELS.find((f) => seriesId(f) === params.id);
      if (!fuel) return problem(404, 'not_found', 'Không tìm thấy sổ dự báo.');
      const url = new URL(request.url);
      const periods = Number(url.searchParams.get('periods')) || 20;
      return HttpResponse.json({
        ...ledger(fuel, periods),
        following_schedule: following.has(seriesId(fuel)),
      });
    }),
    http.post('*/v1/forecast-series/:action', ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      if (!FUELS.some((f) => seriesId(f) === id))
        return problem(404, 'not_found', 'Không tìm thấy sổ dự báo.');
      if (verb === 'follow') following.add(id);
      else if (verb === 'unfollow') following.delete(id);
      else return problem(404, 'not_found', 'Không có thao tác này.');
      return HttpResponse.json({ following: following.has(id) });
    }),
  ];
}

/** ForecastModelDetail as the api sends it, with the screen's model under display. */
const modelWire = (m: ForecastModel) => ({
  id: m.id,
  metric_code: m.metric_name,
  frequency: 'month',
  status: 'active',
  scope: {},
  owner_id: 'u-long',
  current_version_id: m.candidate?.version_id ?? null,
  retrain_cron: null,
  data_space_id: 'ds-nang-luong',
  created_at: '2026-09-01T09:00:00+07:00',
  updated_at: '2026-10-06T09:00:00+07:00',
  display: m,
});

export function createForecastHandlers() {
  let currentJob = job();
  let polls = 0;
  let forecasts = [forecast(), staleForecast()];
  let bench = workbench();
  let currentModel = model();

  const saveForecast = (next: Forecast) => {
    forecasts = forecasts.map((f) => (f.id === next.id ? next : f));
    return HttpResponse.json(next);
  };

  return [
    ...fuelHandlers(),
    http.get('*/v1/forecast-jobs/:id/workbench', ({ params }) =>
      params.id === JOB_ID
        ? HttpResponse.json(bench)
        : problem(404, 'not_found', 'Không tìm thấy việc dự báo.'),
    ),
    http.get('*/v1/forecast-jobs/:id', ({ params }) => {
      if (params.id !== JOB_ID) return problem(404, 'not_found', 'Không tìm thấy việc dự báo.');
      if (currentJob.status === 'running' && ++polls >= POLLS_TO_FINISH) {
        currentJob = {
          ...currentJob,
          status: 'done',
          message:
            'Dự báo đã xong lúc 10:42. Mô hình v7 (Ensemble) thắng seasonal naive trên 6 điểm cắt; kết quả có khoảng 80% và 95%.',
          steps: currentJob.steps.map((s) => ({
            ...s,
            state: 'done',
            at_text: s.at_text ?? '10:42',
          })),
          forecast_id: FORECAST_ID,
          stale_forecast: null,
        };
      }
      return HttpResponse.json(currentJob);
    }),
    http.post('*/v1/forecast-jobs/:action', ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      if (id !== JOB_ID) return problem(404, 'not_found', 'Không tìm thấy việc dự báo.');
      if (verb === 'rerun-backtest') return HttpResponse.json(bench, { status: 202 });
      if (verb === 'create-model-card') {
        if (!bench.can_create_card.allowed)
          return problem(409, 'invalid_state', bench.can_create_card.reason ?? 'Chưa tạo được.');
        bench = { ...bench, model_id: MODEL_ID };
        currentJob = { ...currentJob, model_id: MODEL_ID };
        return HttpResponse.json({ model_id: MODEL_ID, version_id: V7_ID }, { status: 201 });
      }
      return problem(404, 'not_found', 'Không có thao tác này.');
    }),
    // The assumption is the ordinary HITL :resolve; only this mock's own request is handled
    // here, every other one falls through to the inbox mock.
    http.post('*/v1/hitl-requests/:action', async ({ params, request }) => {
      const [id] = String(params.action).split(':');
      if (id !== HITL_ID) return undefined;
      if (!currentJob.assumption_request)
        return problem(409, 'invalid_state', 'Giả định đã được xác nhận.');
      const body = (await request.json()) as { option?: string };
      const option = currentJob.assumption_request.options.find((o) => o.id === body.option);
      if (!option) return problem(422, 'validation_failed', 'Chọn một giả định.');
      polls = 0;
      currentJob = {
        ...currentJob,
        status: 'running',
        assumption_request: null,
        steps: currentJob.steps.map((s) =>
          s.state === 'needs_you'
            ? { ...s, state: 'done', detail: `Đã chọn: ${option.label}`, at_text: '10:32' }
            : s.title.startsWith('Backtest')
              ? { ...s, state: 'running', at_text: '10:32' }
              : s,
        ),
      };
      return HttpResponse.json({ id, status: 'answered' });
    }),

    http.get('*/v1/forecasts', () => HttpResponse.json({ items: forecasts, next_page_token: '' })),
    http.get('*/v1/forecasts/:id', ({ params }) => {
      const found = forecasts.find((f) => f.id === params.id);
      return found ? HttpResponse.json(found) : problem(404, 'not_found', 'Không tìm thấy dự báo.');
    }),
    http.post('*/v1/forecasts/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      const found = forecasts.find((f) => f.id === id);
      if (!found) return problem(404, 'not_found', 'Không tìm thấy dự báo.');
      if (verb === 'run-scenario') {
        const { text = '' } = (await request.json()) as { text?: string };
        const lower = text.toLowerCase();
        if (!text.trim())
          return problem(422, 'validation_failed', 'Nhập một giả định để chạy kịch bản.');
        // UC-15-05: a variable the model does not use cannot be moved.
        if (!found.scenario_variables.some((v) => lower.includes(v)))
          return problem(
            422,
            'scenario_not_applicable',
            'Mô hình không dùng biến này, nên kịch bản không áp dụng.',
          );
        return saveForecast({ ...found, scenario: scenario(text.trim()) });
      }
      if (verb === 'save-scenario') {
        if (!found.scenario) return problem(409, 'invalid_state', 'Chưa có kịch bản để lưu.');
        return saveForecast({ ...found, scenario: { ...found.scenario, saved: true } });
      }
      if (verb === 'clear-scenario') return saveForecast({ ...found, scenario: null });
      if (verb === 'add-to-session') {
        if (!found.session_id)
          return problem(409, 'invalid_state', 'Dự báo này không thuộc phiên nào.');
        return saveForecast({ ...found, added_to_session: true });
      }
      return problem(404, 'not_found', 'Không có thao tác này.');
    }),

    // ForecastModelView/Detail with the screen's model under display.
    http.get('*/v1/forecast-models', () =>
      HttpResponse.json({ items: [modelWire(currentModel)], next_page_token: '' }),
    ),
    http.get('*/v1/forecast-models/:id', ({ params }) =>
      params.id === MODEL_ID
        ? HttpResponse.json(modelWire(currentModel))
        : problem(404, 'not_found', 'Không tìm thấy mô hình.'),
    ),
    http.post('*/v1/forecast-model-versions/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      const candidate = currentModel.candidate;
      if (!candidate || candidate.version_id !== id)
        return problem(404, 'not_found', 'Không tìm thấy phiên bản mô hình.');
      if (!currentModel.can_approve.allowed)
        return problem(
          403,
          'forbidden',
          currentModel.can_approve.reason ?? 'Bạn không duyệt được.',
        );
      if (candidate.status !== 'pending_review')
        return problem(409, 'invalid_state', 'Phiên bản này đã được quyết.');
      if (verb === 'approve') {
        currentModel = {
          ...currentModel,
          status_text: `${candidate.version_label} đang phát hành · v6 đã thôi phát hành`,
          candidate: { ...candidate, status: 'published' },
          can_approve: { allowed: false, reason: 'Phiên bản đã được duyệt.' },
          monitoring: currentModel.monitoring && { ...currentModel.monitoring, status: 'retired' },
        };
        return HttpResponse.json({ id: candidate.version_id, status: 'published' });
      }
      if (verb === 'reject') {
        const { reason = '' } = (await request.json()) as { reason?: string };
        if (!reason.trim()) return problem(422, 'validation_failed', 'Cần lý do khi từ chối.');
        currentModel = {
          ...currentModel,
          status_text: `v6 đang phát hành · suy giảm từ 06/10 · ${candidate.version_label} bị từ chối`,
          candidate: { ...candidate, status: 'rejected', rejection_reason: reason.trim() },
          can_approve: { allowed: false, reason: 'Phiên bản đã bị từ chối.' },
        };
        return HttpResponse.json({ id: candidate.version_id, status: 'rejected' });
      }
      return problem(404, 'not_found', 'Không có thao tác này.');
    }),
  ];
}
