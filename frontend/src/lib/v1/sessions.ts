import { pathId, v1Get, v1Send, type Page } from './client';
import type { Workspace } from './types';

/*
 * Phiên làm việc (P02, UC-02-01…07): one workspace as a notebook of tiles, its comments and
 * assigned tasks, its sharing, and the report drafted from it.
 *
 * Every type and endpoint here is a PROPOSAL: the system design has workspace.workspace (and
 * the tile_count on it) but no screen-level shape for tiles, comments, sharing or session
 * reports yet. Paths follow "Đặc tả API" A1: /v1/<plural>, `:verb` actions, cursor pages.
 * docs/v3/05-ui-v3.md §4 lists them for the backend.
 */

type Uuid = string;
type Timestamp = string;

/** Who wrote a tile or a comment: a person or an agent, by display name. */
export interface Author {
  kind: 'person' | 'agent';
  name: string;
}

/** PROPOSAL: workspace.tile — one cell of the notebook. */
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

/** PROPOSAL: GET /v1/workspaces/{id} — the workspace row plus what the notebook screen needs. */
export interface WorkspaceDetail extends Workspace {
  /** Session parameters applied to every tile (UC-02-01): "kỳ = T9/2026". */
  params: { name: string; value: string }[];
  /** Who is working in it now, people first, for "… đang làm". */
  working: Author[];
  /** The newest source refresh that made tiles stale, null when every tile is current. */
  refresh: { source_name: string; refreshed_at: Timestamp } | null;
  tiles: WorkspaceTile[];
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
  v1Get<WorkspaceDetail>(`/workspaces/${pathId(id)}`);

/** Re-run the stale tiles; the old results stay in the session history (UC-02-02). */
export const refreshWorkspace = async (id: string, tileIds: string[]) =>
  v1Send<WorkspaceDetail>('POST', `/workspaces/${pathId(id)}:refresh`, { tile_ids: tileIds });

/** Accept or dismiss a tile an agent proposed (UC-02-06). */
export const decideTile = async (workspaceId: string, tileId: string, verb: 'accept' | 'dismiss') =>
  v1Send<WorkspaceDetail>(
    'POST',
    `/workspaces/${pathId(workspaceId)}/tiles/${pathId(tileId)}:${verb}`,
  );

/* Bình luận và giao việc */
export const getTileThread = async (workspaceId: string, tileId: string) =>
  v1Get<TileThread>(`/workspaces/${pathId(workspaceId)}/tiles/${pathId(tileId)}/comments`);

export const addTileComment = async (workspaceId: string, tileId: string, body: string) =>
  v1Send<TileComment>(
    'POST',
    `/workspaces/${pathId(workspaceId)}/tiles/${pathId(tileId)}/comments`,
    { body },
  );

/* Chia sẻ */
export const getSharing = async (id: string) =>
  v1Get<WorkspaceSharing>(`/workspaces/${pathId(id)}/sharing`);

export type SharingInput = { principal_id: string; kind: 'user' | 'group'; role: MemberRole }[];

export const saveSharing = async (id: string, members: SharingInput) =>
  v1Send<WorkspaceSharing>('PUT', `/workspaces/${pathId(id)}/sharing`, { members });

/** "Người nhận thấy gì" for members not saved yet: the server applies each one's data scope. */
export const previewSharing = async (id: string, members: SharingInput) =>
  v1Send<{ preview: string }>('POST', `/workspaces/${pathId(id)}/sharing:preview`, { members });

export const searchPrincipals = (q: string) =>
  v1Get<Page<Principal>>('/principals', { q, page_size: 20 });

/* Báo cáo của phiên */
export const listSessionReports = async (workspaceId: string) =>
  v1Get<Page<SessionReport>>(`/workspaces/${pathId(workspaceId)}/session-reports`, {
    page_size: 20,
  });

/** Asks Agent Báo cáo to draft a report from the session; answers with the draft. */
export const createSessionReport = async (workspaceId: string) =>
  v1Send<SessionReport>('POST', `/workspaces/${pathId(workspaceId)}/session-reports`, {});

export const getSessionReport = async (id: string) =>
  v1Get<SessionReport>(`/session-reports/${pathId(id)}`);

/** Publish needs every check to pass and a reviewer other than the author (409 otherwise). */
export const publishSessionReport = async (id: string) =>
  v1Send<SessionReport>('POST', `/session-reports/${pathId(id)}:publish`, {});

/** Re-runs the pre-publish checks, e.g. after a number was tied to a tile. */
export const checkSessionReport = async (id: string) =>
  v1Send<SessionReport>('POST', `/session-reports/${pathId(id)}:check`, {});

export const requestReportChanges = async (id: string, note: string) =>
  v1Send<SessionReport>('POST', `/session-reports/${pathId(id)}:request-changes`, { note });
