import { pathId, v1Get, v1Send, V1Error, type Page } from './client';
import type { components } from './schema';
import type { SkillStatus } from './skills';

type Schemas = components['schemas'];

/*
 * Skill authoring and running (P03, UC-03-01…04): the skill editor, a skill made from a
 * session, its validation on locked data, running a skill directly and the external-factor
 * search. The library itself (GET /v1/skills, versions, review) lives in skills.ts.
 * Every shape below is PROPOSAL unless noted: A2 names skills, skill versions and test cases,
 * but not the views these screens read.
 */
/** The library's status (skills.ts); re-exported so these screens import one module. */
export type SkillAuthoringStatus = SkillStatus;

/* ─── Trình soạn kỹ năng (UC-03-03, UC-03-04) ─────────────────────────── */

export interface SkillInputSpec {
  /** "metric", "period_a, period_b" — the parameter names as the agent passes them. */
  name: string;
  description: string;
}

export interface SkillStepSpec {
  seq: number;
  text: string;
  /** The tool the step calls ("metric.query"); null for a step the model does itself. */
  tool: string | null;
}

export interface SkillGuardrail {
  text: string;
  /** check = enforced while running; block = a precondition that refuses to start. */
  kind: 'check' | 'block';
}

/** PROPOSAL: GET /v1/skills/{id}/definition — the working (newest) version of a skill. */
export interface SkillDefinition {
  skill_id: string;
  name: string;
  version: number;
  status: SkillAuthoringStatus;
  inputs: SkillInputSpec[];
  output_text: string;
  steps: SkillStepSpec[];
  instructions: string;
  guardrails: SkillGuardrail[];
  owner: { id: string; name: string };
  scope_label: string;
  domain_text: string;
  /** "4 agent · 312 lần/30 ngày". */
  used_by_text: string;
  /** "v3: 96% · v4: chưa chạy thử". */
  quality_text: string;
  test_count: number;
  version_count: number;
  /** The parameters "Thử nhanh" runs with, one line each as shown. */
  quick_test: { params: Record<string, string>; lines: string[] };
}

export type TrialStepState = 'done' | 'error';

/** PROPOSAL: a trial run of the working version (not recorded as a skill run). */
export interface SkillTrial {
  version: number;
  duration_text: string;
  steps: { title: string; detail: string; state: TrialStepState }[];
  bars: { label: string; value: number; value_text: string; main: boolean }[];
  conclusion: string;
}

export const getSkillDefinition = async (id: string) =>
  v1Get<SkillDefinition>(`/skills/${pathId(id)}/definition`);

/** PROPOSAL: the latest trial of the working version; null when it was never tried. */
export const getLatestTrial = async (id: string) =>
  v1Get<SkillTrial | null>(`/skills/${pathId(id)}/trials/latest`);

/** PROPOSAL: PUT /v1/skills/{id}/definition — saves the draft version in place. */
export const saveSkillDefinition = async (
  id: string,
  body: { instructions: string; steps: SkillStepSpec[] },
) => v1Send<SkillDefinition>('PUT', `/skills/${pathId(id)}/definition`, body);

/** PROPOSAL: POST /v1/skills/{id}:try {params} — runs the working version once. */
export const trySkill = async (id: string, params: Record<string, string>) =>
  v1Send<SkillTrial>('POST', `/skills/${pathId(id)}:try`, { params });

/* ─── Tạo kỹ năng từ phiên (UC-03-03) ─────────────────────────────────── */

export interface SessionCellDraft {
  seq: number;
  title: string;
  /** How the agent generalised the cell: "NPL_RATIO → {chỉ số} · …"; null when unchanged. */
  param_text: string | null;
  /** Why a cell is left out, or what it keeps ("giữ khuôn kết luận"). */
  note: string | null;
  /** The agent's proposal: presentation cells and notes are left out. */
  kept: boolean;
}

/** PROPOSAL: GET /v1/workspaces/{id}/skill-draft — the agent's reading of a session as a skill. */
export interface SessionSkillDraft {
  session_id: string;
  session_title: string;
  cells: SessionCellDraft[];
  name: string;
  description: string;
  params: { name: string; type_label: string; default_text: string }[];
  /** The session's own result, kept as the first test case. */
  test_case_text: string;
}

export const getSessionSkillDraft = async (sessionId: string) =>
  v1Get<SessionSkillDraft>(`/workspaces/${pathId(sessionId)}/skill-draft`);

/** PROPOSAL: POST /v1/skills:from-session — creates the skill as a draft (v1) with its first case. */
export const createSkillFromSession = (body: {
  session_id: string;
  kept_cells: number[];
  name: string;
  description: string;
}) => v1Send<{ skill_id: string; version: number }>('POST', '/skills:from-session', body);

/* ─── Biến phiên thành agent (UC-03-03, UC-13-01) ─────────────────────── */

export interface AgentDraftParam {
  key: string;
  label: string;
  value: string;
  options: string[];
}

/** PROPOSAL: GET /v1/workspaces/{id}/agent-draft — the cells as steps and the per-run parameters. */
export interface SessionAgentDraft {
  session_id: string;
  session_title: string;
  cells: { seq: number; label: string; kept: boolean }[];
  params: AgentDraftParam[];
}

