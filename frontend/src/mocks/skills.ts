import { http, HttpResponse } from 'msw';
import type {
  McpServer,
  Skill,
  SkillSuggestion,
  SkillVersion,
  SkillVersionReview,
  Tool,
  ToolLevel,
} from '../lib/v1/skills';

/*
 * P03 Kỹ năng, the library side: skills of "P03 · Kỹ năng · Thư viện" (2293:17995) and the
 * skill cards of "v3.1 · Kỹ năng & công cụ" (2057:1270), the versions of "Phân nhóm khách hàng"
 * (2294:18535), the suggestions of 2294:18728, tools and MCP servers of 2099:9862 / 2099:10149.
 */
const YEN = { id: 'u-yen', name: 'Đỗ Hải Yến' };
const LONG = { id: 'u-long', name: 'Phạm Đức Long' };
const CHAU = { id: 'u-chau', name: 'Lê Minh Châu' };

function skillsFixture(): Skill[] {
  const s = (
    id: string,
    name: string,
    description: string,
    version: number,
    status: Skill['status'],
    status_text: string,
    scope: Skill['scope'],
    scope_label: string,
    owner: Skill['owner'],
    step_count: number,
    agent_count: number,
    runs_30d: number | null,
    pass_rate_pct: number | null,
  ): Skill => ({
    id,
    name,
    description,
    version,
    status,
    status_text,
    scope,
    scope_label,
    owner,
    created_by: owner,
    step_count,
    agent_count,
    runs_30d,
    pass_rate_pct,
    pending_version_id: id === 'sk-segment' ? 'skv-segment-3' : null,
  });
  return [
    s(
      'sk-variance',
      'Phân rã biến động',
      'Tách mức thay đổi của một chỉ số theo các chiều, chỉ ra phần đóng góp lớn nhất',
      4,
      'certified',
      'Đã chứng nhận',
      'organization',
      'Toàn tổ chức',
      YEN,
      5,
      4,
      412,
      96,
    ),
    s(
      'sk-period-compare',
      'So sánh kỳ',
      'So một chỉ số giữa hai kỳ, kèm kiểm định có ý nghĩa thống kê',
      3,
      'certified',
      'Đã chứng nhận',
      'organization',
      'Toàn tổ chức',
      LONG,
      4,
      3,
      288,
      97,
    ),
    s(
      'sk-external',
      'Tìm yếu tố bên ngoài',
      'Gợi ý chuỗi vĩ mô có thể giải thích biến động, kèm kiểm định',
      2,
      'certified',
      'Đã chứng nhận',
      'data_space',
      'Rủi ro tín dụng',
      LONG,
      6,
      2,
      46,
      91,
    ),
    s(
      'sk-forecast',
      'Dự báo thử 13 tuần',
      'Dự báo một chỉ số theo mẫu, có khoảng tin cậy',
      1,
      'testing',
      'Nháp · đang kiểm định',
      'data_space',
      'Tài chính',
      CHAU,
      5,
      0,
      null,
      null,
    ),
    s(
      'sk-segment',
      'Phân nhóm khách hàng',
      'Chia khách hàng theo hành vi trả nợ',
      2,
      'deprecated',
      'Ngừng · thay bằng v3',
      'data_space',
      'Rủi ro tín dụng',
      YEN,
      6,
      4,
      12,
      100,
    ),
    s(
      'sk-reconcile',
      'Đối soát hai nguồn',
      'So khớp số liệu giữa hai nguồn hoặc hai phiên bản, khoanh vùng chênh lệch tới bản ghi.',
      2,
      'certified',
      'Đã chứng nhận',
      'data_space',
      'Tài chính',
      LONG,
      6,
      2,
      88,
      98,
    ),
    s(
      'sk-anomaly',
      'Tìm nguyên nhân bất thường',
      'Kiểm tra lần lượt dữ liệu, định nghĩa, sự kiện kinh doanh trước khi kết luận.',
      3,
      'certified',
      'Đã chứng nhận',
      'organization',
      'Toàn tổ chức',
      YEN,
      7,
      3,
      61,
      89,
    ),
    s(
      'sk-newsletter',
      'Soạn bản tin định kỳ',
      'Tổng hợp chỉ số, biến động và nhận định thành bản tin theo mẫu của tổ chức.',
      2,
      'certified',
      'Đã chứng nhận',
      'domain_pack',
      'Gói ngân hàng',
      CHAU,
      4,
      1,
      42,
      93,
    ),
    s(
      'sk-source-map',
      'Ánh xạ nguồn mới',
      'Đề xuất ánh xạ cột của nguồn mới vào mô hình chuẩn của domain, có độ tin.',
      1,
      'certified',
      'Đã chứng nhận',
      'domain_pack',
      'Gói ngân hàng',
      LONG,
      5,
      1,
      6,
      83,
    ),
    s(
      'sk-npl-region',
      'Điều tra nợ xấu theo vùng',
      'Tạo từ phiên “Điều tra nợ xấu bán lẻ T9” của Đỗ Hải Yến; chờ chạy bộ thử.',
      1,
      'draft',
      'Nháp',
      'data_space',
      'Rủi ro tín dụng',
      YEN,
      6,
      0,
      null,
      null,
    ),
    // Created by "Tạo kỹ năng từ phiên" in the skill-authoring mock (its fixed id).
    s(
      'sk-region-variance',
      'Điều tra biến động theo vùng',
      'Tạo từ phiên phân tích nợ xấu theo vùng; đang kiểm định.',
      1,
      'testing',
      'Nháp · đang kiểm định',
      'data_space',
      'Rủi ro tín dụng',
      YEN,
      4,
      0,
      null,
      null,
    ),
  ];
}

