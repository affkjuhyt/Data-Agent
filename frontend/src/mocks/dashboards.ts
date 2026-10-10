import { http, HttpResponse } from 'msw';
import type { Dashboard, DashboardSchedule, ScheduleInput } from '../lib/v1/dashboards';

/*
 * MSW handlers for /v1/dashboards (lib/v1/dashboards.ts, PROPOSAL). Data is taken from
 * "v3.1 · Dashboard có tóm tắt AI" and its "Dữ liệu cũ" variant: the retail balance is late
 * because DWH bán lẻ stopped refreshing, as sourcesFixture() in fixtures.ts says. State lives in
 * memory: a saved schedule stays, "Viết lại" stamps a new summary time.
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

const WEEKDAY = ['', 'thứ Hai', 'thứ Ba', 'thứ Tư', 'thứ Năm', 'thứ Sáu', 'thứ Bảy', 'Chủ Nhật'];

function scheduleText(s: ScheduleInput): string {
  switch (s.kind) {
    case 'weekly':
      return `${WEEKDAY[s.weekday ?? 1]} ${s.time ?? '07:00'}`;
    case 'daily':
      return `hằng ngày ${s.time ?? '07:00'}`;
    case 'on_new_data':
      return 'khi có dữ liệu mới';
    case 'hourly_business':
      return 'hằng giờ trong giờ làm việc';
    case 'cron':
      return s.cron ?? 'cron';
  }
}

function schedulePreview(s: ScheduleInput): string {
  const skip = s.skip_holidays ? ' Bỏ qua ngày nghỉ theo lịch của ngân hàng.' : '';
  switch (s.kind) {
    case 'weekly':
      return `Gửi ${WEEKDAY[s.weekday ?? 1]} hằng tuần lúc ${s.time ?? '07:00'}, sau khi số liệu tuần được chốt.${skip}`;
    case 'daily':
      return `Gửi mỗi ngày lúc ${s.time ?? '07:00'}.${skip}`;
    case 'on_new_data':
      return `Gửi ngay khi job nạp Core banking xong, thường 06:05–06:15. Nếu đến 08:00 chưa có dữ liệu mới thì báo Kỹ sư dữ liệu.${skip}`;
    case 'hourly_business':
      return `Gửi mỗi giờ từ 08:00 đến 17:00 các ngày làm việc.${skip}`;
    case 'cron':
      return s.cron?.trim()
        ? `Theo biểu thức “${s.cron.trim()}”.${skip}`
        : 'Nhập biểu thức cron để xem lịch dự kiến.';
  }
}

function dashboardsFixture(): Dashboard[] {
  return [
    {
      id: 'dash-credit-risk-weekly',
      title: 'Rủi ro tín dụng · tuần',
      filters: [
        {
          key: 'period',
          label: 'Kỳ',
          value: 'w41',
          options: [
            { value: 'w41', label: 'tuần 41' },
            { value: 'w40', label: 'tuần 40' },
            { value: 'w39', label: 'tuần 39' },
          ],
        },
        {
          key: 'region',
          label: 'Vùng',
          value: 'all',
          options: [
            { value: 'all', label: 'tất cả' },
            { value: 'dh-bac-bo', label: 'DH Bắc Bộ' },
            { value: 'db-song-hong', label: 'ĐB sông Hồng' },
            { value: 'bac-trung-bo', label: 'Bắc Trung Bộ' },
            { value: 'dong-nam-bo', label: 'Đông Nam Bộ' },
            { value: 'tay-nguyen', label: 'Tây Nguyên' },
          ],
        },
      ],
      summary: {
        agent_name: 'Agent Báo cáo',
        written_at: new Date(Date.now() - 40 * 60_000).toISOString(),
        lines: [
          'Nợ xấu bán lẻ 1,84% (+0,22 điểm) — tăng tập trung ở Duyên hải Bắc Bộ, chủ yếu vay tiêu dùng không tài sản bảo đảm.',
          'Dư nợ tăng 1,1%, dẫn đầu bởi vay mua nhà; chất lượng khoản mới tốt hơn trung bình.',
          'Cần xem: 19 khoản tái cơ cấu chưa vào số liệu 30/09 (đang chờ duyệt điều chỉnh).',
        ],
      },
      stale: {
        message:
          'Số dư nợ bán lẻ đang là dữ liệu của 8 giờ trước vì DWH bán lẻ ngừng cập nhật. Các ô bị ảnh hưởng có dấu đồng hồ.',
        source_id: 'src-dwh-retail',
      },
      schedule: {
        kind: 'weekly',
        time: '07:00',
        weekday: 1,
        cron: null,
        skip_holidays: true,
        no_overlap: true,
        retry_on_error: true,
        text: 'thứ Hai 07:00',
      },
      tiles: [
        {
          id: 'kpi-npl',
          kind: 'kpi',
          label: 'Nợ xấu bán lẻ',
          value: '1,84%',
          delta: { text: '+0,22 điểm', direction: 'up', good: false },
          stale: false,
        },
        {
          id: 'kpi-balance',
          kind: 'kpi',
          label: 'Dư nợ',
          value: '182.430 tỷ',
          delta: { text: '+1,1%', direction: 'up', good: true },
          stale: true,
        },
        {
          id: 'kpi-cost',
          kind: 'kpi',
          label: 'Chi phí rủi ro',
          value: '1,12%',
          delta: { text: '+0,05 điểm', direction: 'up', good: false, warning: true },
          stale: false,
        },
        {
          id: 'kpi-new-loans',
          kind: 'kpi',
          label: 'Khoản vay mới',
          value: '8.214',
          delta: { text: '+6%', direction: 'up', good: true },
          stale: false,
        },
        {
          id: 'line-npl-12m',
          kind: 'line',
          title: 'Nợ xấu 12 tháng',
          last_label: '1,84% · T9',
          stale: false,
          points: [
            ['T10', 1.48],
            ['T11', 1.52],
            ['T12', 1.56],
            ['T1', 1.55],
            ['T2', 1.59],
            ['T3', 1.6],
            ['T4', 1.62],
            ['T5', 1.61],
            ['T6', 1.63],
            ['T7', 1.6],
            ['T8', 1.62],
            ['T9', 1.84],
          ].map(([label, value]) => ({ label: String(label), value: Number(value) })),
        },
        {
          id: 'bars-region',
          kind: 'bars',
          title: 'Thay đổi theo vùng (điểm %)',
          stale: false,
          bars: [
            { label: 'DH Bắc Bộ', value: 0.61, text: '+0,61' },
            { label: 'ĐB sông Hồng', value: 0.21, text: '+0,21' },
            { label: 'Bắc Trung Bộ', value: 0.14, text: '+0,14' },
            { label: 'Đông Nam Bộ', value: 0.12, text: '+0,12' },
            { label: 'Tây Nguyên', value: -0.04, text: '−0,04' },
          ],
        },
      ],
    },
  ];
}

export function createDashboardHandlers() {
  let dashboards = dashboardsFixture();
  const find = (id: unknown) => dashboards.find((d) => d.id === id);
  const notFound = () => problem(404, 'not_found', 'Không tìm thấy dashboard.');

  return [
    http.get('*/v1/dashboards', () =>
      page(
        dashboards.map((d) => ({
          id: d.id,
          title: d.title,
          updated_at: d.summary?.written_at ?? '',
        })),
      ),
    ),

    http.get('*/v1/dashboards/:id', ({ params, request }) => {
      const dash = find(params.id);
      if (!dash) return notFound();
      const query = new URL(request.url).searchParams;
      return HttpResponse.json({
        ...dash,
        filters: dash.filters.map((f) => {
          const value = query.get(f.key);
          return value && f.options.some((o) => o.value === value) ? { ...f, value } : f;
        }),
      });
    }),

    http.post('*/v1/dashboards/:action', ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      const dash = find(id);
      if (!dash) return notFound();
      if (verb !== 'summarize' || !dash.summary)
        return problem(404, 'not_found', 'Không có thao tác này.');
      const summary = { ...dash.summary, written_at: new Date().toISOString() };
      dashboards = dashboards.map((d) => (d.id === id ? { ...d, summary } : d));
      return HttpResponse.json(summary);
    }),

    http.post('*/v1/dashboards/:id/:action', async ({ params, request }) => {
      if (params.action !== 'schedule:preview')
        return problem(404, 'not_found', 'Không có thao tác này.');
      if (!find(params.id)) return notFound();
      return HttpResponse.json({ text: schedulePreview((await request.json()) as ScheduleInput) });
    }),

    http.put('*/v1/dashboards/:id/schedule', async ({ params, request }) => {
      const dash = find(params.id);
      if (!dash) return notFound();
      const input = (await request.json()) as ScheduleInput;
      if (input.kind === 'cron' && !input.cron?.trim())
        return problem(422, 'validation_failed', 'Biểu thức cron trống.');
      const schedule: DashboardSchedule = { ...input, text: scheduleText(input) };
      dashboards = dashboards.map((d) => (d.id === dash.id ? { ...d, schedule } : d));
      return HttpResponse.json(schedule);
    }),
  ];
}
