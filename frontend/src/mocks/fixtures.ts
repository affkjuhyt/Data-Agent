import type {
  AgentDef,
  BehaviorPolicy,
  ConflictContext,
  ProposalWire,
  AgentRun,
  AgentStep,
  AppUser,
  DataSpace,
  Dataset,
  GlossaryTerm,
  HitlRequest,
  Metric,
  ModelConfig,
  Source,
  WorkspaceWire,
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
    parent_run_label: null,
    assignee_id: 'me',
    assignee_group: null,
    status: 'open' as const,
    response: null,
    remember_as_rule: false,
    resolved_by: null,
    resolved_at: null,
    channel_card_ids: [],
    updated_at: minutesFromNow(-1),
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
      id: 'hitl-npl-v5',
      kind: 'approve_proposal',
      risk: 'high',
      title: 'NPL_RATIO v5 · tính khoản cơ cấu lần 2 vào nhóm 3',
      context: {
        why: 'văn bản QĐ 845/2026 có hiệu lực 01/09/2026; định nghĩa đổi làm nợ xấu T9 tăng 0,12 điểm.',
      },
      options: [],
      source_ref: 'pr-0318',
      due_at: minutesFromNow(3 * 24 * 60),
      created_at: minutesFromNow(-120),
      agent: agent('agent-analysis', 'Agent Phân tích'),
    },
    {
      ...base,
      id: 'hitl-core-vs-dwh',
      kind: 'conflict',
      risk: 'high',
      title: 'Core banking và DWH lệch 1,8 tỷ ở chi nhánh Hà Nội',
      context: conflictContext(),
      options: [
        {
          id: 'core',
          label: 'Dùng Core banking',
          consequence: 'agent đề xuất · số mới nhất của ngày',
          is_default: true,
        },
        { id: 'dwh', label: 'Dùng DWH bán lẻ', consequence: 'theo số đã tổng hợp cho báo cáo' },
        {
          id: 'both',
          label: 'Trình bày cả hai, mỗi số ghi rõ nguồn và thời điểm',
          consequence: '',
        },
        {
          id: 'investigate',
          label: 'Chưa đủ căn cứ · giao Kỹ sư dữ liệu kiểm tra 0,1 tỷ còn lại',
          consequence: '',
        },
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
  type Spec = {
    id: string;
    code: string;
    agent_id: string;
    agent_name: string;
    status: AgentRun['status'];
    title: string;
    initiated_label: string;
    trigger_kind: AgentRun['trigger_kind'];
    minutes: number;
    hitl: number;
    cost: number;
    now?: string;
    step?: [number, number];
    eta?: string;
    error?: string;
  };
  const run = (r: Spec): AgentRun => {
    const finished = ['succeeded', 'failed', 'cancelled'].includes(r.status);
    return {
      id: r.id,
      code: r.code,
      agent_id: r.agent_id,
      agent_name: r.agent_name,
      config_version: 5,
      status: r.status,
      title: r.title,
      initiated_label: r.initiated_label,
      trigger_text: r.initiated_label.toLowerCase(),
      trigger_kind: r.trigger_kind,
      current_step: r.step?.[0] ?? null,
      total_steps: r.step?.[1] ?? null,
      eta_text: r.eta ?? null,
      narrative: r.now ? [{ at: minutesFromNow(-2), text: r.now, state: 'now' }] : [],
      cost: String(r.cost),
      hitl_count: r.hitl,
      waiting_since: r.status === 'waiting_human' ? minutesFromNow(-6) : null,
      started_at: minutesFromNow(-r.minutes - (finished ? 60 : 0)),
      finished_at: finished ? minutesFromNow(-60) : null,
      error: r.error ?? null,
      stop_reason: r.status === 'failed' ? 'error' : r.status === 'succeeded' ? 'completed' : null,
    };
  };
  const runs = [
    run({
      id: 'run-427',
      code: '#427',
      agent_id: 'agent-reconcile',
      agent_name: 'Agent Đối soát',
      status: 'running',
      title: 'Đối soát dư nợ T9 với báo cáo đã nộp',
      initiated_label: 'Sự kiện kỳ chốt',
      trigger_kind: 'event',
      minutes: 6,
      hitl: 0,
      cost: 8400,
      now: 'Đang so dư nợ tháng 9 với báo cáo đã nộp',
      step: [4, 5],
      eta: 'còn khoảng 2 phút',
    }),
    run({
      id: 'run-418',
      code: '#418',
      agent_id: 'agent-analysis',
      agent_name: 'Agent Phân tích',
      status: 'waiting_human',
      title: 'Điều tra nợ xấu bán lẻ DH Bắc Bộ',
      initiated_label: 'Agent Giám sát rủi ro',
      trigger_kind: 'agent_call',
      minutes: 4,
      hitl: 1,
      cost: 41000,
      now: 'Đang chờ bạn chọn định nghĩa “nợ xấu” để viết kết luận',
      step: [3, 6],
    }),
    run({
      id: 'run-415',
      code: '#415',
      agent_id: 'agent-dq',
      agent_name: 'Agent Chất lượng dữ liệu',
      status: 'waiting_human',
      title: 'Kiểm tra dữ liệu sau ETL đêm',
      initiated_label: 'Sau ETL',
      trigger_kind: 'event',
      minutes: 2,
      hitl: 1,
      cost: 6000,
      now: 'Đang chờ bạn chọn nguồn cho số dư nợ nhóm 3–5',
      step: [3, 4],
    }),
    run({
      id: 'run-414',
      code: '#414',
      agent_id: 'agent-report',
      agent_name: 'Agent Báo cáo',
      status: 'running',
      title: 'Soạn báo cáo rủi ro tháng 9',
      initiated_label: 'Đỗ Hải Yến',
      trigger_kind: 'manual',
      minutes: 6,
      hitl: 0,
      cost: 52000,
      now: 'Đang soạn báo cáo rủi ro tháng 9 · 9/12 trang',
      step: [9, 12],
      eta: 'xong trước 11:00',
    }),
    run({
      id: 'run-412',
      code: '#412',
      agent_id: 'agent-docs',
      agent_name: 'Agent Tài liệu',
      status: 'succeeded',
      title: 'Đọc 3 văn bản mới của cơ quan quản lý',
      initiated_label: 'Văn bản mới',
      trigger_kind: 'event',
      minutes: 2,
      hitl: 0,
      cost: 11000,
    }),
    run({
      id: 'run-409',
      code: '#409',
      agent_id: 'agent-dq',
      agent_name: 'Agent Chất lượng dữ liệu',
      status: 'failed',
      title: 'Kiểm tra DWH bán lẻ sau ETL',
      initiated_label: 'Sau ETL',
      trigger_kind: 'event',
      minutes: 1,
      hitl: 0,
      cost: 1000,
      error: 'Lỗi kết nối',
    }),
    run({
      id: 'run-406',
      code: '#406',
      agent_id: 'agent-report',
      agent_name: 'Agent Báo cáo',
      status: 'succeeded',
      title: 'Bản tin rủi ro tuần 41',
      initiated_label: 'Lịch hằng tuần',
      trigger_kind: 'schedule',
      minutes: 3,
      hitl: 1,
      cost: 24000,
    }),
    run({
      id: 'run-401',
      code: '#401',
      agent_id: 'agent-prep',
      agent_name: 'Agent Chuẩn bị dữ liệu',
      status: 'succeeded',
      title: 'Ánh xạ nguồn thẻ tín dụng mới',
      initiated_label: 'Trần Quốc An',
      trigger_kind: 'manual',
      minutes: 9,
      hitl: 2,
      cost: 33000,
    }),
  ];
  const runStart = new Date(runs[0].started_at as string).getTime();
  const span = (
    seq: number,
    parent: number | null,
    kind: AgentStep['kind'],
    name: string,
    actor: string,
    startSec: number | null,
    seconds: number | null,
    metric: string | null,
  ): AgentStep => ({
    seq,
    kind,
    name,
    summary: null,
    status: 'ok',
    duration_ms: seconds === null ? null : seconds * 1000,
    parent_seq: parent,
    started_at: startSec === null ? null : new Date(runStart + startSec * 1000).toISOString(),
    actor,
    metric_text: metric,
    run_as_text: 'Đỗ Hải Yến',
    rows_text: null,
    sql: null,
  });
  // The run of "P13 · Lượt chạy · Đang làm gì" (2310:19989), told in full.
  runs[0] = {
    ...runs[0],
    trigger_text: 'do kỳ T9 chuyển sang “Đang chốt”',
    narrative: [
      {
        at: minutesFromNow(-6),
        text: 'Bắt đầu vì kỳ tháng 9 vừa chuyển sang đang chốt.',
        state: 'done',
      },
      {
        at: minutesFromNow(-4),
        text: 'Đọc 1,28 triệu khoản vay từ Core banking (dữ liệu 30/09).',
        state: 'done',
      },
      {
        at: minutesFromNow(-1),
        text: 'So với báo cáo đã nộp ngày 05/10: lệch 0,12% (25 tỷ).',
        state: 'done',
      },
      {
        at: minutesFromNow(0),
        text: 'Đang kiểm tra 214 khoản vay có ngày quá hạn khác nhau giữa hai nguồn…',
        state: 'now',
        progress: 0.6,
      },
      {
        at: minutesFromNow(2),
        text: 'Sẽ đề xuất điều chỉnh và hỏi bạn duyệt trước khi tạo bản số liệu mới.',
        state: 'next',
      },
    ],
    plan: [
      { title: 'Đọc dư nợ T9 từ Core banking', state: 'done' },
      { title: 'Lấy số đã nộp NHNN T9', state: 'done' },
      { title: 'So từng chỉ tiêu, khoanh vùng lệch', state: 'done' },
      { title: 'Kiểm tra 214 khoản lệch ngày quá hạn', state: 'running' },
      { title: 'Đề xuất điều chỉnh · cần bạn duyệt', state: 'waiting' },
    ],
    // The trace of "P13 · Lượt chạy · Kỹ thuật" (2310:20156): seconds from the run's start.
    steps: [
      span(1, null, 'plan', 'agent.plan', 'Qwen3-72B', 0, 9, '1.2k tok'),
      span(2, 1, 'query', 'tool.query core_banking', 'SQL', 9, 60, '1,28 tr dòng'),
      span(3, 1, 'query', 'tool.query submitted_report', 'SQL', 69, 6, '12 dòng'),
      span(4, 1, 'skill', 'skill.compare_periods v3', 'kỹ năng', 75, 45, null),
      span(5, 4, 'llm_call', 'llm.sql_gen', 'Qwen3-Coder', 78, 4.5, '840 tok'),
      span(6, 4, 'query', 'tool.query diff', 'SQL', 82.5, 33, '214 dòng'),
      span(7, 1, 'llm_call', 'llm.reason', 'Qwen3-72B', 120, 12, '3.1k tok'),
      {
        ...span(8, 1, 'query', 'tool.query overdue_check', 'SQL', 132, null, null),
        run_as_text: 'Đỗ Hải Yến · chính sách POL-002 áp dụng',
        rows_text: '214 khoản · 2 bảng',
        sql: [
          'SELECT l.loan_id, l.overdue_days, s.overdue_days',
          'FROM core.ld_loans l JOIN submitted.loans_t9 s',
          '  USING (loan_id)',
          'WHERE l.overdue_days <> s.overdue_days',
        ].join('\n'),
      },
      span(9, 1, 'checkpoint', 'hitl.create_proposal', 'chờ', null, null, null),
    ],
  };
  // The failed run of "v3.1 · Trạng thái · Lượt chạy lỗi" (2076:5030).
  const failed = runs.findIndex((r) => r.id === 'run-409');
  runs[failed] = {
    ...runs[failed],
    plan: [
      { title: 'Nhận sự kiện ETL xong', state: 'done', detail: '02:09' },
      {
        title: 'Kết nối DWH bán lẻ',
        state: 'error',
        detail: '3 lần thử theo chính sách',
        duration_text: '0:31',
      },
      { title: 'Kiểm tra 148 bảng', state: 'waiting', detail: 'Chưa chạy' },
    ],
    failure: {
      title: 'Không kết nối được DWH bán lẻ sau 3 lần thử',
      detail: 'ORA-12170: TNS connect timeout · 90 s · 02:10, 02:25, 02:40',
      safety: [
        'Không ghi hoặc đánh dấu dữ liệu nào là “đạt”',
        'Đánh dấu DWH bán lẻ là “trễ” cho 3 agent đang dùng',
        'Báo chủ sở hữu agent và quản trị nguồn qua Teams',
        'Tạm dừng, sẽ tự thử lại khi nguồn có tín hiệu',
      ],
      fallback_source: 'DWH bán lẻ (bản sao 01:00)',
    },
  };
  // The plan "P13 · Can thiệp kế hoạch" (2070:3221) edits: stopped at step 3, waiting on a person.
  const npl = runs.findIndex((r) => r.id === 'run-418');
  runs[npl] = {
    ...runs[npl],
    plan_version: 1,
    plan: [
      {
        id: 'ps-1',
        title: 'Phát hiện vượt ngưỡng',
        agent_name: 'Agent Giám sát rủi ro',
        state: 'done',
      },
      {
        id: 'ps-2',
        title: 'Kế hoạch, truy vấn tổng quan',
        agent_name: 'Agent Phân tích',
        state: 'done',
      },
      {
        id: 'ps-3',
        title: 'Phân rã vùng × sản phẩm',
        agent_name: 'Agent Phân tích',
        state: 'pending_approval',
      },
      {
        id: 'ps-4',
        title: 'Kết luận + bằng chứng',
        agent_name: 'Agent Phân tích',
        state: 'waiting',
      },
      { id: 'ps-5', title: 'Kiểm tra số', agent_name: 'Agent Đối soát', state: 'waiting' },
      { id: 'ps-6', title: 'Đưa vào bản tin tuần', agent_name: 'Agent Báo cáo', state: 'waiting' },
    ],
  };
  return runs;
}

/** The workspace columns the lists do not draw. */
const workspaceColumns = {
  data_space_id: DS,
  owner_id: 'u-yen',
  params: {},
  template_of_skill_id: null,
  created_from_ref: null,
  created_at: minutesFromNow(-7 * 24 * 60),
};

export function workspacesFixture(): WorkspaceWire[] {
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
      ...workspaceColumns,
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
      ...workspaceColumns,
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
      ...workspaceColumns,
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
    // NPL_RATIO shares its id with the detail mock (mocks/metrics.ts).
    id: i === 0 ? 'metric-npl-ratio' : `metric-${i}`,
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
      description: null,
      status: 'active',
      steward_id: 'u-steward',
      opened_at: '2026-09-01T08:00:00+07:00',
      my_roles: [
        { id: 'role-ac01', code: 'AC01', name: 'Chuyên viên phân tích' },
        { id: 'role-ac03', code: 'AC03', name: 'Người phụ trách dữ liệu' },
      ],
      my_capabilities: ['ask', 'steward', 'view_detail'],
    },
    {
      id: 'ds-ban-le',
      code: 'BAN_LE',
      name: 'Bán lẻ',
      description: null,
      status: 'active',
      steward_id: 'u-steward',
      opened_at: '2026-09-15T08:00:00+07:00',
      my_roles: [{ id: 'role-ac01', code: 'AC01', name: 'Chuyên viên phân tích' }],
      my_capabilities: ['ask'],
    },
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

function conflictContext(): ConflictContext {
  return {
    why: 'hai nguồn cùng thẩm quyền cho số khác nhau; agent không được tự chọn.',
    subtitle: 'Agent Chất lượng dữ liệu phát hiện lúc 08:10 · sau hạn chuyển cho Phạm Đức Long',
    sources: [
      {
        name: 'Core banking',
        authority: 'Cấp 1 · gốc',
        authority_tone: 'success',
        detail: 'Postgres · cập nhật 06:00 hôm nay',
        value: '2.296,4 tỷ',
        value_label: 'Dư nợ nhóm 3–5 bán lẻ · ngày 30/09',
        query: 'SUM(balance) WHERE debt_group ≥ 3 AND segment = retail AND snapshot = 30/09',
      },
      {
        name: 'DWH bán lẻ',
        authority: 'Cấp 1 · báo cáo',
        authority_tone: 'info',
        detail: 'Oracle · trễ 8 giờ · chốt 22:00 ngày 29/09',
        value: '2.298,2 tỷ',
        value_label: 'Dư nợ nhóm 3–5 bán lẻ · ngày 30/09',
        query: 'SUM(outstanding) FROM fact_loan_daily WHERE npl_flag = 1 AND as_of = 30/09',
      },
    ],
    gap: {
      headline: 'Chênh 1,8 tỷ (0,08%)',
      detail:
        'Vượt ngưỡng trọng yếu 0,05% · hai nguồn cùng thẩm quyền cấp 1 nên agent không tự chọn',
    },
    checks: [
      { mark: 'ok', text: 'Cùng định nghĩa: nhóm nợ 3–5, phân khúc bán lẻ, cùng đơn vị tỷ đồng' },
      { mark: 'ok', text: 'Phân loại: lệch hiệu lực — hai nguồn chụp ở hai thời điểm khác nhau' },
      {
        mark: 'warn',
        text: '12 khoản tất toán trong ngày 30/09 có ở Core banking, chưa vào DWH: giải thích 1,7 tỷ',
      },
      {
        mark: 'unknown',
        text: 'Còn 0,1 tỷ chưa rõ nguyên nhân · đã đính kèm 3 truy vấn làm bằng chứng',
      },
    ],
    impact_title: 'Ảnh hưởng nếu chọn Core banking',
    impact: [
      { title: '3 câu trả lời', detail: 'đã dùng số DWH sẽ được đánh dấu và báo người hỏi' },
      { title: '1 báo cáo', detail: 'Báo cáo rủi ro tháng 9 đang soạn sẽ dùng số mới' },
      { title: '2 agent', detail: 'Agent Báo cáo, Agent Đối soát chạy lại bước liên quan' },
    ],
    authority_note:
      'Core banking và DWH bán lẻ cùng cấp 1. Chưa có quy tắc nào cho trường hợp DWH trễ, nên việc này cần người quyết.',
    similar_note:
      '2 lần trong 30 ngày, cả hai lần chọn Core banking (Đỗ Hải Yến 12/09, Trần Quốc An 24/09). Nên lưu thành quy tắc.',
    rule_suggestion:
      'Số dư trong ngày lấy Core banking khi DWH trễ hơn 4 giờ (xem lại sau 90 ngày)',
  };
}

/** The proposal columns the screens do not draw; no eval run, so accepting needs no review. */
const proposalColumns = (targetService: string, targetRef: string) => ({
  data_space_id: DS,
  target_service: targetService,
  target_ref: targetRef,
  evidence: [],
  proposed_by: 'agent-analysis',
  assignee_id: 'me',
  eval_run_id: null,
  eval_run: null,
  changed_results: [],
  reviews: [],
  applied_at: null,
  created_at: minutesFromNow(-120),
  updated_at: minutesFromNow(-120),
});

export function proposalsFixture(): ProposalWire[] {
  return [
    {
      id: 'pr-0318',
      code: 'PR-0318',
      kind: 'metric_change',
      title: 'NPL_RATIO v5 · tính khoản cơ cấu lần 2 vào nhóm 3',
      diff: {
        before: { label: 'Bản đang dùng v4', text: 'debt_group IN (3,4,5)' },
        after: {
          label: 'Bản đề xuất v5',
          text: 'debt_group IN (3,4,5)\n  OR restructure_count >= 2',
        },
      },
      reason:
        'văn bản QĐ 845/2026 hiệu lực 01/09/2026 yêu cầu khoản cơ cấu lần 2 xếp tối thiểu nhóm 3.',
      impact: {
        window_label: 'chạy thử trên 6 kỳ',
        columns: ['v4', 'v5'],
        rows: [
          { period: 'T9/26', before: '1,84%', after: '1,96%', delta: '+0,12 điểm · 214 khoản' },
          { period: 'T8/26', before: '1,63%', after: '1,63%', delta: '0 · QĐ chưa hiệu lực' },
          { period: 'T7/26', before: '1,62%', after: '1,62%', delta: '0' },
        ],
        summary:
          '23 artifact và 5 agent dùng chỉ số này sẽ dùng v5 từ kỳ T9. Báo cáo NHNN T9 đã nộp theo v4: cần đối chiếu và đính chính nếu được duyệt.',
      },
      impact_level: 'high',
      status: 'pending_review',
      expires_at: minutesFromNow(3 * 24 * 60),
      requires_compliance: true,
      ...proposalColumns('metric', 'metric-npl-ratio'),
      checks: [
        { ok: true, text: 'Bộ câu hỏi chuẩn 96,8% (v4: 96,5%)' },
        { ok: true, text: 'Không làm hỏng chỉ số phụ thuộc' },
        { ok: false, text: 'Ảnh hưởng số đã nộp kỳ T9' },
      ],
      approval: {
        reason: 'Vì chạm số đã nộp',
        steps: [
          { name: 'Phạm Đức Long', role: 'chủ sở hữu chỉ số' },
          { name: 'Trần Quốc An', role: 'kiểm soát báo cáo' },
        ],
        note: 'Người đề xuất không tự duyệt',
      },
      proposed_by_label: 'Agent Phân tích từ phiên “Nợ xấu bán lẻ T9”',
    },
    {
      id: 'proposal-214',
      code: 'PR-0321',
      kind: 'writeback',
      title: 'Điều chỉnh nợ quá hạn của 214 khoản vay',
      diff: {
        before: { label: 'Số nội bộ hiện tại', text: 'overdue_days theo DWH bán lẻ (chốt 29/09)' },
        after: {
          label: 'Bản số liệu đề xuất',
          text: 'overdue_days theo Core banking (30/09)\n  cho 214 khoản lệch ngày quá hạn',
        },
      },
      reason:
        'số nội bộ thấp hơn số đã nộp 0,12%; 214 khoản có ngày quá hạn khác nhau giữa hai nguồn.',
      impact: {
        window_label: 'so với số đã nộp',
        columns: ['Hiện tại', 'Đề xuất'],
        rows: [
          {
            period: 'T9/26',
            before: '1,82%',
            after: '1,84%',
            delta: '+0,02 điểm · khớp số đã nộp',
          },
        ],
        summary:
          'Bản số liệu mới thay bản cũ trong kỳ T9; 4 báo cáo nội bộ đang mở sẽ được làm mới.',
      },
      impact_level: 'medium',
      status: 'pending_review',
      expires_at: minutesFromNow(5 * 60),
      requires_compliance: false,
      ...proposalColumns('dw', 'dataset-overdue-loans'),
      checks: [
        { ok: true, text: '214/214 khoản có bằng chứng từ Core banking' },
        { ok: true, text: 'Khớp số đã nộp ngày 05/10' },
      ],
      approval: {
        reason: 'Ghi đè số nội bộ của kỳ đã nộp',
        // The chain of "Cộng tác · Duyệt nhiều người" (2084:7085), mocks/collaboration.ts.
        steps: [
          { name: 'Nguyễn Thu Hà', role: 'người lập kiểm tra' },
          { name: 'Bạn', role: 'chủ sở hữu dữ liệu' },
          { name: 'Đỗ Hải Yến', role: 'kiểm soát vì kỳ đã nộp' },
        ],
        note: 'Người đề xuất không tự duyệt',
      },
      proposed_by_label: 'Agent Đối soát từ lượt chạy #427',
    },
  ];
}
