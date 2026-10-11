import { http, HttpResponse } from 'msw';
import type {
  AgentConfig,
  AgentDetail,
  AgentDraft,
  AgentMemory,
  AgentTemplate,
  AgentTrigger,
  ConfigInput,
  DraftRequest,
  MemoryPatch,
  NewMemory,
  TestRun,
} from '../lib/v1/agentBuilder';
import type { AgentDef } from '../lib/v1/types';
import { agentsFixture } from './fixtures';

/*
 * MSW handlers for agent authoring (lib/v1/agentBuilder.ts), with the data of the P13 frames:
 * "Tạo agent từ mô tả", "Agent · Cấu hình & trigger", "Kết quả chạy thử agent", "Agent · Bộ nhớ".
 * State lives in memory: a created agent, a saved version or a forgotten memory stays until reload.
 *
 * GET /v1/runs/:id answers only the test runs made here and falls through otherwise, so the run
 * handlers of handlers.ts keep the other runs; spread these handlers before those.
 */
const page = <T>(items: T[]) => HttpResponse.json({ items, next_page_token: '' });

const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    { type: `https://docs.dataagent/errors/${code}`, title: code, status, code, detail, trace_id: 'mock' },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

const OWNER = { id: 'user-hai-yen', name: 'Đỗ Hải Yến' };

const templatesFixture = (): AgentTemplate[] => [
  {
    ref: 'banking/metric-watch',
    title: 'Theo dõi chỉ số và giải thích biến động',
    pack: 'từ gói Ngân hàng',
    description:
      'Mỗi sáng thứ Hai, xem chỉ số theo chi nhánh. Nếu chi nhánh nào tăng hơn 10% so với tuần trước thì tìm nguyên nhân và gửi tóm tắt cho tôi qua Teams.',
  },
  {
    ref: 'banking/reconcile-filed',
    title: 'Đối soát với báo cáo đã nộp',
    pack: 'từ gói Ngân hàng',
    description:
      'Khi kỳ chốt chuyển sang “Đang chốt”, đối chiếu số trên kho với báo cáo đã nộp và báo cho tôi các dòng lệch vượt ngưỡng trọng yếu.',
  },
  {
    ref: 'general/weekly-brief',
    title: 'Soạn bản tin tuần',
    pack: 'chung',
    description: 'Mỗi chiều thứ Sáu, soạn bản tin tuần từ các chỉ số chính và gửi cho nhóm qua Teams.',
  },
];

/** The proposal of "P13 · Tạo agent từ mô tả" (2309:19992). */
function draftFixture(): AgentDraft {
  return {
    name: 'Agent Theo dõi quá hạn chi nhánh',
    code: 'OVERDUE_WATCH',
    kind: 'monitor',
    config: {
      goal: 'Phát hiện chi nhánh có nợ quá hạn 30+ tăng nhanh và giải thích vì sao',
      data_scope: { metrics: ['OVERDUE_30'] },
      data_scope_text: 'OVERDUE_30 (chưa chứng nhận) · theo chi nhánh · so tuần trước',
      skill_ids: ['skill-variance-v4', 'skill-period-compare-v3'],
      autonomy: 'propose_approve',
      checkpoints: [
        { id: 'cp-outside', text: 'Gửi cho người ngoài danh sách', enabled: true },
        { id: 'cp-cost', text: 'Chi phí một lượt vượt 30.000 đ', enabled: true },
      ],
      denied_actions: ['sửa dữ liệu gốc'],
      limits: { max_cost_per_run: 30000 },
      change_note: null,
      tools: [
        { tool: 'query', permission_level: 'read', requires_checkpoint: false },
        { tool: 'run_skill', permission_level: 'read', requires_checkpoint: false },
        { tool: 'notify', permission_level: 'send_external', requires_checkpoint: true },
      ],
    },
    triggers: [
      { kind: 'schedule', cron: '0 7 * * 1', event_type: null, condition: {}, enabled: true, description: 'Thứ Hai 07:00 hằng tuần' },
    ],
    lines: [
      { field: 'name', label: 'Tên', text: 'Agent Theo dõi quá hạn chi nhánh', flag: null },
      { field: 'goal', label: 'Mục tiêu', text: 'Phát hiện chi nhánh có nợ quá hạn 30+ tăng nhanh và giải thích vì sao', flag: null },
      { field: 'trigger', label: 'Khi nào chạy', text: 'Thứ Hai 07:00 hằng tuần', flag: null },
      { field: 'data', label: 'Dữ liệu', text: 'OVERDUE_30 (chưa chứng nhận) · theo chi nhánh · so tuần trước', flag: 'cần xem' },
      { field: 'skills', label: 'Kỹ năng', text: 'Phân rã biến động v4, So sánh kỳ v3', flag: null },
      { field: 'recipients', label: 'Gửi tới', text: 'Bạn và giám đốc chi nhánh liên quan · Teams', flag: null },
      { field: 'allowed', label: 'Được phép', text: 'Đọc dữ liệu, soạn tóm tắt, gửi tin nội bộ', flag: null },
      { field: 'checkpoints', label: 'Phải hỏi bạn khi', text: 'Gửi cho người ngoài danh sách; chi phí một lượt vượt 30.000 đ', flag: null },
    ],
    warning:
      'OVERDUE_30 chưa được chứng nhận: agent vẫn chạy được nhưng tin gửi sẽ ghi “số chưa chứng nhận”. Có thể nhờ Phạm Đức Long chứng nhận trước.',
  };
}

