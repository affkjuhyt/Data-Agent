import { http, HttpResponse } from 'msw';
import { userWire } from './fixtures';
import type {
  AccessPolicy,
  AccessRequest,
  AdminUser,
  AuditEntry,
  AuditExport,
  AuditWire,
  CapabilityKey,
  CreateDataSpace,
  CreateDsr,
  DataSpaceMember,
  DataSpaceSummary,
  DecideAccess,
  Invitation,
  MemberInput,
  PermissionCell,
  PermissionMatrix,
  PermissionSimulation,
  Role,
  SecurityEvent,
  UserGroup,
} from '../lib/v1/adminAccess';
import { rowScopeText } from '../lib/v1/adminAccess';
import type { components } from '../lib/v1/schema';

type Schemas = components['schemas'];

const STEWARD_ROLE = { id: 'role-ac03', code: 'AC03', name: 'Người phụ trách dữ liệu' };
/*
 * MSW handlers for Quản trị · người, quyền và tuân thủ (lib/v1/adminAccess.ts), data taken from
 * the P11 frames of Figma. State lives in memory: deciding an access request removes it from
 * the queue, disabling a policy changes its status, executing a DSR marks its items done.
 *
 * GET /v1/admin/users and GET /v1/data-spaces return a superset of AppUser / DataSpace, so
 * these handlers must be registered before createHandlers() to answer for the whole app.
 * GET /v1/policies is answered only for `type=access` (an array, as the api answers); anything else
 * falls through.
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

const DS_RISK = 'ds-rui-ro-tin-dung';
const DS_RETAIL = 'ds-ban-le';

const daysFromNow = (days: number, hour = 9) => {
  const d = new Date();
  d.setDate(d.getDate() + days);
  d.setHours(hour, 0, 0, 0);
  return d.toISOString();
};
const todayAt = (h: number, m: number, s = 0) => {
  const d = new Date();
  d.setHours(h, m, s, 0);
  return d.toISOString();
};
const SYNCED_AT = todayAt(6, 0);
const dayMonth = (iso: string) => {
  const d = new Date(iso);
  return `${String(d.getDate()).padStart(2, '0')}/${String(d.getMonth() + 1).padStart(2, '0')}`;
};

/* ─── Fixtures ─────────────────────────────────────────────────────────── */

export function rolesFixture(): Role[] {
  return [
    { id: 'role-business', code: 'AC01', name: 'Người dùng nghiệp vụ', capabilities: ['ask'] },
    {
      id: 'role-analyst',
      code: 'AC02',
      name: 'Chuyên viên phân tích',
      capabilities: ['ask', 'view_detail', 'run_agent'],
    },
    {
      id: 'role-steward',
      code: 'AC03',
      name: 'Người phụ trách dữ liệu',
      capabilities: ['ask', 'view_detail', 'approve'],
    },
    { id: 'role-admin', code: 'AC05', name: 'Quản trị hệ thống', capabilities: ['admin'] },
    { id: 'role-auditor', code: 'AC07', name: 'Kiểm toán nội bộ', capabilities: ['audit_read'] },
  ];
}

export function groupsFixture(): UserGroup[] {
  return [
    { id: 'grp-risk-analyst', name: 'GRP_RISK_ANALYST', member_count: 24, source: 'ad' },
    { id: 'grp-risk-head', name: 'GRP_RISK_HEAD', member_count: 6, source: 'ad' },
    { id: 'grp-branch-hn', name: 'GRP_BRANCH_HN', member_count: 41, source: 'ad' },
    { id: 'grp-audit', name: 'GRP_AUDIT', member_count: 3, source: 'ad' },
  ];
}

const person = (
  id: string,
  name: string,
  group: string,
  role: string,
  spaces: string,
  scope: string,
  limited = false,
  validUntil: string | null = null,
): AdminUser => ({
  id,
  username: id,
  display_name: name,
  kind: 'person',
  status: 'active',
  group_label: group,
  role_name: role,
  data_space_text: spaces,
  row_scope_text: scope,
  row_scope_limited: limited,
  valid_until: validUntil,
  last_synced_at: SYNCED_AT,
  identity_source: 'Active Directory',
});

export function adminUsersFixture(): AdminUser[] {
  const yearEnd = new Date(new Date().getFullYear(), 11, 31, 23, 59).toISOString();
  const named: AdminUser[] = [
    person(
      'u-hai-yen',
      'Đỗ Hải Yến',
      'GRP-RISK-ANALYST',
      'Chuyên viên phân tích',
      'Rủi ro tín dụng, Bán lẻ',
      'Toàn hàng',
    ),
    person(
      'u-duc-long',
      'Phạm Đức Long',
      'GRP-RISK-HEAD',
      'Người phụ trách dữ liệu',
      'Rủi ro tín dụng',
      'Toàn hàng',
    ),
    person(
      'u-minh-chau',
      'Lê Minh Châu',
      'GRP-BRANCH-HN',
      'Chuyên viên phân tích',
      'Kinh doanh bán lẻ',
      '3 chi nhánh Hà Nội',
      true,
    ),
    person(
      'u-khanh-linh',
      'Nguyễn Khánh Linh',
      'GRP-AUDIT',
      'Kiểm toán nội bộ',
      'Tất cả · chỉ đọc nhật ký',
      'Toàn hàng',
      false,
      yearEnd,
    ),
    {
      ...person(
        'agent-doi-soat',
        'Agent Đối soát',
        'agent',
        'Agent',
        'Rủi ro tín dụng',
        'theo người sở hữu',
      ),
      kind: 'agent',
      last_synced_at: null,
    },
  ];
  // The rest of the 412 people synced from AD; the frame shows the first rows only.
  const rest = Array.from({ length: 408 }, (_, i) =>
    person(
      `u-${i}`,
      `Người dùng ${i + 1}`,
      'GRP-BRANCH-HN',
      'Người dùng nghiệp vụ',
      'Kinh doanh bán lẻ',
      'Chi nhánh của mình',
      true,
    ),
  );
  const agents = [
    'Agent Báo cáo',
    'Agent Phân tích',
    'Agent Giám sát rủi ro',
    'Agent Thu nợ',
    'Agent Chất lượng dữ liệu',
  ].map((name, i) => ({
    ...person(`agent-${i}`, name, 'agent', 'Agent', 'Rủi ro tín dụng', 'theo người sở hữu'),
    kind: 'agent' as const,
    last_synced_at: null,
  }));
  return [...named, ...rest, ...agents];
}

