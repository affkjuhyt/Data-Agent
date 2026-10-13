import { http, HttpResponse } from 'msw';
import type { components } from '../lib/v1/schema';

/*
 * Trang chủ's morning summary for the dev mock: the newest digest notification (NotificationView)
 * of "Mobile · Trang chủ" (2231:15114). Other kinds fall through to mocks/notifications.ts.
 */
function digestFixture(): components['schemas']['NotificationView'] {
  const at = new Date();
  at.setHours(6, 0, 0, 0);
  return {
    id: 'ntf-digest-today',
    data_space_id: 'ds-rui-ro-tin-dung',
    kind: 'digest',
    title: 'Tóm tắt sáng nay',
    body: [
      'NPL bán lẻ 1,84%, tăng 0,05 điểm, vẫn dưới ngưỡng cảnh báo 2%.',
      'Core banking và DWH lệch 1,8 tỷ dư nợ nhóm 3–5, đang chờ bạn chọn nguồn.',
      'Kỳ 09/2026 chưa chốt được: lệch 4 khách hàng với sổ cái.',
    ].join('\n'),
    link: null,
    source_ref: 'report-run-digest-today',
    priority: 'normal',
    batched_into: null,
    read_at: null,
    created_at: at.toISOString(),
  };
}

export function createHomeHandlers() {
  return [
    http.get('*/v1/me/notifications', ({ request }) =>
      new URL(request.url).searchParams.get('kind') === 'digest'
        ? HttpResponse.json({ items: [digestFixture()], next_page_token: '' })
        : undefined,
    ),
  ];
}
