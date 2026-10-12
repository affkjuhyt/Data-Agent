import { http, HttpResponse } from 'msw';
import type { ResolvedRequest } from '../lib/v1/inbox';

/*
 * The "Đã xử lý tuần này" list of Cần bạn (2167:19220) for the dev mock. GET /v1/inbox answers
 * here only with status=resolved; the pending inbox stays with handlers.ts.
 */
const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

function resolvedFixture(): ResolvedRequest[] {
  return [
    {
      id: 'r-npl-418',
      title: 'Chọn NPL_RATIO v4 cho lượt #418',
      resolved_by: 'me',
      resolved_at: ago(20),
      outcome_text: 'Agent đã chạy tiếp',
    },
    {
      id: 'r-core-dwh',
      title: 'Lệch Core banking và DWH < 0,05%',
      resolved_by: 'rule',
      resolved_at: ago(120),
      outcome_text: 'Theo quy tắc của bạn',
    },
    {
      id: 'r-casa-v3',
      title: 'Duyệt CASA_RATIO v3',
      resolved_by: 'me',
      resolved_at: ago(26 * 60),
      outcome_text: '2 agent đã cập nhật',
    },
    {
      id: 'r-digest-41',
      title: 'Bản tin tuần 41',
      resolved_by: 'rule',
      resolved_at: ago(48 * 60),
      outcome_text: 'Theo quy tắc của bạn',
    },
  ];
}

export function createInboxHandlers() {
  return [
    http.get('*/v1/inbox', ({ request }) => {
      if (new URL(request.url).searchParams.get('status') !== 'resolved') return undefined;
      return HttpResponse.json({ items: resolvedFixture(), next_page_token: '' });
    }),
  ];
}
