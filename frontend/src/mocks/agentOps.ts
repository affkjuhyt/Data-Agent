import { http, HttpResponse } from 'msw';
import type { EvalCase, NewEvalCase, Orchestration, Rollout } from '../lib/v1/agentOps';

/*
 * MSW handlers for lib/v1/agentOps.ts (PROPOSAL endpoints). Data from "P13 · Đánh giá & triển
 * khai" (2311:20138) and "P13 · Điều phối nhiều agent" (2311:20297); state lives in memory so
 * widening, rolling back and pausing show on the next read.
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

const minutesAgo = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

export function rolloutsFixture(): Rollout[] {
  return [
    {
      agent_id: 'agent-reconcile',
      agent_name: 'Agent Đối soát',
      baseline_version: 5,
      candidate_version: 6,
      candidate_share_pct: 20,
      next_share_pct: 100,
      previous_share_pct: 0,
      status: 'canary',
      change_summary: 'v6 thêm trigger theo job nạp và điều kiện dừng chi phí',
      eval_set: {
        id: 'eval-reconcile',
        case_count: 40,
        criteria: [
          {
            key: 'correct',
            label: 'Kết luận đúng',
            unit: 'percent',
            baseline: 95,
            candidate: 97.5,
            threshold: { op: 'gte', value: 95 },
          },
          {
            key: 'ask_on_time',
            label: 'Dừng hỏi đúng lúc',
            unit: 'percent',
            baseline: 100,
            candidate: 100,
            threshold: { op: 'eq', value: 100 },
          },
          {
            key: 'permission',
            label: 'Không dùng dữ liệu ngoài quyền',
            unit: 'percent',
            baseline: 100,
            candidate: 100,
            threshold: { op: 'eq', value: 100 },
          },
          {
            key: 'cost',
            label: 'Chi phí trung bình',
            unit: 'vnd',
            baseline: 9200,
            candidate: 8400,
            threshold: { op: 'lte', value: 15000 },
          },
          {
            key: 'latency',
            label: 'Thời gian trung bình',
            unit: 'minutes',
            baseline: 7,
            candidate: 6,
            threshold: { op: 'lte', value: 20 },
          },
        ],
      },
      live: {
        window_days: 3,
        baseline: { runs: 12, needs_approval_pct: 58, errors: 0 },
        candidate: { runs: 3, needs_approval_pct: 33, errors: 0 },
      },
      auto_rollback_rules: [
        'Tỷ lệ lỗi v6 cao hơn v5 quá 2 điểm trong 24 giờ',
        'Người dùng báo sai kết luận của v6 hai lần',
        'Chi phí trung bình v6 vượt 15.000 đ',
      ],
      updated_at: minutesAgo(60 * 24 * 3),
    },
  ];
}

export function orchestrationsFixture(): Orchestration[] {
  const by = 'Agent Phân tích';
  return [
    {
      id: 'run-4480',
      code: 'R-4480',
      title: 'Điều tra nợ xấu bán lẻ T9',
      status: 'running',
      lead_agent_name: by,
      started_at: minutesAgo(11),
      started_from: 'cảnh báo #A-2291',
      tasks: [
        {
          run_id: 'run-4480',
          agent_id: 'agent-analysis',
          agent_name: by,
          delegated_by: null,
          task: 'Dẫn dắt: tìm vì sao nợ xấu DH Bắc Bộ tăng 0,61 điểm',
          result_text: 'Đang tổng hợp kết quả từ 3 agent',
          status: 'running',
          cost: '12400',
        },
        {
          run_id: 'run-4481',
          agent_id: 'agent-dq',
          agent_name: 'Agent Chất lượng dữ liệu',
          delegated_by: by,
          task: 'Kiểm tra dữ liệu DH Bắc Bộ có bất thường không',
          result_text: 'Không bất thường; DWH bán lẻ trễ 8 giờ nhưng không ảnh hưởng',
          status: 'succeeded',
          cost: '5600',
        },
        {
          run_id: 'run-4482',
          agent_id: 'agent-reconcile',
          agent_name: 'Agent Đối soát',
          delegated_by: by,
          task: 'So nhóm nợ trước/sau với số đã nộp',
          result_text: '19 khoản cơ cấu chuyển nhóm 2 → 3',
          status: 'succeeded',
          cost: '8400',
        },
        {
          run_id: 'run-4483',
          agent_id: 'agent-document',
          agent_name: 'Agent Tài liệu',
          delegated_by: by,
          task: 'Tìm văn bản quy định về khoản cơ cấu lần 2',
          result_text: 'Thấy QĐ 845/2026; cần người xác nhận áp dụng từ kỳ nào',
          status: 'waiting_human',
          cost: '4800',
        },
      ],
      rules: [
        'Agent con chạy theo quyền của người sở hữu agent dẫn dắt, không rộng hơn',
        'Tổng chi phí cả nhóm tối đa 100.000 đ',
        'Câu hỏi cần người được gom về một thẻ trong Cần bạn',
      ],
      cost: '31200',
    },
  ];
}

const FINISHED = new Set(['succeeded', 'failed', 'cancelled']);

export function createAgentOpsHandlers() {
  let rollouts = rolloutsFixture();
  let orchestrations = orchestrationsFixture();
  const cases: EvalCase[] = [];

  const save = (next: Rollout) => {
    rollouts = rollouts.map((r) => (r.agent_id === next.agent_id ? next : r));
    return HttpResponse.json(next);
  };

  return [
    http.get('*/v1/rollouts', ({ request }) => {
      const wanted = (new URL(request.url).searchParams.get('status') ?? '')
        .split(',')
        .filter(Boolean);
      return page(wanted.length ? rollouts.filter((r) => wanted.includes(r.status)) : rollouts);
    }),

    http.get('*/v1/agents/:id/rollout', ({ params }) => {
      const item = rollouts.find((r) => r.agent_id === params.id);
      return item
        ? HttpResponse.json(item)
        : problem(404, 'not_found', 'Agent này không có phiên bản nào đang thử.');
    }),

    http.post('*/v1/agents/:id/:action', async ({ params, request }) => {
      const item = rollouts.find((r) => r.agent_id === params.id);
      if (!item) return problem(404, 'not_found', 'Agent này không có phiên bản nào đang thử.');
      const now = new Date().toISOString();
      switch (params.action) {
        case 'rollout:set': {
          const { candidate_share_pct: pct } = (await request.json()) as {
            candidate_share_pct: number;
          };
          if (!Number.isInteger(pct) || pct < 0 || pct > 100)
            return problem(422, 'validation_failed', 'Tỷ lệ phải từ 0 đến 100.');
          return save({
            ...item,
            candidate_share_pct: pct,
            previous_share_pct: item.candidate_share_pct,
            next_share_pct: pct < 100 ? 100 : null,
            status: pct === 100 ? 'completed' : pct === 0 ? 'rolled_back' : 'canary',
            updated_at: now,
          });
        }
        case 'rollout:hold':
          return save({ ...item, status: 'held', updated_at: now });
        case 'rollout:rollback':
          if (item.status === 'rolled_back')
            return problem(409, 'invalid_state', 'Phiên bản này đã được lùi lại.');
          return save({
            ...item,
            candidate_share_pct: 0,
            previous_share_pct: item.candidate_share_pct,
            next_share_pct: null,
            status: 'rolled_back',
            updated_at: now,
          });
        default:
          return problem(404, 'not_found', 'Không có thao tác này.');
      }
    }),

    http.post('*/v1/eval-sets/:id/cases', async ({ params, request }) => {
      const body = (await request.json()) as NewEvalCase;
      if (!body.input?.trim() || !body.expected?.trim())
        return problem(422, 'validation_failed', 'Cần nhập đầu vào và kỳ vọng.');
      const created: EvalCase = {
        ...body,
        id: `case-${cases.length + 1}`,
        eval_set_id: String(params.id),
        created_at: new Date().toISOString(),
      };
      cases.push(created);
      rollouts = rollouts.map((r) =>
        r.eval_set.id === params.id
          ? { ...r, eval_set: { ...r.eval_set, case_count: r.eval_set.case_count + 1 } }
          : r,
      );
      return HttpResponse.json(created, { status: 201 });
    }),

    http.get('*/v1/orchestrations', ({ request }) => {
      const status = new URL(request.url).searchParams.get('status');
      return page(status ? orchestrations.filter((o) => o.status === status) : orchestrations);
    }),

    http.get('*/v1/orchestrations/:id', ({ params }) => {
      const item = orchestrations.find((o) => o.id === params.id);
      return item
        ? HttpResponse.json(item)
        : problem(404, 'not_found', 'Không tìm thấy lượt điều phối.');
    }),

    http.post('*/v1/orchestrations/:action', ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      const item = orchestrations.find((o) => o.id === id);
      if (!item) return problem(404, 'not_found', 'Không tìm thấy lượt điều phối.');
      if (verb !== 'pause' && verb !== 'resume')
        return problem(404, 'not_found', 'Không có thao tác này.');
      if (FINISHED.has(item.status))
        return problem(409, 'invalid_state', 'Lượt điều phối đã kết thúc.');
      const status = verb === 'pause' ? 'paused' : 'running';
      // Pausing touches only runs still going; resuming wakes only the ones it paused.
      const from = verb === 'pause' ? ['running', 'queued', 'planning'] : ['paused'];
      const next: Orchestration = {
        ...item,
        status,
        tasks: item.tasks.map((t) => (from.includes(t.status) ? { ...t, status } : t)),
      };
      orchestrations = orchestrations.map((o) => (o.id === id ? next : o));
      return HttpResponse.json(next);
    }),
  ];
}
