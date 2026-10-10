import { http, HttpResponse } from 'msw';
import type { Alert } from '../lib/v1/alerts';

/*
 * Cảnh báo (P08, UC-08-02) for the dev mock: "#A-2291" of "P08 · Cảnh báo · Chi tiết"
 * (2271:16093) and the other alerts the monitor list links to. Closing one as a false alarm gives
 * the state of "Đã đóng do báo nhầm" (2272:16850), with what the agent learnt from it.
 */
const at = (daysAgo: number, hh: number, mm: number) => {
  const d = new Date();
  d.setDate(d.getDate() - daysAgo);
  d.setHours(hh, mm, 0, 0);
  return d.toISOString();
};

const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    { title: code, status, code, detail },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

// Thirty flat days that bend upward on the last six and cross 2,0% on the last four.
function series(peak: number) {
  return Array.from({ length: 30 }, (_, i) => {
    const d = new Date();
    d.setDate(d.getDate() - (29 - i));
    const flat = 1.7 + 0.004 * i + 0.006 * Math.sin(i);
    const rise = i >= 22 ? ((i - 21) / 8) ** 1.6 * (peak - 1.8) : 0;
    return { date: d.toISOString().slice(0, 10), value: Math.round((flat + rise) * 1000) / 1000 };
  });
}

function alert(
  a: Partial<Alert> & Pick<Alert, 'id' | 'code' | 'title' | 'monitor_id' | 'monitor_name'>,
): Alert {
  const points = series(2.32);
  return {
    status: 'open',
    close_kind: null,
    fired_at: at(0, 7, 15),
    delivered_text: 'Teams, email cho 2 người',
    occurrences: 1,
    chart: {
      metric_label: 'NPL_RATIO v4',
      cutoff_text: 'dữ liệu tới 30/09 23:59',
      series: points,
      threshold: 2,
      breach_from: points[points.length - 4].date,
      unit: '%',
    },
    analysis: null,
    details: [],
    activity: [],
    end_rule_text:
      'Cảnh báo tự đóng khi giá trị dưới 1,8% trong 2 ngày liên tiếp, hoặc khi người phụ trách đánh dấu đã xử lý.',
    closed: null,
    ...a,
  };
}

export function alertsFixture(): Alert[] {
  return [
    alert({
      id: 'alert-npl-north',
      code: '#A-2291',
      title: 'Nợ xấu bán lẻ DH Bắc Bộ vượt 2,0%',
      monitor_id: 'mon-npl-north',
      monitor_name: 'Nợ xấu bán lẻ · DH Bắc Bộ',
      analysis: {
        summary:
          'Mức tăng 0,61 điểm từ T8 sang T9 đến chủ yếu từ vay tiêu dùng không tài sản bảo đảm tại 3 chi nhánh. 19 khoản tái cơ cấu chuyển nhóm 2 sang nhóm 3 cùng tuần.',
        finished_text: 'phân tích xong 07:19 · 2 phút 40 giây',
        factors: [
          { label: 'Tiêu dùng không TSBĐ · CN Hải Phòng', contribution: 0.24 },
          { label: 'Tiêu dùng không TSBĐ · CN Quảng Ninh', contribution: 0.15 },
          { label: 'Tái cơ cấu chuyển nhóm (19 khoản)', contribution: 0.12 },
          { label: 'Thẻ tín dụng · toàn vùng', contribution: 0.06 },
          { label: 'Các yếu tố khác', contribution: 0.04 },
        ],
        record_count: 3412,
        sql: [
          'SELECT branch, product, SUM(npl_delta) AS contribution',
          'FROM mart.npl_daily',
          "WHERE region = 'DH Bắc Bộ' AND segment = 'retail'",
          "  AND as_of BETWEEN '2026-08-31' AND '2026-09-30'",
          'GROUP BY branch, product ORDER BY contribution DESC',
        ].join('\n'),
      },
      details: [
        { label: 'Điều kiện', value: '> 2,0% trong 1 ngày' },
        { label: 'Giá trị', value: '2,32%', mono: true },
        { label: 'Cảnh báo sớm', value: 'từ 05/10' },
        { label: 'Người phụ trách', value: 'Đỗ Hải Yến' },
        { label: 'Mức', value: 'Cao · báo lại sau 4 giờ' },
      ],
      activity: [
        { at: '07:15', text: 'Vượt ngưỡng, gửi Teams và email' },
        { at: '07:19', text: 'Agent Phân tích đính kèm nguyên nhân' },
        { at: '07:42', text: 'Đỗ Hải Yến đã xem' },
        { at: '05/10', text: 'Cảnh báo sớm: 1,93%' },
      ],
    }),
    alert({
      id: 'alert-npl-red-river',
      code: '#A-2290',
      title: 'Nợ xấu bán lẻ ĐB sông Hồng chạm cảnh báo sớm 1,9%',
      monitor_id: 'mon-npl-red-river',
      monitor_name: 'Nợ xấu bán lẻ · ĐB sông Hồng',
      delivered_text: 'Teams cho 1 người',
      details: [
        { label: 'Điều kiện', value: '> 2,0% trong 1 ngày' },
        { label: 'Giá trị', value: '1,94%', mono: true },
        { label: 'Người phụ trách', value: 'Đỗ Hải Yến' },
      ],
      activity: [{ at: '07:15', text: 'Chạm cảnh báo sớm, gửi Teams' }],
    }),
    alert({
      id: 'alert-card-balance',
      code: '#A-2284',
      title: 'Dư nợ thẻ tín dụng giảm 5,4% so với tuần trước',
      monitor_id: 'mon-card-balance',
      monitor_name: 'Dư nợ thẻ tín dụng',
      fired_at: at(1, 18, 0),
      delivered_text: 'Teams cho 1 người',
      details: [
        { label: 'Điều kiện', value: 'giảm > 5% so với tuần trước' },
        { label: 'Người phụ trách', value: 'Phạm Đức Long' },
      ],
      activity: [{ at: 'hôm qua', text: 'Vượt ngưỡng, gửi Teams' }],
    }),
    alert({
      id: 'alert-recovery-closed',
      code: '#A-2241',
      title: 'Tỷ lệ thu hồi nợ thấp bất thường',
      monitor_id: 'mon-recovery',
      monitor_name: 'Tỷ lệ thu hồi nợ',
      status: 'closed',
      close_kind: 'resolved',
      fired_at: at(8, 9, 0),
      delivered_text: 'email cho 1 người',
      closed: {
        by: 'Đỗ Hải Yến',
        at: at(7, 10, 0),
        note: 'Đã đối chiếu với phòng thu hồi nợ, số liệu đúng.',
        agent_note: null,
      },
      details: [{ label: 'Điều kiện', value: 'bất thường · độ nhạy vừa' }],
      activity: [{ at: '03/10', text: 'Phát hiện bất thường, gửi email' }],
    }),
  ];
}

