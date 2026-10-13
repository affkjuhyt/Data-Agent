import { http, HttpResponse } from 'msw';
import type { components } from '../lib/v1/schema';
import type { Skill, SkillSuggestion, SkillVersion } from '../lib/v1/skills';

type Schemas = components['schemas'];

/*
 * P03 Kỹ năng, the library side: skills of "P03 · Kỹ năng · Thư viện" (2293:17995) and the
 * skill cards of "v3.1 · Kỹ năng & công cụ" (2057:1270), the versions of "Phân nhóm khách hàng"
 * (2294:18535) with v3's skill_publish proposal in Cần bạn and the eval runs of its versions, the
 * suggestions of 2294:18728, and the tool catalogue of 2099:9862 as GET /v1/tools sends it.
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

/** The versions of "Phân nhóm khách hàng" as SkillDetail.versions sends them. */
const SEGMENT_VERSIONS: Schemas['VersionSummary'][] = [
  ['skv-segment-3', 3, 'pending_review'],
  ['skv-segment-2', 2, 'published'],
  ['skv-segment-1', 1, 'retired'],
].map(([id, version, status]) => ({
  id: id as string,
  version: version as number,
  status: status as string,
  eval_run_id: `er-${id as string}`,
  approved_at: null,
  retire_at: null,
  created_at: '2026-09-01T09:00:00+07:00',
}));

const evalRun = (id: string, passed: number): Schemas['EvalRunDetail'] => ({
  id,
  eval_set_id: 'eval-skill-segment',
  subject_version: id,
  trigger: 'skill_version',
  trigger_ref: null,
  item_count: 12,
  passed,
  failed: 12 - passed,
  correct_refusals: 0,
  pass_rate: passed / 12,
  baseline_run_id: null,
  regressions: 0,
  status: 'succeeded',
  started_at: '2026-10-01T09:00:00+07:00',
  finished_at: '2026-10-01T09:05:00+07:00',
  created_at: '2026-10-01T09:00:00+07:00',
  baseline: null,
  results: [],
});

const PROPOSAL_ID = 'proposal-skv-segment-3';

/** The skill_publish proposal the worker opens for v3 (target_ref = the version). */
function publishProposalFixture(): Schemas['ProposalDetail'] {
  return {
    id: PROPOSAL_ID,
    data_space_id: 'ds-rui-ro-tin-dung',
    kind: 'skill_publish',
    target_service: 'skill',
    target_ref: 'skv-segment-3',
    title: 'Phát hành kỹ năng phiên bản 3',
    diff: { action: 'publish', skill_id: 'sk-segment', version: 3, scope: 'data_space' },
    reason: null,
    evidence: [],
    impact: null,
    impact_level: 'medium',
    proposed_by: 'u-yen',
    assignee_id: null,
    eval_run_id: 'er-skv-segment-3',
    requires_compliance: false,
    status: 'pending_review',
    expires_at: '2026-12-31T00:00:00+07:00',
    applied_at: null,
    created_at: '2026-10-08T09:00:00+07:00',
    updated_at: '2026-10-08T09:00:00+07:00',
    changed_results: [],
    reviews: [],
    eval_run: null,
  };
}

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

function toolsFixture(): Schemas['services__api__modules__skill__schemas__ToolView'][] {
  const t = (
    code: string,
    level: 'read' | 'write_derived' | 'external_send' | 'system_action',
    description: string,
    executedBy: string,
    checkpoint: string | null = null,
  ) => ({ code, level, description, executed_by: executedBy, default_checkpoint: checkpoint });
  return [
    t('query.run', 'read', 'Truy vấn dữ liệu trong phạm vi', 'query'),
    t('metric.query', 'read', 'Truy vấn qua semantic layer', 'semantic'),
    t('doc.search', 'read', 'Tìm trong tài liệu, bộ sưu tập', 'document'),
    t('snapshot.compare', 'read', 'So hai bản chụp dữ liệu đã chốt', 'query'),
    t('proposal.create', 'write_derived', 'Tạo bản điều chỉnh có phiên bản', 'inbox', 'approve'),
    t('artifact.save', 'write_derived', 'Lưu bảng, biểu đồ vào phiên', 'workspace'),
    t('notify.send', 'external_send', 'Gửi Teams, email', 'notify', 'approve'),
    t('report.publish', 'external_send', 'Phát hành báo cáo đã duyệt', 'report', 'approve'),
    t('source.connect', 'system_action', 'Kết nối nguồn mới', 'catalog', 'admin'),
  ];
}

