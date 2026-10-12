import { pathId, v1Get, v1Send, type Page } from './client';
import type { components } from './schema';
import type {
  AgentDef,
  AgentStep,
  AgentWire,
  BehaviorPolicy,
  NarrativeEvent,
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
  DatasetWire,
  GlossaryTerm,
  HitlRequest,
  Metric,
  ModelConfig,
  ResolveHitl,
  RunStatus,
  RunWire,
  Source,
  SourceWire,
  Workspace,
  WorkspaceWire,
} from './types';
import { readAppUser } from './adminAccess';
import { listModelRoutes } from './adminPlatform';
import { readDataset, readSource, readWorkspace } from './types';

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

export const readAgent = (raw: AgentWire): AgentDef => ({
  ...raw,
  kind: raw.kind as AgentDef['kind'],
  status: raw.status as AgentDef['status'],
  active_runs: raw.active_runs ?? 0,
  waiting_runs: raw.waiting_runs ?? 0,
  goal: raw.goal ?? null,
  now: raw.now ?? null,
  trigger_text: raw.trigger_text ?? null,
  permission_text: raw.permission_text ?? null,
  runs_7d: raw.runs_7d ?? null,
});

/*
 * agent_run.narrative is the run's event log (jsonb). The story screens draw the entries that
 * read as a sentence ({at, text}); the plan edits the api records are said in words; other
 * events are the worker's and are left out.
 */
function readNarrative(raw: unknown): NarrativeEvent[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((entry): NarrativeEvent[] => {
    if (!isRecord(entry)) return [];
    const at = isText(entry.at) ? entry.at : isText(entry.created_at) ? entry.created_at : '';
    if (isText(entry.text)) {
      const state = entry.state === 'now' || entry.state === 'next' ? entry.state : 'done';
      const progress = typeof entry.progress === 'number' ? entry.progress : undefined;
      return [{ at, text: entry.text, state, ...(progress === undefined ? {} : { progress }) }];
    }
    if (entry.event === 'plan.edited')
      return [
        { at, text: `Kế hoạch được sửa (bản ${String(entry.plan_version ?? '')})`, state: 'done' },
      ];
    return [];
  });
}

/** A step as the api sends it (StepView) gets the trace fields it lacks as unknown. */
function readStep(raw: components['schemas']['StepView'] | AgentStep): AgentStep {
  if ('actor' in raw) return raw;
  const input = isRecord(raw.input) ? raw.input : {};
  return {
    seq: raw.seq,
    kind: raw.kind as AgentStep['kind'],
    name: raw.name,
    summary: raw.summary,
    status: raw.status as AgentStep['status'],
    duration_ms: raw.duration_ms,
    parent_seq: null,
    started_at: raw.started_at,
    actor: raw.kind,
    metric_text: null,
    run_as_text: null,
    rows_text: null,
    sql: isText(input.sql) ? input.sql : null,
  };
}

function readRun(raw: RunWire): AgentRun {
  return {
    ...raw,
    status: raw.status as AgentRun['status'],
    trigger_kind: raw.trigger_kind as AgentRun['trigger_kind'],
    narrative: readNarrative(raw.narrative),
    error: raw.error ?? null,
    agent_name: raw.agent_name ?? 'Agent',
    total_steps: raw.total_steps ?? null,
    eta_text: raw.eta_text ?? null,
    code: raw.code ?? `#${raw.id.slice(0, 8)}`,
    title: raw.title ?? '',
    initiated_label: raw.initiated_label ?? null,
    trigger_text: raw.trigger_text ?? null,
    hitl_count: raw.hitl_count ?? null,
    steps: raw.steps ? raw.steps.map(readStep) : undefined,
  };
}

/**
 * Runs carry the agent's id only; its name comes from the agents the viewer can see, and names
 * the run when the PROPOSAL title is missing.
 */
