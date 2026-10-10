import { http, HttpResponse } from 'msw';
import type {
  Principal,
  SessionReport,
  TileComment,
  TileTask,
  WorkspaceDetail,
  WorkspaceMember,
  WorkspaceTile,
} from '../lib/v1/sessions';
import { workspacesFixture } from './fixtures';

/*
 * MSW handlers for the Phiên làm việc endpoints (lib/v1/sessions.ts, all PROPOSAL). Data is
 * taken from "P02 · Phiên · Notebook / Chia sẻ / Duyệt & phát hành báo cáo"; state lives in
 * memory: re-running clears the stale mark, accepting a proposed tile makes it a normal tile,
 * comments append, a drafted report can be published once its checks pass.
 * GET /v1/workspaces (the list) stays in handlers.ts.
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

const minutesFromNow = (m: number) => new Date(Date.now() + m * 60_000).toISOString();

const YEN = { kind: 'person' as const, name: 'Đỗ Hải Yến' };
const LONG = { kind: 'person' as const, name: 'Phạm Đức Long' };
const ANALYST = { kind: 'agent' as const, name: 'Agent Phân tích' };

const tile = (
  t: Omit<WorkspaceTile, 'comment_count' | 'task_count'> & Partial<WorkspaceTile>,
): WorkspaceTile => ({
  comment_count: 0,
  task_count: 0,
  ...t,
});

function sessionDetailsFixture(): WorkspaceDetail[] {
  const [npl, reconcile, cashflow] = workspacesFixture();
  return [
    {
      ...npl,
      params: [
        { name: 'kỳ', value: 'T9/2026' },
        { name: 'phạm vi', value: 'Bán lẻ' },
      ],
      working: [YEN, LONG, ANALYST],
      refresh: { source_name: 'Core banking', refreshed_at: minutesFromNow(-12) },
      tiles: [
        tile({
          id: 'tile-question',
          seq: 1,
          kind: 'text',
          title: 'Câu hỏi',
          body: 'Vì sao nợ xấu bán lẻ T9 tăng 0,22 điểm, vùng nào và sản phẩm nào đóng góp?',
          author: YEN,
          state: 'ok',
        }),
        tile({
          id: 'tile-npl-region',
          seq: 2,
          kind: 'sql',
          title: 'Nợ xấu theo vùng',
          body: 'SELECT region, npl_ratio FROM metric(NPL_RATIO@4) WHERE period IN (:ky_truoc, :ky_nay) …',
          author: ANALYST,
          state: 'stale',
          comment_count: 2,
          task_count: 1,
        }),
        tile({
          id: 'tile-chart',
          seq: 3,
          kind: 'chart',
          title: 'DH Bắc Bộ tăng mạnh nhất',
          body: 'Cột theo vùng, T8 và T9 · DH Bắc Bộ +0,61 điểm',
          author: ANALYST,
          state: 'ok',
        }),
        tile({
          id: 'tile-decompose',
          seq: 4,
          kind: 'skill',
          title: 'Phân rã biến động v4',
          body: 'Tiêu dùng không TSBĐ đóng góp 55% mức tăng',
          author: ANALYST,
          state: 'stale',
        }),
        tile({
          id: 'tile-restructured',
          seq: 5,
          kind: 'sql',
          title: 'Kiểm tra khoản cơ cấu trong kỳ',
          body: '19 khoản tái cơ cấu chuyển nhóm 2 → 3 trong tuần cuối T9 có thể giải thích 0,12 điểm. Thêm ô này?',
          author: ANALYST,
          state: 'proposed',
        }),
      ],
    },
    {
      ...reconcile,
      params: [{ name: 'kỳ', value: 'T9/2026' }],
      working: [YEN, { kind: 'agent', name: 'Agent Đối soát' }],
      refresh: null,
      tiles: [
        tile({
          id: 'tile-reconcile-question',
          seq: 1,
          kind: 'text',
          title: 'Câu hỏi',
          body: 'Dư nợ T9 trên Core banking có khớp báo cáo đã nộp NHNN không?',
          author: YEN,
          state: 'ok',
        }),
      ],
    },
    { ...cashflow, params: [], working: [YEN], refresh: null, tiles: [] },
  ];
}

function threadsFixture(): { comments: TileComment[]; tasks: TileTask[] } {
  const comment = (
    id: string,
    author: TileComment['author'],
    body: string,
    minutesAgo: number,
  ): TileComment => ({
    id,
    tile_id: 'tile-npl-region',
    author,
    body,
    created_at: minutesFromNow(-minutesAgo),
  });
  // Next Friday, for "hạn thứ Sáu".
  const due = new Date();
  due.setDate(due.getDate() + ((5 - due.getDay() + 7) % 7 || 7));
  due.setHours(17, 0, 0, 0);
  return {
    comments: [
      comment(
        'c-1',
        LONG,
        'Số DH Bắc Bộ có gồm khoản cơ cấu lần 2 không? Theo văn bản mới thì phải tính vào nhóm 3.',
        90,
      ),
      comment(
        'c-2',
        YEN,
        '@Agent Phân tích kiểm tra giúp, và giao Long xác nhận định nghĩa trước thứ Sáu.',
        82,
      ),
      comment(
        'c-3',
        ANALYST,
        'Đã có. Bản v4 chưa tính cơ cấu lần 2 vào nhóm 3; tôi đã đề xuất ô 5 để kiểm tra phần chênh lệch.',
        81,
      ),
    ],
    tasks: [
      {
        id: 'task-1',
        tile_id: 'tile-npl-region',
        title: 'Xác nhận định nghĩa nợ xấu cho khoản cơ cấu lần 2',
        assignee_name: 'Phạm Đức Long',
        due_at: due.toISOString(),
      },
    ],
  };
}

function membersFixture(): WorkspaceMember[] {
  return [
    {
      principal_id: 'u-yen',
      kind: 'user',
      name: 'Đỗ Hải Yến',
      detail: 'chủ phiên',
      scope_limited: false,
      role: 'owner',
    },
    {
      principal_id: 'u-long',
      kind: 'user',
      name: 'Phạm Đức Long',
      detail: 'Rủi ro tín dụng · toàn vùng',
      scope_limited: false,
      role: 'editor',
    },
    {
      principal_id: 'u-chau',
      kind: 'user',
      name: 'Lê Minh Châu',
      detail: 'Chi nhánh Hà Nội · chỉ thấy 3 chi nhánh',
      scope_limited: true,
      role: 'viewer',
    },
    {
      principal_id: 'g-risk',
      kind: 'group',
      name: 'GRP-RISK-ANALYST',
      detail: '14 người',
      scope_limited: false,
      role: 'viewer',
    },
  ];
}

function principalsFixture(): Principal[] {
  return [
    {
      id: 'u-long',
      kind: 'user',
      name: 'Phạm Đức Long',
      detail: 'Rủi ro tín dụng · toàn vùng',
      scope_limited: false,
    },
    {
      id: 'u-chau',
      kind: 'user',
      name: 'Lê Minh Châu',
      detail: 'Chi nhánh Hà Nội · chỉ thấy 3 chi nhánh',
      scope_limited: true,
    },
    {
      id: 'u-an',
      kind: 'user',
      name: 'Trần Bảo An',
      detail: 'Ban điều hành',
      scope_limited: false,
    },
    {
      id: 'g-risk',
      kind: 'group',
      name: 'GRP-RISK-ANALYST',
      detail: '14 người',
      scope_limited: false,
    },
    {
      id: 'g-exec',
      kind: 'group',
      name: 'Ban điều hành',
      detail: '18 người',
      scope_limited: false,
    },
  ];
}

const sharePreview = (members: WorkspaceMember[]) => {
  const limited = members.filter((m) => m.scope_limited);
  if (limited.length === 0)
    return 'Mỗi người chỉ thấy số trong phạm vi của mình. Mọi người nhận đều có quyền xem toàn bộ số của phiên.';
  return `Mỗi người chỉ thấy số trong phạm vi của mình. ${limited
    .map((m) => m.name)
    .join(
      ', ',
    )} sẽ thấy ô 2–4 với số của 3 chi nhánh Hà Nội; câu kết luận về DH Bắc Bộ được ẩn và ghi “ngoài phạm vi của bạn”.`;
};

function draftReport(workspace: WorkspaceDetail, id = `report-${workspace.id}`): SessionReport {
  return {
    id,
    workspace_id: workspace.id,
    status: 'pending_review',
    drafted_by: 'Agent Báo cáo',
    tile_count: workspace.tiles.filter((t) => t.state !== 'proposed').length,
    drafted_at: new Date().toISOString(),
    reviewer_name: 'Phạm Đức Long',
    document: {
      heading: 'Nợ xấu bán lẻ tháng 9/2026',
      summary:
        'Nợ xấu bán lẻ T9 là 1,84%, tăng 0,22 điểm so với T8. 74% mức tăng đến từ Duyên hải Bắc Bộ, chủ yếu vay tiêu dùng không tài sản bảo đảm và 19 khoản tái cơ cấu chuyển nhóm.',
      figures: [
        { value: '1,84%', grounded: true },
        { value: '+0,22 điểm', grounded: true },
        { value: '74%', grounded: true },
        { value: '19 khoản', grounded: false },
      ],
      recommendation:
        'Đề xuất: rà soát điều kiện vay tiêu dùng không TSBĐ tại Hải Phòng và Quảng Ninh; xác nhận cách tính khoản cơ cấu lần 2 trước kỳ báo cáo NHNN.',
    },
    checks: [
      { ok: true, title: 'Số có căn cứ', detail: '11/12 số gắn với ô hoặc chỉ số' },
      {
        ok: false,
        title: '“19 khoản” chưa gắn nguồn',
        detail: 'Gắn với ô 5 hoặc xoá khỏi báo cáo',
      },
      { ok: true, title: 'Khớp số đã nộp', detail: '1,84% khớp báo cáo NHNN T9' },
      {
        ok: true,
        title: 'Quyền người nhận',
        detail: '32 người nhận đều có quyền xem số liệu bán lẻ toàn vùng',
      },
      {
        ok: true,
        title: 'Không có dữ liệu cá nhân',
        detail: 'Không có tên khách hàng hay số tài khoản',
      },
    ],
    recipients_text: 'GRP-RISK-ANALYST, Ban điều hành · email (PDF) và trong ứng dụng',
  };
}

export function createSessionHandlers() {
  let workspaces = sessionDetailsFixture();
  const threads = threadsFixture();
  let comments = threads.comments;
  const tasks = threads.tasks;
  let members = membersFixture();
  // The report the Cần bạn card "Báo cáo rủi ro tháng 9 đã soạn xong" points at (source_ref).
  let reports: SessionReport[] = [draftReport(workspaces[0], 'report-risk-sept')];

  const find = (id: unknown) => workspaces.find((w) => w.id === id);
  const save = (next: WorkspaceDetail) => {
    workspaces = workspaces.map((w) => (w.id === next.id ? next : w));
    return HttpResponse.json(next);
  };
  const resolveMembers = (input: { principal_id: string; role: WorkspaceMember['role'] }[]) => {
    const known = [
      ...members,
      ...principalsFixture().map((p) => ({ ...p, principal_id: p.id, role: 'viewer' as const })),
    ];
    return input.flatMap((m) => {
      const base = known.find((k) => k.principal_id === m.principal_id);
      return base
        ? [
            {
              principal_id: base.principal_id,
              kind: base.kind,
              name: base.name,
              detail: base.detail,
              scope_limited: base.scope_limited,
              role: m.role,
            },
          ]
        : [];
    });
  };
  const notFound = () => problem(404, 'not_found', 'Không tìm thấy phiên làm việc.');

  return [
    http.get('*/v1/workspaces/:id', ({ params }) => {
      const ws = find(params.id);
      return ws ? HttpResponse.json(ws) : notFound();
    }),

    http.post('*/v1/workspaces/:action', async ({ params, request }) => {
      // MSW reads ":refresh" as part of the id segment: "<id>:refresh".
      const [id, verb] = String(params.action).split(':');
      if (verb !== 'refresh') return problem(404, 'not_found', 'Không có thao tác này.');
      const ws = find(id);
      if (!ws) return notFound();
      const { tile_ids } = (await request.json()) as { tile_ids: string[] };
      return save({
        ...ws,
        refresh: null,
        last_refreshed_at: new Date().toISOString(),
        tiles: ws.tiles.map((t) =>
          tile_ids.includes(t.id) && t.state === 'stale' ? { ...t, state: 'ok' } : t,
        ),
      });
    }),

    http.post('*/v1/workspaces/:id/tiles/:action', ({ params }) => {
      const ws = find(params.id);
      if (!ws) return notFound();
      const [tileId, verb] = String(params.action).split(':');
      const target = ws.tiles.find((t) => t.id === tileId);
      if (!target) return problem(404, 'not_found', 'Không tìm thấy ô.');
      if (target.state !== 'proposed')
        return problem(409, 'invalid_state', 'Ô này không còn là đề xuất.');
      if (verb === 'accept')
        return save({
          ...ws,
          tiles: ws.tiles.map((t) => (t.id === tileId ? { ...t, state: 'ok' } : t)),
        });
      if (verb === 'dismiss')
        return save({ ...ws, tiles: ws.tiles.filter((t) => t.id !== tileId) });
      return problem(404, 'not_found', 'Không có thao tác này.');
    }),

    http.get('*/v1/workspaces/:id/tiles/:tileId/comments', ({ params }) => {
      if (!find(params.id)) return notFound();
      return HttpResponse.json({
        items: comments.filter((c) => c.tile_id === params.tileId),
        tasks: tasks.filter((t) => t.tile_id === params.tileId),
      });
    }),

    http.post('*/v1/workspaces/:id/tiles/:tileId/comments', async ({ params, request }) => {
      const ws = find(params.id);
      if (!ws) return notFound();
      const { body } = (await request.json()) as { body: string };
      if (!body?.trim()) return problem(422, 'validation_failed', 'Bình luận trống.');
      const created: TileComment = {
        id: `c-${comments.length + 1}`,
        tile_id: String(params.tileId),
        author: YEN,
        body: body.trim(),
        created_at: new Date().toISOString(),
      };
      comments = [...comments, created];
      workspaces = workspaces.map((w) =>
        w.id === ws.id
          ? {
              ...w,
              tiles: w.tiles.map((t) =>
                t.id === params.tileId ? { ...t, comment_count: t.comment_count + 1 } : t,
              ),
            }
          : w,
      );
      return HttpResponse.json(created, { status: 201 });
    }),

    http.get('*/v1/workspaces/:id/sharing', ({ params }) => {
      const ws = find(params.id);
      if (!ws) return notFound();
      return HttpResponse.json({
        members,
        preview: sharePreview(members),
        link: `/analysis/sessions/${ws.id}`,
      });
    }),

    http.put('*/v1/workspaces/:id/sharing', async ({ params, request }) => {
      const ws = find(params.id);
      if (!ws) return notFound();
      const body = (await request.json()) as {
        members: { principal_id: string; role: WorkspaceMember['role'] }[];
      };
      if (!body.members.some((m) => m.role === 'owner'))
        return problem(422, 'validation_failed', 'Phiên cần một người sở hữu.');
      members = resolveMembers(body.members);
      return HttpResponse.json({
        members,
        preview: sharePreview(members),
        link: `/analysis/sessions/${ws.id}`,
      });
    }),

    http.post('*/v1/workspaces/:id/:action', async ({ params, request }) => {
      if (params.action !== 'sharing:preview')
        return problem(404, 'not_found', 'Không có thao tác này.');
      if (!find(params.id)) return notFound();
      const body = (await request.json()) as {
        members: { principal_id: string; role: WorkspaceMember['role'] }[];
      };
      return HttpResponse.json({ preview: sharePreview(resolveMembers(body.members)) });
    }),

    http.get('*/v1/principals', ({ request }) => {
      const q = (new URL(request.url).searchParams.get('q') ?? '').toLowerCase();
      return page(principalsFixture().filter((p) => p.name.toLowerCase().includes(q)));
    }),

    http.get('*/v1/workspaces/:id/session-reports', ({ params }) =>
      find(params.id) ? page(reports.filter((r) => r.workspace_id === params.id)) : notFound(),
    ),

    http.post('*/v1/workspaces/:id/session-reports', ({ params }) => {
      const ws = find(params.id);
      if (!ws) return notFound();
      const report = draftReport(ws);
      reports = [...reports.filter((r) => r.id !== report.id), report];
      return HttpResponse.json(report, { status: 201 });
    }),

    http.get('*/v1/session-reports/:id', ({ params }) => {
      const report = reports.find((r) => r.id === params.id);
      return report
        ? HttpResponse.json(report)
        : problem(404, 'not_found', 'Không tìm thấy báo cáo.');
    }),

    http.post('*/v1/session-reports/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      const report = reports.find((r) => r.id === id);
      if (!report) return problem(404, 'not_found', 'Không tìm thấy báo cáo.');
      if (report.status !== 'pending_review')
        return problem(409, 'invalid_state', 'Báo cáo không ở trạng thái chờ duyệt.');
      let next: SessionReport;
      if (verb === 'check') {
        // The mock's checks do not change on their own; a re-check answers with the same result.
        next = { ...report };
      } else if (verb === 'publish') {
        if (report.checks.some((c) => !c.ok))
          return problem(409, 'checks_failed', 'Còn kiểm tra chưa đạt, chưa phát hành được.');
        next = { ...report, status: 'published' };
      } else if (verb === 'request-changes') {
        const { note } = (await request.json()) as { note?: string };
        if (!note?.trim())
          return problem(422, 'validation_failed', 'Yêu cầu sửa cần ghi nội dung.');
        next = { ...report, status: 'changes_requested' };
      } else return problem(404, 'not_found', 'Không có thao tác này.');
      reports = reports.map((r) => (r.id === id ? next : r));
      return HttpResponse.json(next);
    }),
  ];
}
