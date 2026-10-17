import { http, HttpResponse } from 'msw';
import type {
  AgentOpsSummary,
  AiPolicy,
  AiProvider,
  ChannelRules,
  ClientWire,
  GpuLoad,
  ModelRoute,
  NewModel,
  NewProvider,
  NotificationPreferences,
  OpsPeriod,
  ProviderWire,
  RoutingLogEntry,
  SystemHealth,
} from '../lib/v1/adminPlatform';
import type { Me } from '../lib/v1/me';
import type { components } from '../lib/v1/schema';
import type { ModelConfig } from '../lib/v1/types';

type Schemas = components['schemas'];
type ApiClientCreate = Schemas['ApiClientCreate'];
type ApiClientIssued = Schemas['ApiClientIssued'];
type UsageRow = Schemas['UsageRow'];
type WebhookCreate = Schemas['WebhookCreate'];
type WebhookIssued = Schemas['WebhookIssued'];

/*
 * MSW handlers for lib/v1/adminPlatform.ts. Data is the data drawn in the Figma frames
 * (Giám sát hệ thống 2237:15149, Vận hành agent 2058:1356, P11 Mô hình 2306:19746 and its Provider tab 2436:15566,
 * P12 Tích hợp 2306:20082, Cài đặt 2324:20173 / 2272:16341, Kênh · Quy tắc 2112:12987).
 * State lives in memory: an added model or integration shows in the list, a rollback swaps
 * the model, saved notification choices come back on the next read.
 */
const page = <T>(items: T[]) => HttpResponse.json({ items, next_page_token: '' });

const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    {
      type: `https://docs.dataagent/errors/${code}`,
      title: code,
      status,
      code,
      detail,
      trace_id: 'mock',
    },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

export function systemHealthFixture(): SystemHealth {
  return {
    as_of: '2026-10-10T08:15:00+07:00',
    no_data: false,
    services_up: 15,
    services_total: 16,
    services_note: 'Ingestion khởi động lại 07:42',
    latency_p95_ms: 6800,
    latency_target_ms: 8000,
    gpu_utilization: 0.71,
    gpu_hosts: 2,
    gpu_cards: 4,
    error_rate_24h: 0.004,
    error_rate_delta_pp: 0.1,
    services: [
      {
        name: 'Agent runtime',
        status: 'ok',
        version: '3.1.4',
        latency_p95_ms: 2100,
        error_rate_24h: 0.002,
        note: '3 lượt chạy đang chờ người',
      },
      {
        name: 'Semantic compiler',
        status: 'ok',
        version: '3.1.4',
        latency_p95_ms: 180,
        error_rate_24h: 0,
        note: null,
      },
      {
        name: 'Query engine',
        status: 'degraded',
        version: '3.1.4',
        latency_p95_ms: 4900,
        error_rate_24h: 0.006,
        note: 'DWH bán lẻ trả lời chậm từ 07:30',
      },
      {
        name: 'Ingestion service',
        status: 'restarted',
        version: '3.1.3',
        latency_p95_ms: null,
        error_rate_24h: 0.011,
        note: 'Hết bộ nhớ khi nạp CDC, đã tăng giới hạn',
      },
      {
        name: 'Model serving',
        status: 'ok',
        version: 'vLLM 0.8',
        latency_p95_ms: 1600,
        error_rate_24h: 0.001,
        note: 'Hàng đợi 4 yêu cầu',
      },
      {
        name: 'Channel gateway',
        status: 'ok',
        version: '3.1.4',
        latency_p95_ms: 320,
        error_rate_24h: 0,
        note: null,
      },
    ],
    models: [
      {
        name: 'Mô hình chính 70B',
        tasks_text: 'Lập kế hoạch, viết câu trả lời',
        tokens_today: 18_200_000,
        queue: 4,
        queue_unit: 'request',
        gpu_cards: 2,
        gpu_utilization: 0.82,
      },
      {
        name: 'Mô hình nhỏ 8B',
        tasks_text: 'Phân loại, trích xuất',
        tokens_today: 6_500_000,
        queue: 0,
        queue_unit: 'request',
        gpu_cards: 1,
        gpu_utilization: 0.54,
      },
      {
        name: 'Embedding + OCR',
        tasks_text: 'Tài liệu, tìm kiếm',
        tokens_today: null,
        queue: 12,
        queue_unit: 'page',
        gpu_cards: 1,
        gpu_utilization: 0.63,
      },
    ],
    alerts: [
      {
        id: 'al-query',
        severity: 'warning',
        title: 'Query engine chậm',
        detail: 'DWH bán lẻ phản hồi 4,9 s · từ 07:30',
      },
      {
        id: 'al-minio',
        severity: 'warning',
        title: 'Dung lượng MinIO',
        detail: '78% · dự kiến đầy sau 41 ngày',
      },
      { id: 'al-sso', severity: 'info', title: 'Chứng chỉ SSO', detail: 'Hết hạn sau 12 ngày' },
    ],
    backup_text: 'PostgreSQL sao lưu 02:00 hôm nay · sao chép sang site DR trễ 40 giây.',
  };
}

