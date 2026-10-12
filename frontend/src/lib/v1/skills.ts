import { listUsers } from './api';
import { pathId, v1Get, v1Send, type Page } from './client';
import type { components } from './schema';

type Schemas = components['schemas'];

/*
 * Skills and tools, the library side of P03 (UC-03-01, UC-03-05…07, UC-13-02): the skill
 * library, the versions of one skill with its review and deprecation, the agent's skill
 * suggestions and sharing, the tool registry by permission level and the MCP connections.
 * The authoring side (definition, tests, run) lives in skillAuthoring.ts and reads these
 * types. Fields marked PROPOSAL are what the screens need and the spec does not fix yet. The
 * api's rows are read below; the screen's richer row travels under `display` when the mock sends
 * it and is said from the api's row otherwise.
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

type SkillWire = (Schemas['SkillView'] | Schemas['SkillDetail']) & { display?: Skill };

/** skill.scope personal, data_space, org; a pack's skill is the domain pack's. */
const scopeOf = (raw: Schemas['SkillView']): SkillScope =>
  raw.origin === 'pack' ? 'domain_pack' : raw.scope === 'org' ? 'organization' : 'data_space';

const SCOPE_LABEL: Record<string, string> = {
  personal: 'Cá nhân',
  data_space: 'Data Space',
  org: 'Toàn tổ chức',
};

/*
 * The skill's status from its versions when the detail has them (a version waiting for review,
 * none published yet), else from the row: replaced is deprecated, no version a draft.
 */
function statusOf(raw: SkillWire): { status: SkillStatus; pending: string | null } {
  const versions = 'versions' in raw ? (raw.versions ?? []) : [];
  const pending = versions.find((v) => v.status === 'pending_review') ?? null;
  if (raw.replaced_by_skill_id) return { status: 'deprecated', pending: null };
  if (pending) return { status: 'pending_review', pending: pending.id };
  if (versions.some((v) => v.status === 'testing')) return { status: 'testing', pending: null };
  if (raw.current_version === null) return { status: 'draft', pending: null };
  return { status: 'certified', pending: null };
}

const STATUS_TEXT: Record<SkillStatus, string> = {
  draft: 'Nháp',
  testing: 'Đang kiểm định',
  pending_review: 'Chờ duyệt',
  certified: 'Đã chứng nhận',
  deprecated: 'Đã ngừng',
};

function readSkill(raw: SkillWire, names: Map<string, string>): Skill {
  if (raw.display) return raw.display;
  const { status, pending } = statusOf(raw);
  const owner = { id: raw.owner_id, name: names.get(raw.owner_id) ?? '' };
  const steps = 'published_version' in raw ? raw.published_version?.steps : null;
  return {
    id: raw.id,
    name: raw.name,
    description: raw.description ?? '',
    version: raw.current_version ?? 0,
    status,
    status_text: STATUS_TEXT[status],
    scope: scopeOf(raw),
    scope_label: SCOPE_LABEL[raw.scope] ?? raw.scope,
    owner,
    created_by: owner,
    step_count: Array.isArray(steps) ? steps.length : 0,
    agent_count: 0,
    runs_30d: raw.run_count_30d || null,
    pass_rate_pct: null,
    pending_version_id: pending,
  };
}

/** The owners' names come from the people the viewer may see; a failure leaves them empty. */
const userNames = async () =>
  new Map<string, string>(
    (await listUsers().catch(() => null))?.items.map((u) => [u.id, u.display_name]) ?? [],
  );

/** A2 filters by scope and category; `status` is the screen's, applied to the page read. */
export const listSkills = async (query: { scope?: SkillScope; status?: SkillStatus } = {}) => {
  const [page, names] = await Promise.all([
    v1Get<Page<SkillWire>>('/skills', {
      scope:
        query.scope === 'organization'
          ? 'org'
          : query.scope === 'data_space'
            ? 'data_space'
            : undefined,
      page_size: 500,
    }),
    userNames(),
  ]);
  const items = page.items.map((s) => readSkill(s, names));
  return { ...page, items: query.status ? items.filter((s) => s.status === query.status) : items };
};

export const getSkill = async (id: string) => {
  const [raw, names] = await Promise.all([v1Get<SkillWire>(`/skills/${pathId(id)}`), userNames()]);
  return readSkill(raw, names);
};

/** "Phân rã biến động" → "phan_ra_bien_dong" (skill.code). */
const codeOf = (name: string) =>
  name
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[đĐ]/g, 'd')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 60) || 'skill';

/**
 * POST /v1/skills (SkillCreate) — an empty draft owned by the caller, opened in the editor
 * (skillAuthoring.ts fills its definition). It starts personal and custom; sharing widens it.
 */
