import { http, HttpResponse } from 'msw';
import type { Collaboration } from '../lib/v1/collaboration';

/*
 * "Cộng tác · Duyệt nhiều người" (2084:7085) for the dev mock: proposal-214 with four people on
 * its chain and the discussion so far, and the two-step chain of pr-0318. A comment that @mentions Agent Đối soát gets its answer.
 * Decisions go through the proposal mock in handlers.ts.
 */
function collaborationFixture(): Collaboration {
  return {
    proposal_id: 'proposal-214',
    hitl_id: 'hitl-adjust-214',
    title: 'Điều chỉnh nợ quá hạn của 214 khoản vay',
    steps: [
      {
        name: 'Agent Đối soát',
        initials: null,
        role_text: 'Đề xuất · 09:48',
        state: 'sent',
        me: false,
      },
      {
        name: 'Nguyễn Thu Hà',
        initials: 'NH',
        role_text: 'Người lập kiểm tra · 10:10',
        state: 'agreed',
        me: false,
      },
      {
        name: 'Trần Quốc An',
        initials: 'TA',
        role_text: 'Chủ sở hữu dữ liệu · bắt buộc',
        state: 'viewing',
        me: true,
      },
      {
        name: 'Đỗ Hải Yến',
        initials: 'HY',
        role_text: 'Kiểm soát · bắt buộc vì kỳ đã nộp',
        state: 'waiting',
        me: false,
      },
    ],
    deadline_text:
      'Hạn duyệt 17:00 hôm nay (trước khi Agent Báo cáo khoá bản tin). Nếu quá hạn: tự nhắc qua Teams và chuyển cho người dự phòng Phạm Đức Long.',
    comments: [
      {
        author: 'Nguyễn Thu Hà',
        initials: 'NH',
        at: '10:10',
        text: 'Đã kiểm tra mẫu 20/187 khoản tính lại ngày quá hạn, khớp hồ sơ.',
      },
      {
        author: 'Trần Quốc An',
        initials: 'TA',
        at: '10:22',
        text: '@Agent Đối soát 19 khoản tái cơ cấu lấy từ nguồn nào? Có cần chờ bảng tải tay của chi nhánh không?',
      },
      {
        author: 'Agent Đối soát',
        initials: null,
        at: '10:23',
        text: 'Lấy từ Core banking (bảng restructure_log). Bảng tải tay upload.restructured_loans_t9 khớp 19/19, không cần chờ.',
      },
      {
        author: 'Đỗ Hải Yến',
        initials: 'HY',
        at: '10:31',
        text: 'Kỳ đã nộp: cần ghi chú “điều chỉnh sau nộp” vào bản derived trước khi tôi duyệt.',
      },
    ],
    log: [
      { at: '09:48', text: 'Agent Đối soát tạo đề xuất v4' },
      { at: '10:10', text: 'Nguyễn Thu Hà đồng ý (bước 1/3)' },
      { at: '10:23', text: 'Agent trả lời câu hỏi nguồn' },
      { at: '10:31', text: 'Đỗ Hải Yến yêu cầu thêm ghi chú' },
      { at: '10:33', text: 'Agent Đối soát thêm ghi chú vào v4.1' },
    ],
    rule_text:
      'Kỳ chưa nộp: chủ sở hữu dữ liệu. Kỳ đã nộp: thêm Kiểm soát. Người đề xuất (kể cả agent) không tự duyệt.',
  };
}

function nplV5Fixture(): Collaboration {
  return {
    proposal_id: 'pr-0318',
    hitl_id: 'hitl-npl-v5',
    title: 'NPL_RATIO v5 · tính khoản cơ cấu lần 2 vào nhóm 3',
    steps: [
      { name: 'Agent Phân tích', initials: null, role_text: 'Đề xuất', state: 'sent', me: false },
      {
        name: 'Phạm Đức Long',
        initials: 'PL',
        role_text: 'Chủ sở hữu chỉ số',
        state: 'viewing',
        me: true,
      },
      {
        name: 'Trần Quốc An',
        initials: 'TA',
        role_text: 'Kiểm soát báo cáo · bắt buộc vì chạm số đã nộp',
        state: 'waiting',
        me: false,
      },
    ],
    deadline_text:
      'Hạn duyệt sau 3 ngày. Nếu quá hạn: đề xuất hết hiệu lực, agent không tự áp dụng.',
    comments: [],
    log: [{ at: '08:12', text: 'Agent Phân tích tạo đề xuất v5' }],
    rule_text:
      'Chạm số đã nộp: chủ sở hữu chỉ số rồi Kiểm soát báo cáo. Người đề xuất không tự duyệt.',
  };
}

export function createCollaborationHandlers() {
  let chains = [collaborationFixture(), nplV5Fixture()];
  return [
    http.get('*/v1/proposals/:id/collaboration', ({ params }) => {
      const chain = chains.find((c) => c.proposal_id === params.id);
      return chain
        ? HttpResponse.json(chain)
        : HttpResponse.json(
            {
              title: 'not_found',
              status: 404,
              code: 'not_found',
              detail: 'Đề xuất này không có luồng duyệt nhiều người.',
            },
            { status: 404, headers: { 'Content-Type': 'application/problem+json' } },
          );
    }),
    http.post('*/v1/proposals/:id/comments', async ({ params, request }) => {
      const collaboration = chains.find((c) => c.proposal_id === params.id);
      if (!collaboration) return undefined;
      const { text } = (await request.json()) as { text: string };
      const now = new Date();
      const at = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}`;
      const comments = [
        ...collaboration.comments,
        { author: 'Trần Quốc An', initials: 'TA', at, text },
      ];
      if (text.includes('@Agent Đối soát'))
        comments.push({
          author: 'Agent Đối soát',
          initials: null,
          at,
          text: 'Đã ghi nhận; tôi trả lời trong vài phút kèm nguồn.',
        });
      const updated = { ...collaboration, comments };
      chains = chains.map((c) => (c.proposal_id === updated.proposal_id ? updated : c));
      return HttpResponse.json(updated, { status: 201 });
    }),
  ];
}
