import { http, HttpResponse } from 'msw';
import type {
  AgentOpsSummary,
  AiPolicy,
  AiProvider,
  ChannelRules,
  GpuLoad,
  Integration,
  IntegrationUsage,
  Me,
  ModelRoute,
  NewIntegration,
  NewModel,
  NewProvider,
  NotificationPreferences,
  OpsPeriod,
  RoutingLogEntry,
  SystemHealth,
} from '../lib/v1/adminPlatform';

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
      previous: { model_name: 'LLM-L 32B', cost_ratio: 3.5 },
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
    points: load.map((utilization, hour) => ({
      at: `2026-10-09T${String(hour).padStart(2, '0')}:00:00+07:00`,
      utilization,
    })),
    peak: { at: '2026-10-09T15:00:00+07:00', utilization: 0.83 },
    failover_text: 'Khi vượt 90% trong 10 phút, tác vụ sinh SQL chuyển sang mô hình dự phòng.',
  };
}

export function integrationsFixture(): Integration[] {
  return [
    {
      id: 'cli-copilot',
      name: 'Copilot nội bộ',
      kind: 'mcp',
      owner_name: 'Ngô Thanh Bình',
      scopes: ['ask', 'get_metric', 'run_skill'],
      calls_7d: 12480,
      error_rate_7d: 0.004,
      expires_at: '2026-12-31T00:00:00+07:00',
      status: 'active',
    },
    {
      id: 'cli-excel',
      name: 'Excel add-in',
      kind: 'api',
      owner_name: 'Lê Minh Châu',
      scopes: ['get_metric', 'query'],
      calls_7d: 3204,
      error_rate_7d: 0.011,
      expires_at: '2027-03-31T00:00:00+07:00',
      status: 'active',
    },
    {
      id: 'cli-sbv',
      name: 'Hệ thống báo cáo NHNN',
      kind: 'api',
      owner_name: 'Trần Quốc An',
      scopes: ['get_frozen_values'],
      calls_7d: 62,
      error_rate_7d: 0,
      expires_at: '2027-06-30T00:00:00+07:00',
      status: 'active',
    },
    {
      id: 'cli-zalo',
      name: 'Bot Zalo OA',
      kind: 'webhook',
      owner_name: 'Pháp chế',
      scopes: ['hitl.*'],
      calls_7d: 840,
      error_rate_7d: 0.026,
      expires_at: '2026-11-15T00:00:00+07:00',
      status: 'expiring',
    },
    {
      id: 'cli-hackathon',
      name: 'Thử nghiệm Hackathon',
      kind: 'mcp',
      owner_name: 'Phạm Đức Long',
      scopes: ['ask'],
      calls_7d: 0,
      error_rate_7d: null,
      expires_at: '2026-10-01T00:00:00+07:00',
      status: 'expired',
    },
  ];
}

const usageFixture = (id: string): IntegrationUsage =>
  id === 'cli-copilot'
    ? {
        daily_calls: [1600, 1720, 1880, 1690, 2010, 1820, 1760],
        top_tools: [
          { name: 'ask', share: 0.71 },
          { name: 'get_metric', share: 0.24 },
        ],
        quota_per_day: 5000,
        quota_used_ratio: 0.41,
        blocked_by_permission: 52,
      }
    : {
        daily_calls: [0, 0, 0, 0, 0, 0, 0],
        top_tools: [],
        quota_per_day: 1000,
        quota_used_ratio: 0,
        blocked_by_permission: 0,
      };

