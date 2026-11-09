import { V1Error, type Page, api, ok, okAs } from './client';
import type { components } from './schema';
import type { AppUser, DataSpace } from './types';

type Schemas = components['schemas'];

/*
 * Quản trị · người, quyền và tuân thủ (P11: UC-11-01, -02, -03, -06, -07, -08), with the paths
 * of "Đặc tả API" A2: org (admin users, Data Space, access requests), governance (policies,
 * DSR) and audit. Columns follow D4 tables org.app_user, org.role, org.data_space_member,
 * org.access_request, governance.access_policy, governance.dsr_request/dsr_item.
 *
 * Fields marked PROPOSAL are not in the spec: the screens need a display text or a computed
 * figure the tables do not hold, and the backend is asked to add them. Endpoints marked
 * PROPOSAL are missing from A2 (the spec has PUT members but no GET, …).
 * The api's rows are read into the screens' shapes below; what only a PROPOSAL field says is
 * derived from the row or left empty.
 */

type Uuid = string;
type Timestamp = string;

const ALL = { page_size: 500 };

/* ─── Người dùng và vai trò (UC-11-01) ───────────────────────────────── */

/** GET /v1/admin/users: "người dùng và vai trò hiệu lực". */
export interface AdminUser extends AppUser {
  username: string;
  /** PROPOSAL: the identity-source group that gives the role, e.g. GRP-RISK-ANALYST; "agent" for agents. */
  group_label: string;
  /** PROPOSAL: name of the effective role (org.user_role or data_space_member → org.role). */
  role_name: string;
  /** PROPOSAL: Data Space names the user is a member of, already joined for display. */
  data_space_text: string;
  /** PROPOSAL: row scope in words ("Toàn hàng", "3 chi nhánh Hà Nội"). */
  row_scope_text: string;
  /** PROPOSAL: true when the row scope narrows the data (drawn in warning). */
  row_scope_limited: boolean;
  /** Earliest valid_until of the user's grants (BR-142); null = no expiry. */
  valid_until: Timestamp | null;
  last_synced_at: Timestamp | null;
  /** PROPOSAL: label of the identity source, e.g. "Active Directory". */
  identity_source: string;
}

/** org.role. GET /v1/admin/roles is a PROPOSAL (A2 has no role list). */
export interface Role {
  id: Uuid;
  code: string;
  name: string;
  /** ask, view_detail, approve, run_agent, create_agent, admin, audit_read… */
  capabilities: string[];
}

/** org.user_group. GET /v1/admin/groups is a PROPOSAL. */
export interface UserGroup {
  id: Uuid;
  name: string;
  member_count: number;
  source: 'ad' | 'ldap' | 'oidc' | 'local';
}

/** PROPOSAL: POST /v1/admin/invitations — A2 only syncs people from the identity source. */
export interface Invitation {
  email: string;
  role_id: Uuid;
  data_space_id: Uuid;
  row_scope: RowScope | null;
  valid_until: string | null;
}

/**
 * A row boundary of a membership (org.data_space_member.row_scope): the values a column may take,
 * {"region": ["Bắc", "Trung"]}. The query engine adds it as `column IN (values)` on every table
 * of the Data Space that has the column; null is every row.
 */
export type RowScope = Record<string, string[]>;

/** "region: Bắc, Trung · branch: HN01"; null for every row. */
export const rowScopeText = (scope: RowScope | null): string | null =>
  scope && Object.keys(scope).length
    ? Object.entries(scope)
        .map(([column, values]) => `${column}: ${values.join(', ')}`)
        .join(' · ')
    : null;

/** GET /v1/admin/users as the api sends it, with the PROPOSAL display fields when present. */
type AdminUserWire = Schemas['AdminUser'] &
  Partial<
    Pick<
      AdminUser,
      | 'group_label'
      | 'role_name'
      | 'data_space_text'
      | 'row_scope_text'
      | 'row_scope_limited'
      | 'valid_until'
      | 'identity_source'
    >
  >;

/*
 * org.app_user.kind is human, agent or service. A service account (an API client) is not a
 * person anyone may pick as an owner or a reviewer, so it reads as a non-person like an agent.
 */
export const readAppUser = (raw: Schemas['AdminUser']): AppUser => ({
  id: raw.id,
  display_name: raw.display_name,
  kind: raw.kind === 'human' ? 'person' : 'agent',
  status: raw.status,
});

/** The effective roles in words: the organisation's, then each Data Space's by its code. */
function readAdminUser(raw: AdminUserWire): AdminUser {
  const orgRoles = (raw.org_roles ?? []).map((r) => r.name);
  const spaceRoles = raw.data_space_roles ?? [];
  return {
    ...readAppUser(raw),
    username: raw.username,
    group_label: raw.group_label ?? (raw.kind === 'agent' ? 'agent' : ''),
    role_name:
      raw.role_name ??
      [...orgRoles, ...spaceRoles.flatMap((s) => s.roles.map((r) => r.name))]
        .filter((n, i, all) => all.indexOf(n) === i)
        .join(', '),
    data_space_text: raw.data_space_text ?? spaceRoles.map((s) => s.data_space_code).join(', '),
    row_scope_text: raw.row_scope_text ?? '',
    row_scope_limited: raw.row_scope_limited ?? false,
    valid_until: raw.valid_until ?? null,
    last_synced_at: raw.last_synced_at,
    identity_source: raw.identity_source ?? '',
  };
}