export function agentOpsFixture(period: OpsPeriod): AgentOpsSummary {
  return {
    period,
    headline:
      'Tuần này agent tự hoàn tất 92% việc. 1 agent đang lỗi kết nối; chi phí vẫn trong ngân sách.',
    runs: 286,
    runs_change_ratio: 0.12,
    auto_rate: 0.941,
    auto_rate_change_pp: 1.8,
    hitl_requests: 17,
    hitl_median_response_s: 360,
    errors: 9,
    error_rate: 0.031,
    error_note: '+5 do mất kết nối DWH',
    cost: '4200000',
    budget_used_ratio: 0.62,
    series: [
      { label: 'T6', auto: 34, human: 2, error: 0 },
      { label: 'T7', auto: 22, human: 1, error: 0 },
      { label: 'CN', auto: 18, human: 1, error: 0 },
      { label: 'T2', auto: 41, human: 3, error: 1 },
      { label: 'T3', auto: 44, human: 4, error: 0 },
      { label: 'T4', auto: 47, human: 3, error: 7 },
      { label: 'T5', auto: 43, human: 3, error: 1 },
    ],
    agents: [
      {
        agent_id: 'agent-dq',
        name: 'Agent Chất lượng dữ liệu',
        runs: 96,
        auto_rate: 0.91,
        hitl_requests: 2,
        avg_duration_s: 38,
        cost: '600000',
        eval_score: 0.97,
        eval_change_pp: 0,
      },
      {
        agent_id: 'agent-analysis',
        name: 'Agent Phân tích',
        runs: 61,
        auto_rate: 0.92,
        hitl_requests: 6,
        avg_duration_s: 130,
        cost: '1900000',
        eval_score: 0.94,
        eval_change_pp: 0,
      },
      {
        agent_id: 'agent-risk',
        name: 'Agent Giám sát rủi ro',
        runs: 48,
        auto_rate: 1,
        hitl_requests: 0,
        avg_duration_s: 12,
        cost: '200000',
        eval_score: 0.99,
        eval_change_pp: 0,
      },
      {
        agent_id: 'agent-docs',
        name: 'Agent Tài liệu',
        runs: 34,
        auto_rate: 0.97,
        hitl_requests: 1,
        avg_duration_s: 65,
        cost: '700000',
        eval_score: 0.95,
        eval_change_pp: 0,
      },
      {
        agent_id: 'agent-recon',
        name: 'Agent Đối soát',
        runs: 12,
        auto_rate: 1,
        hitl_requests: 5,
        avg_duration_s: 220,
        cost: '500000',
        eval_score: 0.96,
        eval_change_pp: 0,
      },
      {
        agent_id: 'agent-report',
        name: 'Agent Báo cáo',
        runs: 9,
        auto_rate: 0.89,
        hitl_requests: 3,
        avg_duration_s: 380,
        cost: '300000',
        eval_score: 0.89,
        eval_change_pp: -4,
      },
    ],
    issues: [
      {
        id: 'iss-dq',
        severity: 'error',
        title: 'Agent Chất lượng dữ liệu lỗi lặp 7 lần',
        detail:
          'Mất kết nối DWH bán lẻ từ 02:10. Agent đã tự thử lại theo chính sách rồi tạm dừng.',
        action: { kind: 'run', label: 'Xem trace', target_id: 'run-dq-0210' },
      },
      {
        id: 'iss-report',
        severity: 'warning',
        title: 'Đánh giá Agent Báo cáo giảm 93% → 89%',
        detail: 'Sau khi đổi mô hình ngày 05/10; 2 tình huống thử về định dạng số không đạt.',
        action: { kind: 'eval', label: 'So sánh 2 cấu hình', target_id: 'agent-report' },
      },
      {
        id: 'iss-budget',
        severity: 'info',
        title: 'Agent Phân tích dùng 81% ngân sách tháng',
        detail: 'Dự kiến vượt ngân sách vào 24/10 với tốc độ hiện tại.',
        action: { kind: 'budget', label: 'Điều chỉnh giới hạn', target_id: 'agent-analysis' },
      },
    ],
  };
}

export function modelRoutesFixture(): ModelRoute[] {
  const route = (r: Omit<ModelRoute, 'status'>): ModelRoute => ({ status: 'active', ...r });
  return [
    route({
      id: 'planning',
      task: 'Lập kế hoạch, điều phối agent',
      model_name: 'Qwen3-72B',
      placement: 'GPU cụm A',
      fallback_name: 'Qwen3-32B',
      latency_p95_ms: 4100,
      eval_score: 0.948,
      latest_eval_delta: 0,
      previous: null,
      eval_suite_name: 'Agent Phân tích',
    }),
    route({
      id: 'sql',
      task: 'Sinh SQL',
      model_name: 'Qwen3-Coder-32B',
      placement: null,
      fallback_name: 'Qwen3-32B',
      latency_p95_ms: 1900,
      eval_score: 0.962,
      latest_eval_delta: 0,
      previous: null,
      eval_suite_name: 'Agent Phân tích',
    }),
    route({
      id: 'documents',
      task: 'Đọc tài liệu, trích dẫn',
      model_name: 'Qwen3-32B',
      placement: null,
      fallback_name: null,
      latency_p95_ms: 2700,
      eval_score: 0.921,
      latest_eval_delta: 0,
      previous: null,
      eval_suite_name: 'Agent Tài liệu',
    }),
    route({
      id: 'report',
      task: 'Soạn báo cáo',
      model_name: 'LLM-M 14B',
      placement: null,
      fallback_name: null,
      latency_p95_ms: 3200,
      eval_score: 0.89,
      latest_eval_delta: -4,
      previous: { model_name: 'LLM-L 32B', endpoint: 'https://llm.internal/v1', cost_ratio: 3.5 },
      eval_suite_name: 'Agent Báo cáo',
    }),
    route({
      id: 'embedding',
      task: 'Nhúng văn bản',
      model_name: 'bge-m3',
      placement: null,
      fallback_name: null,
      latency_p95_ms: 40,
      eval_score: null,
      latest_eval_delta: null,
      previous: null,
      eval_suite_name: null,
    }),
    route({
      id: 'pii',
      task: 'Phân loại dữ liệu cá nhân',
      model_name: 'mô hình nội bộ PII-vi',
      placement: null,
      fallback_name: 'luật',
      latency_p95_ms: 12,
      eval_score: 0.989,
      latest_eval_delta: 0,
      previous: null,
      eval_suite_name: null,
    }),
  ];
}

