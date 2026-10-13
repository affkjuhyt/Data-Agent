import { http, HttpResponse } from 'msw';
import type { components } from '../lib/v1/schema';

type Rule = components['schemas']['BehaviorPolicyDetail'];

/*
 * Rules drafted from decisions, for "P07 · Quy tắc từ quyết định" (2302:19305), as A2 sends
 * them (BehaviorPolicyDetail). The handlers answer only the ids they hold and let every other
 * policy fall through to the access-policy mock, since A2 serves both kinds from /v1/policies.
 */
function decisionRulesFixture(): Rule[] {
  return [
    {
      id: 'rule-core-under-2b',
      type: 'behavior',
      scope: 'data_space',
      data_space_id: 'ds-rui-ro-tin-dung',
      subject_id: null,
      kind: 'auto_handle',
      description: 'Luôn chọn Core banking khi lệch dư nợ dưới 2 tỷ',
      condition: {
        text: "loại = 'dư nợ' VÀ nguồn IN (Core banking, Kho dữ liệu) VÀ |chênh lệch| < 2 tỷ VÀ không phải số đã báo cáo",
      },
      action: { text: 'dùng Core banking, ghi chú lệch vào nhật ký' },
      exceptions: [],
      origin: 'hitl',
      origin_ref: null,
      status: 'draft',
      review_at: '2027-01-06',
      override_count: 0,
      approved_by: null,
      approved_at: null,
      latest_backtest: {
        id: 'backtest-core-under-2b',
        window_from: '2026-09-08T00:00:00+07:00',
        window_to: '2026-10-08T00:00:00+07:00',
        matched_count: 41,
        auto_handled_count: 33,
        blocked_count: 0,
        changed_result_count: 1,
        affected_agent_ids: [],
        created_at: '2026-10-08T09:41:00+07:00',
      },
      created_at: '2026-10-08T09:40:00+07:00',
      updated_at: '2026-10-08T09:40:00+07:00',
    },
  ];
}

const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    { title: code, status, code, detail },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

export function createRuleHandlers() {
  let rules = decisionRulesFixture();
  const update = (id: string, change: Partial<Rule>) => {
    rules = rules.map((r) => (r.id === id ? { ...r, ...change } : r));
    return rules.find((r) => r.id === id) as Rule;
  };
  // BehaviorPolicyView: the list and the verbs answer the row without its backtest.
  const view = ({ latest_backtest: _, ...row }: Rule) => row;

  return [
    // The Data Space's behaviour policies, as an array; every other policy list falls through.
    http.get('*/v1/policies', ({ request }) => {
      const q = new URL(request.url).searchParams;
      if (q.get('type') !== 'behavior' || q.get('scope') !== 'data_space') return undefined;
      const status = q.get('status');
      return HttpResponse.json(rules.filter((r) => !status || r.status === status).map(view));
    }),
    http.get('*/v1/policies/:id', ({ params }) => {
      const rule = rules.find((r) => r.id === params.id);
      return rule ? HttpResponse.json(rule) : undefined;
    }),
    http.post('*/v1/policies/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      const rule = rules.find((r) => r.id === id);
      if (!rule) return undefined;
      if (verb === 'backtest') return HttpResponse.json({ status: 'queued' }, { status: 202 });
      if (verb === 'activate') {
        if (rule.status !== 'draft')
          return problem(409, 'invalid_state', 'Quy tắc không còn là bản nháp.');
        return HttpResponse.json(
          view(update(rule.id, { status: 'active', approved_at: new Date().toISOString() })),
        );
      }
      if (verb === 'disable') {
        const body = (await request.json()) as { reason?: string };
        if (!body.reason?.trim()) return problem(422, 'validation_failed', 'Cần ghi lý do.');
        return HttpResponse.json(view(update(rule.id, { status: 'disabled' })));
      }
      return undefined;
    }),
  ];
}