export const listAdminUsers = async () => {
  const page = await ok<Page<AdminUserWire>>(
    api.GET('/v1/admin/users', { params: { query: ALL } }),
  );
  return { ...page, items: page.items.map(readAdminUser) };
};
export const listRoles = () => ok<Page<Role>>(api.GET('/v1/admin/roles'));
export const listGroups = () => okAs<Page<UserGroup>>(api.GET('/v1/admin/groups'));
/** PROPOSAL: re-read people and groups from the identity source now (202). */
export const syncDirectory = () => ok<{ status: string }>(api.POST('/v1/admin/directory:sync'));
export const inviteUser = (body: Invitation) =>
  okAs<AdminUser>(api.POST('/v1/admin/invitations', { body }));

/* ─── Tổ chức: tự duyệt ──────────────────────────────────────────────── */

/** org.organization with allow_self_approval: on, a creator may approve their own work. */
export type Organization = Schemas['OrganizationView'];

/** GET /v1/admin/organization: anyone in the organisation may read it. */
export const getOrganization = () => ok<Organization>(api.GET('/v1/admin/organization'));

/** PATCH /v1/admin/organization (admin): each change is audited with its before and after. */
export const setSelfApproval = (on: boolean) =>
  ok<Organization>(
    api.PATCH('/v1/admin/organization', {
      body: { allow_self_approval: on } satisfies Schemas['OrganizationPatch'],
    }),
  );

/* ─── Data Space, thành viên và quyền (UC-11-08) ─────────────────────── */

export interface DataSpaceSummary extends DataSpace {
  /** PROPOSAL: steward display name (steward_id is the column); empty without it. */
  steward_name: string;
  /** PROPOSAL: members counted over people, groups' people and agents; null without it. */
  member_count: number | null;
}

/** The four flags of "Tạo Data Space" (data_space.default_policy). */
export interface DefaultPolicy {
  mask_pii: boolean;
  agent_write_derived_only: boolean;
  show_filed_and_current: boolean;
  allow_external_channels: boolean;
}

export interface CreateDataSpace {
  name: string;
  description?: string;
  steward_id: Uuid;
  default_policy: DefaultPolicy;
  /** PUT /v1/data-spaces/{id}/sources is a separate call in A2; PROPOSAL: accept them on create. */
  source_ids: Uuid[];
  /** PROPOSAL: group mappings created with the Data Space. */
  group_ids: Uuid[];
  /** "Lưu nháp" keeps it draft; "Tạo Data Space" moves it to onboarding. */
  status: 'draft' | 'onboarding';
}

/*
 * The members, sources and permission matrix of a Data Space act on the space named by
 * X-Data-Space and answer 404 for any other (org/admin/common.py same_space): the admin pages
 * open a space that need not be the one picked in the top bar, or one just created.
 */
const inSpace = (id: string) => ({ 'X-Data-Space': id });

/** org.data_space_member as GET /v1/data-spaces/{id}/members reads it (MemberDetail). */
export interface DataSpaceMember {
  id: Uuid;
  principal_type: 'user' | 'group' | 'agent';
  principal_id: Uuid;
  role_id: Uuid;
  source: 'group_mapping' | 'manual' | 'access_request';
  valid_until: Timestamp | null;
  /** The person's, group's or agent's name (principal_name). */
  display_name: string;
  role_name: string;
  row_scope: RowScope | null;
  /** The row scope in words; null = toàn bộ. */
  row_scope_text: string | null;
  /** PROPOSAL: only for agents, e.g. "running". */
  agent_status?: 'running' | 'waiting' | 'idle' | 'paused' | 'error';
  /** PROPOSAL: "đọc" when the role is read-only. */
  access_note?: string;
}

/** One body row of PUT /v1/data-spaces/{id}/members. */
export interface MemberInput {
  principal_type: 'user' | 'group' | 'agent';
  principal_id: Uuid;
  role_id: Uuid;
  row_scope: RowScope | null;
  valid_until: string | null;
}

export type PermissionCell = 'yes' | 'approval' | 'no' | 'never';

export const CAPABILITIES = [
  { key: 'view_aggregate', label: 'Xem tổng hợp' },
  { key: 'view_detail', label: 'Xem chi tiết' },
  { key: 'view_pii', label: 'Xem PII' },
  { key: 'ask', label: 'Hỏi & phiên' },
  { key: 'run_agent', label: 'Chạy agent' },
  { key: 'create_agent', label: 'Tạo agent' },
  { key: 'approve', label: 'Duyệt thay đổi' },
  { key: 'publish', label: 'Xuất bản' },
  { key: 'admin', label: 'Quản trị' },
] as const;
export type CapabilityKey = (typeof CAPABILITIES)[number]['key'];

/** PROPOSAL: GET /v1/data-spaces/{id}/permissions — the role × capability matrix in effect. */
export interface PermissionMatrix {
  roles: {
    role_id: Uuid;
    name: string;
    member_count: number;
    member_unit: 'người' | 'agent';
    cells: Record<CapabilityKey, PermissionCell>;
  }[];
  /** Row, column and agent rules in words (access_policy of the Data Space). */
  rules: { row: string; column: string; agent: string };
}

/** One table as one role or one person reads it (TableView of POST /v1/permissions:simulate). */
export type PermissionView = Schemas['TableView'];
export type PermissionSimulation = Schemas['PermissionSimulation'];