export function aiPolicyFixture(): AiPolicy {
  return {
    rules: [
      {
        key: 'max_cost_per_run',
        label: 'Chi phí tối đa mỗi lượt chạy agent',
        value_text: '50.000 đ · vượt thì dừng và hỏi',
      },
      {
        key: 'input_filter',
        label: 'Bộ lọc đầu vào',
        value_text: 'Chặn yêu cầu lấy dữ liệu cá nhân hàng loạt',
      },
      {
        key: 'output_filter',
        label: 'Bộ lọc đầu ra',
        value_text: 'Không trả số tài khoản, CCCD đầy đủ',
      },
      {
        key: 'prompt_log',
        label: 'Lưu nhật ký prompt',
        value_text: '30 ngày, đã che dữ liệu cá nhân',
      },
    ],
    frontier_allowed: true,
    frontier: { tasks: [], data_space_ids: [] },
    routing_rules: [
      'Mặc định mọi lời gọi chạy trên vLLM tại chỗ.',
      'Chỉ gửi frontier khi mọi dữ liệu trong ngữ cảnh có nhãn Công khai, hoặc câu hỏi ngoài phạm vi data product và không kèm dữ liệu nội bộ.',
      'Policy guard kiểm nhãn của từng dữ liệu trước khi gọi; còn một nhãn Nội bộ trở lên thì chặn và chạy tại chỗ.',
      'Mỗi lời gọi ghi kiểm toán: quyết định, lý do, provider, người hỏi (BR-180, BR-181).',
    ],
  };
}

export function providersFixture(): AiProvider[] {
  return [
    {
      id: 'prov-vllm',
      name: 'vLLM tại chỗ',
      kind: 'local',
      model_name: 'Qwen3.6-27B',
      model_note: 'Qwen3.6-35B-A3B',
      endpoint_label: 'Cụm GPU nội bộ',
      region_text: 'llm.noibo.local · trong ngân hàng',
      zero_retention: 'not_applicable',
      ac06_status: 'approved',
      month_cost_vnd: null,
      month_budget_vnd: null,
      budget_month: '2026-10',
    },
    {
      id: 'prov-a',
      name: 'Nhà cung cấp A (API)',
      kind: 'frontier',
      model_name: 'Mô hình frontier A',
      model_note: 'chỉ ngữ cảnh Công khai',
      endpoint_label: 'Endpoint API nhà cung cấp A',
      region_text: 'Vùng Singapore',
      zero_retention: 'committed',
      ac06_status: 'approved',
      month_cost_vnd: 18_400_000,
      month_budget_vnd: 30_000_000,
      budget_month: '2026-10',
    },
    {
      id: 'prov-b',
      name: 'Nhà cung cấp B (API)',
      kind: 'frontier',
      model_name: 'Mô hình frontier B',
      model_note: 'chưa được dùng',
      endpoint_label: 'Endpoint API nhà cung cấp B',
      region_text: 'Vùng chưa xác nhận',
      zero_retention: 'verifying',
      ac06_status: 'pending',
      month_cost_vnd: 0,
      month_budget_vnd: 10_000_000,
      budget_month: '2026-10',
    },
  ];
}

/* Week 41's audit, newest first: the three rows of the frame, then older ones of the week. */
export function routingLogFixture(): RoutingLogEntry[] {
  const rows: [string, string, RoutingLogEntry['decision'], string, string][] = [
    [
      '2026-10-09T09:42:00+07:00',
      'Giải thích khái niệm NIM là gì?',
      'frontier',
      'Ngoài phạm vi data product, không kèm dữ liệu',
      'Nhà cung cấp A',
    ],
    [
      '2026-10-09T09:31:00+07:00',
      'So sánh dư nợ CN Hà Nội với kế hoạch',
      'blocked',
      'Có dữ liệu nhãn Nội bộ',
      'vLLM tại chỗ',
    ],
    [
      '2026-10-09T09:20:00+07:00',
      'NPL bán lẻ T9 theo vùng',
      'local',
      'Dữ liệu nhãn Mật, mặc định',
      'vLLM tại chỗ',
    ],
    [
      '2026-10-09T08:57:00+07:00',
      'Tóm tắt thông tư 02/2023 về cơ cấu nợ',
      'frontier',
      'Văn bản nhãn Công khai',
      'Nhà cung cấp A',
    ],
    [
      '2026-10-08T16:12:00+07:00',
      'Huy động vốn CN Đà Nẵng tuần này',
      'local',
      'Dữ liệu nhãn Nội bộ, mặc định',
      'vLLM tại chỗ',
    ],
    [
      '2026-10-08T15:40:00+07:00',
      'Viết lại email gửi khách hàng doanh nghiệp',
      'blocked',
      'Có tên khách hàng, nhãn Mật',
      'vLLM tại chỗ',
    ],
    [
      '2026-10-08T10:05:00+07:00',
      'Lãi suất điều hành của NHNN hiện nay',
      'frontier',
      'Ngoài phạm vi data product, không kèm dữ liệu',
      'Nhà cung cấp A',
    ],
    [
      '2026-10-07T14:22:00+07:00',
      'Top 10 khách hàng theo dư nợ',
      'local',
      'Dữ liệu nhãn Mật, mặc định',
      'vLLM tại chỗ',
    ],
  ];
  return rows.map(([at, question, decision, reason, provider_name], i) => ({
    id: `route-${i + 1}`,
    at,
    question,
    decision,
    reason,
    provider_name,
    asked_by_name: i % 2 ? 'Trần Minh Anh' : 'Lê Quốc Huy',
  }));
}

export function gpuLoadFixture(): GpuLoad {
  // Hourly load of the drawn curve: low overnight, a ramp from 09:00, peak 83% at 15:00.
  const load = [
    0.42, 0.4, 0.38, 0.37, 0.38, 0.4, 0.46, 0.55, 0.66, 0.74, 0.78, 0.8, 0.79, 0.77, 0.79, 0.83,
    0.8, 0.72, 0.64, 0.6, 0.58, 0.56, 0.55, 0.54,
  ];
  return {
    checked_at: '2026-10-10T08:15:00+07:00',
    cards: [],
    average: null,
    points: load.map((utilization, hour) => ({
      at: `2026-10-09T${String(hour).padStart(2, '0')}:00:00+07:00`,
      utilization,
    })),
    peak: { at: '2026-10-09T15:00:00+07:00', utilization: 0.83 },
    failover_text: 'Khi vượt 90% trong 10 phút, tác vụ sinh SQL chuyển sang mô hình dự phòng.',
  };
}

const daysAhead = (days: number) => {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return d.toISOString();
};

