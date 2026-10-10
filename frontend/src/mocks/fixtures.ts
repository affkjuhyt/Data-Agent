import type {
  AgentDef,
  BehaviorPolicy,
  AgentRun,
  AppUser,
  DataSpace,
  Dataset,
  GlossaryTerm,
  HitlRequest,
  Metric,
  ModelConfig,
  Source,
  Workspace,
} from '../lib/v1/types';

/*
 * Sample data for the v1 mock, taken from the Figma screens (banking is the example domain of
 * the design). Times are relative to when the mock starts, so "còn 1 giờ 04 phút" stays true.
 * Ids are fixed strings so links and tests are stable.
 */
const minutesFromNow = (m: number) => new Date(Date.now() + m * 60_000).toISOString();
const DS = 'ds-rui-ro-tin-dung';

const agent = (id: string, name: string) => ({ id, name });

export function inboxFixture(): HitlRequest[] {
  const base = {
    data_space_id: DS,
    source_service: 'agent',
    assignee_id: 'me',
    status: 'open' as const,
    resolved_at: null,
  };
  return [
    {
      ...base,
      id: 'hitl-npl-definition',
      kind: 'clarification',
      risk: 'medium',
      title: '“Nợ xấu” nên tính theo định nghĩa nào?',
      context: {
        why: 'hai định nghĩa hợp lệ cho kết quả lệch 0,3 điểm; agent cần biết để viết kết luận.',
      },
      options: [
        {
          id: 'npl-v4',
          label: 'Nhóm 3–5 · NPL_RATIO v4',
          consequence: 'Khớp số đã nộp; tăng 0,22 điểm',
          is_default: true,
        },
        {
          id: 'npl-internal',
          label: 'Nhóm 2–5 · nội bộ',
          consequence: 'Cảnh báo sớm hơn; tăng 0,52 điểm',
        },
      ],
      source_ref: 'run-418',
      due_at: minutesFromNow(64),
      created_at: minutesFromNow(-6),
      agent: agent('agent-analysis', 'Agent Phân tích'),
    },
    {
      ...base,
      id: 'hitl-adjust-214',
      kind: 'approve_proposal',
      risk: 'high',
      title: 'Điều chỉnh nợ quá hạn của 214 khoản vay',
      context: {
        why: 'số nội bộ thấp hơn số đã nộp 0,12%; agent đã tìm ra nguyên nhân và đề xuất bản số liệu mới.',
      },
      options: [],
      source_ref: 'proposal-214',
      due_at: minutesFromNow(5 * 60),
      created_at: minutesFromNow(-40),
      agent: agent('agent-reconcile', 'Agent Đối soát'),
    },
    {
      ...base,
      id: 'hitl-core-vs-dwh',
      kind: 'conflict',
      risk: 'high',
      title: 'Core banking và DWH lệch 1,8 tỷ ở chi nhánh Hà Nội',
      context: {
        why: 'hai nguồn cùng thẩm quyền cho số khác nhau; agent không được tự chọn.',
      },
      options: [
        { id: 'core', label: 'Dùng Core banking', consequence: 'Theo số ghi sổ cuối ngày' },
        { id: 'dwh', label: 'Dùng DWH', consequence: 'Theo số đã tổng hợp cho báo cáo' },
      ],
      source_ref: 'conflict-hn-0930',
      due_at: minutesFromNow(6 * 60),
      created_at: minutesFromNow(-90),
      agent: agent('agent-dq', 'Agent Chất lượng dữ liệu'),
    },
    {
      ...base,
      id: 'hitl-report-sept',
      kind: 'report_publish',
      risk: 'medium',
      title: 'Báo cáo rủi ro tháng 9 đã soạn xong',
      context: {
        why: 'còn 1 nhận định độ tin thấp; báo cáo chỉ phát hành sau khi bạn xem.',
      },
      options: [],
      source_ref: 'report-risk-sept',
      due_at: minutesFromNow(9 * 60),
      created_at: minutesFromNow(-15),
      agent: agent('agent-report', 'Agent Báo cáo'),
    },
  ];
}

const sevenDays = (count: number, needs_approval_pct = 0, errors = 0) => ({
  count,
  needs_approval_pct,
  errors,
});

