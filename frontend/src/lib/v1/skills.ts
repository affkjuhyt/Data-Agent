import { pathId, v1Get, v1Send, type Page } from './client';

/*
 * Skills and tools, the library side of P03 (UC-03-01, UC-03-05…07, UC-13-02): the skill
 * library, the versions of one skill with its review and deprecation, the agent's skill
 * suggestions and sharing, the tool registry by permission level and the MCP connections.
 * The authoring side (definition, tests, run) lives in skillAuthoring.ts and reads these
 * types. Fields marked PROPOSAL are what the screens need and the spec does not fix yet.
 */
export type SkillStatus = 'draft' | 'testing' | 'pending_review' | 'certified' | 'deprecated';
export type SkillScope = 'data_space' | 'organization' | 'domain_pack';

interface PersonRef {
  id: string;
  name: string;
}

/** GET /v1/skills — one skill at its newest version. */
export interface Skill {
  id: string;
  name: string;
  description: string;
  version: number;
  status: SkillStatus;
  /** PROPOSAL: the status in words with its detail: "Nháp · đang kiểm định", "Ngừng · thay bằng v3". */
  status_text: string;
  scope: SkillScope;
  /** PROPOSAL: "Toàn tổ chức", "Rủi ro tín dụng" — the Data Space(s) or the pack it belongs to. */
  scope_label: string;
  owner: PersonRef;
  created_by: PersonRef;
  step_count: number;
  /** PROPOSAL: usage over the last 30 days; null when it never ran. */
  agent_count: number;
  runs_30d: number | null;
  pass_rate_pct: number | null;
  /** PROPOSAL: the version waiting for review, if any ("Đang chờ duyệt"). */
  pending_version_id: string | null;
}

export const listSkills = (query: { scope?: SkillScope; status?: SkillStatus } = {}) =>
  v1Get<Page<Skill>>('/skills', { ...query, page_size: 500 });

export const getSkill = async (id: string) => v1Get<Skill>(`/skills/${pathId(id)}`);

/**
 * PROPOSAL: POST /v1/skills — an empty draft owned by the caller, opened in the editor
 * (skillAuthoring.ts fills its definition).
 */
export const createSkill = (body: { name: string }) => v1Send<Skill>('POST', '/skills', body);

/* ─── Phiên bản kỹ năng · Duyệt & ngừng (UC-03-05, UC-03-06) ───────────── */

export type SkillVersionStatus =
  | 'draft'
  | 'pending_review'
  | 'changes_requested'
  | 'active'
  | 'deprecated';

/** GET /v1/skills/{id}/versions — newest first. */
export interface SkillVersion {
  id: string;
  version: number;
  /** PROPOSAL: what changed against the previous version, one line. */
  change_text: string;
  eval_passed: number;
  eval_total: number;
  status: SkillVersionStatus;
  /** PROPOSAL: "Chờ duyệt · Đỗ Hải Yến", "Đang dùng", "Đã ngừng 03/2026". */
  status_text: string;
}

export const listSkillVersions = async (skillId: string) =>
  v1Get<Page<SkillVersion>>(`/skills/${pathId(skillId)}/versions`);

interface Option {
  value: string;
  label: string;
}

/**
 * PROPOSAL: GET /v1/skill-versions/{id}/review — what the reviewer needs to decide: the
 * pre-approval checks, the scopes they may publish to, and who runs the version it replaces.
 */
export interface SkillVersionReview {
  version_id: string;
  version: number;
  /** The version in use that the new one replaces; null for a first version. */
  current: { id: string; version: number; agent_count: number; session_count: number } | null;
  checks: { text: string; ok: boolean }[];
  scope_options: Option[];
  scope_value: string;
  /** When the current version stops: "30 ngày sau khi phát hành v3 · 07/11". */
  deprecate_options: Option[];
  /** Where its users move: "v3 · tham số giữ nguyên". */
  replacement_options: Option[];
  consumers: { name: string; kind: 'agent' | 'session'; action_text: string }[];
  /** BR: the reviewer is not the author; org-wide publishing needs an administrator. */
  can_approve: { allowed: boolean; reason: string | null };
}

export const getSkillVersionReview = async (versionId: string) =>
  v1Get<SkillVersionReview>(`/skill-versions/${pathId(versionId)}/review`);

/** :approve — certified and published at the chosen scope; the previous version is scheduled to stop. */
export const approveSkillVersion = async (
  versionId: string,
  body: { scope: string; deprecate_previous: string; replacement: string },
) => v1Send<SkillVersion>('POST', `/skill-versions/${pathId(versionId)}:approve`, body);

/** :request-changes — back to the author with the reviewer's note. */
export const requestSkillChanges = async (versionId: string, comment: string) =>
  v1Send<SkillVersion>('POST', `/skill-versions/${pathId(versionId)}:request-changes`, {
    comment,
  });

/* ─── Đề xuất từ agent & chia sẻ (UC-03-07) ───────────────────────────── */

/** PROPOSAL: GET /v1/skill-suggestions — step chains the agent saw repeated. */
export interface SkillSuggestion {
  id: string;
  title: string;
  /** "Lặp lại 14 lần trong 6 phiên của 3 người, 30 ngày qua". */
  repeat_text: string;
  /** Short form for a card: "14 lần · 3 người". */
  count_text: string;
  steps: string[];
  used_by: string[];
  /** The sessions the chain was seen in; the first one seeds "Tạo kỹ năng". */
  session_ids: string[];
  occurrences: { at: string; where: string }[];
}

export const listSkillSuggestions = () =>
  v1Get<Page<SkillSuggestion>>('/skill-suggestions', { page_size: 500 });

/** :dismiss — the agent does not suggest the same chain again for 90 days. */
export const dismissSkillSuggestion = async (id: string) =>
  v1Send<void>('POST', `/skill-suggestions/${pathId(id)}:dismiss`, {});

/**
 * PROPOSAL: :share — a certified skill to another Data Space or the whole organisation
 * (`data_space_id` null). The receiver runs it with its own data permissions; org-wide sharing
 * waits for an administrator.
 */
export const shareSkill = async (id: string, dataSpaceId: string | null) =>
  v1Send<{ status: 'shared' | 'pending_admin' }>('POST', `/skills/${pathId(id)}:share`, {
    data_space_id: dataSpaceId,
  });

/* ─── Công cụ & kết nối MCP (UC-13-02) ────────────────────────────────── */

export type ToolLevel = 'read' | 'write_derived' | 'external' | 'system';

/** PROPOSAL: GET /v1/tools — the actions an agent may call, by permission level. */
export interface Tool {
  id: string;
  name: string;
  level: ToolLevel;
  description: string;
  /** null = built in; otherwise the MCP server that provides it. */
  mcp_server_id: string | null;
  agent_count: number;
  calls_30d: number;
}

export const listTools = () => v1Get<Page<Tool>>('/tools', { page_size: 500 });

export const registerTool = (body: {
  name: string;
  /** 'builtin' or an MCP server id. */
  provider: string;
  level: ToolLevel;
  description: string;
  agent_ids: string[];
}) => v1Send<Tool>('POST', '/tools', body);

type McpStatus = 'active' | 'auth_error' | 'pending_pii_review';

/** PROPOSAL: GET /v1/mcp-servers. */
export interface McpServer {
  id: string;
  name: string;
  tools: string[];
  auth: 'oauth2' | 'sso' | 'api_key';
  status: McpStatus;
}

export const listMcpServers = () => v1Get<Page<McpServer>>('/mcp-servers', { page_size: 500 });
