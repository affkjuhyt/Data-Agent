import { pathId, v1Get, v1Send, type Page } from './client';
import type { components } from './schema';
import { readWorkspace, type Workspace } from './types';

/*
 * Phiên làm việc (P02, UC-02-01…07): one workspace as a notebook of tiles, its comments and
 * assigned tasks, its sharing, and the report drafted from it.
 *
 * The api serves the workspace with its tiles, the tile and comment writes, the shares and the
 * report's create, check and publish (A2). A2 has no read for a tile's comments, a workspace's
 * shares or a session report, and no principal search: those calls stay PROPOSAL, answered by
 * the dev mock (docs/v3/05-ui-v3.md §4).
 */

type Uuid = string;
type Timestamp = string;

/** Who wrote a tile or a comment: a person or an agent, by display name. */
export interface Author {
  kind: 'person' | 'agent';
  name: string;
}

/**
 * GET /v1/workspaces/{id} as the api sends it. PROPOSAL: the tile's author name and activity
 * counts, who is working and the source refresh that made tiles stale.
 */
type TileWire = components['schemas']['TileView'] & {
  author_name?: string;
  comment_count?: number;
  task_count?: number;
};

export type WorkspaceDetailWire = Omit<components['schemas']['WorkspaceDetail'], 'tiles'> & {
  tiles: TileWire[];
  working?: Author[];
  refresh?: WorkspaceDetail['refresh'];
};

/** One cell of the notebook as the screen draws it, read from workspace.tile. */
export interface WorkspaceTile {
  id: Uuid;
  /** Position in the notebook, 1-based, shown as "[1]". */
  seq: number;
  kind: 'text' | 'sql' | 'chart' | 'skill';
  title: string;
  /** The question, the SQL, or the result in one sentence. */
  body: string;
  author: Author;
  /**
   * ok = result is current; stale = the data under it changed and it must be re-run (UC-02-02);
   * proposed = an agent suggested it and it does not run until a person accepts it (UC-02-06).
   */
  state: 'ok' | 'stale' | 'proposed';
  comment_count: number;
  task_count: number;
}

/** The workspace as the notebook draws it. */
export interface WorkspaceDetail extends Omit<Workspace, 'params'> {
  /** Session parameters applied to every tile (UC-02-01): "kỳ = T9/2026". */
  params: { name: string; value: string }[];
  /** The viewer's right on it: view, comment, edit or owner. */
  permission: components['schemas']['WorkspaceDetail']['permission'];
  /** Who is working in it now, people first, for "… đang làm". Empty without the PROPOSAL field. */
  working: Author[];
  /** The newest source refresh that made tiles stale, null when unknown or every tile is current. */
  refresh: { source_name: string; refreshed_at: Timestamp } | null;
  /** Without the ones a person dismissed. */
  tiles: WorkspaceTile[];
}

const KIND: Record<string, WorkspaceTile['kind']> = {
  sql: 'sql',
  chart: 'chart',
  skill_run: 'skill',
};

/** What a tile says in one line: its SQL, its text or its question, whichever `spec` holds. */
const bodyOf = (spec: unknown) => {
  if (typeof spec !== 'object' || spec === null) return '';
  const fields = spec as Record<string, unknown>;
  const text = ['sql', 'md', 'text', 'question', 'summary'].map((k) => fields[k]);
  return (text.find((v) => typeof v === 'string') as string | undefined) ?? '';
};

function readTile(raw: TileWire): WorkspaceTile {
  const agent = raw.author_kind === 'agent';
  return {
    id: raw.id,
    seq: raw.position,
    kind: KIND[raw.kind] ?? 'text',
    title: raw.title ?? '',
    body: bodyOf(raw.spec),
    author: { kind: agent ? 'agent' : 'person', name: raw.author_name ?? (agent ? 'Agent' : '') },
    state: raw.agent_suggestion_state === 'suggested' ? 'proposed' : raw.stale ? 'stale' : 'ok',
    comment_count: raw.comment_count ?? 0,
    task_count: raw.task_count ?? 0,
  };
}