type DataSpaceWire = DataSpace & Partial<Pick<DataSpaceSummary, 'steward_name' | 'member_count'>>;

const readSummary = (raw: DataSpaceWire): DataSpaceSummary => ({
  ...raw,
  steward_name: raw.steward_name ?? '',
  member_count: raw.member_count ?? null,
});

export const listDataSpaceSummaries = async () => {
  const page = await ok<Page<DataSpaceWire>>(
    api.GET('/v1/data-spaces', { params: { query: ALL } }),
  );
  return { ...page, items: page.items.map(readSummary) };
};

/** "Rủi ro tín dụng" → "RUI_RO_TIN_DUNG" (org.data_space.code). */
const codeOf = (name: string) =>
  name
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[đĐ]/g, 'D')
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 60) || 'DATA_SPACE';
/** A2 has no GET /v1/data-spaces/{id}: one space is read from the spaces I am a member of. */
export const getDataSpace = async (id: string) => {
  const space = (await listDataSpaceSummaries()).items.find((s) => s.id === id);
  if (!space) throw new V1Error(404, 'not_found', 'Không tìm thấy Data Space.');
  return space;
};
/**
 * DataSpaceCreate carries the name, steward and default policy; the sources follow in
 * PUT /v1/data-spaces/{id}/sources. Group mappings and the draft/onboarding choice are PROPOSAL:
 * the api opens the space as it is and maps groups through PUT /v1/admin/group-mappings.
 */
export const createDataSpace = async (body: CreateDataSpace) => {
  const created = await ok<Schemas['DataSpaceView']>(
    api.POST('/v1/data-spaces', {
      body: {
        code: codeOf(body.name),
        name: body.name,
        description: body.description ?? null,
        steward_id: body.steward_id,
        default_policy: body.default_policy,
      } satisfies Schemas['DataSpaceCreate'],
    }),
  );
  if (body.source_ids.length > 0)
    await ok(
      api.PUT('/v1/data-spaces/{id}/sources', {
        params: { path: { id: created.id } },
        headers: inSpace(created.id),
        body: {
          source_ids: body.source_ids,
        },
      }),
    );
  return created;
};
/** A stored scope as the screen holds it: each column's values as text, a lone value as a list. */
const readRowScope = (raw: unknown): RowScope | null =>
  raw && typeof raw === 'object' && !Array.isArray(raw)
    ? Object.fromEntries(
        Object.entries(raw as Record<string, unknown>).map(([column, values]) => [
          column,
          (Array.isArray(values) ? values : [values]).map(String),
        ]),
      )
    : null;

const readMember = (raw: Schemas['MemberDetail']): DataSpaceMember => {
  const scope = readRowScope(raw.row_scope);
  return {
    id: raw.id,
    principal_type: raw.principal_type as DataSpaceMember['principal_type'],
    principal_id: raw.principal_id,
    role_id: raw.role_id,
    source: raw.source as DataSpaceMember['source'],
    valid_until: raw.valid_until,
    display_name: raw.principal_name,
    role_name: raw.role_name,
    row_scope: scope,
    row_scope_text: rowScopeText(scope),
  };
};

export const listMembers = async (id: string): Promise<Page<DataSpaceMember>> => {
  const page = await ok(
    api.GET('/v1/data-spaces/{id}/members', {
      params: { path: { id }, query: ALL },
      headers: inSpace(id),
    }),
  );
  return { ...page, items: page.items.map(readMember) };
};
/** A2: PUT replaces the whole member list; the screen reads the list again for the names. */
export const putMembers = async (id: string, members: MemberInput[]) =>
  ok(
    api.PUT('/v1/data-spaces/{id}/members', {
      params: { path: { id } },
      body: { members },
      headers: inSpace(id),
    }),
  );
export const getPermissions = async (id: string) =>
  okAs<PermissionMatrix>(
    api.GET('/v1/data-spaces/{id}/permissions', { params: { path: { id } }, headers: inSpace(id) }),
  );
/**
 * The table as one or two roles or people read it: the visible, masked and hidden columns and the
 * row conditions of the active access policies (BR-137: they only ever narrow).
 */
export const simulatePermissions = (body: Schemas['PermissionSimulate']) =>
  ok(api.POST('/v1/permissions:simulate', { body }));

/**
 * The columns a row scope can name, from one table of a Data Space, read in that Data Space: each
 * with the most frequent values the latest profile found. Personal-data columns are left out —
 * their profiled values are masked, and a boundary is not drawn on them.
 */
export const listScopeColumns = async (spaceId: string, datasetId: string) => {
  const detail = await ok(
    api.GET('/v1/datasets/{id}', {
      params: { path: { id: datasetId } },
      headers: { 'X-Data-Space': spaceId },
    }),
  );
  return (detail.columns ?? [])
    .filter((c) => !c.pii_label)
    .map((c) => ({
      name: c.name,
      label: c.display_name ? `${c.display_name} (${c.name})` : c.name,
      values: (Array.isArray(c.profile?.top_values) ? c.profile.top_values : [])
        .map((t) => (t && typeof t === 'object' ? (t as { value?: unknown }).value : undefined))
        .filter((v): v is string | number => typeof v === 'string' || typeof v === 'number')
        .map(String),
    }))
    .filter((c) => c.values.length > 0);
};