function named(run: AgentRun, agents: AgentDef[]): AgentRun {
  const name = agents.find((a) => a.id === run.agent_id)?.name ?? run.agent_name;
  return { ...run, agent_name: name, title: run.title || `Lượt chạy của ${name}` };
}

export const listAgents = async (): Promise<Page<AgentDef>> => {
  const page = await v1Get<Page<AgentWire>>('/agents', ALL);
  return { ...page, items: page.items.map(readAgent) };
};

export const listRuns = async (
  query: { status?: RunStatus; agent_id?: string } = {},
): Promise<Page<AgentRun>> => {
  const [page, agents] = await Promise.all([
    v1Get<Page<RunWire>>('/runs', { ...ALL, ...query }),
    listAgents().catch(() => null),
  ]);
  return {
    ...page,
    items: page.items.map((r) =>
      r.agent_name ? readRun(r) : named(readRun(r), agents?.items ?? []),
    ),
  };
};

export const getRun = async (id: string, view: 'simple' | 'technical' = 'simple') => {
  const raw = await v1Get<RunWire>(`/runs/${pathId(id)}`, { view });
  const run = readRun(raw);
  if (raw.agent_name) return run;
  const agent = await v1Get<AgentWire>(`/agents/${pathId(run.agent_id)}`).catch(() => null);
  return named(run, agent ? [readAgent(agent)] : []);
};

/** Pause stops safely at a step boundary; resume continues; cancel keeps partial results 30 days. */
export const controlRun = async (
  id: string,
  verb: 'pause' | 'resume' | 'cancel',
  reason?: string,
) =>
  readRun(
    await v1Send<RunWire>(
      'POST',
      `/runs/${pathId(id)}:${verb}`,
      verb === 'cancel' ? { reason } : undefined,
    ),
  );

/**
 * PROPOSAL: POST /v1/runs/{id}:retry — a failed run again, as a new run (202); `use_fallback`
 * reads the agent's fallback source instead of the one that failed. The spec has no retry yet.
 */
export const retryRun = async (id: string, useFallback = false) =>
  readRun(
    await v1Send<RunWire>('POST', `/runs/${pathId(id)}:retry`, { use_fallback: useFallback }),
  );

/* Phiên làm việc */
export const listWorkspaces = async (): Promise<Page<Workspace>> => {
  const page = await v1Get<Page<WorkspaceWire>>('/workspaces', { ...ALL, status: 'active' });
  return { ...page, items: page.items.map(readWorkspace) };
};

/* Nguồn, danh mục, lớp ngữ cảnh */
export const listSources = async (): Promise<Page<Source>> => {
  const page = await v1Get<Page<SourceWire>>('/sources', ALL);
  return { ...page, items: page.items.map(readSource) };
};
export const listDatasets = async (): Promise<Page<Dataset>> => {
  const page = await v1Get<Page<DatasetWire>>('/datasets', { ...ALL, enabled: true });
  return { ...page, items: page.items.map(readDataset) };
};
export const listMetrics = () => v1Get<Page<Metric>>('/metrics', ALL);
export const listTerms = () => v1Get<Page<GlossaryTerm>>('/terms', ALL);

/* Chính sách: the viewer's own rules, made from earlier decisions (UC-07-08) */
export const listMyRules = () =>
  v1Get<Page<BehaviorPolicy>>('/policies', { ...ALL, scope: 'user', status: 'active' });

/* Quản trị */
export const listDataSpaces = () => v1Get<Page<DataSpace>>('/data-spaces', ALL);
/** GET /v1/admin/users read as people and agents (lib/v1/adminAccess readAppUser). */
export const listUsers = async (): Promise<Page<AppUser>> => {
  const page = await v1Get<Page<components['schemas']['AdminUser']>>('/admin/users', ALL);
  return { ...page, items: page.items.map(readAppUser) };
};
/** GET /v1/admin/models answers the whole list, not a page. */
export const listModels: () => Promise<Page<ModelConfig>> = listModelRoutes;
