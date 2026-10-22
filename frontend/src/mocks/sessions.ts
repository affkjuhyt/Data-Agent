import { http, HttpResponse } from 'msw';
import type { components } from '../lib/v1/schema';
import type {
  Author,
  Principal,
  SessionReport,
  TileComment,
  TileTask,
  WorkspaceDetailWire,
  WorkspaceMember,
} from '../lib/v1/sessions';
import { workspacesFixture } from './fixtures';

/*
 * MSW handlers for the Phiên làm việc endpoints (lib/v1/sessions.ts): the served ones answer with
 * the api's shapes, the PROPOSAL reads with the screen's. Data is
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

type TileWire = WorkspaceDetailWire['tiles'][number];

const tile = (
  workspaceId: string,
  t: Pick<TileWire, 'id' | 'position' | 'kind' | 'title' | 'spec'> & {
    author: Author;
    stale?: boolean;
    suggested?: boolean;
    comment_count?: number;
    task_count?: number;
  },
): TileWire => ({
  id: t.id,
  workspace_id: workspaceId,
  position: t.position,
  kind: t.kind,
  title: t.title,
  spec: t.spec,
  depends_on: [],
  author_kind: t.author.kind === 'agent' ? 'agent' : 'user',
  author_name: t.author.name,
  agent_suggestion_state: t.suggested ? 'suggested' : null,
  latest_snapshot_id: null,
  stale: t.stale ?? false,
  comment_count: t.comment_count ?? 0,
  task_count: t.task_count ?? 0,
  created_at: minutesFromNow(-120),
  updated_at: minutesFromNow(-30),
});

const participant = (workspaceId: string, id: string, type: 'user' | 'agent') => ({
  id: `p-${workspaceId}-${id}`,
  workspace_id: workspaceId,
  principal_type: type,
  principal_id: id,
  role: type === 'agent' ? 'agent_helper' : 'editor',
  joined_at: minutesFromNow(-120),
});

function sessionDetailsFixture(): WorkspaceDetailWire[] {
  const [npl, reconcile, cashflow] = workspacesFixture();
  const detail = (
    ws: typeof npl,
    rest: Pick<WorkspaceDetailWire, 'params' | 'working' | 'refresh' | 'tiles'>,
  ): WorkspaceDetailWire => {
    const { participants: _counts, agent_state: _state, ...row } = ws;
    return {
      ...row,
      ...rest,
      permission: 'owner',
      participants: (rest.working ?? []).map((a, i) =>
        participant(ws.id, `${a.kind}-${i}`, a.kind === 'agent' ? 'agent' : 'user'),
      ),
    };
  };
  return [
    detail(npl, {
      params: { kỳ: 'T9/2026', 'phạm vi': 'Bán lẻ' },
      working: [YEN, LONG, ANALYST],
      refresh: { source_name: 'Core banking', refreshed_at: minutesFromNow(-12) },
      tiles: [
        tile(npl.id, {
          id: 'tile-question',
          position: 1,
          kind: 'question',
          title: 'Câu hỏi',
          spec: {
            question: 'Vì sao nợ xấu bán lẻ T9 tăng 0,22 điểm, vùng nào và sản phẩm nào đóng góp?',
            answer_id: 'answer-npl-session',
          },
          author: YEN,
        }),
        tile(npl.id, {
          id: 'tile-npl-region',
          position: 2,
          kind: 'sql',
          title: 'Nợ xấu theo vùng',
          spec: {
            sql: 'SELECT region, npl_ratio FROM metric(NPL_RATIO@4) WHERE period IN (:ky_truoc, :ky_nay) …',
          },
          author: ANALYST,
          stale: true,
          comment_count: 2,
          task_count: 1,
        }),
        tile(npl.id, {
          id: 'tile-chart',
          position: 3,
          kind: 'chart',
          title: 'DH Bắc Bộ tăng mạnh nhất',
          spec: { summary: 'Cột theo vùng, T8 và T9 · DH Bắc Bộ +0,61 điểm' },
          author: ANALYST,
        }),
        tile(npl.id, {
          id: 'tile-decompose',
          position: 4,
          kind: 'skill_run',
          title: 'Phân rã biến động v4',
          spec: { summary: 'Tiêu dùng không TSBĐ đóng góp 55% mức tăng' },
          author: ANALYST,
          stale: true,
        }),
        tile(npl.id, {
          id: 'tile-restructured',
          position: 5,
          kind: 'sql',
          title: 'Kiểm tra khoản cơ cấu trong kỳ',
          spec: {
            summary:
              '19 khoản tái cơ cấu chuyển nhóm 2 → 3 trong tuần cuối T9 có thể giải thích 0,12 điểm. Thêm ô này?',
          },
          author: ANALYST,
          suggested: true,
        }),
      ],
    }),
    detail(reconcile, {
      params: { kỳ: 'T9/2026' },
      working: [YEN, { kind: 'agent', name: 'Agent Đối soát' }],
      refresh: null,
      tiles: [
        tile(reconcile.id, {
          id: 'tile-reconcile-question',
          position: 1,
          kind: 'question',
          title: 'Câu hỏi',
          spec: { question: 'Dư nợ T9 trên Core banking có khớp báo cáo đã nộp NHNN không?' },
          author: YEN,
        }),
      ],
    }),
    detail(cashflow, { params: {}, working: [YEN], refresh: null, tiles: [] }),
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

function draftReport(workspace: WorkspaceDetailWire, id = `report-${workspace.id}`): SessionReport {
  return {
    id,
    workspace_id: workspace.id,
    status: 'pending_review',
    drafted_by: 'Agent Báo cáo',
    tile_count: workspace.tiles.filter((t) => t.agent_suggestion_state !== 'suggested').length,
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
    // A report waiting for review has its review request in Cần bạn.
    hitl_request_id: `hitl-${id}`,
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
  const save = (next: WorkspaceDetailWire) => {
    workspaces = workspaces.map((w) => (w.id === next.id ? next : w));
  };
  const accepted = (id: string) =>
    HttpResponse.json({ id, status: 'queued', poll_after_ms: 1000 }, { status: 202 });
  /** The api's report row (ReportView) for a drafted review. */
  const reportRow = (
    r: SessionReport,
  ): components['schemas']['ReportView'] & Partial<SessionReport> => ({
    id: r.id,
    workspace_id: r.workspace_id,
    version: 1,
    title: r.document.heading,
    body_ref: `s3://reports/${r.id}.md`,
    checks: r.checks,
    status: r.status,
    approved_by: null,
    published_at: r.status === 'published' ? new Date().toISOString() : null,
    hitl_request_id: r.status === 'pending_review' ? r.hitl_request_id : null,
    created_at: r.drafted_at,
    updated_at: new Date().toISOString(),
    drafted_by: r.drafted_by,
    tile_count: r.tile_count,
    reviewer_name: r.reviewer_name,
    document: r.document,
    recipients_text: r.recipients_text,
  });
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
    // A new workspace (WorkspaceView); an investigation names its alert in params.
    http.post('*/v1/workspaces', async ({ request }) => {
      const body = (await request.json()) as components['schemas']['WorkspaceCreate'];
      const params = (body.params ?? {}) as Record<string, string>;
      const now = new Date().toISOString();
      return HttpResponse.json(
        {
          id: params.alert_id ? `ws-alert-${params.alert_id}` : `ws-new-${workspaces.length}`,
          data_space_id: 'ds-rui-ro-tin-dung',
          owner_id: 'user-me',
          title: body.title,
          description: body.description ?? null,
          params,
          template_of_skill_id: null,
          created_from: body.created_from ?? 'blank',
          created_from_ref: body.created_from_ref ?? null,
          tile_count: 0,
          last_refreshed_at: null,
          status: 'active',
          created_at: now,
          updated_at: now,
        } satisfies components['schemas']['WorkspaceView'],
        { status: 201 },
      );
    }),

    http.get('*/v1/workspaces/:id', ({ params }) => {
      const ws = find(params.id);
      return ws ? HttpResponse.json(ws) : notFound();
    }),

    // Ask / Explore "Thêm vào phiên": append a tile (idempotent enough for UI tests).
    http.post('*/v1/workspaces/:id/tiles', async ({ params, request }) => {
      const ws = find(params.id);
      if (!ws) return notFound();
      const body = (await request.json()) as components['schemas']['TileCreate'];
      const answerId =
        typeof body.spec === 'object' && body.spec && 'answer_id' in body.spec
          ? String((body.spec as { answer_id?: unknown }).answer_id ?? '')
          : '';
      const existing = answerId
        ? ws.tiles.find((t) => {
            const spec = t.spec as { answer_id?: string } | null;
            return spec?.answer_id === answerId;
          })
        : undefined;
      if (existing) return HttpResponse.json(existing, { status: 200 });
      const position = Math.max(0, ...ws.tiles.map((t) => t.position)) + 1;
      const next = {
        id: `tile-ask-${position}`,
        workspace_id: ws.id,
        position,
        kind: body.kind,
        title: body.title ?? null,
        spec: body.spec,
        depends_on: body.depends_on ?? [],
        author_kind: 'user',
        author_name: YEN.name,
        agent_suggestion_state: null,
        latest_snapshot_id: null,
        stale: false,
        comment_count: 0,
        task_count: 0,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      } as TileWire;
      save({ ...ws, tiles: [...ws.tiles, next] });
      return HttpResponse.json(next, { status: 201 });
    }),

    http.post('*/v1/workspaces/:action', ({ params }) => {
      // MSW reads ":refresh" as part of the id segment: "<id>:refresh".
      const [id, verb] = String(params.action).split(':');
      if (verb !== 'refresh') return problem(404, 'not_found', 'Không có thao tác này.');
      const ws = find(id);
      if (!ws) return notFound();
      // The api re-runs in the background; the mock is done before the workspace is read again.
      save({
        ...ws,
        refresh: null,
        last_refreshed_at: new Date().toISOString(),
        tiles: ws.tiles.map((t) => ({ ...t, stale: false })),
      });
      return accepted(`refresh-${id}`);
    }),

    http.patch('*/v1/tiles/:id', async ({ params, request }) => {
      const ws = workspaces.find((w) => w.tiles.some((t) => t.id === params.id));
      const target = ws?.tiles.find((t) => t.id === params.id);
      if (!ws || !target) return problem(404, 'not_found', 'Không tìm thấy ô.');
      const body = (await request.json()) as components['schemas']['TilePatch'];
      if (body.agent_suggestion_state && target.agent_suggestion_state !== 'suggested')
        return problem(409, 'invalid_state', 'Ô này không còn là đề xuất.');
      const next = { ...target, ...body } as TileWire;
      save({ ...ws, tiles: ws.tiles.map((t) => (t.id === target.id ? next : t)) });
      return HttpResponse.json(next);
    }),

    http.get('*/v1/workspaces/:id/tiles/:tileId/comments', ({ params }) => {
      if (!find(params.id)) return notFound();
      return HttpResponse.json({
        items: comments.filter((c) => c.tile_id === params.tileId),
        tasks: tasks.filter((t) => t.tile_id === params.tileId),
      });
    }),

    http.get('*/v1/tiles/:tileId/comments', ({ params }) =>
      HttpResponse.json({
        items: comments.filter((c) => c.tile_id === params.tileId),
        tasks: tasks.filter((t) => t.tile_id === params.tileId),
        next_page_token: '',
      }),
    ),

    http.post('*/v1/tiles/:tileId/comments', async ({ params, request }) => {
      const ws = workspaces.find((w) => w.tiles.some((t) => t.id === params.tileId));
      if (!ws) return problem(404, 'not_found', 'Không tìm thấy ô.');
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
      save({
        ...ws,
        tiles: ws.tiles.map((t) =>
          t.id === params.tileId ? { ...t, comment_count: (t.comment_count ?? 0) + 1 } : t,
        ),
      });
      const view: components['schemas']['CommentView'] = {
        id: created.id,
        workspace_id: ws.id,
        tile_id: created.tile_id,
        parent_id: null,
        author_id: 'u-yen',
        body: created.body,
        mentions: [],
        assignee_id: null,
        due_at: null,
        status: 'open',
        resolved_at: null,
        agent_run_id: null,
        created_at: created.created_at,
        updated_at: created.created_at,
      };
      return HttpResponse.json(view, { status: 201 });
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

    http.get('*/v1/workspaces/:id/shares', ({ params }) => {
      const ws = find(params.id);
      if (!ws) return notFound();
      return HttpResponse.json({
        items: members.map((member, index) => ({
          id: `share-${index}`,
          workspace_id: ws.id,
          principal_type: member.kind,
          principal_id: member.principal_id,
          permission: member.role === 'editor' ? 'edit' : 'view',
          granted_by: 'u-yen',
          expires_at: null,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        })),
        next_page_token: '',
        members,
        preview: sharePreview(members),
        link: `/analysis/sessions/${ws.id}`,
      });
    }),

    http.put('*/v1/workspaces/:id/shares', async ({ params, request }) => {
      const ws = find(params.id);
      if (!ws) return notFound();
      const { shares } = (await request.json()) as components['schemas']['SharesPut'];
      // The owner is the workspace's and stays; the shares replace everyone else (A1.3).
      const owners = members.filter((m) => m.role === 'owner');
      members = [
        ...owners,
        ...resolveMembers(
          shares.map((g) => ({
            principal_id: g.principal_id,
            role: g.permission === 'edit' ? ('editor' as const) : ('viewer' as const),
          })),
        ),
      ];
      return page(
        shares.map((g, i) => ({
          id: `share-${i}`,
          workspace_id: ws.id,
          principal_type: g.principal_type,
          principal_id: g.principal_id,
          permission: g.permission,
          granted_by: 'u-yen',
          expires_at: g.expires_at ?? null,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        })),
      );
    }),

    http.post('*/v1/workspaces/:id/shares:preview', async ({ params, request }) => {
      if (!find(params.id)) return notFound();
      const body = (await request.json()) as {
        shares: { principal_id: string; permission: 'view' | 'edit' }[];
      };
      return HttpResponse.json({
        preview: sharePreview(
          resolveMembers(
            body.shares.map((share) => ({
              principal_id: share.principal_id,
              role: share.permission === 'edit' ? ('editor' as const) : ('viewer' as const),
            })),
          ),
        ),
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

    http.get('*/v1/session-reports', ({ request }) => {
      const workspaceId = new URL(request.url).searchParams.get('workspace_id');
      return page(
        (workspaceId ? reports.filter((r) => r.workspace_id === workspaceId) : reports).map(
          reportRow,
        ),
      );
    }),

    http.post('*/v1/workspaces/:id/reports', ({ params }) => {
      const ws = find(params.id);
      if (!ws) return notFound();
      const report = draftReport(ws);
      reports = [...reports.filter((r) => r.id !== report.id), report];
      return HttpResponse.json(reportRow(report), { status: 201 });
    }),

    http.get('*/v1/session-reports/:id', ({ params }) => {
      const report = reports.find((r) => r.id === params.id);
      return report
        ? HttpResponse.json(reportRow(report))
        : problem(404, 'not_found', 'Không tìm thấy báo cáo.');
    }),

    http.get('*/v1/session-reports/:id/document', ({ params }) => {
      const report = reports.find((r) => r.id === params.id);
      if (!report) return problem(404, 'not_found', 'Không tìm thấy báo cáo.');
      const figures = report.document.figures
        .map((figure) => `- ${figure.value} | ${figure.grounded ? 'grounded' : 'ungrounded'}`)
        .join('\n');
      return HttpResponse.json({
        report_id: report.id,
        body_ref: `s3://reports/${report.id}.md`,
        content_type: 'text/markdown',
        markdown: [
          `# ${report.document.heading}`,
          report.document.summary,
          '## Figures',
          figures,
          '## Recommendation',
          report.document.recommendation,
        ].filter(Boolean).join('\n\n'),
      });
    }),

    http.post('*/v1/session-reports/:action', ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      const report = reports.find((r) => r.id === id);
      if (!report) return problem(404, 'not_found', 'Không tìm thấy báo cáo.');
      if (report.status !== 'pending_review')
        return problem(409, 'invalid_state', 'Báo cáo không ở trạng thái chờ duyệt.');
      // The mock's checks do not change on their own; a re-check finds the same result.
      if (verb === 'check') return accepted(`check-${id}`);
      if (verb !== 'publish') return problem(404, 'not_found', 'Không có thao tác này.');
      if (report.checks.some((c) => !c.ok))
        return problem(409, 'checks_failed', 'Còn kiểm tra chưa đạt, chưa phát hành được.');
      const next: SessionReport = { ...report, status: 'published' };
      reports = reports.map((r) => (r.id === id ? next : r));
      return HttpResponse.json(reportRow(next));
    }),

    // The reviewer's answer on a report's review request (HitlResolve); other ids fall through.
    http.post('*/v1/hitl-requests/:action', async ({ params, request }) => {
      const [hitlId, verb] = String(params.action).split(':');
      const report = reports.find((r) => r.hitl_request_id === hitlId);
      if (!report || verb !== 'resolve') return undefined;
      const body = (await request.json()) as components['schemas']['HitlResolve'];
      if (body.decision !== 'request_changes') return undefined;
      if (!body.note?.trim())
        return problem(422, 'validation_failed', 'Yêu cầu sửa cần ghi nội dung.');
      reports = reports.map((r) =>
        r.id === report.id ? { ...r, status: 'changes_requested' } : r,
      );
      return HttpResponse.json({ id: hitlId, status: 'resolved' });
    }),
  ];
}