/** "P13 · Agent · Cấu hình & trigger" (2309:20146): Agent Đối soát at v5, saving makes v6. */
function reconcileSetup(agent: AgentDef): Pick<AgentDetail, 'config' | 'triggers'> {
  return {
    config: {
      id: 'cfg-reconcile-5',
      agent_id: agent.id,
      version: 5,
      goal: agent.goal,
      data_scope: { tables: ['mart.credit_risk'], sources: ['core-banking'] },
      data_scope_text: 'mart.credit_risk, Core banking (chỉ đọc)',
      skill_ids: [],
      autonomy: 'propose_approve',
      checkpoints: [
        { id: 'cp-filed', text: 'Ghi bản số liệu mới cho kỳ đã nộp (duyệt 2 bước)', enabled: true },
        { id: 'cp-material', text: 'Hai nguồn lệch vượt ngưỡng trọng yếu', enabled: true },
        { id: 'cp-ambiguous', text: 'Câu hỏi có hơn một cách hiểu hợp lệ', enabled: true },
        { id: 'cp-budget', text: 'Chi phí một lượt vượt 50.000 đ hoặc chạy quá 20 phút', enabled: true },
      ],
      denied_actions: ['sửa dữ liệu gốc', 'xem số tài khoản', 'gửi ra ngoài tổ chức'],
      limits: { max_cost_per_run: 50000, max_duration_min: 20 },
      change_note: null,
      status: 'deployed',
      tools: [
        { tool: 'query', permission_level: 'read', requires_checkpoint: false },
        { tool: 'run_skill', permission_level: 'read', requires_checkpoint: false },
        { tool: 'create_proposal', permission_level: 'write_derived', requires_checkpoint: true },
        { tool: 'notify', permission_level: 'send_external', requires_checkpoint: true },
      ],
      created_at: '2026-10-01T02:00:00Z',
    },
    triggers: [
      { id: 'trg-1', kind: 'schedule', cron: '0 7 * * 1', event_type: null, condition: {}, enabled: true, description: 'Thứ Hai 07:00' },
      { id: 'trg-2', kind: 'event', cron: null, event_type: 'period.closing', condition: {}, enabled: true, description: 'Kỳ chốt chuyển sang “Đang chốt”' },
      {
        id: 'trg-3',
        kind: 'event',
        cron: null,
        event_type: 'job.succeeded',
        condition: { job: 'ld_loans', drift_pct_gt: 0.1 },
        enabled: true,
        description: 'Job nạp ld_loans xong và lệch > 0,1% so với hôm qua',
      },
      { id: 'trg-4', kind: 'manual', cron: null, event_type: null, condition: {}, enabled: true, description: 'Người dùng giao việc từ phiên' },
    ],
  };
}

function genericSetup(agent: AgentDef): Pick<AgentDetail, 'config' | 'triggers'> {
  return {
    config: {
      id: `cfg-${agent.id}-3`,
      agent_id: agent.id,
      version: 3,
      goal: agent.goal,
      data_scope: {},
      data_scope_text: 'mart.credit_risk (chỉ đọc)',
      skill_ids: [],
      autonomy: 'propose_approve',
      checkpoints: [{ id: 'cp-ambiguous', text: 'Câu hỏi có hơn một cách hiểu hợp lệ', enabled: true }],
      denied_actions: ['sửa dữ liệu gốc'],
      limits: {},
      change_note: null,
      status: 'deployed',
      tools: [{ tool: 'query', permission_level: 'read', requires_checkpoint: false }],
      created_at: '2026-09-20T02:00:00Z',
    },
    triggers: [{ id: `trg-${agent.id}`, kind: 'schedule', cron: null, event_type: null, condition: {}, enabled: true, description: agent.trigger_text }],
  };
}

