import { pathId, v1Get, v1Send, type Page } from './client';
import type {
  AgentDef,
  BehaviorPolicy,
  DecideProposal,
  Proposal,
  AgentRun,
  AppUser,
  DataSpace,
  Dataset,
  GlossaryTerm,
  HitlRequest,
  Metric,
  ModelConfig,
  ResolveHitl,
  RunStatus,
  Source,
  Workspace,
} from './types';

/*
 * The endpoints the v3.1 screens call, with the paths and parameters of "Đặc tả API" A2/A3.
 * Lists ask for the maximum page (A1.4: 500) because the overview screens count items;
 * the spec has no total, see docs/v3/05-ui-v3.md §4.
 */
const ALL = { page_size: 500 };

/* Cần bạn và đánh giá */
export const listInbox = (query: { kind?: string; status?: string } = {}) =>
  v1Get<Page<HitlRequest>>('/inbox', { ...ALL, ...query });

export const getHitl = async (id: string) => v1Get<HitlRequest>(`/hitl-requests/${pathId(id)}`);

export const resolveHitl = async (id: string, body: ResolveHitl) =>
  v1Send<HitlRequest>('POST', `/hitl-requests/${pathId(id)}:resolve`, body);

export const getProposal = async (id: string) => v1Get<Proposal>(`/proposals/${pathId(id)}`);

export const decideProposal = async (id: string, body: DecideProposal) =>
  v1Send<Proposal>('POST', `/proposals/${pathId(id)}:decide`, body);

/* Agent */
export const listAgents = () => v1Get<Page<AgentDef>>('/agents', ALL);

export const listRuns = (query: { status?: RunStatus; agent_id?: string } = {}) =>
  v1Get<Page<AgentRun>>('/runs', { ...ALL, ...query });

export const getRun = async (id: string, view: 'simple' | 'technical' = 'simple') =>
  v1Get<AgentRun>(`/runs/${pathId(id)}`, { view });

/** Pause stops safely at a step boundary; resume continues; cancel keeps partial results 30 days. */
export const controlRun = async (
  id: string,
  verb: 'pause' | 'resume' | 'cancel',
  reason?: string,
) =>
  v1Send<AgentRun>(
    'POST',
    `/runs/${pathId(id)}:${verb}`,
    verb === 'cancel' ? { reason } : undefined,
  );

/* Phiên làm việc */
export const listWorkspaces = () =>
  v1Get<Page<Workspace>>('/workspaces', { ...ALL, status: 'active' });

/* Nguồn, danh mục, lớp ngữ cảnh */
export const listSources = () => v1Get<Page<Source>>('/sources', ALL);
export const listDatasets = () => v1Get<Page<Dataset>>('/datasets', { ...ALL, enabled: true });
export const listMetrics = () => v1Get<Page<Metric>>('/metrics', ALL);
export const listTerms = () => v1Get<Page<GlossaryTerm>>('/terms', ALL);

/* Chính sách: the viewer's own rules, made from earlier decisions (UC-07-08) */
export const listMyRules = () =>
  v1Get<Page<BehaviorPolicy>>('/policies', { ...ALL, scope: 'user', status: 'active' });

/* Quản trị */
export const listDataSpaces = () => v1Get<Page<DataSpace>>('/data-spaces', ALL);
export const listUsers = () => v1Get<Page<AppUser>>('/admin/users', ALL);
export const listModels = () => v1Get<Page<ModelConfig>>('/admin/models', ALL);