/**
 * The tables of a Data Space, read in that Data Space: the compare page is opened for a space
 * that need not be the one the user works in.
 */
export const listSpaceDatasets = (spaceId: string) =>
  ok(
    api.GET('/v1/datasets', {
      params: { query: ALL },
      headers: { 'X-Data-Space': spaceId },
    }),
  );

/* ─── Yêu cầu quyền (UC-11-07) ──────────────────────────────────────── */

export type AccessCapability = 'view_aggregate' | 'view_detail' | 'ask' | 'run_agent';
export type AccessRequestStatus =
  | 'pending'
  | 'approved'
  | 'narrowed'
  | 'rejected'
  | 'expired'
  | 'cancelled';

export interface AccessRequest {
  id: Uuid;
  requester_id: Uuid;
  data_space_id: Uuid;
  capability: AccessCapability;
  reason: string;
  requested_until: string;
  status: AccessRequestStatus;
  needs_compliance: boolean;
  approver_id: Uuid | null;
  decided_at: Timestamp | null;
  decision_note: string | null;
  created_at: Timestamp;
  /** PROPOSAL: AR-0091 */
  code: string;
  /** PROPOSAL: one sentence, "Lê Minh Châu xin xem nợ xấu toàn vùng Bắc Bộ". */
  title: string;
  /** PROPOSAL: short form for the queue, "Lê Minh Châu · nợ xấu Bắc Bộ". */
  queue_label: string;
  /** PROPOSAL: what exactly is asked, in words. */
  scope_text: string;
  /** PROPOSAL: the requester's current scope in words. */
  current_scope_text: string;
  /** PROPOSAL: where it came from, "từ câu hỏi bị giới hạn phạm vi". */
  origin_text: string;
  /** PROPOSAL */
  approver_name: string;
  /** PROPOSAL */
  approver_role: string;
  /** PROPOSAL: the agent's recommendation, and the narrower scope it suggests. */
  suggestion: string | null;
  suggested_scope_text: string | null;
  /** PROPOSAL: "Nếu cấp" — before/after of what the requester would see; empty without it. */
  impact: { label: string; before: string; after: string }[];
  /** PROPOSAL: decision due date, for the queue; null when the api does not set one. */
  due_at: Timestamp | null;
}

export interface DecideAccess {
  decision: 'approve' | 'narrow' | 'reject';
  note?: string;
  /** GrantedScope when narrowing: the narrower capability actually granted. */
  granted_scope?: { capability: AccessCapability };
}

/**
 * What a request may be narrowed to, the request's own capability left out (that is a plain
 * approval). Mirrors NARROWER in backend/services/api/modules/org/admin/access.py, which answers
 * 422 for anything else.
 */
export const NARROWER_CAPABILITIES: Record<AccessCapability, AccessCapability[]> = {
  view_aggregate: ['ask'],
  ask: ['view_aggregate'],
  view_detail: ['view_aggregate', 'ask'],
  run_agent: ['view_aggregate', 'ask'],
};

/** AccessRequestView as the api sends it, with the PROPOSAL display fields when present. */
type AccessRequestWire = Schemas['AccessRequestView'] &
  Partial<Omit<AccessRequest, keyof Schemas['AccessRequestView']>>;

/** What is asked, in words, as the object of "xin …". */
export const CAPABILITY_ASKED: Record<AccessCapability, string> = {
  view_aggregate: 'xem số tổng hợp',
  view_detail: 'xem dữ liệu chi tiết',
  ask: 'hỏi đáp',
  run_agent: 'chạy agent',
};

/** AR-1A2B3C: the last characters of the id, as DSR codes are made (the table has no code). */
const accessRequestCode = (id: string) => `AR-${id.replace(/-/g, '').slice(-6).toUpperCase()}`;

interface AccessRequestNames {
  users: Map<string, string>;
  spaces: Map<string, string>;
}

const NO_NAMES: AccessRequestNames = { users: new Map(), spaces: new Map() };

/*
 * The api keeps ids, not words: the sentences the screen shows are made from the capability and
 * the names of the requester and the Data Space when the caller may read them. What only a
 * PROPOSAL field could say (the impact, the agent's suggestion, a decision deadline) is left
 * empty, and the screen leaves out what is empty.
 */
function readAccessRequest(raw: AccessRequestWire, names: AccessRequestNames): AccessRequest {
  const capability = raw.capability as AccessCapability;
  const asked = CAPABILITY_ASKED[capability] ?? raw.capability;
  const requester = names.users.get(raw.requester_id) ?? 'Người dùng';
  const space = names.spaces.get(raw.data_space_id);
  return {
    id: raw.id,
    requester_id: raw.requester_id,
    data_space_id: raw.data_space_id,
    capability,
    reason: raw.reason,
    requested_until: raw.requested_until,
    status: raw.status as AccessRequestStatus,
    needs_compliance: raw.needs_compliance,
    approver_id: raw.approver_id,
    decided_at: raw.decided_at,
    decision_note: raw.decision_note,
    created_at: raw.created_at,
    code: raw.code ?? accessRequestCode(raw.id),
    title: raw.title ?? `${requester} xin ${asked}${space ? ` · ${space}` : ''}`,
    queue_label: raw.queue_label ?? `${requester} · ${asked}`,
    scope_text:
      raw.scope_text ??
      `${asked.charAt(0).toUpperCase()}${asked.slice(1)}${space ? ` trong Data Space ${space}` : ''}`,
    current_scope_text: raw.current_scope_text ?? '',
    origin_text: raw.origin_text ?? '',
    approver_name:
      raw.approver_name ?? (raw.approver_id ? (names.users.get(raw.approver_id) ?? '') : ''),
    approver_role: raw.approver_role ?? '',
    suggestion: raw.suggestion ?? null,
    suggested_scope_text: raw.suggested_scope_text ?? null,
    impact: raw.impact ?? [],
    due_at: raw.due_at ?? null,
  };
}