const DS_RISK_ID = 'ds-rui-ro-tin-dung';

/* ApiClientListed as GET /v1/admin/clients sends it; the owner's name and 7-day figures under `display` (PROPOSAL). */
function clientRow(
  row: Pick<ClientWire, 'id' | 'name' | 'kind' | 'tools' | 'expires_at' | 'status'> &
    Partial<ClientWire>,
): ClientWire {
  const at = '2026-09-01T02:00:00Z';
  return {
    identity_mode: 'on_behalf_of_user',
    owner_user_id: '00000000-0000-4000-8000-0000000000b1',
    keycloak_client_id: `da-${row.id}`,
    data_space_ids: [DS_RISK_ID],
    quota_per_minute: 60,
    quota_per_day: 5000,
    calls_24h: 0,
    errors_24h: 0,
    owner_confirmed_at: row.status === 'pending_owner' ? null : at,
    secret_rotated_at: null,
    revoked_at: row.status === 'revoked' ? at : null,
    created_at: at,
    updated_at: at,
    ...row,
  };
}

export function integrationsFixture(): ClientWire[] {
  return [
    clientRow({
      id: 'cli-copilot',
      name: 'Copilot nội bộ',
      kind: 'mcp',
      tools: ['ask_question', 'get_metric_definition', 'run_skill'],
      expires_at: daysAhead(80),
      status: 'active',
      display: { owner_name: 'Ngô Thanh Bình', calls_7d: 12480, error_rate_7d: 0.004 },
    }),
    clientRow({
      id: 'cli-excel',
      name: 'Excel add-in',
      kind: 'rest',
      tools: ['get_metric_definition', 'search_metrics'],
      expires_at: daysAhead(170),
      status: 'active',
      display: { owner_name: 'Lê Minh Châu', calls_7d: 3204, error_rate_7d: 0.011 },
    }),
    clientRow({
      id: 'cli-sbv',
      name: 'Hệ thống báo cáo NHNN',
      kind: 'rest',
      tools: ['get_published_result'],
      expires_at: daysAhead(260),
      status: 'active',
      display: { owner_name: 'Trần Quốc An', calls_7d: 62, error_rate_7d: 0 },
    }),
    clientRow({
      id: 'cli-zalo',
      name: 'Bot Zalo OA',
      kind: 'mcp',
      tools: ['ask_question'],
      expires_at: daysAhead(20),
      status: 'active',
      display: { owner_name: 'Pháp chế', calls_7d: 840, error_rate_7d: 0.026 },
    }),
    clientRow({
      id: 'cli-hackathon',
      name: 'Thử nghiệm Hackathon',
      kind: 'mcp',
      tools: ['ask_question'],
      expires_at: daysAhead(-11),
      status: 'expired',
      display: { owner_name: 'Phạm Đức Long', calls_7d: 0, error_rate_7d: null },
    }),
  ];
}

/* Per local day, the copilot's calls split over its tools; one row at noon per day and tool. */
const COPILOT_DAYS = [1600, 1720, 1880, 1690, 2010, 1820, 2050];
const COPILOT_TOOLS: [string, number][] = [
  ['ask_question', 0.71],
  ['get_metric_definition', 0.24],
  ['run_skill', 0.05],
];

function usageRows(id: string): UsageRow[] {
  if (id !== 'cli-copilot') return [];
  const today = new Date();
  return COPILOT_DAYS.flatMap((calls, i) =>
    COPILOT_TOOLS.map(([tool, share]) => ({
      bucket: new Date(
        today.getFullYear(),
        today.getMonth(),
        today.getDate() - 6 + i,
        12,
      ).toISOString(),
      tool,
      data_space_id: DS_RISK_ID,
      calls: Math.round(calls * share),
      errors: 0,
      blocked_by_permission: tool === 'ask_question' && i === 6 ? 52 : 0,
      blocked_by_quota: 0,
      p95_ms: 820,
    })),
  );
}

/* The clients the signed-in person (meFixture) answers for. */
export function myClientsFixture(): ClientWire[] {
  const me = '01920000-0000-7000-8000-00000000a5c1';
  return [
    clientRow({
      id: 'cli-power-bi',
      name: 'Power BI phòng Rủi ro',
      kind: 'rest',
      owner_user_id: me,
      tools: ['get_metric_definition', 'search_metrics'],
      expires_at: daysAhead(180),
      status: 'pending_owner',
      updated_at: '2026-10-12T03:15:00.123456Z',
    }),
    clientRow({
      id: 'cli-teams-bot',
      name: 'Bot Teams của tôi',
      kind: 'mcp',
      owner_user_id: me,
      tools: ['ask_question'],
      expires_at: daysAhead(90),
      status: 'active',
    }),
  ];
}

export function meFixture(): Me {
  return {
    id: '01920000-0000-7000-8000-00000000a5c1',
    username: 'yen.dh',
    display_name: 'Đỗ Hải Yến',
    email: 'yen.dh@bank.vn',
    department: 'Khối Quản lý rủi ro · Phòng Rủi ro tín dụng',
    kind: 'human',
    default_data_space_id: null,
    locale: 'vi-VN',
    time_zone: 'Asia/Ho_Chi_Minh',
    roles: [
      {
        role: {
          id: '01920000-0000-7000-8000-0000000000a1',
          code: 'AC01',
          name: 'Chuyên viên phân tích',
        },
        source: 'group_mapping',
        group_name: 'GRP-RISK-ANALYST',
        valid_until: null,
      },
    ],
    capabilities: ['ask', 'view_detail', 'run_agent'],
  };
}

