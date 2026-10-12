import { http, HttpResponse } from 'msw';
import type { DecisionRule } from '../lib/v1/rules';

/*
 * Rules drafted from decisions, for "P07 · Quy tắc từ quyết định" (2302:19305). The handlers
 * answer only the ids they hold and let every other policy fall through to the access-policy
 * mock, since A2 serves both kinds from /v1/policies.
 */
function decisionRulesFixture(): DecisionRule[] {
  return [
    {
      id: 'rule-core-under-2b',
      scope: 'data_space',
      kind: 'auto_handle',
      description: 'Lệch dư nợ dưới 2 tỷ → dùng Core banking',
      origin: 'hitl',
      status: 'draft',
      applied_this_week: 0,
      title: 'Luôn chọn Core banking khi lệch dư nợ dưới 2 tỷ',
      rule_text: [
        "KHI loại = 'dư nợ' VÀ nguồn IN (Core banking, Kho dữ liệu)",
        '  VÀ |chênh lệch| < 2 tỷ VÀ không phải số đã báo cáo',
        'THÌ dùng Core banking, ghi chú lệch vào nhật ký',
        'NGƯỢC LẠI hỏi Trần Quốc An qua Cần bạn',
      ].join('\n'),
      origin_label:
        'Tạo từ quyết định của Trần Quốc An lúc 09:40 (thẻ “So sánh hai nguồn” chi nhánh Hà Nội)',
      backtest: {
        window_days: 30,
        case_label: 'Vụ lệch dư nợ',
        cases: 41,
        auto_handled: 33,
        asked_people: 8,
        asked_reason: 'vượt 2 tỷ hoặc số đã nộp',
        disagreements: [
          {
            case_text: '18/09 · CN Đà Nẵng lệch 1,2 tỷ do Core banking chạy lại cuối ngày',
            person_chose: 'Kho dữ liệu',
            rule_chooses: 'Core banking',
          },
        ],
      },
      scope_label: 'Rủi ro tín dụng',
      owner: { id: 'user-an', name: 'Trần Quốc An' },
      expires_at: '2027-01-06T00:00:00+07:00',
      review_reminder_days: 7,
    },
  ];
}

export function createRuleHandlers() {
  let rules = decisionRulesFixture();
  const update = (id: string, change: Partial<DecisionRule>) => {
    rules = rules.map((r) => (r.id === id ? { ...r, ...change } : r));
    return rules.find((r) => r.id === id) as DecisionRule;
  };

  return [
    // Only the drafts-from-decisions query; every other policy list falls through.
    http.get('*/v1/policies', ({ request }) => {
      const q = new URL(request.url).searchParams;
      if (q.get('origin') !== 'hitl' || q.get('status') !== 'draft') return undefined;
      return HttpResponse.json({
        items: rules.filter((r) => r.status === 'draft'),
        next_page_token: '',
      });
    }),
    http.get('*/v1/policies/:id', ({ params }) => {
      const rule = rules.find((r) => r.id === params.id);
      return rule ? HttpResponse.json(rule) : undefined;
    }),
    http.post('*/v1/policies/:action', ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      const rule = rules.find((r) => r.id === id);
      if (!rule) return undefined;
      const conflict = () =>
        HttpResponse.json(
          {
            title: 'invalid_state',
            status: 409,
            code: 'invalid_state',
            detail: 'Quy tắc không còn là bản nháp.',
          },
          { status: 409, headers: { 'Content-Type': 'application/problem+json' } },
        );
      if (verb === 'backtest') return HttpResponse.json({ status: 'queued' }, { status: 202 });
      if (verb === 'submit') {
        if (rule.status !== 'draft') return conflict();
        return HttpResponse.json(update(rule.id, { status: 'pending_review' }));
      }
      if (verb === 'disable') return HttpResponse.json(update(rule.id, { status: 'disabled' }));
      return undefined;
    }),
  ];
}
