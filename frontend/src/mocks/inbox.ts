import { http, HttpResponse } from 'msw';
import type { components } from '../lib/v1/schema';

/*
 * The "Đã xử lý tuần này" list of Cần bạn (2167:19220) for the dev mock: the settled requests, in
 * the api's HitlView, for the statuses a decision leaves; the pending inbox stays with handlers.ts.
 * The viewer is GET /v1/me's (adminPlatform.ts); a request no person resolved was settled by rule.
 */
type HitlView = components['schemas']['HitlView'];

const ME = '01920000-0000-7000-8000-00000000a5c1';
const SETTLED = new Set(['answered', 'approved', 'rejected', 'changes_requested']);

// Spread over the current week so the list never depends on the day it is opened.
function inWeek(fraction: number): string {
  const now = new Date();
  const monday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7));
  return new Date(monday.getTime() + (now.getTime() - monday.getTime()) * fraction).toISOString();
}

const settled = (
  id: string,
  title: string,
  status: string,
  resolvedBy: string | null,
  at: string,
): HitlView => ({
  id,
  data_space_id: null,
  kind: 'approve_proposal',
  risk: 'low',
  title,
  context: {},
  options: [],
  source_service: 'governance',
  source_ref: null,
  parent_run_label: null,
  assignee_id: ME,
  assignee_group: null,
  assignee_capability: null,
  due_at: null,
  status,
  response: null,
  remember_as_rule: false,
  resolved_by: resolvedBy,
  resolved_at: at,
  channel_card_ids: [],
  created_at: at,
  updated_at: at,
});

function resolvedFixture(): HitlView[] {
  return [
    settled('r-npl-418', 'Chọn NPL_RATIO v4 cho lượt #418', 'answered', ME, inWeek(0.95)),
    settled('r-core-dwh', 'Lệch Core banking và DWH < 0,05%', 'approved', null, inWeek(0.8)),
    settled('r-casa-v3', 'Duyệt CASA_RATIO v3', 'approved', ME, inWeek(0.5)),
    settled('r-digest-41', 'Bản tin tuần 41', 'approved', null, inWeek(0.2)),
  ];
}

export function createInboxHandlers() {
  return [
    http.get('*/v1/inbox', ({ request }) => {
      const status = new URL(request.url).searchParams.get('status') ?? '';
      if (!SETTLED.has(status)) return undefined;
      const items = resolvedFixture().filter((r) => r.status === status);
      return HttpResponse.json({ items, next_page_token: '' });
    }),
  ];
}