const page = <T>(items: T[]) => HttpResponse.json({ items, next_page_token: '' });

const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    { title: code, status, code, detail },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

/** SkillView as the api sends it, with the screen's card under display. */
const skillWire = (s: Skill) => ({
  id: s.id,
  code: s.id,
  name: s.name,
  description: s.description,
  category: 'custom',
  origin: s.scope === 'domain_pack' ? 'pack' : 'manual',
  origin_ref: null,
  scope: s.scope === 'organization' ? 'org' : 'data_space',
  owner_id: s.owner.id,
  current_version: s.status === 'draft' ? null : s.version,
  replaced_by_skill_id: null,
  run_count_30d: s.runs_30d ?? 0,
  data_space_id: 'ds-rui-ro-tin-dung',
  created_at: '2026-09-01T09:00:00+07:00',
  updated_at: '2026-10-01T09:00:00+07:00',
  display: s,
});

export function createSkillsHandlers() {
  const skills = skillsFixture();
  let versions = versionsFixture();
  let proposal = publishProposalFixture();
  let suggestions = suggestionsFixture();
  const tools = toolsFixture();
  let created = 0;

  return [
    // SkillView, filtered by A2's scope (org, data_space); the screen's card under display.
    http.get('*/v1/skills', ({ request }) => {
      const scope = new URL(request.url).searchParams.get('scope');
      const wanted = scope === 'org' ? 'organization' : scope;
      return page(skills.filter((s) => !wanted || s.scope === wanted).map(skillWire));
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
      return HttpResponse.json(skillWire(skill), { status: 201 });
    }),
    // SkillDetail with its versions (A2 has no GET /skills/{id}/versions); the screen's rows
    // ride under display and display_versions. Other sub-paths fall through to authoring.
    http.get('*/v1/skills/:id', ({ params }) => {
      const skill = skills.find((s) => s.id === params.id);
      if (!skill) return undefined;
      const rows: SkillVersion[] =
        skill.id === 'sk-segment'
          ? versions
          : [
              {
                id: `skv-${skill.id}-${skill.version}`,
                version: skill.version,
                change_text: 'Bản hiện tại',
                eval_passed: 0,
                eval_total: 0,
                status: skill.status === 'certified' ? 'active' : 'draft',
                status_text: skill.status_text,
              },
            ];
      return HttpResponse.json({
        ...skillWire(skill),
        versions: skill.id === 'sk-segment' ? SEGMENT_VERSIONS : [],
        display_versions: rows,
      });
    }),
    http.post('*/v1/skills/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      if (verb !== 'share') return undefined;
      const skill = skills.find((s) => s.id === id);
      if (!skill) return undefined;
      if (skill.status !== 'certified')
        return problem(409, 'invalid_state', 'Chỉ chia sẻ được kỹ năng đã chứng nhận.');
      // ShareRequest names the scope; org-wide waits for an administrator, so until then the
      // skill keeps the Data Space scope it has.
      await request.json();
      return HttpResponse.json({ ...skillWire(skill), scope: 'data_space' });
    }),
    // The caller's open approve_proposal requests: v3's publish proposal. Every other inbox
    // read falls through to the inbox mock.
    http.get('*/v1/inbox', ({ request }) => {
      if (new URL(request.url).searchParams.get('kind') !== 'approve_proposal') return undefined;
      return page(
        proposal.status === 'pending_review'
          ? [
              {
                id: 'hitl-skv-segment-3',
                data_space_id: 'ds-rui-ro-tin-dung',
                kind: 'approve_proposal',
                risk: 'medium',
                title: proposal.title,
                context: {},
                options: [],
                source_service: 'inbox',
                source_ref: PROPOSAL_ID,
                parent_run_label: null,
                assignee_id: 'me',
                assignee_group: null,
                assignee_capability: null,
                due_at: null,
                status: 'open',
                response: null,
                remember_as_rule: false,
                resolved_by: null,
                resolved_at: null,
                channel_card_ids: [],
                created_at: proposal.created_at,
                updated_at: proposal.updated_at,
              },
            ]
          : [],
      );
    }),
    http.get('*/v1/proposals/:id', ({ params }) =>
      params.id === PROPOSAL_ID ? HttpResponse.json(proposal) : undefined,
    ),
    http.get('*/v1/eval-runs/:id', ({ params }) => {
      const version = SEGMENT_VERSIONS.find((v) => v.eval_run_id === params.id);
      return version
        ? HttpResponse.json(evalRun(String(params.id), version.version === 1 ? 8 : 12))
        : undefined;
    }),
    // Deciding the proposal is what publishes v3 or sends it back (worker_agent/proposals.py).
    http.post('*/v1/proposals/:action', async ({ params, request }) => {
      const [id] = String(params.action).split(':');
      if (id !== PROPOSAL_ID) return undefined;
      if (proposal.status !== 'pending_review')
        return problem(409, 'invalid_state', 'Đề xuất đã được xử lý.');
      const body = (await request.json()) as Schemas['ProposalDecision'];
      const skill = skills.find((s) => s.id === 'sk-segment');
      if (body.decision === 'request_changes') {
        if (!body.note?.trim()) return problem(422, 'validation_failed', 'Ghi điều cần sửa.');
        versions = versions.map((v) =>
          v.id === 'skv-segment-3'
            ? { ...v, status: 'changes_requested', status_text: 'Trả về người tạo' }
            : v,
        );
        if (skill) skill.pending_version_id = null;
        proposal = { ...proposal, status: 'open' };
        return HttpResponse.json(proposal);
      }
      versions = versions.map((v) =>
        v.id === 'skv-segment-3'
          ? { ...v, status: 'active', status_text: 'Đang dùng' }
          : v.status === 'active'
            ? { ...v, status_text: 'Ngừng 07/11 · thay bằng v3' }
            : v,
      );
      if (skill)
        Object.assign(skill, {
          version: 3,
          status: 'certified',
          status_text: 'Đã chứng nhận',
          pending_version_id: null,
        });
      proposal = { ...proposal, status: 'accepted' };
      return HttpResponse.json(proposal);
    }),
    http.get('*/v1/skill-suggestions', () =>
      page(
        suggestions.map((sg) => ({
          id: sg.id,
          pattern: { steps: sg.steps },
          session_ids: sg.session_ids,
          user_count: sg.used_by.length,
          status: 'open',
          accepted_skill_id: null,
          dismissed_until: null,
          data_space_id: 'ds-rui-ro-tin-dung',
          created_at: '2026-10-01T09:00:00+07:00',
          updated_at: '2026-10-01T09:00:00+07:00',
          display: sg,
        })),
      ),
    ),
    http.post('*/v1/skill-suggestions/:action', ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      if (verb !== 'dismiss' || !suggestions.some((s) => s.id === id))
        return problem(404, 'not_found', 'Không tìm thấy đề xuất.');
      suggestions = suggestions.filter((s) => s.id !== id);
      return new HttpResponse(null, { status: 204 });
    }),
    http.get('*/v1/tools', () => page(tools)),
  ];
}