export function agentsFixture(): AgentDef[] {
  const base = {
    data_space_id: DS,
    paused_reason: null,
    monthly_budget: '2000000',
    runs_running: 0,
    runs_waiting_human: 0,
  };
  return [
    {
      ...base,
      id: 'agent-reconcile',
      code: 'RECONCILE',
      name: 'Agent Đối soát',
      kind: 'reconcile',
      status: 'running',
      spent_this_month: '412000',
      runs_running: 1,
      goal: 'So số nội bộ với báo cáo đã nộp',
      now: { text: 'Đang so dư nợ T9 · còn ~2 phút', tone: 'normal' },
      trigger_text: 'Khi khoá sổ · thứ Hai 07:00',
      permission_text: 'Đọc · đề xuất',
      runs_7d: sevenDays(12, 58),
    },
    {
      ...base,
      id: 'agent-analysis',
      code: 'ANALYSIS',
      name: 'Agent Phân tích',
      kind: 'analysis',
      status: 'running',
      spent_this_month: '386000',
      runs_waiting_human: 1,
      goal: 'Điều tra biến động khi có cảnh báo',
      now: { text: 'Chờ bạn chọn định nghĩa nợ xấu', tone: 'waiting' },
      trigger_text: 'Khi có cảnh báo',
      permission_text: 'Đọc · đề xuất',
      runs_7d: sevenDays(31),
    },
    {
      ...base,
      id: 'agent-risk',
      code: 'RISK_WATCH',
      name: 'Agent Giám sát rủi ro',
      kind: 'monitor',
      status: 'running',
      spent_this_month: '158000',
      runs_running: 1,
      goal: 'Theo dõi 14 chỉ số rủi ro',
      now: { text: 'Kiểm tra lúc 11:00', tone: 'normal' },
      trigger_text: 'Mỗi giờ',
      permission_text: 'Đọc',
      runs_7d: sevenDays(168),
    },
    {
      ...base,
      id: 'agent-dq',
      code: 'DATA_QUALITY',
      name: 'Agent Chất lượng dữ liệu',
      kind: 'data_quality',
      status: 'running',
      spent_this_month: '97000',
      runs_waiting_human: 1,
      goal: 'Kiểm tra dữ liệu sau mỗi lần nạp',
      now: { text: 'Dừng: không kết nối DWH bán lẻ', tone: 'error' },
      trigger_text: 'Sau mỗi job nạp',
      permission_text: 'Đọc',
      runs_7d: sevenDays(30, 0, 1),
    },
    {
      ...base,
      id: 'agent-report',
      code: 'REPORT',
      name: 'Agent Báo cáo',
      kind: 'report',
      status: 'running',
      spent_this_month: '64000',
      goal: 'Soạn báo cáo và bản tin định kỳ',
      now: { text: 'Chờ lịch thứ Hai', tone: 'normal' },
      trigger_text: 'Thứ Hai 06:00',
      permission_text: 'Đọc · soạn · gửi (cần duyệt)',
      runs_7d: sevenDays(2),
    },
  ];
}

export function runsFixture(): AgentRun[] {
  const run = (
    id: string,
    agent_id: string,
    agent_name: string,
    status: AgentRun['status'],
    text: string,
    current_step: number,
    total_steps: number,
    eta_text: string | null,
  ): AgentRun => ({
    id,
    agent_id,
    agent_name,
    status,
    trigger_kind: 'event',
    current_step,
    total_steps,
    eta_text,
    narrative: [{ at: minutesFromNow(-2), text }],
    cost: '4200',
    waiting_since: status === 'waiting_human' ? minutesFromNow(-6) : null,
    started_at: minutesFromNow(-12),
    finished_at: null,
    error: null,
  });
  return [
    run(
      'run-427',
      'agent-reconcile',
      'Agent Đối soát',
      'running',
      'Đang so dư nợ tháng 9 với báo cáo đã nộp',
      4,
      6,
      'còn khoảng 2 phút',
    ),
    run(
      'run-418',
      'agent-analysis',
      'Agent Phân tích',
      'waiting_human',
      'Đang chờ bạn chọn định nghĩa “nợ xấu” để viết kết luận',
      2,
      5,
      null,
    ),
    run(
      'run-431',
      'agent-report',
      'Agent Báo cáo',
      'running',
      'Đang soạn báo cáo rủi ro tháng 9 · 9/12 trang',
      9,
      12,
      'xong trước 11:00',
    ),
  ];
}

export function workspacesFixture(): Workspace[] {
  return [
    {
      id: 'ws-npl-sept',
      title: 'Điều tra nợ xấu bán lẻ T9',
      description: null,
      created_from: 'blank',
      tile_count: 14,
      last_refreshed_at: minutesFromNow(-30),
      status: 'active',
      updated_at: minutesFromNow(-10),
      participants: { people: 3, agents: 2 },
      agent_state: 'waiting_you',
    },
    {
      id: 'ws-reconcile-sept',
      title: 'Đối soát dư nợ tháng 9',
      description: null,
      created_from: 'conversation',
      tile_count: 4,
      last_refreshed_at: minutesFromNow(-5),
      status: 'active',
      updated_at: minutesFromNow(-5),
      participants: { people: 1, agents: 1 },
      agent_state: 'running',
    },
    {
      id: 'ws-cashflow',
      title: 'Dự báo dòng tiền 13 tuần',
      description: null,
      created_from: 'blank',
      tile_count: 9,
      last_refreshed_at: minutesFromNow(-3 * 24 * 60),
      status: 'active',
      updated_at: minutesFromNow(-3 * 24 * 60),
      participants: { people: 1, agents: 0 },
      agent_state: null,
    },
  ];
}