/** `params` is the jsonb object of WorkspaceCreate, {"kỳ": "T9/2026"}. */
const readParams = (raw: unknown) =>
  typeof raw === 'object' && raw !== null && !Array.isArray(raw)
    ? Object.entries(raw).map(([name, value]) => ({ name, value: String(value) }))
    : [];

function readWorkspaceDetail(raw: WorkspaceDetailWire): WorkspaceDetail {
  // The detail lists its participants; the session lists count them (people, agents).
  const { participants, ...row } = raw;
  const count = (type: string) => participants.filter((p) => p.principal_type === type).length;
  return {
    ...readWorkspace({ ...row, participants: { people: count('user'), agents: count('agent') } }),
    params: readParams(raw.params),
    permission: raw.permission,
    working: raw.working ?? [],
    refresh: raw.refresh ?? null,
    tiles: raw.tiles
      .filter((t) => t.agent_suggestion_state !== 'dismissed')
      .sort((a, b) => a.position - b.position)
      .map(readTile),
  };
}

/** PROPOSAL: a comment on a tile (UC-02-05). */
export interface TileComment {
  id: Uuid;
  tile_id: Uuid;
  author: Author;
  body: string;
  created_at: Timestamp;
}

/** PROPOSAL: a task given from a tile's comments; it shows in the assignee's Cần bạn. */
export interface TileTask {
  id: Uuid;
  tile_id: Uuid;
  title: string;
  assignee_name: string;
  due_at: Timestamp;
}

/** PROPOSAL: GET /v1/workspaces/{id}/tiles/{tile}/comments — the thread and its tasks. */
export interface TileThread {
  items: TileComment[];
  tasks: TileTask[];
}

export type MemberRole = 'owner' | 'editor' | 'viewer';

/** PROPOSAL: one person or group the workspace is shared with (UC-02-03). */
export interface WorkspaceMember {
  principal_id: Uuid;
  kind: 'user' | 'group';
  name: string;
  /** "Rủi ro tín dụng · toàn vùng", "14 người", "chủ phiên". */
  detail: string;
  /** True when the member's data scope is narrower than the workspace's: shown in warning. */
  scope_limited: boolean;
  role: MemberRole;
}

/** PROPOSAL: GET/PUT /v1/workspaces/{id}/sharing. */
export interface WorkspaceSharing {
  members: WorkspaceMember[];
  /** "Người nhận thấy gì": what each recipient will see, computed from their data scope. */
  preview: string;
  link: string;
}

/** PROPOSAL: GET /v1/principals?q= — people and groups to share with. */
export interface Principal {
  id: Uuid;
  kind: 'user' | 'group';
  name: string;
  detail: string;
  scope_limited: boolean;
}

/** PROPOSAL: a check run before a report can be published (UC-02-07). */
export interface ReportCheck {
  ok: boolean;
  title: string;
  detail: string;
}

/** PROPOSAL: one number of the report, and whether it is tied to a tile or a metric. */
export interface ReportFigure {
  value: string;
  grounded: boolean;
}

/** PROPOSAL: the report drafted from a session by Agent Báo cáo (UC-02-07). */
export interface SessionReport {
  id: Uuid;
  workspace_id: Uuid;
  status: 'drafting' | 'pending_review' | 'changes_requested' | 'published';
  /** "Agent Báo cáo soạn từ 5 ô của phiên lúc 11:02". */
  drafted_by: string;
  tile_count: number;
  drafted_at: Timestamp;
  reviewer_name: string;
  document: {
    heading: string;
    summary: string;
    figures: ReportFigure[];
    recommendation: string;
  };
  checks: ReportCheck[];
  /** "GRP-RISK-ANALYST, Ban điều hành · email (PDF) và trong ứng dụng". */
  recipients_text: string;
}

/* Phiên */
export const getWorkspace = async (id: string) =>
  readWorkspaceDetail(await v1Get<WorkspaceDetailWire>(`/workspaces/${pathId(id)}`));

/**
 * Re-run the stale tiles in dependency order; the old results stay in the session history
 * (UC-02-02). The api answers 202 and runs them in the background, so this reads the workspace
 * again for what is already done.
 */
