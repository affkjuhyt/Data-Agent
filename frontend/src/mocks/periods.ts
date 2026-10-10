import { http, HttpResponse } from 'msw';
import type { PeriodClose, PeriodSummary } from '../lib/v1/periods';

/*
 * The periods of "P10 · Kỳ chốt · Chốt kỳ" (2304:19277). T9/2026 is mid-close and stopped on a
 * reconciliation mismatch; its HITL request is answered here (and only here, so the Cần bạn
 * list keeps its own fixture), which moves the close on to the lock step.
 */
const ISSUE_HITL = 'hitl-period-t9-2141';

const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    { title: code, status, code, detail },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

function summaries(): PeriodSummary[] {
  return [
    { id: 'period-2026-10', code: 'T10/2026', status: 'open', adjustment_count: 0 },
    { id: 'period-2026-09', code: 'T9/2026', status: 'closing', adjustment_count: 0 },
    { id: 'period-2026-08', code: 'T8/2026', status: 'locked', adjustment_count: 2 },
    { id: 'period-2026-07', code: 'T7/2026', status: 'locked', adjustment_count: 0 },
    { id: 'period-2026-q2', code: 'Q2/2026', status: 'published', adjustment_count: 0 },
  ];
}

function september(): PeriodClose {
  return {
    ...summaries()[1],
    title: 'Chốt kỳ tháng 9/2026',
    context_text: 'Data Space Rủi ro tín dụng · hạn chốt 05/10 · báo cáo NHNN phải nộp 10/10',
    steps: [
      {
        title: 'Chụp dữ liệu',
        detail: '46 bảng · 1,8 tỷ dòng · 05/10 23:59 · Agent Đối soát',
        state: 'done',
      },
      {
        title: 'Tính mã băm từng bảng',
        detail: 'Lưu để chứng minh dữ liệu không đổi sau khi khoá',
        state: 'done',
      },
      {
        title: 'Đối soát với sổ cái',
        detail: 'Dư nợ khớp tới 0,00%; tài khoản 2141 lệch 4,2 tỷ',
        state: 'needs_you',
        issue: {
          text: 'TK 2141 (dự phòng rủi ro) lệch 4,2 tỷ: Core banking ghi nhận trích lập bổ sung ngày 30/09 lúc 23:58, sổ cái ghi ngày 01/10. Agent đề xuất ghi nhận theo ngày Core banking và ghi chú.',
          hitl_id: ISSUE_HITL,
          options: [
            { id: 'accept_with_note', label: 'Chấp nhận kèm ghi chú', needs_note: true },
            { id: 'wait_for_ledger', label: 'Chờ sổ cái điều chỉnh', needs_note: false },
          ],
        },
      },
      {
        title: 'Khoá kỳ và đóng băng số đã công bố',
        detail: 'Sau khi khoá, mọi thay đổi đi qua quy trình hồi tố',
        state: 'pending',
      },
    ],
    frozen_figures: [
      { label: 'Nợ xấu bán lẻ', value: '1,84%' },
      { label: 'Dư nợ cho vay', value: '182.430 tỷ' },
      { label: 'Tỷ lệ dự phòng', value: '1,46%' },
    ],
    frozen_note:
      'Số đóng băng được gắn vào báo cáo NHNN T9 và không sửa được nữa; chỉ có thể tạo bản điều chỉnh.',
    can_lock: { allowed: false, reason: 'Còn bước đối soát chờ bạn quyết định.' },
  };
}

/** The other periods read as a finished (or not started) close with no open questions. */
function plain(summary: PeriodSummary): PeriodClose {
  const done = summary.status === 'locked' || summary.status === 'published';
  return {
    ...summary,
    title: `Kỳ ${summary.code}`,
    context_text: 'Data Space Rủi ro tín dụng',
    steps: [
      { title: 'Chụp dữ liệu', detail: '', state: done ? 'done' : 'pending' },
      { title: 'Tính mã băm từng bảng', detail: '', state: done ? 'done' : 'pending' },
      { title: 'Đối soát với sổ cái', detail: '', state: done ? 'done' : 'pending' },
      { title: 'Khoá kỳ và đóng băng số đã công bố', detail: '', state: done ? 'done' : 'pending' },
    ],
    frozen_figures: [],
    frozen_note: '',
    can_lock: { allowed: false, reason: done ? 'Kỳ đã khoá.' : 'Kỳ chưa bắt đầu chốt.' },
  };
}

