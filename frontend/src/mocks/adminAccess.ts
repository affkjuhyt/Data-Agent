import { http, HttpResponse } from 'msw';
import type {
  AccessPolicy,
  AccessRequest,
  AdminUser,
  AuditEntry,
  CapabilityKey,
  CreateDataSpace,
  CreateDsr,
  DataSpaceMember,
  DataSpaceSummary,
  DecideAccess,
  DsrRequest,
  Invitation,
  MemberInput,
  PermissionCell,
  PermissionMatrix,
  PermissionSimulation,
  Role,
  UserGroup,
} from '../lib/v1/adminAccess';


const STEWARD_ROLE = { id: 'role-ac03', code: 'AC03', name: 'Người phụ trách dữ liệu' };
/*
 * MSW handlers for Quản trị · người, quyền và tuân thủ (lib/v1/adminAccess.ts), data taken from
 * the P11 frames of Figma. State lives in memory: deciding an access request removes it from
 * the queue, disabling a policy changes its status, executing a DSR marks its items done.
 *
 * GET /v1/admin/users and GET /v1/data-spaces return a superset of AppUser / DataSpace, so
 * these handlers must be registered before createHandlers() to answer for the whole app.
 * GET /v1/policies is answered only for `family=access`; anything else falls through.
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
    { id: 'role-analyst', code: 'AC02', name: 'Chuyên viên phân tích', capabilities: ['ask', 'view_detail', 'run_agent'] },
    { id: 'role-steward', code: 'AC03', name: 'Người phụ trách dữ liệu', capabilities: ['ask', 'view_detail', 'approve'] },
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
    person('u-hai-yen', 'Đỗ Hải Yến', 'GRP-RISK-ANALYST', 'Chuyên viên phân tích', 'Rủi ro tín dụng, Bán lẻ', 'Toàn hàng'),
    person('u-duc-long', 'Phạm Đức Long', 'GRP-RISK-HEAD', 'Người phụ trách dữ liệu', 'Rủi ro tín dụng', 'Toàn hàng'),
    person('u-minh-chau', 'Lê Minh Châu', 'GRP-BRANCH-HN', 'Chuyên viên phân tích', 'Kinh doanh bán lẻ', '3 chi nhánh Hà Nội', true),
    person('u-khanh-linh', 'Nguyễn Khánh Linh', 'GRP-AUDIT', 'Kiểm toán nội bộ', 'Tất cả · chỉ đọc nhật ký', 'Toàn hàng', false, yearEnd),
    {
      ...person('agent-doi-soat', 'Agent Đối soát', 'agent', 'Agent', 'Rủi ro tín dụng', 'theo người sở hữu'),
      kind: 'agent',
      last_synced_at: null,
    },
  ];
  // The rest of the 412 people synced from AD; the frame shows the first rows only.
  const rest = Array.from({ length: 408 }, (_, i) =>
    person(`u-${i}`, `Người dùng ${i + 1}`, 'GRP-BRANCH-HN', 'Người dùng nghiệp vụ', 'Kinh doanh bán lẻ', 'Chi nhánh của mình', true),
  );
  const agents = ['Agent Báo cáo', 'Agent Phân tích', 'Agent Giám sát rủi ro', 'Agent Thu nợ', 'Agent Chất lượng dữ liệu'].map(
    (name, i) => ({
      ...person(`agent-${i}`, name, 'agent', 'Agent', 'Rủi ro tín dụng', 'theo người sở hữu'),
      kind: 'agent' as const,
      last_synced_at: null,
    }),
  );
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
    ds(DS_RISK, 'RUI_RO_TIN_DUNG', 'Rủi ro tín dụng', 'Trần Quốc An', 66, 'active', 'Dùng cho phân tích rủi ro tín dụng bán lẻ và doanh nghiệp'),
    ds(DS_RETAIL, 'BAN_LE', 'Kinh doanh bán lẻ', 'Phạm Đức Long', 128),
    ds('ds-nguon-von', 'NGUON_VON', 'Nguồn vốn', 'Vũ Thị Mai', 22),
    ds('ds-bao-cao-nhnn', 'BAO_CAO_NHNN', 'Báo cáo NHNN', 'Hoàng Văn Bình', 15, 'onboarding'),
    ds('ds-thu-no', 'THU_NO', 'Thu hồi nợ', 'Đặng Thu Trang', 9, 'draft'),
  ];
}

export function membersFixture(): DataSpaceMember[] {
  const m = (
    id: string,
    name: string,
    roleId: string,
    roleName: string,
    scope: string | null,
    extra: Partial<DataSpaceMember> = {},
  ): DataSpaceMember => ({
    id,
    principal_type: 'user',
    principal_id: id,
    role_id: roleId,
    source: 'manual',
    valid_until: null,
    display_name: name,
    role_name: roleName,
    row_scope_text: scope,
    ...extra,
  });
  return [
    m('u-quoc-an', 'Trần Quốc An', 'role-owner', 'Chủ sở hữu dữ liệu', null),
    m('u-hai-yen', 'Đỗ Hải Yến', 'role-control', 'Kiểm soát, tuân thủ', null, { access_note: 'đọc' }),
    m('u-thu-ha', 'Nguyễn Thu Hà', 'role-ds-analyst', 'Phân tích viên', 'Vùng: Bắc', { source: 'group_mapping' }),
    m('u-minh-tam', 'Lê Minh Tâm', 'role-viewer', 'Người xem', null, {
      source: 'access_request',
      valid_until: new Date(new Date().getFullYear(), 9, 30, 23, 59).toISOString(),
    }),
    m('agent-doi-soat', 'Agent Đối soát', 'role-agent', 'Agent (theo chủ sở hữu)', null, {
      principal_type: 'agent',
      agent_status: 'running',
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
      cells: Object.fromEntries(keys.map((k, i) => [k, cells[i]])) as Record<CapabilityKey, PermissionCell>,
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
      column: 'national_id, account_no che với mọi vai trò trừ Chủ sở hữu khi có lý do ghi kiểm toán',
      agent: 'Quyền agent = giao của chủ sở hữu agent và phạm vi cấu hình; không bao giờ có “Xem PII”',
    },
  };
}

export function permissionSimulationFixture(): PermissionSimulation {
  const columns = ['loan_id', 'customer', 'region', 'balance'];
  return {
    table: 'ld_loans',
    views: [
      {
        principal_name: 'Nguyễn Thu Hà',
        role_name: 'Phân tích viên',
        scope_text: 'Chỉ thấy vùng Bắc · PII bị che',
        columns,
        rows: [
          ['LD2409-0187', '••••4821', 'DH Bắc Bộ', '1.250.000.000'],
          ['LD2409-0190', '••••7712', 'DH Bắc Bộ', '480.000.000'],
          ['LD2311-0456', '••••3390', 'DH Bắc Bộ', '620.000.000'],
        ],
        muted: Array.from({ length: 3 }, () => [false, true, false, false]),
        actions: [
          { label: 'Tạo bảng dẫn xuất', allowed: true },
          { label: 'Chạy agent', allowed: true },
        ],
      },
      {
        principal_name: 'Lê Minh Tâm',
        role_name: 'Người xem',
        scope_text: 'Chỉ thấy số tổng hợp theo vùng',
        columns,
        rows: [
          ['Tổng hợp', '—', 'DH Bắc Bộ', '2.350.000.000'],
          ['Tổng hợp', '—', 'ĐB sông Hồng', '4.120.000.000'],
          ['Tổng hợp', '—', 'Đông Nam Bộ', '3.880.000.000'],
        ],
        muted: Array.from({ length: 3 }, () => [false, true, false, false]),
        actions: [
          {
            label: 'Tạo bảng dẫn xuất',
            allowed: false,
            reason: 'Cần vai trò Phân tích viên · bấm để yêu cầu quyền',
            request_capability: 'view_detail',
          },
          {
            label: 'Chạy agent',
            allowed: false,
            reason: 'Cần vai trò Phân tích viên · bấm để yêu cầu quyền',
            request_capability: 'run_agent',
          },
        ],
      },
    ],
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
      suggested_scope_text: 'Số tổng hợp theo vùng DH Bắc Bộ và ĐB sông Hồng, không có dòng khách hàng',
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
  const list = names.map(([name, kind, ds, dsName], i) => policy(i + 1, name, kind, ds, dsName, 'active', 3 + i));
  const pol007: AccessPolicy = {
    ...policy(7, 'Chi nhánh chỉ thấy khách hàng của mình', 'row', DS_RETAIL, 'Kinh doanh bán lẻ', 'active', 23),
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
    policy(8 + i, `Chính sách theo dòng ${8 + i}`, 'row', DS_RETAIL, 'Kinh doanh bán lẻ', i < 2 ? 'draft' : 'active', i),
  );
  return [...list, pol007, ...more];
}

export function auditFixture(): AuditEntry[] {
  return [
    { id: 'au-6', at: todayAt(10, 52, 4), actor_label: 'Đỗ Hải Yến', actor_kind: 'person', action_text: 'Xuất Excel tổng hợp nợ xấu theo vùng', data_text: 'NPL_RATIO · 6 dòng', channel: 'web', outcome: 'ok' },
    { id: 'au-5', at: todayAt(10, 41, 12), actor_label: 'Agent Đối soát (của Đỗ Hải Yến)', actor_kind: 'agent', action_text: 'Chạy truy vấn đối soát dư nợ T9', data_text: 'mart.credit_risk · 1,28 tr dòng', channel: 'agent', outcome: 'ok' },
    { id: 'au-4', at: todayAt(10, 12, 40), actor_label: 'Lê Minh Châu', actor_kind: 'person', action_text: 'Gửi yêu cầu quyền AR-0091', data_text: 'NPL_RATIO · vùng Bắc Bộ', channel: 'web', outcome: 'ok' },
    { id: 'au-3', at: todayAt(9, 40, 2), actor_label: 'Trần Quốc An', actor_kind: 'person', action_text: 'Chọn Core banking cho vụ lệch HN', data_text: 'Xung đột CF-221', channel: 'teams', outcome: 'ok' },
    { id: 'au-2', at: todayAt(9, 12, 33), actor_label: 'Phạm Đức Long', actor_kind: 'person', action_text: 'Bị chặn: xem cột id_number', data_text: 'LD_LOANS · chính sách POL-007', channel: 'web', outcome: 'blocked' },
    { id: 'au-1', at: todayAt(8, 5, 17), actor_label: 'Client “Copilot nội bộ”', actor_kind: 'client', action_text: 'Gọi công cụ get_metric', data_text: 'NPL_RATIO', channel: 'mcp', outcome: 'ok' },
  ];
}

export function dsrFixture(): DsrRequest[] {
  return [
    {
      id: 'dsr-014',
      code: 'DSR-014',
      request_kind: 'delete',
      title: 'Khách hàng yêu cầu xoá dữ liệu cá nhân',
      received_at: daysFromNow(-4),
      due_at: daysFromNow(10),
      status: 'review',
      handler_id: 'u-khanh-linh',
      handler_name: 'Nguyễn Khánh Linh',
      channel_label: 'kênh pháp chế',
      legal_ref: 'Nghị định 13',
      closed_at: null,
      items: [
        { id: 'it-1', location_kind: 'conversation', location_ref: 'qa.question', proposed_action: 'delete', legal_basis: null, decision: 'pending', location_label: 'Câu hỏi và trả lời trong phiên', kind_label: 'Nhật ký hỏi đáp', entry_count: 14 },
        { id: 'it-2', location_kind: 'chunk', location_ref: 'kb.document', proposed_action: 'delete', legal_basis: null, decision: 'pending', location_label: 'Tài liệu tải lên: hồ sơ vay', kind_label: 'Tài liệu', entry_count: 3 },
        { id: 'it-3', location_kind: 'agent_memory', location_ref: 'agent.memory', proposed_action: 'delete', legal_basis: null, decision: 'pending', location_label: 'Ghi nhớ của Agent Phân tích', kind_label: 'Bộ nhớ agent', entry_count: 1 },
        { id: 'it-4', location_kind: 'tile_snapshot', location_ref: 'period.snapshot', proposed_action: 'retain', legal_basis: 'nghĩa vụ lưu trữ báo cáo', decision: 'pending', location_label: 'Bản kỳ chốt T6–T9 đã đóng băng', kind_label: 'Kỳ chốt', entry_count: 4 },
        { id: 'it-5', location_kind: 'audit', location_ref: 'audit.audit_log', proposed_action: 'retain', legal_basis: 'đã che, chỉ giữ mã', decision: 'pending', location_label: 'Nhật ký kiểm toán', kind_label: 'Kiểm toán', entry_count: 22 },
      ],
    },
    {
      id: 'dsr-015',
      code: 'DSR-015',
      request_kind: 'copy',
      title: 'Khách hàng yêu cầu nhận bản sao dữ liệu',
      received_at: daysFromNow(-1),
      due_at: daysFromNow(14),
      status: 'searching',
      handler_id: 'u-khanh-linh',
      handler_name: 'Nguyễn Khánh Linh',
      channel_label: 'quầy giao dịch Hà Nội',
      legal_ref: 'Nghị định 13',
      closed_at: null,
      items: [],
    },
  ];
}

/* ─── Handlers ─────────────────────────────────────────────────────────── */