export function dataSpaceSummariesFixture(): DataSpaceSummary[] {
  const ds = (
    id: string,
    code: string,
    name: string,
    steward: string,
    members: number,
    status: DataSpaceSummary['status'] = 'active',
    description: string | null = null,
  ): DataSpaceSummary => ({
    id,
    code,
    name,
    status,
    steward_id: `u-${code.toLowerCase()}`,
    opened_at: status === 'active' ? '2026-09-01T08:00:00+07:00' : null,
    my_roles: [STEWARD_ROLE],
    my_capabilities: ['ask', 'steward'],
    description,
    steward_name: steward,
    member_count: members,
  });
  return [
    ds(
      DS_RISK,
      'RUI_RO_TIN_DUNG',
      'Rủi ro tín dụng',
      'Trần Quốc An',
      66,
      'active',
      'Dùng cho phân tích rủi ro tín dụng bán lẻ và doanh nghiệp',
    ),
    ds(DS_RETAIL, 'BAN_LE', 'Kinh doanh bán lẻ', 'Phạm Đức Long', 128),
    ds('ds-nguon-von', 'NGUON_VON', 'Nguồn vốn', 'Vũ Thị Mai', 22),
    ds('ds-bao-cao-nhnn', 'BAO_CAO_NHNN', 'Báo cáo NHNN', 'Hoàng Văn Bình', 15, 'onboarding'),
    ds('ds-thu-no', 'THU_NO', 'Thu hồi nợ', 'Đặng Thu Trang', 9, 'draft'),
  ];
}

type MemberWire = Schemas['MemberDetail'];

/** GET /v1/data-spaces/{id}/members as the api answers it (MemberDetail). */
export function membersFixture(spaceId: string = DS_RISK): MemberWire[] {
  const stamp = new Date(new Date().getFullYear(), 0, 5).toISOString();
  const m = (
    id: string,
    name: string,
    roleId: string,
    roleName: string,
    scope: Record<string, string[]> | null,
    extra: Partial<MemberWire> = {},
  ): MemberWire => ({
    id: `m-${id}`,
    data_space_id: spaceId,
    principal_type: 'user',
    principal_id: id,
    principal_name: name,
    role_id: roleId,
    role_code: roleId.toUpperCase(),
    role_name: roleName,
    row_scope: scope,
    source: 'manual',
    source_ref: null,
    valid_until: null,
    created_at: stamp,
    updated_at: stamp,
    ...extra,
  });
  return [
    m('u-quoc-an', 'Trần Quốc An', 'role-owner', 'Chủ sở hữu dữ liệu', null),
    m('u-hai-yen', 'Đỗ Hải Yến', 'role-control', 'Kiểm soát, tuân thủ', null),
    m(
      'u-thu-ha',
      'Nguyễn Thu Hà',
      'role-ds-analyst',
      'Phân tích viên',
      { region: ['Bắc'] },
      {
        source: 'group_mapping',
      },
    ),
    m('u-minh-tam', 'Lê Minh Tâm', 'role-viewer', 'Người xem', null, {
      source: 'access_request',
      valid_until: new Date(new Date().getFullYear(), 9, 30, 23, 59).toISOString(),
    }),
    m('agent-doi-soat', 'Agent Đối soát', 'role-agent', 'Agent (theo chủ sở hữu)', null, {
      principal_type: 'agent',
    }),
  ];
}

export function permissionMatrixFixture(): PermissionMatrix {
  const row = (
    roleId: string,
    name: string,
    count: number,
    cells: PermissionCell[],
    unit: 'người' | 'agent' = 'người',
  ) => {
    const keys: CapabilityKey[] = [
      'view_aggregate',
      'view_detail',
      'view_pii',
      'ask',
      'run_agent',
      'create_agent',
      'approve',
      'publish',
      'admin',
    ];
    return {
      role_id: roleId,
      name,
      member_count: count,
      member_unit: unit,
      cells: Object.fromEntries(keys.map((k, i) => [k, cells[i]])) as Record<
        CapabilityKey,
        PermissionCell
      >,
    };
  };
  const Y = 'yes' as const;
  const A = 'approval' as const;
  const N = 'no' as const;
  return {
    roles: [
      row('role-owner', 'Chủ sở hữu dữ liệu', 2, [Y, Y, Y, Y, Y, Y, Y, Y, Y]),
      row('role-ds-analyst', 'Phân tích viên', 14, [Y, Y, A, Y, Y, Y, A, A, N]),
      row('role-control', 'Kiểm soát, tuân thủ', 3, [Y, Y, Y, Y, A, A, Y, A, A]),
      row('role-viewer', 'Người xem', 41, [Y, N, N, Y, N, N, N, N, N]),
      row('role-agent', 'Agent (theo chủ sở hữu)', 6, [Y, Y, 'never', Y, N, Y, N, N, N], 'agent'),
    ],
    rules: {
      row: 'Phân tích viên chỉ thấy khoản vay của vùng mình phụ trách; Kiểm soát thấy tất cả',
      column:
        'national_id, account_no che với mọi vai trò trừ Chủ sở hữu khi có lý do ghi kiểm toán',
      agent:
        'Quyền agent = giao của chủ sở hữu agent và phạm vi cấu hình; không bao giờ có “Xem PII”',
    },
  };
}

