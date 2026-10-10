import { http, HttpResponse } from 'msw';
import type { AppNotification } from '../lib/v1/notifications';

/*
 * The notification feed of "Trung tâm thông báo" (2072:3821), timed relative to now so the
 * Hôm nay / Hôm qua groups always hold the rows the design shows.
 */
function at(daysAgo: number, hour: number, minute: number): string {
  const d = new Date();
  d.setDate(d.getDate() - daysAgo);
  d.setHours(hour, minute, 0, 0);
  return d.toISOString();
}

function notificationsFixture(): AppNotification[] {
  return [
    {
      id: 'ntf-adjust-214',
      category: 'needs_you',
      text: 'Agent Đối soát cần bạn duyệt 214 khoản vay',
      icon: 'approve',
      created_at: at(0, 9, 48),
      read_at: null,
      target: { kind: 'proposal', id: 'proposal-214' },
    },
    {
      id: 'ntf-npl-definition',
      category: 'needs_you',
      text: 'Agent Phân tích hỏi định nghĩa “nợ xấu”',
      icon: 'alert',
      created_at: at(0, 9, 52),
      read_at: null,
      target: { kind: 'hitl', id: 'hitl-npl-definition' },
    },
    {
      id: 'ntf-bulletin-41',
      category: 'agent',
      text: 'Bản tin rủi ro tuần 41 đã gửi tới 32 người',
      icon: 'check',
      created_at: at(0, 7, 0),
      read_at: at(0, 7, 5),
      target: null,
    },
    {
      id: 'ntf-dq-paused',
      category: 'system',
      text: 'Agent Chất lượng dữ liệu tạm dừng · mất kết nối DWH',
      icon: 'alert',
      created_at: at(0, 2, 40),
      read_at: at(0, 8, 0),
      target: { kind: 'run', id: 'run-409' },
    },
    {
      id: 'ntf-skill-provision',
      category: 'agent',
      text: 'Agent đề xuất kỹ năng mới “Kiểm tra trích lập dự phòng”',
      icon: 'agent',
      created_at: at(1, 16, 20),
      read_at: at(1, 17, 0),
      target: null,
    },
    {
      id: 'ntf-npl-v5',
      category: 'needs_you',
      text: 'NPL_RATIO v5 (nháp) sẵn sàng để xem',
      icon: 'layers',
      created_at: at(1, 15, 5),
      read_at: at(1, 15, 30),
      target: { kind: 'proposal', id: 'pr-0318' },
    },
  ];
}

export function createNotificationHandlers() {
  let notifications = notificationsFixture();
  return [
    http.get('*/v1/me/notifications', () =>
      HttpResponse.json({
        items: [...notifications].sort((a, b) => b.created_at.localeCompare(a.created_at)),
        next_page_token: '',
      }),
    ),
    http.post('*/v1/me/notifications\\:mark-read', async ({ request }) => {
      const { ids } = (await request.json()) as { ids: string[] };
      const now = new Date().toISOString();
      let marked = 0;
      notifications = notifications.map((n) => {
        if (!ids.includes(n.id) || n.read_at) return n;
        marked += 1;
        return { ...n, read_at: now };
      });
      return HttpResponse.json({ marked });
    }),
  ];
}