export const refreshWorkspace = async (id: string) => {
  await v1Send('POST', `/workspaces/${pathId(id)}:refresh`, {});
  return getWorkspace(id);
};

/** Accept or dismiss a tile an agent proposed (UC-02-06). */
export const decideTile = async (
  workspaceId: string,
  tileId: string,
  verb: 'accept' | 'dismiss',
) => {
  await v1Send('PATCH', `/tiles/${pathId(tileId)}`, {
    agent_suggestion_state: verb === 'accept' ? 'accepted' : 'dismissed',
  });
  return getWorkspace(workspaceId);
};

/* Bình luận và giao việc */
/** PROPOSAL: A2 writes comments but has no read for them. */
export const getTileThread = async (workspaceId: string, tileId: string) =>
  v1Get<TileThread>(`/workspaces/${pathId(workspaceId)}/tiles/${pathId(tileId)}/comments`);

export const addTileComment = async (tileId: string, body: string) =>
  v1Send<components['schemas']['CommentView']>('POST', `/tiles/${pathId(tileId)}/comments`, {
    body,
  });

/* Chia sẻ */
/** PROPOSAL: A2 replaces the shares but has no read for them. */
export const getSharing = async (id: string) =>
  v1Get<WorkspaceSharing>(`/workspaces/${pathId(id)}/sharing`);

export type SharingInput = { principal_id: string; kind: 'user' | 'group'; role: MemberRole }[];

/*
 * PUT replaces the whole set (A1.3). The owner is the workspace's, not a share, so it is left
 * out; editor and viewer are the "edit" and "view" permissions.
 */
export const saveSharing = async (id: string, members: SharingInput) =>
  v1Send<Page<components['schemas']['ShareView']>>('PUT', `/workspaces/${pathId(id)}/shares`, {
    shares: members
      .filter((m) => m.role !== 'owner')
      .map((m) => ({
        principal_type: m.kind,
        principal_id: m.principal_id,
        permission: m.role === 'editor' ? ('edit' as const) : ('view' as const),
      })),
  } satisfies components['schemas']['SharesPut']);

/** PROPOSAL: "Người nhận thấy gì" for members not saved yet, from each one's data scope. */
export const previewSharing = async (id: string, members: SharingInput) =>
  v1Send<{ preview: string }>('POST', `/workspaces/${pathId(id)}/sharing:preview`, { members });

/** PROPOSAL: people and groups to share with. */
export const searchPrincipals = (q: string) =>
  v1Get<Page<Principal>>('/principals', { q, page_size: 20 });

/* Báo cáo của phiên */
/** PROPOSAL: A2 has no list of a session's reports. */
export const listSessionReports = async (workspaceId: string) =>
  v1Get<Page<SessionReport>>(`/workspaces/${pathId(workspaceId)}/session-reports`, {
    page_size: 20,
  });

/** Asks Agent Báo cáo to draft a report from every tile of the session. */
export const createSessionReport = async (workspaceId: string, title: string) =>
  v1Send<components['schemas']['ReportView']>(
    'POST',
    `/workspaces/${pathId(workspaceId)}/reports`,
    {
      title,
    },
  );

/** PROPOSAL: A2 has no read for a session report. */
export const getSessionReport = async (id: string) =>
  v1Get<SessionReport>(`/session-reports/${pathId(id)}`);

/**
 * Publish needs every check to pass and a reviewer other than the author (409 otherwise). The
 * api answers with the bare report row, so the review shape is read again.
 */
export const publishSessionReport = async (id: string) => {
  await v1Send('POST', `/session-reports/${pathId(id)}:publish`, { recipients: [] });
  return getSessionReport(id);
};

/** Re-runs the pre-publish checks (202), e.g. after a number was tied to a tile. */
export const checkSessionReport = async (id: string) => {
  await v1Send('POST', `/session-reports/${pathId(id)}:check`);
  return getSessionReport(id);
};

/** PROPOSAL: A2 has no request-changes for a session report. */
export const requestReportChanges = async (id: string, note: string) =>
  v1Send<SessionReport>('POST', `/session-reports/${pathId(id)}:request-changes`, { note });