/** POST /v1/permissions:simulate as the api answers it: one TableView per role or person asked. */
export function permissionSimulationFixture(
  datasetId: string,
  subjects: { kind: 'role' | 'user'; id: string }[],
): PermissionSimulation {
  const columns = ['loan_id', 'customer_name', 'national_id', 'region', 'balance'];
  return {
    dataset_id: datasetId,
    views: subjects.map(({ kind, id }, i) =>
      i === 0
        ? {
            subject_kind: kind,
            subject_id: id,
            visible_columns: columns.filter((c) => c !== 'national_id'),
            hidden_columns: ['national_id'],
            masked_columns: { customer_name: 'partial' } as Record<string, string>,
            row_conditions: ["region = 'Bắc'"],
            policy_ids: ['0192f1a2-7c3d-7e4f-8a9b-000000000201'],
          }
        : {
            subject_kind: kind,
            subject_id: id,
            visible_columns: columns,
            hidden_columns: [],
            masked_columns: { national_id: 'last4' } as Record<string, string>,
            row_conditions: [],
            policy_ids: ['0192f1a2-7c3d-7e4f-8a9b-000000000202'],
          },
    ),
  };
}

export function accessRequestsFixture(): AccessRequest[] {
  const base = {
    requester_id: 'u-minh-chau',
    data_space_id: DS_RETAIL,
    status: 'pending' as const,
    needs_compliance: false,
    approver_id: null,
    decided_at: null,
    decision_note: null,
    approver_name: 'Phạm Đức Long',
    approver_role: 'chủ sở hữu dữ liệu',
  };
  return [
    {
      ...base,
      id: 'ar-0091',
      code: 'AR-0091',
      capability: 'view_aggregate',
      title: 'Lê Minh Châu xin xem nợ xấu toàn vùng Bắc Bộ',
      queue_label: 'Lê Minh Châu · nợ xấu Bắc Bộ',
      scope_text: 'Xem chỉ số NPL_RATIO và LOAN_BALANCE cho vùng DH Bắc Bộ và ĐB sông Hồng',
      reason: 'Chuẩn bị báo cáo so sánh chi nhánh Hà Nội với cả vùng cho giám đốc khối',
      requested_until: daysFromNow(28).slice(0, 10),
      current_scope_text: '3 chi nhánh Hà Nội',
      origin_text: 'từ câu hỏi bị giới hạn phạm vi',
      created_at: daysFromNow(-2, 10),
      suggestion:
        'Cấp quyền xem số tổng hợp theo vùng (không thấy dòng khách hàng) là đủ cho mục đích nêu. 4 người cùng nhóm đã được cấp quyền tương tự trong 90 ngày qua.',
      suggested_scope_text:
        'Số tổng hợp theo vùng DH Bắc Bộ và ĐB sông Hồng, không có dòng khách hàng',
      impact: [
        { label: 'Chi nhánh thấy được', before: '3', after: '3 + số tổng hợp 2 vùng' },
        { label: 'Dòng khách hàng', before: '18.420', after: '18.420 (không đổi)' },
        { label: 'Hết hạn', before: '—', after: `${dayMonth(daysFromNow(28))}, tự thu hồi` },
      ],
      due_at: daysFromNow(3),
    },
    {
      ...base,
      id: 'ar-0092',
      code: 'AR-0092',
      requester_id: 'agent-0',
      data_space_id: DS_RISK,
      capability: 'view_detail',
      title: 'Agent Báo cáo xin xuất PDF chi tiết',
      queue_label: 'Agent Báo cáo · xuất PDF chi tiết',
      scope_text: 'Xuất PDF danh sách khoản vay quá hạn kèm tên khách hàng',
      reason: 'Báo cáo tuần gửi Ban điều hành',
      requested_until: daysFromNow(7).slice(0, 10),
      current_scope_text: 'Số tổng hợp',
      origin_text: 'từ lượt chạy của agent',
      created_at: daysFromNow(-1, 15),
      suggestion: null,
      suggested_scope_text: null,
      impact: [{ label: 'Dòng khách hàng', before: '0', after: '1.204' }],
      due_at: todayAt(17, 0),
    },
    {
      ...base,
      id: 'ar-0093',
      code: 'AR-0093',
      requester_id: 'u-van-nam',
      data_space_id: DS_RETAIL,
      capability: 'ask',
      title: 'Trần Văn Nam xin hỏi đáp trên bảng thẻ tín dụng',
      queue_label: 'Trần Văn Nam · bảng thẻ tín dụng',
      scope_text: 'Hỏi đáp trên bảng card_transactions',
      reason: 'Phân tích chi tiêu thẻ theo mùa',
      requested_until: daysFromNow(30).slice(0, 10),
      current_scope_text: 'Không có',
      origin_text: 'từ trang Dữ liệu',
      created_at: daysFromNow(-1, 9),
      suggestion: null,
      suggested_scope_text: null,
      impact: [{ label: 'Bảng thấy được', before: '0', after: '1' }],
      due_at: daysFromNow(2),
    },
  ];
}