// "Phân nhóm khách hàng" is listed at v2 (the version in use) while v3 waits for review.
function versionsFixture(): SkillVersion[] {
  return [
    {
      id: 'skv-segment-3',
      version: 3,
      change_text: 'Thêm nhóm “trả trước hạn”; bỏ biến thu nhập tự khai',
      eval_passed: 12,
      eval_total: 12,
      status: 'pending_review',
      status_text: 'Chờ duyệt · Đỗ Hải Yến',
    },
    {
      id: 'skv-segment-2',
      version: 2,
      change_text: 'Phân 5 nhóm theo hành vi trả nợ 12 tháng',
      eval_passed: 10,
      eval_total: 10,
      status: 'active',
      status_text: 'Đang dùng',
    },
    {
      id: 'skv-segment-1',
      version: 1,
      change_text: 'Bản đầu, 4 nhóm',
      eval_passed: 8,
      eval_total: 8,
      status: 'deprecated',
      status_text: 'Đã ngừng 03/2026',
    },
  ];
}

const REVIEW: SkillVersionReview = {
  version_id: 'skv-segment-3',
  version: 3,
  current: { id: 'skv-segment-2', version: 2, agent_count: 4, session_count: 9 },
  checks: [
    { text: 'Kiểm định 12/12 đạt', ok: true },
    { text: 'Chỉ gọi chỉ số đã chứng nhận', ok: true },
    { text: 'Không dùng dữ liệu cá nhân trực tiếp', ok: true },
    { text: 'Người duyệt khác người tạo', ok: true },
  ],
  scope_options: [
    { value: 'ds-credit-retail', label: 'Rủi ro tín dụng, Kinh doanh bán lẻ' },
    { value: 'ds-credit', label: 'Rủi ro tín dụng' },
    { value: 'organization', label: 'Toàn tổ chức' },
  ],
  scope_value: 'ds-credit-retail',
  deprecate_options: [
    { value: 'p30d', label: '30 ngày sau khi phát hành v3 · 07/11' },
    { value: 'p7d', label: '7 ngày sau khi phát hành v3 · 15/10' },
    { value: 'now', label: 'Ngay khi phát hành v3' },
  ],
  replacement_options: [{ value: 'v3', label: 'v3 · tham số giữ nguyên' }],
  consumers: [
    { name: 'Agent Giám sát rủi ro', kind: 'agent', action_text: 'Chủ sở hữu duyệt nâng lên v3' },
    { name: 'Agent Báo cáo', kind: 'agent', action_text: 'Chủ sở hữu duyệt nâng lên v3' },
    {
      name: '9 phiên làm việc',
      kind: 'session',
      action_text: 'Giữ v2 cho kết quả cũ; lần chạy mới dùng v3',
    },
  ],
  can_approve: { allowed: true, reason: null },
};

