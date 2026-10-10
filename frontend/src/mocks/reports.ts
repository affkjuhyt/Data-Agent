import { http, HttpResponse } from 'msw';
import type { DigestItem, ReportRun, ReportSchedule } from '../lib/v1/reports';

/*
 * Báo cáo định kỳ and Bản tin (P08) for the dev mock: the weekly credit-risk report of
 * "Báo cáo định kỳ · Thiết lập" (2271:16289) and the reader's digests of "Bản tin · Đọc"
 * (2272:16218), week 41 opened first.
 */
const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    { title: code, status, code, detail },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

function reportsFixture(): ReportSchedule[] {
  return [
    {
      id: 'rep-risk-weekly',
      title: 'Rủi ro tín dụng · tuần',
      status: 'active',
      summary_text:
        'Agent Báo cáo soạn từ phiên “Điều tra nợ xấu bán lẻ T9” · gửi gần nhất thứ Hai 06/10 07:00',
      sections: [
        {
          key: 'summary',
          title: 'Tóm tắt điều đáng chú ý',
          hint: 'Agent viết, tối đa 3 ý',
          enabled: true,
          personal: false,
        },
        {
          key: 'kpis',
          title: 'Chỉ số chính',
          hint: 'Nợ xấu, dư nợ, chi phí rủi ro, khoản vay mới',
          enabled: true,
          personal: false,
        },
        {
          key: 'npl_region',
          title: 'Nợ xấu theo vùng',
          hint: 'Biểu đồ cột, so với kỳ trước',
          enabled: true,
          personal: false,
        },
        {
          key: 'decisions',
          title: 'Việc đang chờ quyết định',
          hint: 'Từ Cần bạn, chỉ việc của người nhận',
          enabled: true,
          personal: false,
        },
        {
          key: 'top_loans',
          title: 'Chi tiết 20 khoản lớn nhất',
          hint: 'Có dữ liệu cá nhân · chỉ gửi trong ứng dụng',
          enabled: false,
          personal: true,
        },
      ],
      params: [
        { label: 'Data Space', value: 'Rủi ro tín dụng' },
        { label: 'Kỳ', value: 'Tuần trước (thứ Hai – Chủ nhật)' },
        { label: 'Dữ liệu', value: 'Số liệu đã chốt; nếu chưa chốt, dùng mới nhất và ghi chú' },
      ],
      delivery: [
        { label: 'Lịch', value: 'Thứ Hai hằng tuần, 07:00' },
        { label: 'Người nhận', value: '32 người · nhóm GRP-RISK-ANALYST và Ban điều hành' },
        { label: 'Kênh', value: 'Email (PDF đính kèm), Teams' },
        { label: 'Theo quyền', value: 'Mỗi người chỉ thấy số liệu trong phạm vi của mình' },
      ],
      require_publish_check: true,
      publish_check_text: 'Đỗ Hải Yến duyệt trước 06:30. Quá hạn thì giữ lại, không tự gửi.',
      recent_runs: [
        {
          id: 'rr-1006',
          at_text: '06/10 07:00',
          status: 'sent',
          corrected: false,
          detail: '32 người · 28 đã mở',
        },
        {
          id: 'rr-2909',
          at_text: '29/09 07:00',
          status: 'sent',
          corrected: true,
          detail: '1 số liệu sửa lúc 09:10',
        },
        {
          id: 'rr-2209',
          at_text: '22/09 07:00',
          status: 'blocked_by_checks',
          corrected: false,
          detail: 'Chưa được duyệt trước 06:30',
        },
        {
          id: 'rr-1509',
          at_text: '15/09 07:00',
          status: 'sent',
          corrected: false,
          detail: '31 người · 26 đã mở',
        },
      ],
    },
  ];
}

function digestsFixture(): DigestItem[] {
  return [
    {
      id: 'rr-1006',
      title: 'Rủi ro tín dụng · tuần 41',
      meta_text: '06/10 · Agent Báo cáo',
      unread: false,
    },
    {
      id: 'rr-2909',
      title: 'Rủi ro tín dụng · tuần 40',
      meta_text: '29/09 · có 1 đính chính',
      unread: false,
    },
    {
      id: 'rr-board-09',
      title: 'Danh mục cho vay · tháng 9',
      meta_text: '01/10 · Ban điều hành',
      unread: true,
    },
    { id: 'rr-1509', title: 'Rủi ro tín dụng · tuần 39', meta_text: '22/09', unread: false },
  ];
}