export function accessPoliciesFixture(): AccessPolicy[] {
  const policy = (
    n: number,
    name: string,
    kind: AccessPolicy['kind'],
    dsId: string,
    dsName: string,
    status: AccessPolicy['status'] = 'active',
    applied = 0,
  ): AccessPolicy => ({
    id: `pol-${String(n).padStart(3, '0')}`,
    code: `POL-${String(n).padStart(3, '0')}`,
    data_space_id: dsId,
    data_space_name: dsName,
    name,
    kind,
    condition_expr: kind === 'row' ? 'branch_code IN :user.branches' : null,
    columns: kind === 'row' ? null : ['id_number'],
    version: 1,
    status,
    simulated_at: daysFromNow(-10),
    activated_at: status === 'active' ? daysFromNow(-9) : null,
    owner_name: 'Phạm Đức Long',
    rule_text: '',
    sample_question: '',
    applied_this_week: applied,
    last_backtest: null,
  });
  const names: [string, AccessPolicy['kind'], string, string][] = [
    ['Che số CCCD với mọi vai trò trừ Kiểm soát', 'mask', DS_RISK, 'Rủi ro tín dụng'],
    ['Che số tài khoản trong kết quả xuất', 'mask', DS_RISK, 'Rủi ro tín dụng'],
    ['Phân tích viên chỉ thấy vùng mình phụ trách', 'row', DS_RISK, 'Rủi ro tín dụng'],
    ['Ẩn cột lương trong bảng nhân sự', 'column', 'ds-nguon-von', 'Nguồn vốn'],
    ['Che số điện thoại khách hàng', 'mask', DS_RETAIL, 'Kinh doanh bán lẻ'],
    ['Che địa chỉ khách hàng', 'mask', DS_RETAIL, 'Kinh doanh bán lẻ'],
  ];
  const list = names.map(([name, kind, ds, dsName], i) =>
    policy(i + 1, name, kind, ds, dsName, 'active', 3 + i),
  );
  const pol007: AccessPolicy = {
    ...policy(
      7,
      'Chi nhánh chỉ thấy khách hàng của mình',
      'row',
      DS_RETAIL,
      'Kinh doanh bán lẻ',
      'active',
      23,
    ),
    rule_text: [
      'Áp cho: mọi bảng có cột branch_code',
      'Khi: người dùng thuộc nhóm GRP-BRANCH-*',
      'Chỉ cho thấy dòng: branch_code IN user.ad.branches',
      'Cột che: id_number, phone, address (trừ GRP-RISK-HEAD)',
    ].join('\n'),
    sample_question: 'dư nợ bán lẻ theo chi nhánh tháng 9',
    last_backtest: {
      window_days: 7,
      affected_queries: 2140,
      total_queries: 18302,
      affected_users_text: '96 (đều thuộc GRP-BRANCH-*)',
      empty_queries_text: '12 · người hỏi chi nhánh khác',
    },
  };
  const more = Array.from({ length: 11 }, (_, i) =>
    policy(
      8 + i,
      `Chính sách theo dòng ${8 + i}`,
      'row',
      DS_RETAIL,
      'Kinh doanh bán lẻ',
      i < 2 ? 'draft' : 'active',
      i,
    ),
  );
  return [...list, pol007, ...more];
}

/* The audit_log row as the api sends it, with the screen's wording under `display` (PROPOSAL). */
function auditRow(
  seq: number,
  at: string,
  wire: Pick<AuditWire, 'actor_kind' | 'service' | 'action' | 'object_kind' | 'outcome'> &
    Partial<AuditWire>,
  display: Omit<AuditEntry, 'id' | 'at' | 'service' | 'outcome' | 'actor_kind'>,
): AuditWire {
  const id = `00000000-0000-7000-8000-${String(seq).padStart(12, '0')}`;
  const row: AuditWire = {
    id,
    seq,
    occurred_at: at,
    actor_id: `00000000-0000-4000-8000-${String(seq).padStart(12, '0')}`,
    on_behalf_of: null,
    data_space_id: DS_RISK,
    object_id: null,
    before: null,
    after: null,
    agent_run_id: null,
    ip: '10.0.4.21',
    prev_hash: `h${seq - 1}`,
    hash: `h${seq}`,
    ...wire,
  };
  const kind = { user: 'person', agent: 'agent', client: 'client' }[row.actor_kind] ?? 'system';
  return {
    ...row,
    display: {
      id,
      at,
      service: row.service,
      actor_kind: kind as AuditEntry['actor_kind'],
      outcome: row.outcome === 'denied' ? 'blocked' : row.outcome === 'error' ? 'error' : 'ok',
      ...display,
    },
  };
}

export function auditFixture(): AuditWire[] {
  return [
    auditRow(
      6,
      todayAt(10, 52, 4),
      {
        actor_kind: 'user',
        service: 'api',
        action: 'answer.exported',
        object_kind: 'answer',
        outcome: 'ok',
      },
      {
        actor_label: 'Đỗ Hải Yến',
        action_text: 'Xuất Excel tổng hợp nợ xấu theo vùng',
        data_text: 'NPL_RATIO · 6 dòng',
      },
    ),
    auditRow(
      5,
      todayAt(10, 41, 12),
      {
        actor_kind: 'agent',
        service: 'worker-agent',
        action: 'query.executed',
        object_kind: 'query_run',
        outcome: 'ok',
      },
      {
        actor_label: 'Agent Đối soát (của Đỗ Hải Yến)',
        action_text: 'Chạy truy vấn đối soát dư nợ T9',
        data_text: 'mart.credit_risk · 1,28 tr dòng',
      },
    ),
    auditRow(
      4,
      todayAt(10, 12, 40),
      {
        actor_kind: 'user',
        service: 'api',
        action: 'access_request.created',
        object_kind: 'access_request',
        outcome: 'ok',
      },
      {
        actor_label: 'Lê Minh Châu',
        action_text: 'Gửi yêu cầu quyền AR-0091',
        data_text: 'NPL_RATIO · vùng Bắc Bộ',
      },
    ),
    auditRow(
      3,
      todayAt(9, 40, 2),
      {
        actor_kind: 'user',
        service: 'api',
        action: 'conflict.resolved',
        object_kind: 'conflict',
        outcome: 'ok',
      },
      {
        actor_label: 'Trần Quốc An',
        action_text: 'Chọn Core banking cho vụ lệch HN',
        data_text: 'Xung đột CF-221',
      },
    ),
    auditRow(
      2,
      todayAt(9, 12, 33),
      {
        actor_kind: 'user',
        service: 'api',
        action: 'column.read',
        object_kind: 'dataset',
        outcome: 'denied',
      },
      {
        actor_label: 'Phạm Đức Long',
        action_text: 'Bị chặn: xem cột id_number',
        data_text: 'LD_LOANS · chính sách POL-007',
      },
    ),
    auditRow(
      1,
      todayAt(8, 5, 17),
      {
        actor_kind: 'client',
        service: 'api',
        action: 'mcp.tool_called',
        object_kind: 'metric',
        outcome: 'ok',
      },
      {
        actor_label: 'Client “Copilot nội bộ”',
        action_text: 'Gọi công cụ get_metric',
        data_text: 'NPL_RATIO',
      },
    ),
  ];
}