function suggestionsFixture(): SkillSuggestion[] {
  return [
    {
      id: 'sug-reconcile-filed',
      title: 'Đối soát số với báo cáo đã nộp',
      repeat_text: 'Lặp lại 14 lần trong 6 phiên của 3 người, 30 ngày qua',
      count_text: '14 lần · 3 người',
      steps: [
        'Lấy số đã nộp',
        'lấy số nội bộ cùng kỳ',
        'so từng chỉ tiêu',
        'khoanh vùng chênh lệch',
      ],
      used_by: ['Đỗ Hải Yến', 'Phạm Đức Long', 'Agent Đối soát'],
      session_ids: ['ws-filed-recon-q3', 'ws-filed-recon-aug'],
      occurrences: [
        {
          at: '2026-10-08T09:12:00+07:00',
          where: 'Phiên “Đối soát báo cáo NHNN quý 3” · Đỗ Hải Yến',
        },
        { at: '2026-10-02T14:40:00+07:00', where: 'Phiên “Đối soát tháng 8” · Phạm Đức Long' },
        { at: '2026-09-28T08:05:00+07:00', where: 'Lượt chạy của Agent Đối soát' },
      ],
    },
    {
      id: 'sug-restructured',
      title: 'Kiểm tra khoản cơ cấu trong kỳ',
      repeat_text: 'Lặp lại 9 lần trong 5 lượt chạy của Agent Phân tích',
      count_text: '9 lần · 1 agent',
      steps: ['Lọc khoản cơ cấu trong kỳ', 'so nhóm nợ trước/sau', 'tính phần đóng góp vào nợ xấu'],
      used_by: ['Agent Phân tích'],
      session_ids: ['ws-restructured-sep'],
      occurrences: [
        {
          at: '2026-10-07T07:30:00+07:00',
          where: 'Lượt chạy của Agent Phân tích · nợ xấu tuần 41',
        },
        {
          at: '2026-09-30T07:30:00+07:00',
          where: 'Lượt chạy của Agent Phân tích · nợ xấu tuần 40',
        },
      ],
    },
    {
      id: 'sug-branch-target',
      title: 'So hiệu quả chi nhánh với chỉ tiêu',
      repeat_text: 'Lặp lại 6 lần trong 4 phiên',
      count_text: '6 lần · 4 phiên',
      steps: ['Ghép chỉ tiêu tải lên với số thực', 'xếp hạng', 'giải thích 5 chi nhánh lệch nhất'],
      used_by: ['Lê Minh Châu'],
      session_ids: ['ws-branch-q3'],
      occurrences: [
        {
          at: '2026-10-06T10:00:00+07:00',
          where: 'Phiên “Hiệu quả chi nhánh quý 3” · Lê Minh Châu',
        },
      ],
    },
  ];
}