/** The organisation's people by id; GET /v1/admin/users needs `admin`. */
const userNames = async () => {
  const users = await ok(api.GET('/v1/admin/users', { params: { query: ALL } }));
  return new Map(users.items.map((u) => [u.id, u.display_name]));
};

/*
 * The names behind the ids. The user list needs `admin` and a steward may not hold it, so each
 * lookup that is refused leaves its ids unnamed instead of failing the request.
 */
const accessRequestNames = async (): Promise<AccessRequestNames> => {
  const [users, spaces] = await Promise.all([
    userNames().catch(() => new Map<string, string>()),
    listDataSpaceSummaries().catch(() => null),
  ]);
  return { users, spaces: new Map(spaces?.items.map((s) => [s.id, s.name]) ?? []) };
};

/** GET /v1/access-requests: the caller's own requests, or every one for a steward. */
export const listAccessRequests = async (
  query: { status?: AccessRequestStatus; data_space_id?: string } = {},
): Promise<Page<AccessRequest>> => {
  const [page, names] = await Promise.all([
    ok<Page<AccessRequestWire>>(
      api.GET('/v1/access-requests', { params: { query: { ...ALL, ...query } } }),
    ),
    accessRequestNames(),
  ]);
  return { ...page, items: page.items.map((r) => readAccessRequest(r, names)) };
};
export const getAccessRequest = async (id: string) => {
  const [raw, names] = await Promise.all([
    ok<AccessRequestWire>(api.GET('/v1/access-requests/{id}', { params: { path: { id } } })),
    accessRequestNames(),
  ]);
  return readAccessRequest(raw, names);
};
/*
 * :decide reads the request in the Data Space of X-Data-Space, which is the switcher's and may be
 * none ("Toàn tổ chức") or another space: the request's own goes with it.
 */
export const decideAccessRequest = async (id: string, body: DecideAccess, dataSpaceId?: string) => {
  const [raw, names] = await Promise.all([
    okAs<AccessRequestWire>(
      api.POST('/v1/access-requests/{id}:decide', {
        params: { path: { id } },
        body,
        ...(dataSpaceId ? { headers: { 'X-Data-Space': dataSpaceId } } : {}),
      }),
    ),
    accessRequestNames(),
  ]);
  return readAccessRequest(raw, names);
};
/** The requester only needs the code of what was sent, so no names are looked up. */
export const requestAccess = async (body: {
  data_space_id: Uuid;
  capability: AccessCapability;
  reason: string;
  requested_until: string;
}) =>
  readAccessRequest(
    await ok<AccessRequestWire>(api.POST('/v1/access-requests', { body })),
    NO_NAMES,
  );

/* ─── Chính sách dữ liệu (UC-11-02) ─────────────────────────────────── */

export interface AccessPolicy {
  id: Uuid;
  data_space_id: Uuid;
  name: string;
  kind: 'row' | 'column' | 'mask';
  condition_expr: string | null;
  columns: string[] | null;
  version: number;
  status: 'draft' | 'active' | 'disabled';
  simulated_at: Timestamp | null;
  activated_at: Timestamp | null;
  /** PROPOSAL: POL-007 */
  code: string;
  /** PROPOSAL */
  data_space_name: string;
  /** PROPOSAL */
  owner_name: string;
  /** PROPOSAL: the rule in plain lines (Áp cho / Khi / Chỉ cho thấy dòng / Cột che). */
  rule_text: string;
  /** PROPOSAL: question used for the simulation by default. */
  sample_question: string;
  /** PROPOSAL: how many requests the policy handled this week (the policyOff confirmation). */
  applied_this_week: number;
  /** The last backtest (AccessPolicyDetail.latest_backtest); null in the list and until one ran. */
  last_backtest: Backtest | null;
}

export interface Backtest {
  window_days: number;
  affected_queries: number;
  total_queries: number;
  affected_users_text: string;
  empty_queries_text: string;
}

interface PolicySimulation {
  question: string;
  rows: { label: string; without: string; with: string }[];
}

type AccessPolicyWire = Schemas['AccessPolicyView'] &
  Partial<Omit<AccessPolicy, keyof Schemas['AccessPolicyView']>> &
  Partial<Pick<Schemas['AccessPolicyDetail'], 'latest_backtest'>>;

const DAY_MS = 86_400_000;

/** BacktestResultView in the panel's words: queries whose result the policy changes, of all. */
function readBacktest(raw: Schemas['BacktestResultView']): Backtest {
  return {
    window_days: Math.max(
      1,
      Math.round((new Date(raw.window_to).getTime() - new Date(raw.window_from).getTime()) / DAY_MS),
    ),
    affected_queries: raw.changed_result_count,
    total_queries: raw.matched_count,
    affected_users_text: raw.affected_agent_ids.length
      ? `${raw.affected_agent_ids.length} agent`
      : 'Không agent nào',
    empty_queries_text: raw.blocked_count ? `${raw.blocked_count} truy vấn bị chặn` : 'Không có',
  };
}

