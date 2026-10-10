import { http, HttpResponse } from 'msw';
import type {
  PeriodClose,
  PeriodComparison,
  PeriodSummary,
  PeriodVersion,
  RetroChange,
} from '../lib/v1/periods';

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

// "P10 · Thay đổi hồi tố" (2304:19466): T8 changed at the source after it was locked.
function retroChanges(): RetroChange[] {
  return [
    {
      id: 'retro-012',
      code: 'RC-012',
      period_id: 'period-2026-08',
      period_code: 'T8/2026',
      status: 'open',
      title: 'Dữ liệu kỳ T8 thay đổi sau khi khoá',
      detected_text:
        'Phát hiện 07/10 08:12 khi Agent Đối soát so mã băm · 214 khoản vay ở Core banking đổi ngày quá hạn',
      source_changes: [
        {
          table: 'LD_LOANS',
          rows: 214,
          fields: 'overdue_days, debt_group',
          changed_at_text: '03/10 22:40 · sửa lô',
        },
        { table: 'LD_REPAYMENTS', rows: 38, fields: 'paid_date', changed_at_text: '03/10 22:41' },
      ],
      impact: [
        {
          metric: 'Nợ xấu bán lẻ',
          locked: '1,62%',
          recalculated: '1,66%',
          delta: '+0,04 điểm',
          published_to: 'NHNN T8',
        },
        {
          metric: 'Dư nợ quá hạn 30+',
          locked: '5.812 tỷ',
          recalculated: '5.890 tỷ',
          delta: '+1,34%',
          published_to: null,
        },
        {
          metric: 'Số khoản nhóm 3–5',
          locked: '24.102',
          recalculated: '24.316',
          delta: '+214',
          published_to: null,
        },
      ],
      next_version_label: 'T8 · v2',
      version_note:
        'T8 · v1 giữ nguyên làm bản đã công bố. T8 · v2 chứa số tính lại; câu hỏi “theo số đã công bố” vẫn trả v1, câu hỏi “theo số mới nhất” trả v2.',
      escalation: {
        title: 'Vì nợ xấu đã công bố bị ảnh hưởng',
        text: 'Cần Trần Quốc An duyệt và Pháp chế quyết có gửi đính chính cho NHNN không.',
      },
      decision_note: null,
    },
  ];
}

// "P10 · So sánh hai bản" (2304:19616): T8 as published against its retro adjustment.
const VERSIONS: PeriodVersion[] = [
  { id: 'pv-2026-08-v1', label: 'T8/2026 · v1 · đã công bố', note: 'đã công bố 05/09' },
  { id: 'pv-2026-08-v2', label: 'T8/2026 · v2 · điều chỉnh', note: 'điều chỉnh hồi tố 07/10' },
  { id: 'pv-2026-07-v1', label: 'T7/2026 · v1 · đã công bố', note: 'đã công bố 05/08' },
];

const FIGURES: Record<string, Record<string, string>> = {
  'Nợ xấu bán lẻ': { 'pv-2026-08-v1': '1,62%', 'pv-2026-08-v2': '1,66%', 'pv-2026-07-v1': '1,58%' },
  'Dư nợ quá hạn 30+': {
    'pv-2026-08-v1': '5.812 tỷ',
    'pv-2026-08-v2': '5.890 tỷ',
    'pv-2026-07-v1': '5.640 tỷ',
  },
  'Dư nợ cho vay': {
    'pv-2026-08-v1': '180.416 tỷ',
    'pv-2026-08-v2': '180.416 tỷ',
    'pv-2026-07-v1': '178.902 tỷ',
  },
  'Tỷ lệ dự phòng': {
    'pv-2026-08-v1': '1,44%',
    'pv-2026-08-v2': '1,44%',
    'pv-2026-07-v1': '1,43%',
  },
  'Số khoản nhóm 3–5': {
    'pv-2026-08-v1': '24.102',
    'pv-2026-08-v2': '24.316',
    'pv-2026-07-v1': '23.870',
  },
};

/** The design's gaps for v1 → v2; other pairs only say whether the figure moved. */
const V1_TO_V2: Record<string, string> = {
  'Nợ xấu bán lẻ': '+0,04 điểm',
  'Dư nợ quá hạn 30+': '+78 tỷ',
  'Số khoản nhóm 3–5': '+214',
};

function compare(leftId: string, rightId: string): PeriodComparison | null {
  const left = VERSIONS.find((v) => v.id === leftId);
  const right = VERSIONS.find((v) => v.id === rightId);
  if (!left || !right) return null;
  const pair = [left.id, right.id].sort().join('|') === 'pv-2026-08-v1|pv-2026-08-v2';
  let explained = false;
  return {
    left,
    right,
    versions: VERSIONS,
    rows: Object.entries(FIGURES).map(([metric, values]) => {
      const changed = values[left.id] !== values[right.id];
      const retro = pair && changed;
      // The first moved figure names its cause; the rest say "như trên", as in the design.
      const reason = retro
        ? explained
          ? 'như trên'
          : '214 khoản đổi ngày quá hạn (RC-012)'
        : null;
      if (retro) explained = true;
      return {
        metric,
        left: values[left.id],
        right: values[right.id],
        delta: !changed ? '0' : pair ? (V1_TO_V2[metric] ?? '≠') : '≠',
        changed,
        reason: changed ? (reason ?? 'khác kỳ') : null,
        retro_change_id: retro ? 'retro-012' : null,
      };
    }),
  };
}

export function createPeriodHandlers() {
  let retro = retroChanges();
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
    // Before /v1/periods/:id, which would otherwise take "compare" as an id.
    http.get('*/v1/periods/compare', ({ request }) => {
      const params = new URL(request.url).searchParams;
      const result = compare(params.get('left') ?? '', params.get('right') ?? '');
      return result ? HttpResponse.json(result) : problem(404, 'not_found', 'Không tìm thấy bản.');
    }),
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
    http.get('*/v1/retro-changes', ({ request }) => {
      const status = new URL(request.url).searchParams.get('status');
      return HttpResponse.json({
        items: retro.filter((r) => !status || r.status === status),
        next_page_token: '',
      });
    }),
    http.get('*/v1/retro-changes/:id', ({ params }) => {
      const change = retro.find((r) => r.id === params.id);
      return change
        ? HttpResponse.json(change)
        : problem(404, 'not_found', 'Không tìm thấy thay đổi hồi tố.');
    }),
    http.post('*/v1/retro-changes/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      const change = retro.find((r) => r.id === id);
      if (!change || verb !== 'decide')
        return problem(404, 'not_found', 'Không tìm thấy thay đổi hồi tố.');
      if (change.status !== 'open') return problem(409, 'invalid_state', 'Thay đổi đã được quyết.');
      const body = (await request.json()) as {
        decision: 'new_version' | 'no_change';
        decision_note: string;
      };
      if (!body.decision_note?.trim())
        return problem(422, 'validation_failed', 'Cần lý do cho quyết định.');
      const decided: RetroChange = {
        ...change,
        status: body.decision,
        decision_note: body.decision_note,
      };
      retro = retro.map((r) => (r.id === id ? decided : r));
      // A new version is one more adjustment on the locked period (BR-43: v1 stays).
      if (body.decision === 'new_version') {
        periods = periods.map((p) =>
          p.id === change.period_id ? { ...p, adjustment_count: p.adjustment_count + 1 } : p,
        );
      }
      return HttpResponse.json(decided);
    }),
  ];
}