export function notificationPreferencesFixture(): NotificationPreferences {
  const on = (in_app: boolean, teams: boolean, zalo: boolean, email: boolean, digest: boolean) => ({
    in_app,
    teams,
    zalo,
    email,
    digest,
  });
  return {
    events: [
      {
        key: 'decision_needed',
        label: 'Agent cần tôi quyết định',
        description: 'Làm rõ, duyệt, xung đột nguồn',
        channels: on(true, true, false, true, false),
        locked: ['in_app'],
      },
      {
        key: 'watch_alert',
        label: 'Cảnh báo từ theo dõi của tôi',
        description: 'Vượt ngưỡng, bất thường',
        channels: on(true, true, true, false, false),
        locked: [],
      },
      {
        key: 'run_finished',
        label: 'Lượt chạy của tôi xong hoặc lỗi',
        description: 'Agent tôi sở hữu',
        channels: on(true, false, false, false, true),
        locked: [],
      },
      {
        key: 'report_published',
        label: 'Báo cáo định kỳ và bản tin',
        description: 'Khi có số mới',
        channels: on(true, false, false, true, false),
        locked: [],
      },
      {
        key: 'source_issue',
        label: 'Nguồn dữ liệu có vấn đề',
        description: 'Nguồn trong Data Space của tôi',
        channels: on(true, false, false, false, true),
        locked: [],
      },
      {
        key: 'mention',
        label: 'Bình luận và nhắc tên',
        description: 'Trong phiên, báo cáo',
        channels: on(true, true, false, false, true),
        locked: [],
      },
    ],
    quiet_hours_text:
      '21:00 – 07:00 và cuối tuần · Teams, Zalo không rung; việc có hạn trong ngày vẫn gửi.',
    grouping_text: 'Nhiều việc từ cùng một agent trong 15 phút được gộp thành một thẻ.',
  };
}

export function channelRulesFixture(): ChannelRules {
  const ok = (text: string) => ({ text, tone: 'success' as const });
  const limited = (text: string) => ({ text, tone: 'warning' as const });
  const blocked = (text: string) => ({ text, tone: 'error' as const });
  return {
    channels: [
      { key: 'app', label: 'Ứng dụng' },
      { key: 'chat', label: 'Teams, Zalo' },
      { key: 'mobile', label: 'Mobile' },
      { key: 'email', label: 'Email' },
    ],
    rows: [
      {
        kind: 'alert',
        label: 'Cảnh báo, bản tin',
        cells: {
          app: ok('Xem'),
          chat: ok('Xem + hành động nhanh'),
          mobile: ok('Xem'),
          email: ok('Xem, liên kết'),
        },
        note: 'Không cần xác thực lại',
      },
      {
        kind: 'clarification',
        label: 'Làm rõ (chọn phương án)',
        cells: {
          app: ok('Xử lý'),
          chat: ok('Trả lời một chạm'),
          mobile: ok('Trả lời một chạm'),
          email: limited('Chỉ liên kết'),
        },
        note: 'Câu trả lời ghi vào trace',
      },
      {
        kind: 'approve_low',
        label: 'Duyệt · rủi ro thấp (kỳ chưa nộp)',
        cells: {
          app: ok('Xử lý'),
          chat: ok('Duyệt + xác thực lại (SSO, OTP)'),
          mobile: ok('Duyệt + sinh trắc'),
          email: limited('Chỉ liên kết'),
        },
        note: 'Ví dụ: chứng nhận thuật ngữ',
      },
      {
        kind: 'approve_high',
        label: 'Duyệt · rủi ro cao (kỳ đã nộp, gửi ra ngoài, xoá)',
        cells: {
          app: ok('Xử lý'),
          chat: blocked('Chỉ liên kết'),
          mobile: blocked('Chỉ liên kết'),
          email: blocked('Chỉ liên kết'),
        },
        note: 'Bắt buộc xem diff trong ứng dụng',
      },
      {
        kind: 'conflict',
        label: 'Xung đột nguồn',
        cells: {
          app: ok('Xử lý'),
          chat: blocked('Chỉ liên kết'),
          mobile: blocked('Chỉ liên kết'),
          email: blocked('Chỉ liên kết'),
        },
        note: 'Cần so sánh hai nguồn',
      },
      {
        kind: 'review',
        label: 'Đánh giá kết quả (báo cáo)',
        cells: {
          app: ok('Xử lý'),
          chat: limited('Chấp nhận nhanh'),
          mobile: limited('Chấp nhận nhanh'),
          email: limited('Chỉ liên kết'),
        },
        note: 'Yêu cầu sửa phải mở ứng dụng',
      },
    ],
    principles: [
      {
        title: 'Đồng bộ trạng thái',
        body: 'Xử lý ở một kênh → mọi bản sao cập nhật trong vài giây; thẻ cũ không thể bấm lần hai.',
      },
      {
        title: 'Nhắc & chuyển',
        body: 'Nhắc sau 2 giờ; quá hạn chuyển người dự phòng; ngoài giờ làm việc chỉ gửi việc có hạn trong ngày.',
      },
      {
        title: 'Bảo mật',
        body: 'Không gửi PII, số tài khoản qua kênh ngoài; nội dung tối thiểu để quyết định; mọi thao tác ghi kiểm toán kèm kênh.',
      },
      {
        title: 'Chống làm phiền',
        body: 'Gộp nhiều yêu cầu cùng agent trong 15 phút thành một thẻ; người dùng chọn kênh theo loại ở Trung tâm thông báo.',
      },
    ],
  };
}

const PERIODS: OpsPeriod[] = ['24h', '7d', '30d'];

/*
 * Where A2 has the resource, the mock answers at its path in its shape (ModelConfigView,
 * ModelProviderView, ModelRouteDecisionView, PrefView) and sends the screen's richer row under
 * `display`, which the client prefers when present.
 */
const AT = '2026-10-01T09:00:00+07:00';

export const modelWire = (m: ModelConfig & Partial<ModelRoute>) => ({
  ...m,
  id: m.id ?? `model-${m.task}`,
  provider_id: 'prov-1',
  endpoint: 'http://llm.noibo.local',
  route_role: 'primary',
  rollout_pct: 100,
  version: 1,
  limits: {},
  fallback_model: m.fallback_name ?? null,
  latest_eval: null,
  eval_run_id: null,
  activated_at: AT,
  activated_by: null,
  created_at: AT,
  updated_at: AT,
});