export function createAlertHandlers() {
  let alerts = alertsFixture();
  const update = (id: string, patch: (a: Alert) => Alert) => {
    const found = alerts.find((a) => a.id === id);
    if (!found) return null;
    const next = patch(found);
    alerts = alerts.map((a) => (a.id === id ? next : a));
    return next;
  };
  return [
    http.get('*/v1/alerts', ({ request }) => {
      const status = new URL(request.url).searchParams.get('status');
      const items = alerts.filter((a) => !status || a.status === status);
      return HttpResponse.json({ items, next_cursor: null, total: items.length });
    }),
    http.get('*/v1/alerts/:id', ({ params }) => {
      const found = alerts.find((a) => a.id === params.id);
      return found
        ? HttpResponse.json(found)
        : problem(404, 'not_found', 'Không tìm thấy cảnh báo.');
    }),
    http.post('*/v1/alerts/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      const body = (await request.json().catch(() => ({}))) as Record<string, string>;
      const now = new Date();
      const hhmm = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
      if (verb === 'investigate')
        return alerts.some((a) => a.id === id)
          ? HttpResponse.json({ workspace_id: `ws-alert-${id}` }, { status: 201 })
          : problem(404, 'not_found', 'Không tìm thấy cảnh báo.');
      if (verb === 'dispute-analysis')
        return HttpResponse.json({ id: `dispute-${id}` }, { status: 201 });
      const result =
        verb === 'assign'
          ? update(id, (a) => ({
              ...a,
              status: 'assigned',
              activity: [
                ...a.activity,
                { at: hhmm, text: `Giao cho ${body.assignee_name ?? 'người khác'}` },
              ],
            }))
          : verb === 'close'
            ? (() => {
                const found = alerts.find((a) => a.id === id);
                if (found && found.occurrences > 2 && !body.close_note?.trim())
                  return 'needs_note' as const;
                return update(id, (a) => ({
                  ...a,
                  status: 'closed',
                  close_kind: body.kind === 'false_positive' ? 'false_positive' : 'resolved',
                  closed: {
                    by: 'Đỗ Hải Yến',
                    at: now.toISOString(),
                    note: body.close_note ?? '',
                    agent_note:
                      body.kind === 'false_positive'
                        ? 'Agent ghi nhớ: khi giải thích nợ xấu, tách riêng khoản tái cơ cấu chưa điều chỉnh. Ngưỡng theo dõi giữ nguyên. Bạn có thể xem hoặc xoá ghi nhớ này trong Bộ nhớ của agent.'
                        : null,
                  },
                }));
              })()
            : verb === 'reopen'
              ? update(id, (a) => ({ ...a, status: 'open', close_kind: null, closed: null }))
              : undefined;
      if (result === undefined) return problem(404, 'not_found', 'Không có thao tác này.');
      if (result === 'needs_note')
        return problem(
          422,
          'business_rule_violated',
          'Cảnh báo lặp hơn 2 lần cần ghi chú khi đóng (BR-124).',
        );
      return result
        ? HttpResponse.json(result)
        : problem(404, 'not_found', 'Không tìm thấy cảnh báo.');
    }),
  ];
}