function toolsFixture(): Tool[] {
  const t = (
    id: string,
    level: ToolLevel,
    description: string,
    agent_count: number,
    calls_30d: number,
    mcp_server_id: string | null = null,
  ): Tool => ({ id, name: id, level, description, agent_count, calls_30d, mcp_server_id });
  return [
    t('query.run', 'read', 'Truy vấn dữ liệu trong phạm vi', 8, 2418),
    t('metric.query', 'read', 'Truy vấn qua semantic layer', 6, 1904),
    t('doc.search', 'read', 'Tìm trong tài liệu, bộ sưu tập', 4, 611),
    t('snapshot.compare', 'read', 'So hai bản chụp dữ liệu đã chốt', 3, 140),
    t('proposal.create', 'write_derived', 'Tạo bản điều chỉnh có phiên bản', 2, 14),
    t('artifact.save', 'write_derived', 'Lưu bảng, biểu đồ vào phiên', 5, 380),
    t('notify.send', 'external', 'Gửi Teams, email', 3, 212),
    t('report.publish', 'external', 'Phát hành báo cáo đã duyệt', 1, 9),
    t('source.connect', 'system', 'Kết nối nguồn mới', 0, 0),
  ];
}

function mcpFixture(): McpServer[] {
  return [
    {
      id: 'mcp-ticket',
      name: 'Ticket nội bộ',
      tools: ['ticket.create', 'ticket.search'],
      auth: 'oauth2',
      status: 'active',
    },
    {
      id: 'mcp-calendar',
      name: 'Lịch tổ chức',
      tools: ['calendar.events'],
      auth: 'sso',
      status: 'active',
    },
    {
      id: 'mcp-sharepoint',
      name: 'SharePoint',
      tools: ['sp.search', 'sp.read'],
      auth: 'sso',
      status: 'active',
    },
    {
      id: 'mcp-regdoc',
      name: 'Cổng văn bản pháp quy',
      tools: ['regdoc.fetch'],
      auth: 'api_key',
      status: 'auth_error',
    },
    {
      id: 'mcp-crm',
      name: 'CRM',
      tools: ['crm.customer'],
      auth: 'oauth2',
      status: 'pending_pii_review',
    },
  ];
}

const page = <T>(items: T[]) => HttpResponse.json({ items, next_page_token: '' });

const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    { title: code, status, code, detail },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

