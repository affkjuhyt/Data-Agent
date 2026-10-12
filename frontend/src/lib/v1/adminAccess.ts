import { pathId, v1Get, v1Send, type Page } from './client';
import type { AppUser, DataSpace } from './types';

/*
 * Quản trị · người, quyền và tuân thủ (P11: UC-11-01, -02, -03, -06, -07, -08), with the paths
 * of "Đặc tả API" A2: org (admin users, Data Space, access requests), governance (policies,
 * DSR) and audit. Columns follow D4 tables org.app_user, org.role, org.data_space_member,
 * org.access_request, governance.access_policy, governance.dsr_request/dsr_item.
 *
 * Fields marked PROPOSAL are not in the spec: the screens need a display text or a computed
 * figure the tables do not hold, and the backend is asked to add them. Endpoints marked
 * PROPOSAL are missing from A2 (the spec has PUT members but no GET, :decide but no list, …).
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
  row_scope: string | null;
  valid_until: string | null;
}

export const listAdminUsers = () => v1Get<Page<AdminUser>>('/admin/users', ALL);
export const listRoles = () => v1Get<Page<Role>>('/admin/roles', ALL);
export const listGroups = () => v1Get<Page<UserGroup>>('/admin/groups', ALL);
/** PROPOSAL: re-read people and groups from the identity source now (202). */
export const syncDirectory = () => v1Send<{ status: string }>('POST', '/admin/directory:sync');
export const inviteUser = (body: Invitation) => v1Send<AdminUser>('POST', '/admin/invitations', body);

/* ─── Data Space, thành viên và quyền (UC-11-08) ─────────────────────── */

export interface DataSpaceSummary extends DataSpace {
  /** PROPOSAL: steward display name (steward_id is the column). */
  steward_name: string;
  /** PROPOSAL: members counted over people, groups' people and agents. */
  member_count: number;
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

/** org.data_space_member, with display fields. GET is a PROPOSAL (A2 has PUT only). */
export interface DataSpaceMember {
  id: Uuid;
  principal_type: 'user' | 'group' | 'agent';
  principal_id: Uuid;
  role_id: Uuid;
  source: 'group_mapping' | 'manual' | 'access_request';
  valid_until: Timestamp | null;
  /** PROPOSAL */
  display_name: string;
  /** PROPOSAL */
  role_name: string;
  /** PROPOSAL: row scope in words; null = toàn bộ. */
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
  /** PROPOSAL: row_scope is jsonb in D4; the screen offers named scopes. */
  row_scope: string | null;
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

/** POST /v1/permissions:simulate — one table seen under two roles. */
export interface PermissionView {
  principal_name: string;
  role_name: string;
  /** e.g. "Chỉ thấy vùng Bắc · PII bị che" */
  scope_text: string;
  columns: string[];
  rows: string[][];
  /** Which cells are masked or aggregated (muted). Same shape as rows. */
  muted: boolean[][];
  actions: { label: string; allowed: boolean; reason?: string; request_capability?: string }[];
}

export interface PermissionSimulation {
  table: string;
  views: PermissionView[];
}

export const listDataSpaceSummaries = () => v1Get<Page<DataSpaceSummary>>('/data-spaces', ALL);
export const getDataSpace = async (id: string) =>
  v1Get<DataSpaceSummary>(`/data-spaces/${pathId(id)}`);
export const createDataSpace = (body: CreateDataSpace) =>
  v1Send<DataSpaceSummary>('POST', '/data-spaces', body);
export const listMembers = async (id: string) =>
  v1Get<Page<DataSpaceMember>>(`/data-spaces/${pathId(id)}/members`, ALL);
/** A2: PUT replaces the whole member list. */
export const putMembers = async (id: string, members: MemberInput[]) =>
  v1Send<Page<DataSpaceMember>>('PUT', `/data-spaces/${pathId(id)}/members`, { items: members });
export const getPermissions = async (id: string) =>
  v1Get<PermissionMatrix>(`/data-spaces/${pathId(id)}/permissions`);
export const simulatePermissions = (body: { data_space_id: Uuid; table?: string }) =>
  v1Send<PermissionSimulation>('POST', '/permissions:simulate', body);

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
  /** PROPOSAL: "Nếu cấp" — before/after of what the requester would see. */
  impact: { label: string; before: string; after: string }[];
  /** PROPOSAL: decision due date, for the queue. */
  due_at: Timestamp;
}

export interface DecideAccess {
  decision: 'approve' | 'narrow' | 'reject';
  note?: string;
  /** granted_scope when narrowing. */
  granted_scope?: string;
}

/** PROPOSAL: GET /v1/access-requests (A2 has create and :decide only). */
export const listAccessRequests = (query: { status?: AccessRequestStatus; data_space_id?: string } = {}) =>
  v1Get<Page<AccessRequest>>('/access-requests', { ...ALL, ...query });
/** PROPOSAL: GET /v1/access-requests/{id} */
export const getAccessRequest = async (id: string) =>
  v1Get<AccessRequest>(`/access-requests/${pathId(id)}`);
export const decideAccessRequest = async (id: string, body: DecideAccess) =>
  v1Send<AccessRequest>('POST', `/access-requests/${pathId(id)}:decide`, body);
export const requestAccess = (body: {
  data_space_id: Uuid;
  capability: AccessCapability;
  reason: string;
  requested_until: string;
}) => v1Send<AccessRequest>('POST', '/access-requests', body);

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
  /** PROPOSAL: the last backtest, so the side panel shows without re-running it. */
  last_backtest: Backtest | null;
}