/** The rule in the lines the screen edits: who it applies to, the row condition, the columns. */
const ruleTextOf = (raw: Schemas['AccessPolicyView']) =>
  [
    raw.condition_expr && `Chỉ cho thấy dòng: ${raw.condition_expr}`,
    raw.columns?.length &&
      `${raw.kind === 'mask' ? 'Cột che' : 'Cột ẩn'}: ${raw.columns.join(', ')}${raw.mask_method ? ` (${raw.mask_method})` : ''}`,
  ]
    .filter(Boolean)
    .join('\n');

function readAccessPolicy(raw: AccessPolicyWire, spaces: Map<string, string>): AccessPolicy {
  return {
    id: raw.id,
    data_space_id: raw.data_space_id,
    name: raw.name,
    kind: raw.kind as AccessPolicy['kind'],
    condition_expr: raw.condition_expr,
    columns: raw.columns,
    version: raw.version,
    status: raw.status as AccessPolicy['status'],
    simulated_at: raw.simulated_at,
    activated_at: raw.activated_at,
    code: raw.code ?? `v${raw.version}`,
    data_space_name: raw.data_space_name ?? spaces.get(raw.data_space_id) ?? '',
    owner_name: raw.owner_name ?? '',
    rule_text: raw.rule_text ?? ruleTextOf(raw),
    sample_question: raw.sample_question ?? '',
    applied_this_week: raw.applied_this_week ?? 0,
    last_backtest:
      raw.last_backtest ?? (raw.latest_backtest ? readBacktest(raw.latest_backtest) : null),
  };
}

/** GET /v1/policies?type=access answers an array; the Data Space names come from the spaces. */
export const listAccessPolicies = async (): Promise<Page<AccessPolicy>> => {
  const [rows, spaces] = await Promise.all([
    okAs<AccessPolicyWire[]>(api.GET('/v1/policies', { params: { query: { type: 'access' } } })),
    listDataSpaceSummaries().catch(() => null),
  ]);
  const names = new Map<string, string>(spaces?.items.map((s) => [s.id, s.name]) ?? []);
  return { items: rows.map((r) => readAccessPolicy(r, names)), next_page_token: '' };
};

/** GET /v1/policies/{id} answers either type; a behaviour policy is not shown here. */
export const getAccessPolicy = async (id: string) => {
  const [raw, spaces] = await Promise.all([
    ok<AccessPolicyWire | Schemas['BehaviorPolicyDetail']>(
      api.GET('/v1/policies/{id}', { params: { path: { id } } }),
    ),
    listDataSpaceSummaries().catch(() => null),
  ]);
  if (raw.type !== 'access') throw new V1Error(404, 'not_found', 'Không tìm thấy chính sách.');
  const names = new Map<string, string>(spaces?.items.map((s) => [s.id, s.name]) ?? []);
  return readAccessPolicy(raw as AccessPolicyWire, names);
};
/** PROPOSAL: the screen's comparison table, which the mock sends under `display`. */
type SimulationWire = Schemas['SimulationView'] & { display?: PolicySimulation };

/*
 * PolicySimulate runs the policy as one person (as_user_id); the question stays on the screen.
 * Without the PROPOSAL table the comparison is the api's: the row condition, the hidden and the
 * masked columns, without the policy and with it.
 */
export const simulatePolicy = async (id: string, body: { user_id: Uuid; question: string }) => {
  const raw = await ok<SimulationWire>(
    api.POST('/v1/policies/{id}:simulate', {
      params: { path: { id } },
      body: {
        as_user_id: body.user_id,
      } satisfies Schemas['PolicySimulate'],
    }),
  );
  if (raw.display) return raw.display;
  const masked = Object.keys((raw.masked_columns as Record<string, unknown> | undefined) ?? {});
  return {
    question: raw.applies ? body.question : `${body.question} · ${raw.reason}`,
    rows: [
      { label: 'Dòng được thấy', without: 'tất cả', with: raw.row_condition ?? 'tất cả' },
      { label: 'Cột ẩn', without: '—', with: raw.hidden_columns?.join(', ') || '—' },
      { label: 'Cột che', without: '—', with: masked.join(', ') || '—' },
    ],
  } satisfies PolicySimulation;
};
/**
 * :backtest answers 202 and the worker replays the window's queries afterwards; the counts come
 * back as latest_backtest on the policy. The mock answers the counts at once (PROPOSAL).
 */
export const backtestPolicy = async (id: string): Promise<Backtest | null> => {
  const raw = await ok<Schemas['BacktestAccepted'] | Backtest>(
    api.POST('/v1/policies/{id}:backtest', { params: { path: { id } } }),
  );
  return 'total_queries' in raw ? raw : null;
};
/** :disable takes the reason, which the audit keeps. */
export const disablePolicy = async (id: string, reason: string) =>
  okAs<AccessPolicy>(
    api.POST('/v1/policies/{id}:disable', {
      params: { path: { id } },
      body: {
        reason,
      } satisfies Schemas['PolicyDisable'],
    }),
  );

/* ─── Kiểm toán (UC-11-03) ──────────────────────────────────────────── */