export function createSkillsHandlers() {
  const skills = skillsFixture();
  let versions = versionsFixture();
  let review = { ...REVIEW };
  let suggestions = suggestionsFixture();
  const tools = toolsFixture();
  const servers = mcpFixture();
  let created = 0;

  return [
    http.get('*/v1/skills', ({ request }) => {
      const url = new URL(request.url);
      const scope = url.searchParams.get('scope');
      const status = url.searchParams.get('status');
      return page(
        skills.filter((s) => (!scope || s.scope === scope) && (!status || s.status === status)),
      );
    }),
    http.post('*/v1/skills', async ({ request }) => {
      const { name } = (await request.json()) as { name: string };
      if (!name?.trim()) return problem(422, 'validation_failed', 'Đặt tên kỹ năng.');
      created += 1;
      const skill: Skill = {
        id: `sk-new-${created}`,
        name,
        description: '',
        version: 1,
        status: 'draft',
        status_text: 'Nháp',
        scope: 'data_space',
        scope_label: 'Rủi ro tín dụng',
        owner: YEN,
        created_by: YEN,
        step_count: 0,
        agent_count: 0,
        runs_30d: null,
        pass_rate_pct: null,
        pending_version_id: null,
      };
      skills.unshift(skill);
      return HttpResponse.json(skill, { status: 201 });
    }),
    // Sub-paths of one skill other than these belong to the authoring mock: fall through.
    http.get('*/v1/skills/:id', ({ params }) => {
      const skill = skills.find((s) => s.id === params.id);
      return skill ? HttpResponse.json(skill) : undefined;
    }),
    http.get('*/v1/skills/:id/versions', ({ params }) => {
      if (params.id === 'sk-segment') return page(versions);
      const skill = skills.find((s) => s.id === params.id);
      if (!skill) return undefined;
      return page<SkillVersion>([
        {
          id: `skv-${skill.id}-${skill.version}`,
          version: skill.version,
          change_text: 'Bản hiện tại',
          eval_passed: 0,
          eval_total: 0,
          status: skill.status === 'certified' ? 'active' : 'draft',
          status_text: skill.status_text,
        },
      ]);
    }),
    http.post('*/v1/skills/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      if (verb !== 'share') return undefined;
      const skill = skills.find((s) => s.id === id);
      if (!skill) return undefined;
      if (skill.status !== 'certified')
        return problem(409, 'invalid_state', 'Chỉ chia sẻ được kỹ năng đã chứng nhận.');
      const { data_space_id } = (await request.json()) as { data_space_id: string | null };
      return HttpResponse.json({ status: data_space_id ? 'shared' : 'pending_admin' });
    }),
    http.get('*/v1/skill-versions/:id/review', ({ params }) =>
      params.id === review.version_id
        ? HttpResponse.json(review)
        : problem(404, 'not_found', 'Không có phiên bản chờ duyệt này.'),
    ),
    http.post('*/v1/skill-versions/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      const target = versions.find((v) => v.id === id);
      if (!target) return problem(404, 'not_found', 'Không tìm thấy phiên bản.');
      if (target.status !== 'pending_review')
        return problem(409, 'invalid_state', 'Phiên bản không còn chờ duyệt.');
      if (verb === 'request-changes') {
        const { comment } = (await request.json()) as { comment: string };
        if (!comment?.trim()) return problem(422, 'validation_failed', 'Ghi điều cần sửa.');
        target.status = 'changes_requested';
        target.status_text = 'Trả về người tạo';
        const skill = skills.find((s) => s.pending_version_id === id);
        if (skill) skill.pending_version_id = null;
        return HttpResponse.json(target);
      }
      if (verb !== 'approve') return problem(404, 'not_found', 'Không có thao tác này.');
      const { scope } = (await request.json()) as { scope: string };
      if (scope === 'organization')
        return problem(403, 'forbidden', 'Phát hành toàn tổ chức cần quản trị viên.');
      versions = versions.map((v) =>
        v.id === id
          ? { ...v, status: 'active', status_text: 'Đang dùng' }
          : v.status === 'active'
            ? { ...v, status_text: 'Ngừng 07/11 · thay bằng v3' }
            : v,
      );
      review = { ...review, can_approve: { allowed: false, reason: 'Đã phát hành.' } };
      const skill = skills.find((s) => s.id === 'sk-segment');
      if (skill)
        Object.assign(skill, {
          version: 3,
          status: 'certified',
          status_text: 'Đã chứng nhận',
          pending_version_id: null,
        });
      return HttpResponse.json(versions.find((v) => v.id === id));
    }),
    http.get('*/v1/skill-suggestions', () => page(suggestions)),
    http.post('*/v1/skill-suggestions/:action', ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      if (verb !== 'dismiss' || !suggestions.some((s) => s.id === id))
        return problem(404, 'not_found', 'Không tìm thấy đề xuất.');
      suggestions = suggestions.filter((s) => s.id !== id);
      return new HttpResponse(null, { status: 204 });
    }),
    http.get('*/v1/tools', () => page(tools)),
    http.post('*/v1/tools', async ({ request }) => {
      const body = (await request.json()) as {
        name: string;
        provider: string;
        level: ToolLevel;
        description: string;
        agent_ids: string[];
      };
      if (!/^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$/.test(body.name ?? ''))
        return problem(
          422,
          'validation_failed',
          'Tên công cụ dạng nhóm.hành_động, ví dụ ticket.create.',
        );
      if (tools.some((t) => t.name === body.name))
        return problem(409, 'conflict', 'Đã có công cụ cùng tên.');
      if (body.level === 'system' && body.agent_ids.length > 0)
        return problem(422, 'business_rule_violated', 'Công cụ hệ thống không gán cho agent.');
      const tool: Tool = {
        id: body.name,
        name: body.name,
        level: body.level,
        description: body.description,
        mcp_server_id: body.provider === 'builtin' ? null : body.provider,
        agent_count: body.agent_ids.length,
        calls_30d: 0,
      };
      tools.push(tool);
      return HttpResponse.json(tool, { status: 201 });
    }),
    http.get('*/v1/mcp-servers', () => page(servers)),
  ];
}