const providerWire = (p: AiProvider): ProviderWire => ({
  id: p.id,
  code: p.id,
  display_name: p.name,
  kind: p.kind === 'local' ? 'on_prem' : 'frontier',
  api_style: 'openai_compatible',
  endpoint: `https://${p.endpoint_label}`,
  data_region: p.region_text,
  zero_retention: p.zero_retention === 'committed',
  max_classification: p.kind === 'local' ? 'restricted' : 'public',
  enabled: p.ac06_status === 'approved',
  approved_at: p.ac06_status === 'approved' ? AT : null,
  approved_by: null,
  contract_ref: null,
  monthly_budget_vnd: p.month_budget_vnd,
  monthly_cost_vnd: p.month_cost_vnd ?? 0,
  price: { [p.model_name]: {} },
  status: 'active',
  created_at: AT,
  updated_at: AT,
  display: p,
});

export const prefsWire = (p: NotificationPreferences) => ({
  items: p.events.map((e) => ({
    id: null,
    kind: e.key,
    channels: Object.entries(e.channels)
      .filter(([c, on]) => on && c !== 'digest')
      .map(([c]) => c),
    batch_mode: e.channels.digest ? 'daily' : 'instant',
    quiet_hours: null,
    created_at: null,
    updated_at: null,
  })),
  next_page_token: '',
  display: p,
});