export interface AuditEntry {
  id: Uuid;
  at: Timestamp;
  /** Who, in words — "Agent Đối soát (của Đỗ Hải Yến)"; the actor's kind and id without a name. */
  actor_label: string;
  actor_kind: 'person' | 'agent' | 'client' | 'system';
  /** PROPOSAL: what happened, in words; the action code otherwise. */
  action_text: string;
  /** PROPOSAL: the data touched, in words; the object's kind and id otherwise. */
  data_text: string;
  /** The service that wrote the row (api, worker-agent, …). */
  service: string;
  outcome: 'ok' | 'blocked' | 'error';
}

export interface AuditQuery {
  /** An action code, matched exactly (audit.audit_log.action). */
  action?: string;
  outcome?: 'ok' | 'denied' | 'error';
  /** YYYY-MM-DD, a local day. */
  date?: string;
  page_token?: string;
}

/** PROPOSAL: the row in words, which the mock sends under `display`. */
export type AuditWire = Schemas['AuditLogView'] & { display?: AuditEntry };

const ACTOR_KIND: Record<string, AuditEntry['actor_kind']> = {
  user: 'person',
  agent: 'agent',
  client: 'client',
  system: 'system',
};

const ACTOR_WORD: Record<AuditEntry['actor_kind'], string> = {
  person: 'Người dùng',
  agent: 'Agent',
  client: 'Client',
  system: 'Hệ thống',
};

/* The api's own audit actions; any other is shown by its code. */
const ACTION_TEXT: Record<string, string> = {
  'audit.searched': 'Tra cứu nhật ký kiểm toán',
  'audit.verified': 'Kiểm chuỗi băm của nhật ký',
  'audit.export_requested': 'Yêu cầu xuất nhật ký',
};

const shortId = (id: string | null) => (id ? id.slice(0, 8) : '');

function readAuditEntry(raw: AuditWire, names: Map<string, string>): AuditEntry {
  if (raw.display) return raw.display;
  const kind = ACTOR_KIND[raw.actor_kind] ?? 'system';
  const who =
    (raw.actor_id ? names.get(raw.actor_id) : undefined) ??
    [ACTOR_WORD[kind], shortId(raw.actor_id)].filter(Boolean).join(' ');
  // An agent or a client acting for a person names that person (BR-48).
  const forWhom = raw.on_behalf_of
    ? ` (của ${names.get(raw.on_behalf_of) ?? shortId(raw.on_behalf_of)})`
    : '';
  return {
    id: raw.id,
    at: raw.occurred_at,
    actor_label: who + forWhom,
    actor_kind: kind,
    action_text: ACTION_TEXT[raw.action] ?? raw.action,
    data_text: raw.object_kind
      ? [raw.object_kind, shortId(raw.object_id)].filter(Boolean).join(' · ')
      : '—',
    service: raw.service,
    outcome: raw.outcome === 'denied' ? 'blocked' : raw.outcome === 'error' ? 'error' : 'ok',
  };
}

/** The screen's filters as GET /v1/audit's; a day is [local midnight, next midnight). */
function auditFilters(query: AuditQuery): Schemas['AuditFilters'] {
  const filters: Schemas['AuditFilters'] = {};
  if (query.action) filters.action = query.action;
  if (query.outcome) filters.outcome = query.outcome;
  if (query.date) {
    const [y, m, d] = query.date.split('-').map(Number);
    filters.from = new Date(y, m - 1, d).toISOString();
    filters.to = new Date(y, m - 1, d + 1).toISOString();
  }
  return filters;
}

/*
 * GET /v1/audit, newest first. The log keeps ids, not names: they are joined from the user list
 * when the caller may read it, and an auditor without that right sees the actor's kind and id.
 */
export const searchAudit = async (query: AuditQuery): Promise<Page<AuditEntry>> => {
  const [page, users] = await Promise.all([
    ok<Page<AuditWire>>(
      api.GET('/v1/audit', {
        params: {
          query: {
            page_size: 50,
            page_token: query.page_token,
            ...auditFilters(query),
          },
        },
      }),
    ),
    listAdminUsers().catch(() => null),
  ]);
  const names = new Map<string, string>(users?.items.map((u) => [u.id, u.display_name]) ?? []);
  return { ...page, items: page.items.map((e) => readAuditEntry(e, names)) };
};

/**
 * 202: a worker writes and signs the file (poll GET /v1/audit/exports/{id}). Over 100.000 rows
 * taken by one person in a day the job waits in waiting_approval for a second person with
 * audit_read, who approves it in Cần bạn (BR-138).
 */
export const exportAudit = (query: AuditQuery) =>
  ok<Schemas['ExportAccepted']>(
    api.POST('/v1/audit:export', {
      body: {
        filters: auditFilters(query),
        format: 'csv',
      } satisfies Schemas['ExportIn'],
    }),
  );

/* openapi-typescript writes a free jsonb object as Record<string, never>; it holds anything. */
export type AuditExport = Omit<Schemas['ExportJobView'], 'filters'> & {
  filters: Record<string, unknown>;
};

export const getAuditExport = (id: string) =>
  ok<AuditExport>(api.GET('/v1/audit/exports/{id}', { params: { path: { id } } }));

/** Recomputes the hash chain; a break is also recorded as a security event. */
export const verifyAuditChain = () =>
  ok<Schemas['VerifyResult']>(
    api.POST('/v1/audit:verify', { body: {} satisfies Schemas['VerifyIn'] }),
  );

export type SecurityEvent = Omit<Schemas['SecurityEventView'], 'detail'> & {
  detail: Record<string, unknown>;
};