export function sourcesFixture(): Source[] {
  const ok = (id: string, name: string, kind: string, refreshedMinutesAgo: number): Source => ({
    id,
    name,
    kind,
    mode: 'federated',
    status: 'active',
    status_reason: null,
    last_checked_at: minutesFromNow(-5),
    last_refreshed_at: minutesFromNow(-refreshedMinutesAgo),
    late_by_minutes: null,
  });
  return [
    ok('src-core', 'Core banking', 'oracle', 4 * 60),
    ok('src-gl', 'Sổ cái GL', 'sqlserver', 4.5 * 60),
    {
      id: 'src-dwh-retail',
      name: 'DWH bán lẻ',
      kind: 'sqlserver',
      mode: 'federated',
      status: 'needs_fix',
      status_reason:
        'Không đăng nhập được từ 02:10. Thường do mật khẩu tài khoản kỹ thuật đã hết hạn.',
      last_checked_at: minutesFromNow(-3),
      last_refreshed_at: minutesFromNow(-8 * 60),
      late_by_minutes: 8 * 60,
    },
    {
      id: 'src-credit-files',
      name: 'Hồ sơ tín dụng',
      kind: 'file',
      mode: 'ingest',
      status: 'testing',
      status_reason: '312 tệp đang đọc',
      last_checked_at: minutesFromNow(-1),
      last_refreshed_at: null,
      late_by_minutes: null,
    },
    ...[
      'Thẻ tín dụng',
      'LOS',
      'Tổng đài',
      'Hộp thư nghiệp vụ',
      'Tỷ giá NHNN',
      'Danh mục chi nhánh',
      'CRM',
      'Kho văn bản',
    ].map((name, i) => ok(`src-${i}`, name, 'postgresql', 60 + i * 10)),
  ];
}

export function datasetsFixture(): Dataset[] {
  return [
    'Rủi ro tín dụng bán lẻ',
    'Dư nợ & huy động',
    'Thu hồi nợ',
    'Thẻ tín dụng',
    'Khiếu nại khách hàng',
    'Hiệu quả chi nhánh',
  ].map((display_name, i) => ({
    id: `dataset-${i}`,
    display_name,
    enabled: true,
    certified: true,
  }));
}

export function metricsFixture(): Metric[] {
  return Array.from({ length: 148 }, (_, i) => ({
    id: `metric-${i}`,
    code: i === 0 ? 'NPL_RATIO' : i === 1 ? 'LOAN_BALANCE' : `METRIC_${i}`,
    status: 'active' as const,
    current_version: 1,
  }));
}

export function termsFixture(): GlossaryTerm[] {
  return Array.from({ length: 612 }, (_, i) => ({ id: `term-${i}`, term: `Thuật ngữ ${i}` }));
}

export function dataSpacesFixture(): DataSpace[] {
  return [
    {
      id: DS,
      code: 'RUI_RO_TIN_DUNG',
      name: 'Rủi ro tín dụng',
      status: 'active',
      my_roles: ['analyst', 'steward'],
    },
    { id: 'ds-ban-le', code: 'BAN_LE', name: 'Bán lẻ', status: 'active', my_roles: ['analyst'] },
  ];
}

export function usersFixture(): AppUser[] {
  const people = Array.from({ length: 1284 }, (_, i) => ({
    id: `user-${i}`,
    display_name: `Người dùng ${i}`,
    kind: 'person' as const,
    status: 'active',
  }));
  const agents = Array.from({ length: 8 }, (_, i) => ({
    id: `agent-user-${i}`,
    display_name: `Agent ${i}`,
    kind: 'agent' as const,
    status: 'active',
  }));
  return [...people, ...agents];
}

export function modelsFixture(): ModelConfig[] {
  return [
    'Hiểu câu hỏi',
    'Viết SQL',
    'Soạn báo cáo',
    'Tóm tắt',
    'Trích xuất tài liệu',
    'Nhúng văn bản',
  ].map((task) => ({
    task,
    model_name: 'qwen3-32b',
    status: 'active',
    latest_eval_delta: task === 'Soạn báo cáo' ? -4 : 0,
  }));
}

export function myRulesFixture(): BehaviorPolicy[] {
  const rule = (id: string, description: string, applied_this_week: number): BehaviorPolicy => ({
    id,
    scope: 'user',
    kind: 'auto_handle',
    description,
    origin: 'hitl',
    status: 'active',
    applied_this_week,
  });
  return [
    rule('rule-core-wins', 'Lệch < 0,05% ngày chốt → dùng Core banking', 12),
    rule('rule-code-table', 'Kỳ chưa nộp → tự cập nhật bảng mã', 6),
    rule('rule-weekly-brief', 'Bản tin tuần → gửi nội bộ không cần duyệt', 5),
  ];
}