function runFixture(id: string, title: string): ReportRun {
  return {
    id,
    title,
    meta_text:
      'Thứ Hai 06/10/2026 · Agent Báo cáo soạn, Đỗ Hải Yến duyệt lúc 06:52 · số liệu đã chốt tới 04/10',
    changed:
      'Nợ xấu bán lẻ lên 1,84%, tăng 0,22 điểm so với tháng 8 — mức tăng lớn nhất trong 6 tháng. Dư nợ tăng 1,1% trong tuần, chủ yếu vay mua nhà. Chi phí rủi ro 1,12%, trong kế hoạch.',
    kpis: [
      { label: 'Nợ xấu bán lẻ', value: '1,84%', delta: '+0,22 điểm', tone: 'up_bad' },
      { label: 'Dư nợ', value: '182.430 tỷ', delta: '+1,1%', tone: 'up_good' },
      { label: 'Chi phí rủi ro', value: '1,12%', delta: '+0,05 điểm', tone: 'neutral' },
    ],
    why: '74% mức tăng nằm ở Duyên hải Bắc Bộ, do vay tiêu dùng không tài sản bảo đảm tại Hải Phòng và Quảng Ninh, cộng 19 khoản tái cơ cấu chuyển nhóm. Các vùng khác biến động dưới 0,2 điểm.',
    decisions: [
      {
        text: 'Duyệt điều chỉnh nợ quá hạn 214 khoản vay',
        who_text: 'Đỗ Hải Yến · hạn 17:00 thứ Hai',
        hitl_id: 'hitl-overdue-214',
      },
      {
        text: 'Có siết điều kiện vay tiêu dùng tại DH Bắc Bộ không',
        who_text: 'Ban điều hành · họp thứ Tư',
        hitl_id: null,
      },
    ],
    footer_text:
      'Mọi con số bấm được để xem cách tính và nguồn. Bạn nhận bản tin này vì thuộc nhóm GRP-RISK-ANALYST.',
  };
}

export function createReportHandlers() {
  let reports = reportsFixture();
  let digests = digestsFixture();
  return [
    http.get('*/v1/reports', () =>
      HttpResponse.json({
        items: reports.map(({ id, title, status }) => ({ id, title, status })),
        next_cursor: null,
        total: reports.length,
      }),
    ),
    http.get('*/v1/reports/:id', ({ params }) => {
      const found = reports.find((r) => r.id === params.id);
      return found
        ? HttpResponse.json(found)
        : problem(404, 'not_found', 'Không tìm thấy báo cáo.');
    }),
    http.patch('*/v1/reports/:id', async ({ params, request }) => {
      const patch = (await request.json()) as Partial<ReportSchedule>;
      const found = reports.find((r) => r.id === params.id);
      if (!found) return problem(404, 'not_found', 'Không tìm thấy báo cáo.');
      const updated = { ...found, ...patch };
      reports = reports.map((r) => (r.id === found.id ? updated : r));
      return HttpResponse.json(updated);
    }),
    http.post('*/v1/reports/:action', ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      const found = reports.find((r) => r.id === id);
      if (!found) return problem(404, 'not_found', 'Không tìm thấy báo cáo.');
      if (verb === 'pause') {
        const paused: ReportSchedule = { ...found, status: 'paused' };
        reports = reports.map((r) => (r.id === id ? paused : r));
        return HttpResponse.json(paused);
      }
      if (verb === 'run-now')
        return HttpResponse.json({ id: `rr-test-${Date.now()}` }, { status: 202 });
      return problem(404, 'not_found', 'Không có thao tác này.');
    }),
    http.get('*/v1/me/digests', () =>
      HttpResponse.json({ items: digests, next_cursor: null, total: digests.length }),
    ),
    http.get('*/v1/report-runs/:id', ({ params }) => {
      const item = digests.find((d) => d.id === params.id);
      if (!item) return problem(404, 'not_found', 'Không tìm thấy bản tin.');
      // Opening a digest reads it.
      digests = digests.map((d) => (d.id === item.id ? { ...d, unread: false } : d));
      return HttpResponse.json(runFixture(item.id, item.title));
    }),
  ];
}