export function securityEventsFixture(): SecurityEvent[] {
  return [
    {
      id: 'se-2',
      occurred_at: todayAt(9, 12, 33),
      kind: 'denied_spike',
      severity: 'medium',
      data_space_id: DS_RISK,
      actor_id: '00000000-0000-4000-8000-000000000002',
      source_ref: 'audit_log',
      detail: { denied: 14, window_minutes: 10 },
      status: 'open',
      handled_by: null,
    },
    {
      id: 'se-1',
      occurred_at: todayAt(7, 30),
      kind: 'prompt_injection',
      severity: 'high',
      data_space_id: DS_RISK,
      actor_id: null,
      source_ref: 'chunk',
      detail: { document: 'Báo cáo thường niên 2025' },
      status: 'closed',
      handled_by: '00000000-0000-4000-8000-000000000009',
    },
  ];
}

/** GET /v1/dsr/{id} as the api answers it (DsrRequestView), with the list's subject_hash. */
type DsrWire = Schemas['DsrRequestView'] & { subject_hash: string };

const dsrItem = (
  n: number,
  location_kind: string,
  location_ref: string,
  proposed_action: 'delete' | 'provide' | 'retain',
  legal_basis: string | null = null,
): NonNullable<DsrWire['items']>[number] => ({
  id: `0192f1a2-7c3d-7e4f-8a9b-00000000010${n}`,
  location_kind,
  location_ref,
  proposed_action,
  legal_basis,
  decision: 'pending',
  executed_at: null,
});

export function dsrFixture(): DsrWire[] {
  const stamp = daysFromNow(-4);
  return [
    {
      id: '0192f1a2-7c3d-7e4f-8a9b-000000000014',
      subject_hash: '9f2c41d0a7',
      request_kind: 'delete',
      received_at: stamp,
      due_at: daysFromNow(10),
      status: 'review',
      handler_id: 'u-khanh-linh',
      response_ref: null,
      closed_at: null,
      created_at: stamp,
      updated_at: stamp,
      items: [
        dsrItem(1, 'conversation', 'qa.question:0192f1a2-7c3d-7e4f-8a9b-0c1d2e3f4a5b', 'delete'),
        dsrItem(2, 'chunk', 'document.chunk:hồ sơ vay 2024.pdf#p3', 'delete'),
        dsrItem(3, 'agent_memory', 'agent.memory:Agent Phân tích', 'delete'),
        dsrItem(4, 'tile_snapshot', 'period.snapshot:T6–T9', 'retain', 'nghĩa vụ lưu trữ báo cáo'),
        dsrItem(5, 'source_system', 'core_banking.customers:CIF 0041872', 'provide'),
      ],
    },
    {
      id: '0192f1a2-7c3d-7e4f-8a9b-000000000015',
      subject_hash: '51ab07e3c9',
      request_kind: 'copy',
      received_at: daysFromNow(-1),
      due_at: daysFromNow(14),
      status: 'searching',
      handler_id: 'u-khanh-linh',
      response_ref: null,
      closed_at: null,
      created_at: daysFromNow(-1),
      updated_at: daysFromNow(-1),
      items: [],
    },
  ];
}

/* ─── Handlers ─────────────────────────────────────────────────────────── */