const atToday = (h: number, m: number) => {
  const d = new Date();
  d.setHours(h, m, 0, 0);
  return d.toISOString();
};
const daysAgo = (n: number) => new Date(Date.now() - n * 86_400_000).toISOString();

/** "P13 · Agent · Bộ nhớ" (2310:20345): what Agent Phân tích remembers. */
function memoriesFixture(): AgentMemory[] {
  const base = {
    agent_id: 'agent-analysis',
    user_id: null,
    use_count: 0,
    created_at: '2026-09-28T02:00:00Z',
    expiry: 'days_90' as const,
  };
  return [
    {
      ...base,
      id: 'mem-1',
      scope: 'data_space',
      kind: 'decision',
      content: 'Khi giải thích nợ xấu, tách riêng khoản tái cơ cấu chưa điều chỉnh',
      expiry: 'until_wrong_2',
      source_kind: 'hitl',
      status: 'active',
      use_count: 4,
      source_text: 'Đỗ Hải Yến đóng cảnh báo #A-2291 là báo nhầm, 08:05',
      scope_text: 'Data Space',
      last_used_at: atToday(10, 20),
    },
    {
      ...base,
      id: 'mem-2',
      scope: 'data_space',
      kind: 'domain_context',
      content: '“Miền Bắc” gồm DH Bắc Bộ, ĐB sông Hồng, Trung du',
      source_kind: 'hitl',
      status: 'active',
      use_count: 9,
      source_text: 'Phạm Đức Long sửa cách hiểu, 02/10',
      scope_text: 'Toàn tổ chức',
      last_used_at: daysAgo(1),
    },
    {
      ...base,
      id: 'mem-3',
      scope: 'user',
      user_id: OWNER.id,
      kind: 'preference',
      content: 'Báo cáo cho Ban điều hành: tối đa 3 ý, số làm tròn 2 chữ số',
      source_kind: 'manual',
      status: 'active',
      use_count: 2,
      source_text: 'Đỗ Hải Yến, 28/09',
      scope_text: 'Riêng Đỗ Hải Yến',
      last_used_at: new Date(2026, 9, 6, 9, 0).toISOString(),
    },
    {
      ...base,
      id: 'mem-4',
      scope: 'data_space',
      kind: 'preference',
      content: 'Kỳ so sánh mặc định là tháng trước, không phải cùng kỳ năm trước',
      source_kind: 'run',
      status: 'pending_review',
      source_text: 'Tự học từ 12 phiên',
      scope_text: 'Data Space',
      last_used_at: null,
    },
  ];
}

/** "v3.1 · Chi tiết · Kết quả chạy thử agent" (2072:4944), the drawer. */
function finishedTestRun(run: TestRun): TestRun {
  return {
    ...run,
    status: 'succeeded',
    cost: '9200',
    finished_at: new Date().toISOString(),
    test_steps: [
      { title: 'Tính NPL theo vùng tuần 40', detail: '7 vùng · 2,1 s', state: 'done' },
      { title: 'Phát hiện DH Bắc Bộ +0,34 điểm', detail: 'Vượt ngưỡng 0,3', state: 'done' },
      { title: 'Hỏi: có tính khoản vay tái cơ cấu?', detail: 'Người thử trả lời: có', state: 'asked' },
      { title: 'Tìm nguyên nhân · đối chiếu số đã nộp', detail: 'Khớp 100%', state: 'done' },
      { title: 'Soạn tóm tắt Teams (không gửi)', detail: '182 từ · 1 biểu đồ', state: 'done' },
    ],
    test_report: {
      completed: true,
      human_questions: 1,
      policy_violations: 0,
      eval_passed: 11,
      eval_total: 12,
      data_text: 'Trên dữ liệu tuần 40',
      preview: {
        label: 'Tin nhắn Teams sẽ gửi',
        text: 'NPL bán lẻ Duyên hải Bắc Bộ tăng 0,34 điểm trong tuần 40, chủ yếu từ vay tiêu dùng không TSBĐ. Số liệu khớp báo cáo đã nộp. Chi tiết trong phiên “Theo dõi tuần 40”.',
      },
      failures: [
        '1 tình huống không đạt: tuần có ngày nghỉ lễ, agent so với tuần trước thay vì cùng số ngày làm việc.',
      ],
    },
  };
}