export const listSecurityEvents = (query: {
  severity?: 'low' | 'medium' | 'high' | 'critical';
  status?: 'open' | 'investigating' | 'closed';
  page_token?: string;
}) =>
  ok<Page<SecurityEvent>>(
    api.GET('/v1/security-events', { params: { query: { page_size: 50, ...query } } }),
  );

/* ─── Yêu cầu của chủ thể dữ liệu (UC-11-06) ───────────────────────── */

export type DsrKind = 'access' | 'copy' | 'delete' | 'correct';
export type DsrStatus = 'open' | 'searching' | 'review' | 'executing' | 'done' | 'rejected';

export interface DsrItem {
  id: Uuid;
  location_kind:
    | 'chunk'
    | 'tile_snapshot'
    | 'derived_table'
    | 'conversation'
    | 'agent_memory'
    | 'source_system'
    | 'audit';
  location_ref: string;
  proposed_action: 'delete' | 'provide' | 'retain';
  legal_basis: string | null;
  decision: 'pending' | 'done' | 'retained';
  /** Where, in words: the kind of place and its reference. */
  location_label: string;
  /** The kind of place, in words. */
  kind_label: string;
}

export interface DsrRequest {
  id: Uuid;
  request_kind: DsrKind;
  received_at: Timestamp;
  due_at: Timestamp;
  status: DsrStatus;
  handler_id: Uuid;
  closed_at: Timestamp | null;
  /** The request's short reference, from its id (the table has no code). */
  code: string;
  /** The kind of request, in words. */
  title: string;
  /** The handler's name from the organisation's users; their id when they are gone. */
  handler_name: string;
  items: DsrItem[];
}

export type CreateDsr = Schemas['DsrCreate'];

/** What happens to one found item when the request is executed (DsrDecision). */
export type DsrDecision = Schemas['DsrDecision'];

const DSR_TITLE: Record<DsrKind, string> = {
  access: 'Yêu cầu truy cập dữ liệu',
  copy: 'Yêu cầu nhận bản sao dữ liệu',
  delete: 'Yêu cầu xoá dữ liệu',
  correct: 'Yêu cầu sửa dữ liệu',
};

const LOCATION_KIND: Record<DsrItem['location_kind'], string> = {
  chunk: 'Đoạn tài liệu',
  tile_snapshot: 'Kết quả trong phiên',
  derived_table: 'Bảng dẫn xuất',
  conversation: 'Hội thoại',
  agent_memory: 'Bộ nhớ agent',
  source_system: 'Hệ thống nguồn',
  audit: 'Nhật ký kiểm toán',
};

/** DSR-1A2B3C: the last characters of the id, which the list, the toast and the export share. */
const dsrCode = (id: string) => `DSR-${id.replace(/-/g, '').slice(-6).toUpperCase()}`;

const readDsrItem = (raw: Schemas['DsrItemView']): DsrItem => {
  const kind = raw.location_kind as DsrItem['location_kind'];
  const label = LOCATION_KIND[kind] ?? raw.location_kind;
  return {
    ...raw,
    location_kind: kind,
    proposed_action: raw.proposed_action as DsrItem['proposed_action'],
    decision: raw.decision as DsrItem['decision'],
    location_label: `${label} · ${raw.location_ref}`,
    kind_label: label,
  };
};

const readDsr = (
  raw: Schemas['DsrSummary'] | Schemas['DsrRequestView'],
  names: Map<string, string>,
): DsrRequest => ({
  id: raw.id,
  request_kind: raw.request_kind as DsrKind,
  received_at: raw.received_at,
  due_at: raw.due_at,
  status: raw.status as DsrStatus,
  handler_id: raw.handler_id,
  closed_at: raw.closed_at,
  code: dsrCode(raw.id),
  title: DSR_TITLE[raw.request_kind as DsrKind] ?? raw.request_kind,
  handler_name: names.get(raw.handler_id) ?? raw.handler_id,
  items: 'items' in raw ? (raw.items ?? []).map(readDsrItem) : [],
});

/*
 * /v1/dsr is compliance's and the user list admin's: separation of duties means a compliance
 * officer usually lacks the second, so a refused list leaves the handler named by id.
 */
export const listDsr = async (): Promise<Page<DsrRequest>> => {
  const [page, names] = await Promise.all([
    ok(api.GET('/v1/dsr', { params: { query: ALL } })),
    userNames().catch(() => new Map<string, string>()),
  ]);
  return { ...page, items: page.items.map((d) => readDsr(d, names)) };
};

export const getDsr = async (id: string): Promise<DsrRequest> => {
  const [raw, names] = await Promise.all([
    ok(api.GET('/v1/dsr/{id}', { params: { path: { id } } })),
    userNames().catch(() => new Map<string, string>()),
  ]);
  return readDsr(raw, names);
};

/** 202: the search for the subject's data starts; the request is read again by its id. */
export const createDsr = async (body: CreateDsr) => {
  const accepted = await ok(api.POST('/v1/dsr', { body }));
  return { id: accepted.id, code: dsrCode(accepted.id) };
};

/** Every pending item needs its decision; retaining one needs its legal basis (UC-11-06 3a). */
export const executeDsr = async (id: string, items: DsrDecision[]) =>
  ok(api.POST('/v1/dsr/{id}:execute', { params: { path: { id } }, body: { items } }));
