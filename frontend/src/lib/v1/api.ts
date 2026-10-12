import { pathId, v1Get, v1Send, type Page } from './client';
import type {
  AgentDef,
  BehaviorPolicy,
  DecideProposal,
  HitlOption,
  HitlWire,
  Proposal,
  ProposalDiff,
  ProposalImpact,
  ProposalWire,
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

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const isText = (value: unknown): value is string => typeof value === 'string';

/*
 * `options`, `context`, `diff` and `impact` are jsonb that each producer fills (inbox.hitl_request,
 * inbox.proposal), so the shapes the cards draw are read, not assumed: what does not have them is
 * dropped or left null and the screen leaves that block out.
 */
function readOption(raw: unknown): HitlOption | null {
  if (isText(raw)) return { id: raw, label: raw, consequence: '' };
  if (!isRecord(raw) || !isText(raw.id)) return null;
  return {
    id: raw.id,
    label: isText(raw.label) ? raw.label : raw.id,
    consequence: isText(raw.consequence) ? raw.consequence : '',
    is_default: raw.is_default === true,
  };
}

export function readHitl(raw: HitlWire): HitlRequest {
  return {
    ...raw,
    kind: raw.kind as HitlRequest['kind'],
    risk: raw.risk as HitlRequest['risk'],
    status: raw.status as HitlRequest['status'],
    context: isRecord(raw.context) ? raw.context : {},
    options: Array.isArray(raw.options)
      ? raw.options.map(readOption).filter((o): o is HitlOption => o !== null)
      : [],
    agent: raw.agent ?? null,
  };
}

const readSide = (raw: unknown, label: string) =>
  isText(raw)
    ? { label, text: raw }
    : isRecord(raw) && isText(raw.text)
      ? { label: isText(raw.label) ? raw.label : label, text: raw.text }
      : null;

function readDiff(raw: unknown): ProposalDiff | null {
  if (!isRecord(raw)) return null;
  const before = readSide(raw.before, 'Hiện tại');
  const after = readSide(raw.after, 'Đề xuất');
  return before && after ? { before, after } : null;
}

function readImpact(raw: unknown): ProposalImpact | null {
  if (!isRecord(raw) || !Array.isArray(raw.rows) || !Array.isArray(raw.columns)) return null;
  const [left, right] = raw.columns;
  if (!isText(left) || !isText(right)) return null;
  const rows = raw.rows.filter(
    (row): row is ProposalImpact['rows'][number] =>
      isRecord(row) && [row.period, row.before, row.after, row.delta].every(isText),
  );
  return {
    window_label: isText(raw.window_label) ? raw.window_label : '',
    columns: [left, right],
    rows,
    summary: isText(raw.summary) ? raw.summary : '',
  };
}

/** Without the PROPOSAL `checks`, the eval run the proposal was tested with says the same. */
function checksOf(raw: ProposalWire): Proposal['checks'] {
  if (raw.checks) return raw.checks;
  const run = raw.eval_run;
  if (!run || run.item_count === 0) return [];
  const checks = [
    { ok: run.failed === 0, text: `${run.passed}/${run.item_count} câu đánh giá đạt` },
  ];
  if (run.regressions > 0)
    checks.push({ ok: false, text: `${run.regressions} câu đổi kết quả so với bản trước` });
  return checks;
}

function readProposal(raw: ProposalWire): Proposal {
  return {
    ...raw,
    status: raw.status as Proposal['status'],
    impact_level: raw.impact_level as Proposal['impact_level'],
    diff: readDiff(raw.diff),
    raw_diff: raw.diff,
    impact: readImpact(raw.impact),
    checks: checksOf(raw),
    approval: raw.approval ?? null,
    proposed_by_label: raw.proposed_by_label ?? null,
    code: raw.code ?? null,
  };
}

export const listInbox = async (query: { kind?: string; status?: string } = {}) => {
  const page = await v1Get<Page<HitlWire>>('/inbox', { ...ALL, ...query });
  return { ...page, items: page.items.map(readHitl) };
};

export const getHitl = async (id: string) =>
  readHitl(await v1Get<HitlWire>(`/hitl-requests/${pathId(id)}`));

export const resolveHitl = async (id: string, body: ResolveHitl) =>
  readHitl(await v1Send<HitlWire>('POST', `/hitl-requests/${pathId(id)}:resolve`, body));

export const getProposal = async (id: string) =>
  readProposal(await v1Get<ProposalWire>(`/proposals/${pathId(id)}`));

export const decideProposal = async (id: string, body: DecideProposal) =>
  readProposal(await v1Send<ProposalWire>('POST', `/proposals/${pathId(id)}:decide`, body));

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

/**
 * PROPOSAL: POST /v1/runs/{id}:retry — a failed run again, as a new run (202); `use_fallback`
 * reads the agent's fallback source instead of the one that failed. The spec has no retry yet.
 */
export const retryRun = async (id: string, useFallback = false) =>
  v1Send<AgentRun>('POST', `/runs/${pathId(id)}:retry`, { use_fallback: useFallback });

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