export interface Backtest {
  window_days: number;
  affected_queries: number;
  total_queries: number;
  affected_users_text: string;
  empty_queries_text: string;
}

export interface PolicySimulation {
  question: string;
  rows: { label: string; without: string; with: string }[];
}

/*
 * A2 serves data and behaviour policies from one GET /v1/policies. PROPOSAL: `family=access`
 * selects governance.access_policy so the two shapes do not mix in one list.
 */
export const listAccessPolicies = () =>
  v1Get<Page<AccessPolicy>>('/policies', { ...ALL, family: 'access' });
/** PROPOSAL: GET /v1/policies/{id} */
export const getAccessPolicy = async (id: string) =>
  v1Get<AccessPolicy>(`/policies/${pathId(id)}`);
/** PROPOSAL: PATCH saves the rule as a new version (draft until simulated). */
export const updateAccessPolicy = async (id: string, body: { rule_text: string }) =>
  v1Send<AccessPolicy>('PATCH', `/policies/${pathId(id)}`, body);
export const simulatePolicy = async (id: string, body: { user_id: Uuid; question: string }) =>
  v1Send<PolicySimulation>('POST', `/policies/${pathId(id)}:simulate`, body);
export const backtestPolicy = async (id: string) =>
  v1Send<Backtest>('POST', `/policies/${pathId(id)}:backtest`);
/** PROPOSAL: :disable (A2 has :activate only). */
export const disablePolicy = async (id: string) =>
  v1Send<AccessPolicy>('POST', `/policies/${pathId(id)}:disable`);

/* ─── Kiểm toán (UC-11-03) ──────────────────────────────────────────── */

export type AuditChannel = 'web' | 'agent' | 'teams' | 'mcp' | 'api';

export interface AuditEntry {
  id: Uuid;
  at: Timestamp;
  /** PROPOSAL: who, in words — "Agent Đối soát (của Đỗ Hải Yến)", "Client “Copilot nội bộ”". */
  actor_label: string;
  actor_kind: 'person' | 'agent' | 'client';
  /** PROPOSAL: what happened, in words. */
  action_text: string;
  /** PROPOSAL: the data touched, in words. */
  data_text: string;
  channel: AuditChannel;
  outcome: 'ok' | 'blocked';
}

export interface AuditQuery {
  q?: string;
  actor_kind?: AuditEntry['actor_kind'];
  channel?: AuditChannel;
  /** YYYY-MM-DD */
  date?: string;
  page_token?: string;
}

export const searchAudit = (query: AuditQuery) =>
  v1Get<Page<AuditEntry>>('/audit', { page_size: 50, ...query });
/** 202: a signed CSV is prepared; over 100.000 rows it needs approval first. */
export const exportAudit = (query: AuditQuery) =>
  v1Send<{ status: string; needs_approval: boolean }>('POST', '/audit:export', query);

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
  /** PROPOSAL: where, in words. */
  location_label: string;
  /** PROPOSAL: kind, in words. */
  kind_label: string;
  /** PROPOSAL: number of entries found there. */
  entry_count: number;
}

export interface DsrRequest {
  id: Uuid;
  request_kind: DsrKind;
  received_at: Timestamp;
  due_at: Timestamp;
  status: DsrStatus;
  handler_id: Uuid;
  closed_at: Timestamp | null;
  /** PROPOSAL: DSR-014 */
  code: string;
  /** PROPOSAL */
  title: string;
  /** PROPOSAL: "kênh pháp chế" */
  channel_label: string;
  /** PROPOSAL: "Nghị định 13" */
  legal_ref: string;
  /** PROPOSAL */
  handler_name: string;
  items: DsrItem[];
}

export interface CreateDsr {
  request_kind: DsrKind;
  /** Normalised and hashed by the server (subject_hash); never stored as is. */
  subject_identifier: string;
  /** PROPOSAL */
  channel: string;
  due_at: string;
}

/** PROPOSAL: GET /v1/dsr (A2 has create, get and :execute). */
export const listDsr = () => v1Get<Page<DsrRequest>>('/dsr', ALL);
export const getDsr = async (id: string) => v1Get<DsrRequest>(`/dsr/${pathId(id)}`);
export const createDsr = (body: CreateDsr) => v1Send<DsrRequest>('POST', '/dsr', body);
export const executeDsr = async (id: string, itemIds: string[]) =>
  v1Send<DsrRequest>('POST', `/dsr/${pathId(id)}:execute`, { item_ids: itemIds });
/** PROPOSAL: :reject with a reason (status rejected). */
export const rejectDsr = async (id: string, reason: string) =>
  v1Send<DsrRequest>('POST', `/dsr/${pathId(id)}:reject`, { reason });