export const getSessionAgentDraft = async (sessionId: string) =>
  v1Get<SessionAgentDraft>(`/workspaces/${pathId(sessionId)}/agent-draft`);

/* ─── Kiểm định kỹ năng (UC-03-04) ────────────────────────────────────── */

export interface SkillTestCase {
  id: string;
  name: string;
  period_text: string;
  expected: string;
  actual: string;
  passed: boolean;
  /** The author's note on a failing case; a noted failure does not block review. */
  note: string | null;
}

/** PROPOSAL: why a case failed and the fix the agent proposes. */
export interface SkillTestFailure {
  case_id: string;
  explanation: string;
  /** Unified-diff lines, "- …" / "+ …". */
  fix_diff: string[];
}

/** PROPOSAL: GET /v1/skills/{id}/test-runs/latest — the suite on locked data, newest run. */
export interface SkillTestRun {
  skill_id: string;
  skill_name: string;
  version: number;
  status: SkillAuthoringStatus;
  ran_at: string;
  cases: SkillTestCase[];
  failures: SkillTestFailure[];
}

export const getLatestTestRun = async (id: string) =>
  v1Get<SkillTestRun>(`/skills/${pathId(id)}/test-runs/latest`);

/** PROPOSAL: POST /v1/skills/{id}:test — reruns every case on locked data. */
export const runSkillTests = async (id: string) =>
  v1Send<SkillTestRun>('POST', `/skills/${pathId(id)}:test`, {});

/** PROPOSAL: POST /v1/skills/{id}/test-cases (A2 SkillTestCase). */
export const addSkillTestCase = async (
  id: string,
  body: { name: string; period_text: string; expected: string },
) => v1Send<SkillTestRun>('POST', `/skills/${pathId(id)}/test-cases`, body);

/** PROPOSAL: POST /v1/skills/{id}:apply-fix {case_id} — applies the proposed fix and reruns. */
export const applySkillFix = async (id: string, caseId: string) =>
  v1Send<SkillTestRun>('POST', `/skills/${pathId(id)}:apply-fix`, { case_id: caseId });

/**
 * PROPOSAL: POST /v1/skills/{id}:submit {notes} — sends the version for review; 409 while a
 * failing case has no note.
 */
export const submitSkill = async (id: string, notes: Record<string, string>) =>
  v1Send<{ status: SkillAuthoringStatus }>('POST', `/skills/${pathId(id)}:submit`, { notes });

/* ─── Chạy kỹ năng (UC-03-01) và yếu tố bên ngoài (UC-03-02) ──────────── */

/** generic: a skill A2 serves, whose result has no designed view; it is shown as written. */
export type SkillRunKind = 'decomposition' | 'external_factors' | 'generic';

export interface SkillRunParam {
  key: string;
  label: string;
  value: string;
  /** Only the valid choices: dimensions are those the metric allows; empty = typed freely. */
  options: { value: string; label: string }[];
}

/** PROPOSAL: GET /v1/skills/{id}/run-form — what running the certified version asks for. */
export interface SkillRunForm {
  skill_id: string;
  name: string;
  version: number;
  status: SkillAuthoringStatus;
  kind: SkillRunKind;
  params: SkillRunParam[];
  /** "Chiều chỉ chọn được trong danh sách chiều hợp lệ của chỉ số." */
  params_note: string;
}

export interface WaterfallBar {
  label: string;
  value: number;
  value_text: string;
}

export interface Decomposition {
  summary: string;
  start: WaterfallBar;
  steps: WaterfallBar[];
  end: WaterfallBar;
  contributions: { label: string; points_text: string; share_text: string }[];
}

export type FactorVerdict = 'worth' | 'weak' | 'random';

export interface ExternalFactor {
  id: string;
  name: string;
  source_text: string;
  lag_text: string;
  correlation_text: string;
  test_text: string;
  significant: boolean;
  verdict: FactorVerdict;
  /** Both series indexed to 0…1 over the window (one scale, no second axis); null = not drawn. */
  overlay: {
    title: string;
    factor_label: string;
    metric_label: string;
    factor: number[];
    metric: number[];
    note: string;
  } | null;
}

export interface ExternalFactorSearch {
  title: string;
  /** "Agent so 24 chuỗi … trong 36 tháng". */
  scope_text: string;
  factors: ExternalFactor[];
  method_note: string;
}

/** PROPOSAL: one run of a skill, recorded with its parameters and skill version (UC-03-01). */
export interface SkillRun {
  id: string;
  skill_id: string;
  version: number;
  kind: SkillRunKind;
  params: Record<string, string>;
  ran_at: string;
  decomposition: Decomposition | null;
  external: ExternalFactorSearch | null;
  /** A2 RunDetail of a generic skill: its state, the conclusion and the main figures. */
  outcome: {
    status: 'running' | 'done' | 'failed';
    summary: string | null;
    figures: { label: string; value: string }[];
    error: string | null;
  } | null;
}

type RunWire = Schemas['RunDetail'] & { display?: SkillRun };