export function createPeriodHandlers() {
  let periods: PeriodClose[] = summaries().map((s) =>
    s.id === 'period-2026-09' ? september() : plain(s),
  );
  const save = (next: PeriodClose) => {
    periods = periods.map((p) => (p.id === next.id ? next : p));
    return next;
  };
  const summary = ({ id, code, status, adjustment_count }: PeriodClose): PeriodSummary => ({
    id,
    code,
    status,
    adjustment_count,
  });

  return [
    http.get('*/v1/periods', () =>
      HttpResponse.json({ items: periods.map(summary), next_page_token: '' }),
    ),
    http.get('*/v1/periods/:id', ({ params }) => {
      const period = periods.find((p) => p.id === params.id);
      return period ? HttpResponse.json(period) : problem(404, 'not_found', 'Không tìm thấy kỳ.');
    }),
    http.post('*/v1/periods/:action', ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      const period = periods.find((p) => p.id === id);
      if (!period) return problem(404, 'not_found', 'Không tìm thấy kỳ.');
      if (period.status !== 'closing')
        return problem(409, 'invalid_state', 'Kỳ không ở trạng thái đang chốt.');
      if (verb === 'lock') {
        if (!period.can_lock.allowed)
          return problem(409, 'invalid_state', period.can_lock.reason ?? 'Chưa khoá được.');
        return HttpResponse.json(
          save({
            ...period,
            status: 'locked',
            steps: period.steps.map((s) => ({ ...s, state: 'done' })),
            can_lock: { allowed: false, reason: 'Kỳ đã khoá.' },
          }),
        );
      }
      if (verb === 'cancel-close') {
        return HttpResponse.json(
          save({
            ...period,
            status: 'open',
            steps: period.steps.map((s) => ({ ...s, state: 'pending', issue: undefined })),
            can_lock: { allowed: false, reason: 'Kỳ chưa bắt đầu chốt.' },
          }),
        );
      }
      return problem(404, 'not_found', 'Không có thao tác này.');
    }),
    // The reconciliation question is answered with the ordinary HITL :resolve; only this
    // mock's own request is handled here, every other one falls through to the inbox mock.
    http.post('*/v1/hitl-requests/:action', async ({ params, request }) => {
      const [id] = String(params.action).split(':');
      if (id !== ISSUE_HITL) return undefined;
      const body = (await request.json()) as { option?: string; note?: string };
      const period = periods.find((p) => p.steps.some((s) => s.issue?.hitl_id === id));
      if (!period) return problem(409, 'invalid_state', 'Yêu cầu đã được trả lời.');
      if (body.option === 'accept_with_note' && !body.note?.trim())
        return problem(422, 'validation_failed', 'Cần ghi chú khi chấp nhận chênh lệch.');
      const accepted = body.option === 'accept_with_note';
      save({
        ...period,
        steps: period.steps.map((s) =>
          s.issue?.hitl_id === id
            ? {
                ...s,
                state: accepted ? 'done' : 'running',
                detail: accepted
                  ? 'Chấp nhận lệch TK 2141 theo ngày Core banking, có ghi chú'
                  : 'Chờ sổ cái điều chỉnh TK 2141; agent đối soát lại khi có bút toán',
                issue: undefined,
              }
            : s,
        ),
        can_lock: accepted
          ? { allowed: true, reason: null }
          : { allowed: false, reason: 'Đang chờ sổ cái điều chỉnh.' },
      });
      return HttpResponse.json({ id, status: 'answered' });
    }),
  ];
}