export function createAgentBuilderHandlers() {
  const team = agentsFixture();
  const agents = new Map<string, AgentDetail>(
    team.map((agent) => [
      agent.id,
      { ...agent, ...(agent.id === 'agent-reconcile' ? reconcileSetup(agent) : genericSetup(agent)), owner: OWNER },
    ]),
  );
  let memories = memoriesFixture();
  const testRuns = new Map<string, TestRun>();
  let seq = 0;

  return [
    http.get('*/v1/agent-templates', () => page(templatesFixture())),

    http.post(/\/v1\/agents:draft-from-text$/, async ({ request }) => {
      const body = (await request.json()) as DraftRequest;
      if (!body.description?.trim())
        return problem(422, 'validation_failed', 'Hãy mô tả việc bạn muốn giao.');
      const draft = draftFixture();
      // The config agent re-reads corrections; the mock just takes them as written.
      const corrections = body.corrections ?? {};
      draft.lines = draft.lines.map((line) =>
        corrections[line.field] !== undefined ? { ...line, text: corrections[line.field]!, flag: null } : line,
      );
      if (corrections.name) draft.name = corrections.name;
      if (corrections.goal) draft.config.goal = corrections.goal;
      if (corrections.data !== undefined) draft.warning = null;
      return HttpResponse.json(draft);
    }),

    http.post('*/v1/agents', async ({ request }) => {
      const body = (await request.json()) as AgentDraft & { triggers: AgentDraft['triggers'] };
      if ([...agents.values()].some((a) => a.code === body.code))
        return problem(409, 'conflict', `Mã ${body.code} đã có agent khác dùng.`);
      const id = `agent-new-${++seq}`;
      const detail: AgentDetail = {
        id,
        data_space_id: team[0].data_space_id,
        code: body.code,
        name: body.name,
        kind: body.kind,
        status: 'draft',
        paused_reason: null,
        monthly_budget: null,
        spent_this_month: '0',
        runs_running: 0,
        runs_waiting_human: 0,
        goal: body.config.goal,
        now: { text: 'Chưa kích hoạt', tone: 'normal' },
        trigger_text: body.triggers.map((t) => t.description).join(' · '),
        permission_text: 'Đọc · đề xuất',
        runs_7d: { count: 0, needs_approval_pct: 0, errors: 0 },
        config: { ...body.config, id: `cfg-${id}-1`, agent_id: id, version: 1, status: 'draft', created_at: new Date().toISOString() },
        triggers: body.triggers.map((t, i) => ({ ...t, id: `trg-${id}-${i}` })),
        owner: OWNER,
      };
      agents.set(id, detail);
      return HttpResponse.json(detail, { status: 201 });
    }),

    http.get('*/v1/agents/:id', ({ params }) => {
      const agent = agents.get(String(params.id));
      return agent ? HttpResponse.json(agent) : problem(404, 'not_found', 'Không tìm thấy agent.');
    }),

    http.post('*/v1/agents/:id/configs', async ({ params, request }) => {
      const agent = agents.get(String(params.id));
      if (!agent) return problem(404, 'not_found', 'Không tìm thấy agent.');
      const body = (await request.json()) as ConfigInput;
      if (body.autonomy === 'act_then_report')
        return problem(
          422,
          'business_rule_violated',
          'Mức “Tự làm, báo sau” cần bộ đánh giá đạt từ 95% và chủ sở hữu Data Space duyệt.',
        );
      const config: AgentConfig = {
        ...agent.config,
        ...body,
        change_note: body.change_note ?? null,
        id: `cfg-${agent.id}-${agent.config.version + 1}`,
        version: agent.config.version + 1,
        status: 'draft',
        created_at: new Date().toISOString(),
      };
      agents.set(agent.id, { ...agent, config, goal: config.goal });
      return HttpResponse.json(config, { status: 201 });
    }),

    http.put('*/v1/agents/:id/triggers', async ({ params, request }) => {
      const agent = agents.get(String(params.id));
      if (!agent) return problem(404, 'not_found', 'Không tìm thấy agent.');
      const { triggers } = (await request.json()) as { triggers: AgentTrigger[] };
      agents.set(agent.id, { ...agent, triggers });
      return HttpResponse.json(triggers);
    }),

    http.post('*/v1/agents/:id/test-runs', async ({ params, request }) => {
      const agent = agents.get(String(params.id));
      if (!agent) return problem(404, 'not_found', 'Không tìm thấy agent.');
      const { config_version } = (await request.json()) as { config_version: number };
      const run: TestRun = {
        id: `test-run-${++seq}`,
        agent_id: agent.id,
        agent_name: agent.name,
        config_version,
        status: 'running',
        cost: '0',
        started_at: new Date().toISOString(),
        finished_at: null,
        is_test: true,
        test_steps: [],
        test_report: null,
      };
      testRuns.set(run.id, run);
      return HttpResponse.json(run, { status: 202, headers: { Location: `/v1/runs/${run.id}` } });
    }),

    // Test runs only; any other id falls through to the run handlers of handlers.ts.
    http.get('*/v1/runs/:id', ({ params }) => {
      const run = testRuns.get(String(params.id));
      if (!run) return undefined;
      const done = finishedTestRun(run);
      testRuns.set(run.id, done);
      return HttpResponse.json(done);
    }),

    http.post('*/v1/agents/:id/deployments', async ({ params, request }) => {
      const agent = agents.get(String(params.id));
      if (!agent) return problem(404, 'not_found', 'Không tìm thấy agent.');
      const { config_version, traffic_pct } = (await request.json()) as { config_version: number; traffic_pct: number };
      return HttpResponse.json(
        { id: `dep-${++seq}`, agent_id: agent.id, config_version, traffic_pct, status: 'pending_approval' },
        { status: 201 },
      );
    }),

    http.get('*/v1/agents/:id/memories', ({ params }) =>
      page(memories.filter((m) => m.agent_id === params.id || m.agent_id === null)),
    ),

    http.post('*/v1/agents/:id/memories', async ({ params, request }) => {
      const body = (await request.json()) as NewMemory;
      if (!body.content?.trim()) return problem(422, 'validation_failed', 'Ghi nhớ không được để trống.');
      // BR-76: no personal data; a long digit run stands in for an account or ID number.
      if (/\d{9,}/.test(body.content))
        return problem(422, 'business_rule_violated', 'Ghi nhớ không được chứa dữ liệu cá nhân như số tài khoản.');
      const memory: AgentMemory = {
        id: `mem-new-${++seq}`,
        agent_id: String(params.id),
        scope: body.scope,
        user_id: body.user_id ?? null,
        kind: body.kind,
        content: body.content.trim(),
        source_kind: 'manual',
        // A Data Space memory waits for the Data Space owner.
        status: body.scope === 'data_space' ? 'pending_review' : 'active',
        use_count: 0,
        created_at: new Date().toISOString(),
        source_text: `${OWNER.name}, vừa thêm`,
        scope_text: { agent: 'Agent này', user: `Riêng ${OWNER.name}`, data_space: 'Data Space' }[body.scope],
        last_used_at: null,
        expiry: body.expiry ?? 'days_90',
      };
      memories = [memory, ...memories];
      return HttpResponse.json(memory, { status: 201 });
    }),

    http.patch('*/v1/memories/:id', async ({ params, request }) => {
      const found = memories.find((m) => m.id === params.id);
      if (!found) return problem(404, 'not_found', 'Không tìm thấy ghi nhớ.');
      const body = (await request.json()) as MemoryPatch;
      if (!body.content?.trim()) return problem(422, 'validation_failed', 'Ghi nhớ không được để trống.');
      if (/\d{9,}/.test(body.content))
        return problem(422, 'business_rule_violated', 'Ghi nhớ không được chứa dữ liệu cá nhân như số tài khoản.');
      const updated: AgentMemory = {
        ...found,
        content: body.content.trim(),
        scope: body.scope,
        expiry: body.expiry,
        // Widening to the Data Space goes back to the owner, as a new one would.
        status: body.scope === 'data_space' && found.scope !== 'data_space' ? 'pending_review' : found.status,
        scope_text:
          body.scope === found.scope
            ? found.scope_text
            : { agent: 'Agent này', user: `Riêng ${OWNER.name}`, data_space: 'Data Space' }[body.scope],
      };
      memories = memories.map((m) => (m.id === found.id ? updated : m));
      return HttpResponse.json(updated);
    }),

    http.delete('*/v1/memories/:id', ({ params }) => {
      if (!memories.some((m) => m.id === params.id)) return problem(404, 'not_found', 'Không tìm thấy ghi nhớ.');
      memories = memories.filter((m) => m.id !== params.id);
      return new HttpResponse(null, { status: 204 });
    }),
  ];
}