export function createAdminPlatformHandlers() {
  let models = modelRoutesFixture();
  let integrations = integrationsFixture();
  let myClients = myClientsFixture();
  let preferences = notificationPreferencesFixture();
  let policy = aiPolicyFixture();
  let providers = providersFixture();
  const routingLog = routingLogFixture();

  return [
    // A2's SystemHealth (Prometheus series by section) with the designed snapshot under display.
    http.get('*/v1/admin/system/health', () =>
      HttpResponse.json({
        checked_at: new Date().toISOString(),
        status: 'ok',
        sections: {
          gpu: {
            series: Array.from({ length: 24 }, (_, hour) => ({
              labels: { at: `2026-10-09T${String(hour).padStart(2, '0')}:00:00+07:00` },
              value: [
                42, 40, 38, 37, 38, 40, 46, 55, 66, 74, 78, 80, 79, 77, 79, 83, 80, 72, 64, 60, 58,
                56, 55, 54,
              ][hour],
            })),
          },
        },
        display: systemHealthFixture(),
      }),
    ),

    http.get('*/v1/agent-ops/summary', ({ request }) => {
      const period = new URL(request.url).searchParams.get('period') as OpsPeriod;
      if (!PERIODS.includes(period)) return problem(422, 'validation_failed', 'Kỳ không hợp lệ.');
      return HttpResponse.json(agentOpsFixture(period));
    }),
    http.get('*/v1/admin/agent-usage', ({ request }) => {
      const url = new URL(request.url);
      const summary = agentOpsFixture('7d');
      const day = (label: string) => ({
        day: `2026-10-${label}`,
        runs: 41,
        needs_person: 2,
        errors: 1,
        error_rate: 0.02,
        cost: '600000',
      });
      return HttpResponse.json({
        from: url.searchParams.get('from'),
        to: url.searchParams.get('to'),
        granularity: 'day',
        totals: {
          runs: summary.runs,
          needs_person: summary.hitl_requests,
          errors: summary.errors,
          error_rate: summary.error_rate,
          cost: 4200000,
          avg_duration_s: 360,
        },
        days: ['07', '08', '09', '10', '11', '12', '13'].map(day),
        agents: summary.agents.map((a) => ({
          agent_id: a.agent_id,
          name: a.name,
          runs: a.runs,
          needs_person: a.hitl_requests,
          errors: 0,
          avg_duration_s: a.avg_duration_s,
          cost: a.cost,
          monthly_budget: '2000000',
          spent_this_month: a.cost,
          budget_used_ratio: 0.62,
        })),
        issues: summary.issues,
      });
    }),

    // Before the generic "/admin/models" handler of handlers.ts: this one serves the P11 fields.
    http.get('*/v1/admin/models', () => HttpResponse.json(models.map(modelWire))),
    http.post('*/v1/admin/models', async ({ request }) => {
      const body = (await request.json()) as NewModel;
      if (!body.name?.trim() || !/^https?:\/\//.test(body.endpoint ?? ''))
        return problem(422, 'validation_failed', 'Cần tên và địa chỉ http(s) của mô hình.');
      const added: ModelRoute = {
        id: `trial-${models.length + 1}`,
        task: body.task || 'Chưa định tuyến',
        model_name: body.name,
        status: 'evaluating',
        placement: null,
        fallback_name: null,
        latency_p95_ms: 0,
        eval_score: null,
        latest_eval_delta: null,
        previous: null,
        eval_suite_name: null,
      };
      models = [...models, added];
      return HttpResponse.json(added, { status: 201 });
    }),
    http.put('*/v1/admin/models/:task', async ({ params, request }) => {
      const body = (await request.json()) as { model_name: string; endpoint: string };
      const task = String(params.task);
      const taskLabel: Record<string, string> = {
        planning: 'Lập kế hoạch, điều phối agent',
        sql_generation: 'Sinh SQL',
        answer_writing: 'Soạn báo cáo',
      };
      const current = models.find((m) => m.task === task || m.task === taskLabel[task]);
      const isRollback = Boolean(current?.previous && body.model_name === current.previous.model_name);
      const updated: ModelRoute = {
        ...(current ?? {
          id: `trial-${models.length + 1}`,
          task,
          model_name: body.model_name,
          status: 'evaluating' as const,
          placement: null,
          fallback_name: null,
          latency_p95_ms: null,
          eval_score: null,
          latest_eval_delta: null,
          previous: null,
          eval_suite_name: null,
        }),
        model_name: body.model_name,
        status: isRollback ? 'active' : 'evaluating',
      };
      models = current
        ? models.map((m) => (m.task === task || m.task === taskLabel[task] ? updated : m))
        : [...models, updated];
      return HttpResponse.json({ version: 1 });
    }),
    http.post('*/v1/admin/models\\:evaluate', () =>
      HttpResponse.json({ eval_run_id: 'eval-mock' }, { status: 202 }),
    ),
    http.post('*/v1/admin/models/:action', ({ params }) => {
      // MSW reads ":rollback" as part of the segment: "<id>:rollback".
      const [id, verb] = String(params.action).split(':');
      if (verb !== 'rollback') return problem(404, 'not_found', 'Không có thao tác này.');
      const route = models.find((m) => m.id === id);
      if (!route) return problem(404, 'not_found', 'Không tìm thấy tác vụ.');
      if (!route.previous) return problem(409, 'invalid_state', 'Tác vụ này chưa đổi mô hình.');
      const back: ModelRoute = {
        ...route,
        model_name: route.previous.model_name,
        previous: {
          model_name: route.model_name,
          endpoint: route.previous.endpoint,
          cost_ratio: 1 / route.previous.cost_ratio,
        },
        latest_eval_delta: 0,
      };
      models = models.map((m) => (m.id === id ? back : m));
      return HttpResponse.json(back);
    }),
    http.get('*/v1/admin/ai-policy', () => HttpResponse.json(policy)),
    http.get('*/v1/admin/ai-settings', () =>
      HttpResponse.json([
        { key: 'max_cost_per_run', value: 50000 },
        { key: 'input_filter', value: 'Chặn yêu cầu lấy dữ liệu cá nhân hàng loạt' },
        { key: 'output_filter', value: 'Không trả số tài khoản, CCCD đầy đủ' },
        { key: 'prompt_log_retention_days', value: 30 },
        {
          key: 'frontier_routing',
          value: { enabled: policy.frontier_allowed, tasks: [], data_space_ids: [] },
        },
      ]),
    ),
    http.get('*/v1/admin/ai-settings', () =>
      HttpResponse.json([
        { key: 'max_cost_per_run', value: 50000 },
        { key: 'input_filter', value: 'Chặn yêu cầu lấy dữ liệu cá nhân hàng loạt' },
        { key: 'output_filter', value: 'Chặn gửi dữ liệu cá nhân ra ngoài' },
        { key: 'prompt_log_retention_days', value: 30 },
        {
          key: 'frontier_routing',
          value: {
            enabled: policy.frontier_allowed,
            tasks: policy.frontier.tasks,
            data_space_ids: policy.frontier.data_space_ids,
          },
        },
      ]),
    ),
    http.put('*/v1/admin/ai-settings/frontier_routing', async ({ request }) => {
      const body = (await request.json()) as {
        value?: { enabled?: boolean; tasks?: string[]; data_space_ids?: string[] };
      };
      policy = {
        ...policy,
        frontier_allowed: body.value?.enabled === true,
        frontier: {
          tasks: body.value?.tasks ?? [],
          data_space_ids: body.value?.data_space_ids ?? [],
        },
      };
      return HttpResponse.json({ key: 'frontier_routing', value: body.value ?? {} });
    }),
    http.put('*/v1/admin/ai-policy', async ({ request }) => {
      const body = (await request.json()) as AiPolicy;
      const approved = providers.some((p) => p.kind === 'frontier' && p.ac06_status === 'approved');
      if (body.frontier_allowed && !approved)
        return problem(409, 'invalid_state', 'Chưa có provider frontier nào được AC06 duyệt.');
      policy = { ...policy, frontier_allowed: body.frontier_allowed };
      return HttpResponse.json(policy);
    }),

    http.get('*/v1/admin/model-providers', () => page(providers.map(providerWire))),
    http.post('*/v1/admin/model-providers', async ({ request }) => {
      const raw = (await request.json()) as {
        display_name: string;
        kind: 'on_prem' | 'frontier';
        endpoint: string;
        data_region: string | null;
        monthly_budget_vnd: number | null;
        price: Record<string, unknown>;
      };
      const body: NewProvider = {
        name: raw.display_name,
        kind: raw.kind === 'on_prem' ? 'local' : 'frontier',
        model_name: Object.keys(raw.price ?? {})[0] ?? '',
        endpoint: raw.endpoint,
        region: raw.data_region ?? '',
        month_budget_vnd: raw.monthly_budget_vnd,
      };
      if (
        !body.name?.trim() ||
        !body.model_name?.trim() ||
        !/^https?:\/\//.test(body.endpoint ?? '')
      )
        return problem(422, 'validation_failed', 'Cần tên, mô hình và địa chỉ http(s).');
      const local = body.kind === 'local';
      const added: AiProvider = {
        id: `prov-${providers.length + 1}`,
        name: body.name.trim(),
        kind: body.kind,
        model_name: body.model_name.trim(),
        model_note: local ? null : 'chưa được dùng',
        endpoint_label: new URL(body.endpoint).host,
        region_text: body.region.trim() || 'Vùng chưa xác nhận',
        zero_retention: local ? 'not_applicable' : 'verifying',
        ac06_status: local ? 'approved' : 'pending',
        month_cost_vnd: local ? null : 0,
        month_budget_vnd: local ? null : body.month_budget_vnd,
        budget_month: '2026-10',
      };
      providers = [...providers, added];
      return HttpResponse.json(providerWire(added), { status: 201 });
    }),
    // ModelProviderTestResult: one call through the provider with the model named in the body.
    http.post('*/v1/admin/model-providers/:action', ({ params }) => {
      // MSW reads ":test" as part of the segment: "<id>:test".
      const [id, verb] = String(params.action).split(':');
      if (verb !== 'test') return problem(404, 'not_found', 'Không có thao tác này.');
      const provider = providers.find((p) => p.id === id);
      if (!provider) return problem(404, 'not_found', 'Không tìm thấy provider.');
      const result = { provider_id: provider.id, model_name: provider.model_name };
      if (provider.zero_retention === 'verifying')
        return HttpResponse.json({
          ...result,
          outcome: 'error',
          latency_ms: 0,
          error: 'Endpoint chưa xác nhận vùng dữ liệu nên chưa nhận lời gọi.',
        });
      return HttpResponse.json({
        ...result,
        outcome: 'ok',
        latency_ms: provider.kind === 'local' ? 640 : 1280,
        error: null,
      });
    }),
    // The fixture's week whatever from/to asks; the screen's rows and totals ride along.
    http.get('*/v1/admin/model-route-decisions', ({ request }) => {
      const params = new URL(request.url).searchParams;
      const week = '2026-W41';
      const size = Number(params.get('page_size') ?? 50);
      const start = Number(params.get('page_token') || 0);
      const end = start + size;
      const API_DECISION = {
        frontier: 'frontier_allowed',
        blocked: 'frontier_blocked',
        local: 'on_prem_default',
      } as const;
      return HttpResponse.json({
        items: routingLog.slice(start, end).map((e) => ({
          id: e.id,
          decided_at: e.at,
          task: 'answer',
          decision: API_DECISION[e.decision],
          reasons: [e.reason],
          chosen_provider_id: 'prov-1',
          model_name: e.provider_name,
          caller_kind: 'user',
          display: e,
        })),
        next_page_token: end < routingLog.length ? String(end) : '',
        week,
        frontier_calls: 412,
        blocked_internal: 37,
        local_calls: 9860,
      });
    }),
    http.get('*/v1/admin/gpu-load', () => HttpResponse.json(gpuLoadFixture())),

    // PROPOSAL: GET /v1/admin/clients (A2 has the writes and the usage, not the list).
    http.get('*/v1/admin/clients', () => page(integrations)),
    http.get('*/v1/admin/clients/:id/usage', ({ params }) =>
      integrations.some((i) => i.id === params.id)
        ? page(usageRows(String(params.id)))
        : problem(404, 'not_found', 'Không tìm thấy client.'),
    ),
    http.post('*/v1/admin/clients', async ({ request }) => {
      const body = (await request.json()) as ApiClientCreate;
      if (!body.tools?.length)
        return problem(422, 'validation_failed', 'Chọn ít nhất một công cụ.');
      // Granted to someone else, the client waits for its owner (BR-48).
      const mine = body.owner_user_id === meFixture().id;
      const added = clientRow({
        id: `cli-${integrations.length + 1}`,
        name: body.name,
        kind: body.kind,
        owner_user_id: body.owner_user_id,
        tools: body.tools,
        data_space_ids: body.data_space_ids ?? [],
        expires_at: body.expires_at,
        status: mine ? 'active' : 'pending_owner',
      });
      integrations = [...integrations, added];
      const issued: ApiClientIssued = { ...added, client_secret: 'da_sk_live_9f3c1b7e2a' };
      return HttpResponse.json(issued, { status: 201 });
    }),
    http.post(/\/v1\/admin\/clients\/[^/:]+:(rotate-secret|revoke)$/, ({ request }) => {
      const [, id, verb] = /clients\/([^/:]+):([a-z-]+)$/.exec(new URL(request.url).pathname)!;
      const client = integrations.find((c) => c.id === id);
      if (!client) return problem(404, 'not_found', 'Không tìm thấy client.');
      if (verb === 'revoke') {
        const revoked = { ...client, status: 'revoked', revoked_at: new Date().toISOString() };
        integrations = integrations.map((c) => (c.id === id ? revoked : c));
        return HttpResponse.json(revoked);
      }
      return HttpResponse.json({ ...client, client_secret: 'da_sk_live_rotated_51d0' });
    }),
    http.post('*/v1/webhooks', async ({ request }) => {
      const body = (await request.json()) as WebhookCreate;
      const issued: WebhookIssued = {
        id: 'wh-1',
        ...body,
        status: 'active',
        consecutive_failures: 0,
        paused_reason: null,
        created_at: new Date().toISOString(),
        signing_key: 'whsec_4b8e0c2d',
      };
      return HttpResponse.json(issued, { status: 201 });
    }),
    http.get('*/v1/me/clients', () => page(myClients)),
    http.post(/\/v1\/me\/clients\/[^/:]+:confirm$/, ({ request }) => {
      const id = /clients\/([^/:]+):confirm$/.exec(new URL(request.url).pathname)![1];
      const client = myClients.find((c) => c.id === id);
      if (!client) return problem(404, 'not_found', 'No such client of yours.');
      if (client.status !== 'pending_owner')
        return problem(409, 'invalid_state', `The client is ${client.status}, not pending_owner.`);
      // The api compares instants; the mock sends updated_at back exactly as listed.
      if (request.headers.get('If-Match') !== `"${client.updated_at}"`)
        return problem(
          412,
          'precondition_failed',
          "If-Match must be the client's ETag as you saw it.",
        );
      const now = new Date().toISOString();
      const confirmed = { ...client, status: 'active', owner_confirmed_at: now, updated_at: now };
      myClients = myClients.map((c) => (c.id === id ? confirmed : c));
      return HttpResponse.json(confirmed, { headers: { ETag: `"${now}"` } });
    }),

    http.get('*/v1/me', () => HttpResponse.json(meFixture())),
    // PrefView rows (one per kind) with the screen's labels and locks under display.
    http.get('*/v1/me/notification-prefs', () => HttpResponse.json(prefsWire(preferences))),
    http.put('*/v1/me/notification-prefs', async ({ request }) => {
      const body = (await request.json()) as {
        prefs: { kind: string; channels: string[]; batch_mode: string }[];
      };
      // Locked channels stay on whatever the client sends.
      preferences = {
        ...preferences,
        events: preferences.events.map((event) => {
          const pref = body.prefs.find((p) => p.kind === event.key);
          if (!pref) return event;
          const channels = Object.fromEntries(
            Object.keys(event.channels).map((c) => [
              c,
              c === 'digest' ? pref.batch_mode === 'daily' : pref.channels.includes(c),
            ]),
          ) as typeof event.channels;
          return {
            ...event,
            channels: { ...channels, ...Object.fromEntries(event.locked.map((c) => [c, true])) },
          };
        }),
      };
      return HttpResponse.json(prefsWire(preferences));
    }),
    http.post('*/v1/me/notification-preferences\\:test', () =>
      HttpResponse.json({ channels: ['in_app', 'teams', 'email'] }),
    ),
    http.get('*/v1/channel-rules', () => HttpResponse.json(channelRulesFixture())),
  ];
}