/** 404 from a PROPOSAL read: the thing is A2's, not the designed fixture's. */
const notProposal = (e: unknown) => e instanceof V1Error && e.status === 404;

/*
 * PROPOSAL: GET /v1/skills/{id}/run-form — the designed form, with the kind of view its result
 * gets. A skill it does not know is read from A2's GET /v1/skills/{id}: the published version's
 * params_schema (a JSON Schema) gives the parameters, an enum their valid choices, a default the
 * first value, and the result is shown generically.
 */
export const getSkillRunForm = async (id: string): Promise<SkillRunForm> => {
  try {
    return await v1Get<SkillRunForm>(`/skills/${pathId(id)}/run-form`);
  } catch (e) {
    if (!notProposal(e)) throw e;
  }
  const raw = await v1Get<Schemas['SkillDetail']>(`/skills/${pathId(id)}`);
  const version = raw.published_version ?? null;
  const schema = (version?.params_schema ?? {}) as {
    properties?: Record<string, { title?: string; enum?: unknown[]; default?: unknown }>;
  };
  const params = Object.entries(schema.properties ?? {}).map(([key, prop]) => {
    const options = (prop.enum ?? []).map((v) => ({ value: String(v), label: String(v) }));
    return {
      key,
      label: prop.title ?? key,
      value: prop.default !== undefined ? String(prop.default) : (options[0]?.value ?? ''),
      options,
    };
  });
  return {
    skill_id: raw.id,
    name: raw.name,
    version: version?.version ?? raw.current_version ?? 0,
    status: (version?.status ?? 'draft') as SkillAuthoringStatus,
    kind: 'generic',
    params,
    params_note: version ? '' : 'Kỹ năng chưa có phiên bản được chứng nhận để chạy.',
  };
};

const FINISHED: Record<string, 'done' | 'failed'> = {
  succeeded: 'done',
  completed: 'done',
  failed: 'failed',
  cancelled: 'failed',
  timeout: 'failed',
};

const text = (v: unknown) =>
  v === null || v === undefined ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v);

/** result is "the conclusion and the main figures" (jsonb): a summary line, the rest as figures. */
function readRun(raw: RunWire): SkillRun {
  if (raw.display) return raw.display;
  const result = (raw.result ?? {}) as Record<string, unknown>;
  const summaryKey = ['summary', 'conclusion', 'text'].find((k) => typeof result[k] === 'string');
  return {
    id: raw.id,
    skill_id: raw.skill_id,
    version: raw.version,
    kind: 'generic',
    params: Object.fromEntries(
      Object.entries((raw.params ?? {}) as Record<string, unknown>).map(([k, v]) => [k, text(v)]),
    ),
    ran_at: raw.started_at ?? raw.created_at,
    decomposition: null,
    external: null,
    outcome: {
      status: FINISHED[raw.status] ?? 'running',
      summary: summaryKey ? (result[summaryKey] as string) : null,
      figures: Object.entries(result)
        .filter(([k]) => k !== summaryKey)
        .map(([label, value]) => ({ label, value: text(value) })),
      error: raw.error,
    },
  };
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/*
 * PROPOSAL: GET /v1/skills/{id}/runs — the viewer's runs, newest first. A2 lists no runs of a
 * skill, so there an answer of 404 means "no run yet" rather than an error.
 */
export const listSkillRuns = async (id: string): Promise<Page<SkillRun>> => {
  try {
    return await v1Get<Page<SkillRun>>(`/skills/${pathId(id)}/runs`, { page_size: 1 });
  } catch (e) {
    if (notProposal(e)) return { items: [], next_page_token: '' };
    throw e;
  }
};

/**
 * A2: POST /v1/skills/{id}/runs (202), under the caller's permissions, then the run read back
 * until it finishes or about twenty seconds pass; a run still queued is returned as running.
 * PROPOSAL: POST /v1/skills/{id}:run, which answers the designed result of the other kinds.
 */
export const runSkill = async (
  id: string,
  params: Record<string, string>,
  kind: SkillRunKind,
): Promise<SkillRun> => {
  if (kind !== 'generic') return v1Send<SkillRun>('POST', `/skills/${pathId(id)}:run`, { params });
  const accepted = await v1Send<Schemas['services__api__modules__skill__schemas__Accepted']>(
    'POST',
    `/skills/${pathId(id)}/runs`,
    { params },
  );
  let run = readRun(await v1Get<RunWire>(`/skill-runs/${pathId(accepted.id)}`));
  for (let attempt = 0; attempt < 10 && run.outcome?.status === 'running'; attempt++) {
    await sleep(accepted.poll_after_ms ?? 2000);
    run = readRun(await v1Get<RunWire>(`/skill-runs/${pathId(accepted.id)}`));
  }
  return run;
};

/**
 * PROPOSAL: POST /v1/skill-runs/{id}:add-to-session — adds the result as a tile to the session
 * the run came from, or to a new session when it has none.
 */
export const addRunToSession = async (runId: string) =>
  v1Send<{ session_id: string }>('POST', `/skill-runs/${pathId(runId)}:add-to-session`, {});