export function createAdminAccessHandlers() {
  let users = adminUsersFixture();
  let spaces = dataSpaceSummariesFixture();
  const members: Record<string, MemberWire[]> = { [DS_RISK]: membersFixture() };
  let requests = accessRequestsFixture();
  let policies = accessPoliciesFixture();
  const accessView = <T extends object>(p: T) => ({
    ...p,
    type: 'access',
    dataset_id: 'dataset-0',
    applies_to: null,
    mask_method: null,
    activated_by: null,
    created_at: '2026-09-01T09:00:00+07:00',
    updated_at: '2026-10-01T09:00:00+07:00',
  });
  let dsrs = dsrFixture();
  const exports = new Map<string, AuditExport>();
  /** GET /v1/audit's filters: exact action and outcome, occurred_at in [from, to). */
  const auditOf = (f: Record<string, string>) =>
    auditFixture().filter(
      (e) =>
        (!f.action || e.action === f.action) &&
        (!f.object_id || e.object_id === f.object_id) &&
        (!f.outcome || e.outcome === f.outcome) &&
        (!f.from || e.occurred_at >= new Date(f.from).toISOString()) &&
        (!f.to || e.occurred_at < new Date(f.to).toISOString()),
    );
  const roles = rolesFixture();
  const matrixRoles = permissionMatrixFixture().roles;

  const membersOf = (id: string) => members[id] ?? [];
  const roleName = (id: string) =>
    matrixRoles.find((r) => r.role_id === id)?.name ?? roles.find((r) => r.id === id)?.name ?? id;
  const verbOf = (action: unknown) => String(action).split(':');

  return [
    /* Người dùng */
    http.get('*/v1/admin/users', () => page(users.map(userWire))),
    http.get('*/v1/admin/roles', () => page(roles)),
    http.get('*/v1/admin/groups', () => page(groupsFixture())),
    http.post(/\/v1\/admin\/directory:sync$/, () =>
      HttpResponse.json({ status: 'accepted' }, { status: 202 }),
    ),
    http.post('*/v1/admin/invitations', async ({ request }) => {
      const body = (await request.json()) as Invitation;
      if (!/^[^@\s]+@[^@\s]+$/.test(body.email ?? ''))
        return problem(422, 'validation_failed', 'Email không hợp lệ.');
      const space = spaces.find((s) => s.id === body.data_space_id);
      const invited: AdminUser = {
        id: `u-invited-${users.length}`,
        username: body.email,
        display_name: body.email,
        kind: 'person',
        status: 'invited',
        group_label: 'Mời trực tiếp',
        role_name: roleName(body.role_id),
        data_space_text: space?.name ?? '—',
        row_scope_text: rowScopeText(body.row_scope) ?? 'Toàn hàng',
        row_scope_limited: Boolean(body.row_scope),
        valid_until: body.valid_until,
        last_synced_at: null,
        identity_source: 'SSO',
      };
      users = [invited, ...users];
      return HttpResponse.json(invited, { status: 201 });
    }),

    /* Data Space */
    http.get('*/v1/data-spaces', () => page(spaces)),
    http.post('*/v1/data-spaces', async ({ request }) => {
      const body = (await request.json()) as CreateDataSpace;
      if (!body.name?.trim()) return problem(422, 'validation_failed', 'Data Space cần có tên.');
      if (!body.steward_id)
        return problem(422, 'validation_failed', 'Chọn người phụ trách dữ liệu.');
      if (spaces.some((s) => s.name.toLowerCase() === body.name.trim().toLowerCase()))
        return problem(409, 'conflict', 'Đã có Data Space cùng tên.');
      const created: DataSpaceSummary = {
        id: `ds-${spaces.length + 1}`,
        code: body.name.trim().toUpperCase().replace(/\s+/g, '_'),
        name: body.name.trim(),
        // DataSpaceCreate has no status: the api opens the space as onboarding.
        status: 'onboarding',
        steward_id: body.steward_id,
        opened_at: null,
        my_roles: [STEWARD_ROLE],
        my_capabilities: ['ask', 'steward'],
        description: body.description ?? null,
        steward_name: users.find((u) => u.id === body.steward_id)?.display_name ?? '—',
        member_count: 0,
      };
      spaces = [...spaces, created];
      return HttpResponse.json(created, { status: 201 });
    }),
    // The sources chosen on create follow in their own PUT (A2).
    http.put('*/v1/data-spaces/:id/sources', ({ params }) => {
      const space = spaces.find((s) => s.id === params.id);
      return space
        ? HttpResponse.json(space)
        : problem(404, 'not_found', 'Không tìm thấy Data Space.');
    }),
    http.get('*/v1/data-spaces/:id', ({ params }) => {
      const space = spaces.find((s) => s.id === params.id);
      return space
        ? HttpResponse.json(space)
        : problem(404, 'not_found', 'Không tìm thấy Data Space.');
    }),
    http.get('*/v1/data-spaces/:id/members', ({ params }) => {
      if (!spaces.some((s) => s.id === params.id))
        return problem(404, 'not_found', 'Không tìm thấy Data Space.');
      return page(membersOf(String(params.id)));
    }),
    http.put('*/v1/data-spaces/:id/members', async ({ params, request }) => {
      const id = String(params.id);
      if (!spaces.some((s) => s.id === id))
        return problem(404, 'not_found', 'Không tìm thấy Data Space.');
      const body = (await request.json()) as { members: MemberInput[] };
      const groups = groupsFixture();
      members[id] = body.members.map((input) => {
        const existing = membersOf(id).find(
          (m) => m.principal_id === input.principal_id && m.role_id === input.role_id,
        );
        if (existing) return existing;
        const name =
          users.find((u) => u.id === input.principal_id)?.display_name ??
          groups.find((g) => g.id === input.principal_id)?.name ??
          input.principal_id;
        const now = new Date().toISOString();
        return {
          id: `m-${input.principal_id}-${input.role_id}`,
          data_space_id: id,
          principal_type: input.principal_type,
          principal_id: input.principal_id,
          principal_name: name,
          role_id: input.role_id,
          role_code: input.role_id.toUpperCase(),
          role_name: roleName(input.role_id),
          row_scope: input.row_scope,
          source: 'manual',
          source_ref: null,
          valid_until: input.valid_until,
          created_at: now,
          updated_at: now,
        };
      });
      return page(members[id]);
    }),
    http.get('*/v1/data-spaces/:id/permissions', ({ params }) => {
      if (!spaces.some((s) => s.id === params.id))
        return problem(404, 'not_found', 'Không tìm thấy Data Space.');
      return HttpResponse.json(permissionMatrixFixture());
    }),
    http.post(/\/v1\/permissions:simulate$/, async ({ request }) => {
      const body = (await request.json()) as Schemas['PermissionSimulate'];
      const subjects = [
        ...(body.role_ids ?? []).map((id) => ({ kind: 'role' as const, id })),
        ...(body.user_ids ?? []).map((id) => ({ kind: 'user' as const, id })),
      ];
      if (subjects.length === 0 || subjects.length > 2)
        return problem(422, 'validation_failed', 'Chọn một hoặc hai vai trò hoặc người.');
      return HttpResponse.json(permissionSimulationFixture(body.dataset_id, subjects));
    }),

    /* Yêu cầu quyền */
    http.get('*/v1/access-requests', ({ request }) => {
      const url = new URL(request.url);
      const status = url.searchParams.get('status');
      const space = url.searchParams.get('data_space_id');
      return page(
        requests.filter(
          (r) => (!status || r.status === status) && (!space || r.data_space_id === space),
        ),
      );
    }),
    http.post('*/v1/access-requests', async ({ request }) => {
      const body = (await request.json()) as Pick<
        AccessRequest,
        'data_space_id' | 'capability' | 'reason' | 'requested_until'
      >;
      if (!body.reason?.trim()) return problem(422, 'validation_failed', 'Ghi lý do xin quyền.');
      const n = 94 + requests.length;
      const created: AccessRequest = {
        ...accessRequestsFixture()[2],
        ...body,
        id: `ar-${String(n).padStart(4, '0')}`,
        code: `AR-${String(n).padStart(4, '0')}`,
        requester_id: 'me',
        title: 'Yêu cầu quyền của bạn',
        queue_label: 'Bạn · yêu cầu mới',
        created_at: new Date().toISOString(),
      };
      requests = [...requests, created];
      return HttpResponse.json(created, { status: 201 });
    }),
    http.get('*/v1/access-requests/:id', ({ params }) => {
      const item = requests.find((r) => r.id === params.id);
      return item
        ? HttpResponse.json(item)
        : problem(404, 'not_found', 'Không tìm thấy yêu cầu quyền.');
    }),
    http.post('*/v1/access-requests/:action', async ({ params, request }) => {
      const [id, verb] = verbOf(params.action);
      if (verb !== 'decide') return problem(404, 'not_found', 'Không có thao tác này.');
      const item = requests.find((r) => r.id === id);
      if (!item) return problem(404, 'not_found', 'Không tìm thấy yêu cầu quyền.');
      if (item.status !== 'pending') return problem(409, 'invalid_state', 'Yêu cầu đã được xử lý.');
      const body = (await request.json()) as DecideAccess;
      if (body.decision === 'reject' && !body.note?.trim())
        return problem(422, 'validation_failed', 'Từ chối cần ghi lý do.');
      if (body.decision === 'narrow' && !body.granted_scope?.trim())
        return problem(422, 'validation_failed', 'Ghi phạm vi được cấp.');
      const status = { approve: 'approved', narrow: 'narrowed', reject: 'rejected' }[
        body.decision
      ] as AccessRequest['status'];
      const decided: AccessRequest = {
        ...item,
        status,
        approver_id: 'me',
        decided_at: new Date().toISOString(),
        decision_note: body.note ?? null,
      };
      requests = requests.map((r) => (r.id === id ? decided : r));
      return HttpResponse.json(decided);
    }),

    /* Chính sách dữ liệu: behaviour policies (type=behavior) fall through to the rules mocks. */
    http.get('*/v1/policies', ({ request }) => {
      if (new URL(request.url).searchParams.get('type') !== 'access') return undefined;
      // AccessPolicyView with the screen's display fields alongside.
      return HttpResponse.json(policies.map(({ last_backtest: _, ...p }) => accessView(p)));
    }),
    // AccessPolicyDetail; a behaviour policy's id falls through to the rules mock.
    http.get('*/v1/policies/:id', ({ params }) => {
      const item = policies.find((p) => p.id === params.id);
      return item ? HttpResponse.json(accessView(item)) : undefined;
    }),
    http.patch('*/v1/policies/:id', async ({ params, request }) => {
      const item = policies.find((p) => p.id === params.id);
      if (!item) return undefined;
      const body = (await request.json()) as { rule_text: string };
      if (!body.rule_text?.trim())
        return problem(422, 'validation_failed', 'Quy tắc không được để trống.');
      const saved = { ...item, rule_text: body.rule_text, version: item.version + 1 };
      policies = policies.map((p) => (p.id === item.id ? saved : p));
      return HttpResponse.json(saved);
    }),
    http.post('*/v1/policies/:action', async ({ params, request }) => {
      const [id, verb] = verbOf(params.action);
      const item = policies.find((p) => p.id === id);
      if (!item) return undefined;
      if (verb === 'simulate') {
        const body = (await request.json()) as { as_user_id: string };
        const scoped = users.find((u) => u.id === body.as_user_id)?.row_scope_limited ?? false;
        // SimulationView, with the screen's comparison table under display.
        return HttpResponse.json({
          policy_id: item.id,
          as_user_id: body.as_user_id,
          applies: true,
          reason: scoped ? 'phạm vi dòng của người dùng' : 'không giới hạn dòng',
          display: {
            question: item.sample_question,
            rows: [
              { label: 'Số chi nhánh thấy được', without: '184', with: scoped ? '3' : '184' },
              {
                label: 'Số dòng khách hàng',
                without: '1.102.311',
                with: scoped ? '18.420' : '1.102.311',
              },
              { label: 'Cột id_number', without: 'hiện', with: 'che' },
              {
                label: 'Câu trả lời',
                without: 'số toàn hàng',
                with: scoped
                  ? 'số 3 chi nhánh Hà Nội, ghi rõ “trong phạm vi của bạn”'
                  : 'số toàn hàng',
              },
            ],
          },
        });
      }
      if (verb === 'backtest') {
        const result = item.last_backtest ?? {
          window_days: 7,
          affected_queries: 0,
          total_queries: 18302,
          affected_users_text: '0',
          empty_queries_text: '0',
        };
        policies = policies.map((p) => (p.id === id ? { ...p, last_backtest: result } : p));
        return HttpResponse.json(result);
      }
      if (verb === 'disable') {
        const body = (await request.json().catch(() => null)) as { reason?: string } | null;
        if (!body?.reason?.trim())
          return problem(422, 'validation_failed', 'Ghi lý do tắt chính sách.');
        if (item.status !== 'active')
          return problem(409, 'invalid_state', 'Chính sách không đang bật.');
        const disabled = { ...item, status: 'disabled' as const };
        policies = policies.map((p) => (p.id === id ? disabled : p));
        return HttpResponse.json(disabled);
      }
      return problem(404, 'not_found', 'Không có thao tác này.');
    }),

    /* Kiểm toán */
    http.get('*/v1/audit', ({ request }) => {
      const url = new URL(request.url);
      const filters = Object.fromEntries(url.searchParams) as Record<string, string>;
      return page(auditOf(filters));
    }),
    http.post(/\/v1\/audit:verify$/, () =>
      HttpResponse.json({ ok: true, broken_at_seq: null, checked_rows: auditFixture().length }),
    ),
    http.post(/\/v1\/audit:export$/, async ({ request }) => {
      const body = (await request.json()) as { filters: Record<string, string>; format?: string };
      // Without a day the export is the whole log: past BR-138's threshold, so it waits.
      const rowCount = body.filters.from ? auditOf(body.filters).length : 1_240_000;
      const status = rowCount > 100_000 ? 'waiting_approval' : 'queued';
      const now = new Date().toISOString();
      const job: AuditExport = {
        id: `00000000-0000-7000-9000-${String(exports.size + 1).padStart(12, '0')}`,
        filters: body.filters,
        format: body.format ?? 'csv',
        row_count: rowCount,
        status,
        requested_by: 'me',
        approved_by: null,
        approved_at: null,
        signature: null,
        expires_at: null,
        created_at: now,
        updated_at: now,
      };
      exports.set(job.id, job);
      return HttpResponse.json(
        { id: job.id, status, row_count: rowCount, poll_after_ms: 2000 },
        { status: 202, headers: { Location: `/v1/audit/exports/${job.id}` } },
      );
    }),
    http.get('*/v1/audit/exports/:id', ({ params }) => {
      const job = exports.get(String(params.id));
      if (!job) return problem(404, 'not_found', 'No such export.');
      // A queued export is written and signed by the next read.
      if (job.status === 'queued') {
        const ready: AuditExport = {
          ...job,
          status: 'ready',
          signature: 'MEUCIQDx3sYk8b',
          expires_at: daysFromNow(7),
          updated_at: new Date().toISOString(),
        };
        exports.set(job.id, ready);
        return HttpResponse.json(ready);
      }
      return HttpResponse.json(job);
    }),
    http.get('*/v1/security-events', ({ request }) => {
      const url = new URL(request.url);
      const severity = url.searchParams.get('severity');
      const status = url.searchParams.get('status');
      return page(
        securityEventsFixture().filter(
          (e) => (!severity || e.severity === severity) && (!status || e.status === status),
        ),
      );
    }),

    /* Yêu cầu của chủ thể dữ liệu */
    http.get('*/v1/dsr', () => page(dsrs)),
    http.post('*/v1/dsr', async ({ request }) => {
      const body = (await request.json()) as CreateDsr;
      if (!body.subject_identifier?.trim())
        return problem(422, 'validation_failed', 'Nhập định danh của chủ thể dữ liệu.');
      const now = new Date().toISOString();
      const created: DsrWire = {
        id: `0192f1a2-7c3d-7e4f-8a9b-${String(16 + dsrs.length).padStart(12, '0')}`,
        subject_hash: 'c0ffee1234',
        request_kind: body.request_kind,
        received_at: body.received_at,
        due_at: body.due_at,
        status: 'searching',
        handler_id: 'u-khanh-linh',
        response_ref: null,
        closed_at: null,
        created_at: now,
        updated_at: now,
        items: [],
      };
      dsrs = [...dsrs, created];
      return HttpResponse.json(
        { id: created.id, status: created.status, poll_after_ms: 2000 },
        { status: 202 },
      );
    }),
    http.get('*/v1/dsr/:id', ({ params }) => {
      const item = dsrs.find((d) => d.id === params.id);
      return item ? HttpResponse.json(item) : problem(404, 'not_found', 'Không tìm thấy yêu cầu.');
    }),
    http.post('*/v1/dsr/:action', async ({ params, request }) => {
      const [id, verb] = verbOf(params.action);
      const item = dsrs.find((d) => d.id === id);
      if (!item) return problem(404, 'not_found', 'Không tìm thấy yêu cầu.');
      if (item.status === 'done' || item.status === 'rejected')
        return problem(409, 'invalid_state', 'Yêu cầu đã đóng.');
      if (verb === 'execute') {
        // The api's rules (governance/dsr.py): every pending item decided, a retained one with
        // its legal basis, nothing in a source system deleted.
        const body = (await request.json()) as { items: Schemas['DsrDecision'][] };
        const decided = new Map(body.items.map((d) => [d.id, d]));
        if ((item.items ?? []).some((i) => i.decision === 'pending' && !decided.has(i.id)))
          return problem(422, 'business_rule_violated', 'Mọi mục đang chờ cần một quyết định.');
        for (const i of item.items ?? []) {
          const d = decided.get(i.id);
          if (d?.action === 'retain' && !d.legal_basis)
            return problem(422, 'business_rule_violated', 'Giữ lại cần căn cứ pháp lý.');
          if (d?.action === 'delete' && i.location_kind === 'source_system')
            return problem(422, 'business_rule_violated', 'Dữ liệu ở nguồn chỉ được báo cáo.');
        }
        // What the worker does next: the request done, the decisions carried out.
        const done: DsrWire = {
          ...item,
          status: 'done',
          closed_at: new Date().toISOString(),
          items: (item.items ?? []).map((i) => {
            const d = decided.get(i.id);
            if (!d) return i;
            return d.action === 'retain'
              ? {
                  ...i,
                  proposed_action: 'retain',
                  legal_basis: d.legal_basis ?? null,
                  decision: 'retained',
                }
              : {
                  ...i,
                  proposed_action: d.action,
                  decision: 'done',
                  executed_at: new Date().toISOString(),
                };
          }),
        };
        dsrs = dsrs.map((d) => (d.id === id ? done : d));
        return HttpResponse.json({ id, status: 'executing', poll_after_ms: 2000 }, { status: 202 });
      }
      if (verb === 'reject') {
        const body = (await request.json()) as { reason: string };
        if (!body.reason?.trim()) return problem(422, 'validation_failed', 'Ghi lý do từ chối.');
        const rejected: DsrWire = {
          ...item,
          status: 'rejected',
          closed_at: new Date().toISOString(),
        };
        dsrs = dsrs.map((d) => (d.id === id ? rejected : d));
        return HttpResponse.json(rejected);
      }
      return problem(404, 'not_found', 'Không có thao tác này.');
    }),
  ];
}