export const createSkill = async (body: { name: string }) =>
  readSkill(
    await v1Send<SkillWire>('POST', '/skills', {
      code: codeOf(body.name),
      name: body.name,
      category: 'custom',
      origin: 'manual',
      scope: 'personal',
    } satisfies Schemas['SkillCreate']),
    new Map(),
  );

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

/** skill_version.status in the screen's words: published is in use, retired stopped. */
const VERSION_STATUS: Record<string, SkillVersionStatus> = {
  draft: 'draft',
  testing: 'draft',
  pending_review: 'pending_review',
  needs_review: 'changes_requested',
  published: 'active',
  retired: 'deprecated',
};

const VERSION_TEXT: Record<SkillVersionStatus, string> = {
  draft: 'Nháp',
  pending_review: 'Chờ duyệt',
  changes_requested: 'Cần sửa',
  active: 'Đang dùng',
  deprecated: 'Đã ngừng',
};

/**
 * A2 has no GET /v1/skills/{id}/versions: the skill's detail carries its versions, newest first.
 * The change note and the eval counts are PROPOSAL.
 */
export const listSkillVersions = async (skillId: string): Promise<Page<SkillVersion>> => {
  const raw = await v1Get<Schemas['SkillDetail'] & { display_versions?: SkillVersion[] }>(
    `/skills/${pathId(skillId)}`,
  );
  if (raw.display_versions) return { items: raw.display_versions, next_page_token: '' };
  const items = [...(raw.versions ?? [])]
    .sort((a, b) => b.version - a.version)
    .map((v): SkillVersion => {
      const status = VERSION_STATUS[v.status] ?? 'draft';
      return {
        id: v.id,
        version: v.version,
        change_text: '',
        eval_passed: 0,
        eval_total: 0,
        status,
        status_text: VERSION_TEXT[status],
      };
    });
  return { items, next_page_token: '' };
};

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

type SuggestionWire = Schemas['services__api__modules__skill__schemas__SuggestionView'] & {
  display?: SkillSuggestion;
};

/**
 * `pattern` is the generalised chain (BR-98): step kinds and parameter names, no values. Each
 * step reads as its name or kind; the steps may sit at the top or under `steps`.
 */
function stepsOf(pattern: unknown): string[] {
  const list = Array.isArray(pattern)
    ? pattern
    : typeof pattern === 'object' &&
        pattern !== null &&
        Array.isArray((pattern as { steps?: unknown }).steps)
      ? (pattern as { steps: unknown[] }).steps
      : [];
  return list.flatMap((s) => {
    if (typeof s === 'string') return [s];
    if (typeof s !== 'object' || s === null) return [];
    const { name, kind } = s as { name?: unknown; kind?: unknown };
    return typeof name === 'string' ? [name] : typeof kind === 'string' ? [kind] : [];
  });
}

function readSuggestion(raw: SuggestionWire): SkillSuggestion {
  if (raw.display) return raw.display;
  const steps = stepsOf(raw.pattern);
  const sessions = raw.session_ids.length;
  return {
    id: raw.id,
    title: steps.length ? steps.join(' → ') : 'Chuỗi bước lặp lại',
    repeat_text: `Lặp lại trong ${sessions} phiên của ${raw.user_count} người`,
    count_text: `${sessions} phiên · ${raw.user_count} người`,
    steps,
    used_by: [],
    session_ids: raw.session_ids,
    occurrences: [],
  };
}

export const listSkillSuggestions = async () => {
  const page = await v1Get<Page<SuggestionWire>>('/skill-suggestions', { page_size: 500 });
  return { ...page, items: page.items.map(readSuggestion) };
};

/** :dismiss — the agent does not suggest the same chain again for 90 days. */
export const dismissSkillSuggestion = async (id: string) =>
  v1Send<void>('POST', `/skill-suggestions/${pathId(id)}:dismiss`, {});

/**
 * :share — a certified skill to its Data Space or the whole organisation (`data_space_id` null).
 * A2's ShareRequest names the scope, not another Data Space: sharing to a space is the skill's
 * own. The receiver runs it with its own data permissions; org-wide sharing waits for an
 * administrator, which the skill's scope not yet being org says.
 */
export const shareSkill = async (id: string, dataSpaceId: string | null) => {
  const scope = dataSpaceId === null ? 'org' : 'data_space';
  const shared = await v1Send<Schemas['SkillView']>('POST', `/skills/${pathId(id)}:share`, {
    scope,
  } satisfies Schemas['ShareRequest']);
  return { status: shared.scope === scope ? ('shared' as const) : ('pending_admin' as const) };
};

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