export function meFixture(): Me {
  return {
    display_name: 'Đỗ Hải Yến',
    email: 'yen.dh@bank.vn',
    unit_text: 'Khối Quản lý rủi ro · Phòng Rủi ro tín dụng',
    role_text: 'Chuyên viên phân tích (nhóm GRP-RISK-ANALYST)',
    data_space_names: ['Rủi ro tín dụng', 'Kinh doanh bán lẻ'],
    locale_label: 'Tiếng Việt',
    format_text: '1.234,56 · ngày dd/mm/yyyy · giờ 24h',
    timezone: 'Asia/Ho_Chi_Minh',
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

export function createAdminPlatformHandlers() {
  let models = modelRoutesFixture();
  let integrations = integrationsFixture();
  let preferences = notificationPreferencesFixture();
  let policy = aiPolicyFixture();
  let providers = providersFixture();
  const routingLog = routingLogFixture();

  return [
    http.get('*/v1/admin/system-health', () => HttpResponse.json(systemHealthFixture())),

    http.get('*/v1/agent-ops/summary', ({ request }) => {
      const period = new URL(request.url).searchParams.get('period') as OpsPeriod;
      if (!PERIODS.includes(period)) return problem(422, 'validation_failed', 'Kỳ không hợp lệ.');
      return HttpResponse.json(agentOpsFixture(period));
    }),

    // Before the generic "/admin/models" handler of handlers.ts: this one serves the P11 fields.
    http.get('*/v1/admin/models', () => page(models)),
    http.post('*/v1/admin/models', async ({ request }) => {
      const body = (await request.json()) as NewModel;
      if (!body.name?.trim() || !/^https?:\/\//.test(body.endpoint ?? ''))
        return problem(422, 'validation_failed', 'Cần tên và địa chỉ http(s) của mô hình.');
      const added: ModelRoute = {
        id: `trial-${models.length + 1}`,
        task: body.trial_tasks || 'Chưa định tuyến',
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
        previous: { model_name: route.model_name, cost_ratio: 1 / route.previous.cost_ratio },
        latest_eval_delta: 0,
      };
      models = models.map((m) => (m.id === id ? back : m));
      return HttpResponse.json(back);
    }),
    http.get('*/v1/admin/ai-policy', () => HttpResponse.json(policy)),
    http.put('*/v1/admin/ai-policy', async ({ request }) => {
      const body = (await request.json()) as AiPolicy;
      const approved = providers.some((p) => p.kind === 'frontier' && p.ac06_status === 'approved');
      if (body.frontier_allowed && !approved)
        return problem(409, 'invalid_state', 'Chưa có provider frontier nào được AC06 duyệt.');
      policy = { ...policy, frontier_allowed: body.frontier_allowed };
      return HttpResponse.json(policy);
    }),

    http.get('*/v1/admin/providers', () => page(providers)),
    http.post('*/v1/admin/providers', async ({ request }) => {
      const body = (await request.json()) as NewProvider;
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
      return HttpResponse.json(added, { status: 201 });
    }),
    http.post('*/v1/admin/providers/:action', ({ params }) => {
      // MSW reads ":test" as part of the segment: "<id>:test".
      const [id, verb] = String(params.action).split(':');
      if (verb !== 'test') return problem(404, 'not_found', 'Không có thao tác này.');
      const provider = providers.find((p) => p.id === id);
      if (!provider) return problem(404, 'not_found', 'Không tìm thấy provider.');
      if (provider.zero_retention === 'verifying')
        return HttpResponse.json({
          ok: false,
          latency_ms: null,
          detail: 'Endpoint chưa xác nhận vùng dữ liệu nên chưa nhận lời gọi.',
        });
      return HttpResponse.json({
        ok: true,
        latency_ms: provider.kind === 'local' ? 640 : 1280,
        detail: null,
      });
    }),
    http.get('*/v1/admin/routing-log', ({ request }) => {
      const params = new URL(request.url).searchParams;
      const week = params.get('week') ?? '2026-W41';
      if (week !== '2026-W41') return problem(404, 'not_found', 'Chưa có nhật ký của tuần này.');
      const size = Number(params.get('page_size') ?? 50);
      const start = Number(params.get('page_token') || 0);
      const end = start + size;
      return HttpResponse.json({
        items: routingLog.slice(start, end),
        next_page_token: end < routingLog.length ? String(end) : '',
        week,
        frontier_calls: 412,
        blocked_internal: 37,
        local_calls: 9860,
      });
    }),
    http.get('*/v1/admin/gpu-load', () => HttpResponse.json(gpuLoadFixture())),

    http.get('*/v1/admin/integrations', () => page(integrations)),
    http.get('*/v1/admin/integrations/:id/usage', ({ params }) =>
      integrations.some((i) => i.id === params.id)
        ? HttpResponse.json(usageFixture(String(params.id)))
        : problem(404, 'not_found', 'Không tìm thấy client.'),
    ),
    http.post('*/v1/admin/integrations', async ({ request }) => {
      const body = (await request.json()) as NewIntegration;
      let host: string;
      try {
        host = new URL(body.endpoint).host;
      } catch {
        return problem(422, 'validation_failed', 'Địa chỉ không hợp lệ.');
      }
      const added: Integration = {
        id: `cli-${integrations.length + 1}`,
        name: host,
        kind: body.kind,
        owner_name: 'Đỗ Hải Yến',
        scopes: body.tools,
        calls_7d: 0,
        error_rate_7d: null,
        expires_at: '2027-10-10T00:00:00+07:00',
        status: 'active',
      };
      integrations = [...integrations, added];
      return HttpResponse.json(added, { status: 201 });
    }),

    http.get('*/v1/me', () => HttpResponse.json(meFixture())),
    http.get('*/v1/me/notification-preferences', () => HttpResponse.json(preferences)),
    http.put('*/v1/me/notification-preferences', async ({ request }) => {
      const body = (await request.json()) as NotificationPreferences;
      // Locked channels stay on whatever the client sends.
      preferences = {
        ...body,
        events: body.events.map((event) => ({
          ...event,
          channels: {
            ...event.channels,
            ...Object.fromEntries(event.locked.map((channel) => [channel, true])),
          },
        })),
      };
      return HttpResponse.json(preferences);
    }),
    http.post('*/v1/me/notification-preferences\\:test', () =>
      HttpResponse.json({ channels: ['in_app', 'teams', 'email'] }),
    ),
    http.get('*/v1/channel-rules', () => HttpResponse.json(channelRulesFixture())),
  ];
}