const KIND_TITLE: Record<CreateDsr['request_kind'], string> = {
  access: 'Khách hàng yêu cầu biết dữ liệu được xử lý',
  copy: 'Khách hàng yêu cầu nhận bản sao dữ liệu',
  delete: 'Khách hàng yêu cầu xoá dữ liệu cá nhân',
  correct: 'Khách hàng yêu cầu sửa dữ liệu cá nhân',
};

export function createAdminAccessHandlers() {
  let users = adminUsersFixture();
  let spaces = dataSpaceSummariesFixture();
  const members: Record<string, DataSpaceMember[]> = { [DS_RISK]: membersFixture() };
  let requests = accessRequestsFixture();
  let policies = accessPoliciesFixture();
  let dsrs = dsrFixture();
  const roles = rolesFixture();
  const matrixRoles = permissionMatrixFixture().roles;

  const membersOf = (id: string) => members[id] ?? [];
  const roleName = (id: string) =>
    matrixRoles.find((r) => r.role_id === id)?.name ?? roles.find((r) => r.id === id)?.name ?? id;
  const verbOf = (action: unknown) => String(action).split(':');

  return [
    /* Người dùng */
    http.get('*/v1/admin/users', () => page(users)),
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
        row_scope_text: body.row_scope ?? 'Toàn hàng',
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
        status: body.status,
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
    http.get('*/v1/data-spaces/:id', ({ params }) => {
      const space = spaces.find((s) => s.id === params.id);
      return space ? HttpResponse.json(space) : problem(404, 'not_found', 'Không tìm thấy Data Space.');
    }),
    http.get('*/v1/data-spaces/:id/members', ({ params }) => {
      if (!spaces.some((s) => s.id === params.id))
        return problem(404, 'not_found', 'Không tìm thấy Data Space.');
      return page(membersOf(String(params.id)));
    }),
    http.put('*/v1/data-spaces/:id/members', async ({ params, request }) => {
      const id = String(params.id);
      if (!spaces.some((s) => s.id === id)) return problem(404, 'not_found', 'Không tìm thấy Data Space.');
      const body = (await request.json()) as { items: MemberInput[] };
      const groups = groupsFixture();
      members[id] = body.items.map((input) => {
        const existing = membersOf(id).find(
          (m) => m.principal_id === input.principal_id && m.role_id === input.role_id,
        );
        if (existing) return existing;
        const name =
          users.find((u) => u.id === input.principal_id)?.display_name ??
          groups.find((g) => g.id === input.principal_id)?.name ??
          input.principal_id;
        return {
          id: `m-${input.principal_id}-${input.role_id}`,
          principal_type: input.principal_type,
          principal_id: input.principal_id,
          role_id: input.role_id,
          source: 'manual',
          valid_until: input.valid_until,
          display_name: name,
          role_name: roleName(input.role_id),
          row_scope_text: input.row_scope,
        };
      });
      return page(members[id]);
    }),
    http.get('*/v1/data-spaces/:id/permissions', ({ params }) => {
      if (!spaces.some((s) => s.id === params.id))
        return problem(404, 'not_found', 'Không tìm thấy Data Space.');
      return HttpResponse.json(permissionMatrixFixture());
    }),
    http.post(/\/v1\/permissions:simulate$/, () => HttpResponse.json(permissionSimulationFixture())),

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
      return item ? HttpResponse.json(item) : problem(404, 'not_found', 'Không tìm thấy yêu cầu quyền.');
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
      const status = { approve: 'approved', narrow: 'narrowed', reject: 'rejected' }[body.decision] as AccessRequest['status'];
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

    /* Chính sách dữ liệu: behaviour policies (no family=access) fall through to createHandlers. */
    http.get('*/v1/policies', ({ request }) => {
      if (new URL(request.url).searchParams.get('family') !== 'access') return undefined;
      return page(policies);
    }),
    http.get('*/v1/policies/:id', ({ params }) => {
      // Ids this mock does not hold (behaviour rules) fall through to the rules mock.
      const item = policies.find((p) => p.id === params.id);
      return item ? HttpResponse.json(item) : undefined;
    }),
    http.patch('*/v1/policies/:id', async ({ params, request }) => {
      const item = policies.find((p) => p.id === params.id);
      if (!item) return undefined;
      const body = (await request.json()) as { rule_text: string };
      if (!body.rule_text?.trim()) return problem(422, 'validation_failed', 'Quy tắc không được để trống.');
      const saved = { ...item, rule_text: body.rule_text, version: item.version + 1 };
      policies = policies.map((p) => (p.id === item.id ? saved : p));
      return HttpResponse.json(saved);
    }),
    http.post('*/v1/policies/:action', async ({ params, request }) => {
      const [id, verb] = verbOf(params.action);
      const item = policies.find((p) => p.id === id);
      if (!item) return undefined;
      if (verb === 'simulate') {
        const body = (await request.json()) as { user_id: string; question: string };
        const scoped = users.find((u) => u.id === body.user_id)?.row_scope_limited ?? false;
        return HttpResponse.json({
          question: body.question,
          rows: [
            { label: 'Số chi nhánh thấy được', without: '184', with: scoped ? '3' : '184' },
            { label: 'Số dòng khách hàng', without: '1.102.311', with: scoped ? '18.420' : '1.102.311' },
            { label: 'Cột id_number', without: 'hiện', with: 'che' },
            {
              label: 'Câu trả lời',
              without: 'số toàn hàng',
              with: scoped ? 'số 3 chi nhánh Hà Nội, ghi rõ “trong phạm vi của bạn”' : 'số toàn hàng',
            },
          ],
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
        if (item.status !== 'active') return problem(409, 'invalid_state', 'Chính sách không đang bật.');
        const disabled = { ...item, status: 'disabled' as const };
        policies = policies.map((p) => (p.id === id ? disabled : p));
        return HttpResponse.json(disabled);
      }
      return problem(404, 'not_found', 'Không có thao tác này.');
    }),

    /* Kiểm toán */
    http.get('*/v1/audit', ({ request }) => {
      const url = new URL(request.url);
      const q = url.searchParams.get('q')?.toLowerCase();
      const kind = url.searchParams.get('actor_kind');
      const channel = url.searchParams.get('channel');
      return page(
        auditFixture().filter(
          (e) =>
            (!q || `${e.actor_label} ${e.action_text} ${e.data_text}`.toLowerCase().includes(q)) &&
            (!kind || e.actor_kind === kind) &&
            (!channel || e.channel === channel),
        ),
      );
    }),
    http.post(/\/v1\/audit:export$/, () =>
      HttpResponse.json({ status: 'accepted', needs_approval: false }, { status: 202 }),
    ),

    /* Yêu cầu của chủ thể dữ liệu */
    http.get('*/v1/dsr', () => page(dsrs)),
    http.post('*/v1/dsr', async ({ request }) => {
      const body = (await request.json()) as CreateDsr;
      if (!body.subject_identifier?.trim())
        return problem(422, 'validation_failed', 'Nhập định danh của chủ thể dữ liệu.');
      const n = 14 + dsrs.length;
      const created: DsrRequest = {
        id: `dsr-${String(n).padStart(3, '0')}`,
        code: `DSR-${String(n).padStart(3, '0')}`,
        request_kind: body.request_kind,
        title: KIND_TITLE[body.request_kind],
        received_at: new Date().toISOString(),
        due_at: body.due_at,
        status: 'searching',
        handler_id: 'me',
        handler_name: 'Bạn',
        channel_label: body.channel,
        legal_ref: 'Nghị định 13',
        closed_at: null,
        items: [],
      };
      dsrs = [...dsrs, created];
      return HttpResponse.json(created, { status: 201 });
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
        const body = (await request.json()) as { item_ids: string[] };
        if (!body.item_ids?.length) return problem(422, 'validation_failed', 'Chọn ít nhất một nơi để xoá.');
        if (item.items.some((i) => body.item_ids.includes(i.id) && i.proposed_action === 'retain'))
          return problem(422, 'business_rule_violated', 'Có mục phải giữ theo nghĩa vụ pháp lý.');
        const done: DsrRequest = {
          ...item,
          status: 'done',
          closed_at: new Date().toISOString(),
          items: item.items.map((i) =>
            body.item_ids.includes(i.id)
              ? { ...i, decision: 'done' }
              : i.proposed_action === 'retain'
                ? { ...i, decision: 'retained' }
                : i,
          ),
        };
        dsrs = dsrs.map((d) => (d.id === id ? done : d));
        return HttpResponse.json(done);
      }
      if (verb === 'reject') {
        const body = (await request.json()) as { reason: string };
        if (!body.reason?.trim()) return problem(422, 'validation_failed', 'Ghi lý do từ chối.');
        const rejected: DsrRequest = { ...item, status: 'rejected', closed_at: new Date().toISOString() };
        dsrs = dsrs.map((d) => (d.id === id ? rejected : d));
        return HttpResponse.json(rejected);
      }
      return problem(404, 'not_found', 'Không có thao tác này.');
    }),
  ];
}
