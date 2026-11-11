import { pathId, v1Get, v1Send, V1Error, type Page, api, ok } from './client';
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
  /** A2 skill_version.id of the version shown; null when the skill has none yet. */
  version_id: string | null;
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
  /**
   * The shown version as the api holds it, which a save starts from: the editor shows only a
   * step's text and tool, but the worker runs a step by its `kind` and a run needs the
   * parameter schema. null when A2 does not serve the version's content (a draft is listed as a
   * VersionSummary only).
   */
  source: VersionSource | null;
}

export interface VersionSource {
  steps: Array<Record<string, unknown>>;
  params_schema: unknown;
  metric_version_ids: string[];
  sandbox_limits: unknown;
}

const sourceOf = (version: Schemas['VersionView']): VersionSource => ({
  steps: (Array.isArray(version.steps) ? version.steps : []) as Array<Record<string, unknown>>,
  params_schema: version.params_schema,
  metric_version_ids: version.metric_version_ids ?? [],
  sandbox_limits: version.sandbox_limits,
});

export type TrialStepState = 'done' | 'error';

/** PROPOSAL: a trial run of the working version (not recorded as a skill run). */
export interface SkillTrial {
  version: number;
  duration_text: string;
  steps: { title: string; detail: string; state: TrialStepState }[];
  bars: { label: string; value: number; value_text: string; main: boolean }[];
  conclusion: string;
}

export const getSkillDefinition = async (id: string): Promise<SkillDefinition> => {
  try {
    return await v1Get<SkillDefinition>(`/skills/${pathId(id)}/definition`);
  } catch (error) {
    if (!notProposal(error)) throw error;
  }
  const raw = await ok<Schemas['SkillDetail']>(
    api.GET('/v1/skills/{id}', { params: { path: { id } } }),
  );
  const version = raw.published_version ?? (raw.versions ?? [])[0];
  const params = (version && 'params_schema' in version ? version.params_schema : {}) as {
    properties?: Record<string, { title?: string }>;
  };
  const steps = (version && 'steps' in version ? version.steps : []) as Array<
    Record<string, unknown>
  >;
  return {
    skill_id: raw.id,
    version_id: version?.id ?? null,
    name: raw.name,
    version: version?.version ?? raw.current_version ?? 0,
    status: (version?.status ?? 'draft') as SkillAuthoringStatus,
    inputs: Object.entries(params.properties ?? {}).map(([name, spec]) => ({
      name,
      description: spec.title ?? name,
    })),
    output_text: version && 'template_md' in version ? String(version.template_md ?? '') : '',
    steps: steps.map((step, index) => ({
      seq: Number(step.seq ?? index + 1),
      text: String(step.text ?? step.name ?? ''),
      tool: typeof step.tool === 'string' ? step.tool : null,
    })),
    instructions: version && 'template_md' in version ? String(version.template_md ?? '') : '',
    guardrails: [],
    owner: { id: raw.owner_id, name: '' },
    scope_label: raw.scope,
    domain_text: raw.category,
    used_by_text: `${raw.run_count_30d} lượt/30 ngày`,
    quality_text: version?.status === 'published' ? 'Đã xuất bản' : 'Chưa kiểm định',
    test_count: 0,
    version_count: (raw.versions ?? []).length,
    quick_test: { params: {}, lines: [] },
    source:
      raw.published_version && version === raw.published_version
        ? sourceOf(raw.published_version)
        : null,
  } as SkillDefinition;
};

/** The trial read is not an A2 resource; use the canonical skill state when it is absent. */
export const getLatestTrial = async (id: string): Promise<SkillTrial | null> => {
  try {
    return await v1Get<SkillTrial>(`/skills/${pathId(id)}/trials/latest`);
  } catch (error) {
    if (!notProposal(error)) throw error;
  }
  const raw = await ok<Schemas['SkillDetail']>(
    api.GET('/v1/skills/{id}', { params: { path: { id } } }),
  );
  const version = (raw.versions ?? []).find((candidate) => candidate.status === 'testing');
  return version
    ? {
        version: version.version,
        duration_text: '',
        steps: [],
        bars: [],
        conclusion: 'Đang chờ kết quả kiểm định.',
      }
    : null;
};

/** A version's own fields over the definition it was edited from; the skill's facts stay. */
function definitionOf(current: SkillDefinition, version: Schemas['VersionView']): SkillDefinition {
  const steps = (Array.isArray(version.steps) ? version.steps : []) as Array<
    Record<string, unknown>
  >;
  return {
    ...current,
    version_id: version.id,
    version: version.version,
    status: version.status as SkillAuthoringStatus,
    steps: steps.map((step, index) => ({
      seq: Number(step.seq ?? index + 1),
      text: String(step.text ?? step.name ?? ''),
      tool: typeof step.tool === 'string' ? step.tool : null,
    })),
    instructions: version.template_md ?? '',
    source: sourceOf(version),
  };
}

/**
 * A2: PATCH /v1/skill-versions/{id} edits the draft in place; a skill whose shown version is not
 * a draft gets a new draft version (POST /v1/skills/{id}/versions), since what was tested or
 * published is not edited (BR-12). The answer is the saved version as the editor shows it.
 */
export const saveSkillDefinition = async (
  current: SkillDefinition,
  body: { instructions: string; steps: SkillStepSpec[] },
): Promise<SkillDefinition> => {
  // Each step keeps what the editor does not show (its kind above all: skill_runs.py runs a
  // step by kind, and a step saved without one fails every run); only text and tool change.
  const before = current.source?.steps ?? [];
  const payload = {
    steps: body.steps.map((step, index) => {
      const old = before.find((s) => Number(s.seq) === step.seq) ?? before[index] ?? {};
      return {
        ...old,
        seq: step.seq,
        text: step.text,
        ...(step.tool !== null || 'tool' in old ? { tool: step.tool } : {}),
      };
    }),
    template_md: body.instructions,
  };
  const saved =
    current.status === 'draft' && current.version_id
      ? await ok<Schemas['VersionView']>(
          api.PATCH('/v1/skill-versions/{id}', {
            params: { path: { id: current.version_id } },
            body: payload,
          }),
        )
      : await ok<Schemas['VersionView']>(
          api.POST('/v1/skills/{id}/versions', {
            params: { path: { id: current.skill_id } },
            body: {
              // A new version of a published skill keeps its parameters, metrics and limits.
              params_schema: (current.source?.params_schema ?? {
                type: 'object',
                properties: {},
              }) as Record<string, unknown>,
              metric_version_ids: current.source?.metric_version_ids ?? [],
              ...(current.source?.sandbox_limits
                ? { sandbox_limits: current.source.sandbox_limits as Schemas['SandboxLimits'] }
                : {}),
              ...payload,
            },
          }),
        );
  return definitionOf(current, saved);
};

type Accepted = Schemas['services__api__modules__skill__schemas__Accepted'];

/** GET /v1/skill-runs/{id} until it finishes or about twenty seconds pass. */
async function awaitRun(accepted: Accepted): Promise<RunWire> {
  const read = () =>
    ok<RunWire>(api.GET('/v1/skill-runs/{id}', { params: { path: { id: accepted.id } } }));
  let run = await read();
  for (let attempt = 0; attempt < 10 && !FINISHED[run.status]; attempt++) {
    await sleep(accepted.poll_after_ms ?? 2000);
    run = await read();
  }
  return run;
}

const seconds = (raw: RunWire) => {
  if (raw.started_at && raw.finished_at)
    return (Date.parse(raw.finished_at) - Date.parse(raw.started_at)) / 1000;
  const steps = raw.steps ?? [];
  return steps.length ? steps.reduce((n, s) => n + (s.duration_ms ?? 0), 0) / 1000 : null;
};

/** The trial as the editor draws it, from the run A2 recorded in mode "try". */
function trialOf(raw: RunWire, version: number): SkillTrial {
  const run = readRun(raw);
  const took = seconds(raw);
  return {
    version,
    duration_text: took === null ? '' : `${Math.round(took)} giây`,
    steps: (raw.steps ?? []).map((step) => {
      const output = (step.output ?? null) as { summary?: unknown } | null;
      return {
        title: step.name,
        detail: typeof output?.summary === 'string' ? output.summary : '',
        // skill_runs.py writes a step as "ok" or "error".
        state: step.status === 'error' || step.status === 'failed' ? 'error' : 'done',
      };
    }),
    bars: [],
    conclusion: run.outcome?.summary ?? run.outcome?.error ?? run.decomposition?.summary ?? '',
  };
}

/**
 * A2: POST /v1/skill-versions/{id}:try {params} (202) runs the draft once as the author's own
 * run, which the library does not count; the run is read back from /v1/skill-runs/{id}.
 */
export const trySkill = async (
  current: SkillDefinition,
  params: Record<string, string>,
): Promise<SkillTrial> => {
  if (current.status !== 'draft' || !current.version_id)
    throw new V1Error(
      409,
      'invalid_state',
      'Chỉ chạy thử được bản nháp; lưu thay đổi thành phiên bản mới rồi chạy thử.',
    );
  const accepted = await ok<Accepted>(
    api.POST('/v1/skill-versions/{id}:try', {
      params: { path: { id: current.version_id } },
      body: { params },
    }),
  );
  return trialOf(await awaitRun(accepted), current.version);
};

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

export const getSessionSkillDraft = async (sessionId: string): Promise<SessionSkillDraft> => {
  try {
    return await v1Get<SessionSkillDraft>(`/workspaces/${pathId(sessionId)}/skill-draft`);
  } catch (error) {
    if (!notProposal(error)) throw error;
    // The proposal route answers 404 when the session has no cells; a missing route does not.
    if (error instanceof V1Error && error.message.includes('ô tính')) throw error;
  }
  const workspace = await ok<Schemas['WorkspaceDetail']>(
    api.GET('/v1/workspaces/{id}', { params: { path: { id: sessionId } } }),
  );
  const tiles = workspace.tiles ?? [];
  return {
    session_id: workspace.id,
    session_title: workspace.title,
    cells: tiles.map((tile, index) => ({
      seq: index + 1,
      title: tile.title ?? tile.kind,
      param_text: null,
      note: null,
      kept: true,
    })),
    name: workspace.title,
    description: workspace.description ?? '',
    params: [],
    test_case_text: tiles.at(-1)?.title ?? '',
  };
};

/** PROPOSAL: POST /v1/skills:from-session — creates the skill as a draft (v1) with its first case. */
export const createSkillFromSession = async (body: {
  session_id: string;
  kept_cells: number[];
  name: string;
  description: string;
}): Promise<{ skill_id: string; version: number }> =>
  (async () => {
    try {
      return await v1Send<{ skill_id: string; version: number }>(
        'POST',
        '/skills:from-session',
        body,
      );
    } catch (error) {
      if (!notProposal(error)) throw error;
    }
    const workspace = await ok<Schemas['WorkspaceDetail']>(
      api.GET('/v1/workspaces/{id}', { params: { path: { id: body.session_id } } }),
    );
    const skill = await ok<Schemas['SkillView']>(
      api.POST('/v1/skills', {
        body: {
          code:
            body.name
              .toLowerCase()
              .replace(/[^a-z0-9]+/g, '_')
              .replace(/^_|_$/g, '') || 'session_skill',
          name: body.name,
          description: body.description,
          category: 'custom',
          scope: 'personal',
          origin: 'from_session',
          origin_ref: body.session_id,
        },
      }),
    );
    const selected = new Set(body.kept_cells);
    const steps = (workspace.tiles ?? [])
      .filter((_, index) => selected.size === 0 || selected.has(index + 1))
      .map((tile, index) => ({
        seq: index + 1,
        text: tile.title ?? tile.kind,
        tool: tile.kind === 'sql' ? 'query.run' : null,
      }));
    if (steps.length === 0) steps.push({ seq: 1, text: body.name, tool: null });
    const version = await ok<Schemas['VersionView']>(
      api.POST('/v1/skills/{id}/versions', {
        params: { path: { id: skill.id } },
        body: { params_schema: { type: 'object', properties: {} }, steps },
      }),
    );
    return { skill_id: skill.id, version: version.version };
  })();

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

export const getSessionAgentDraft = async (sessionId: string): Promise<SessionAgentDraft> => {
  try {
    return await v1Get<SessionAgentDraft>(`/workspaces/${pathId(sessionId)}/agent-draft`);
  } catch (error) {
    if (!notProposal(error)) throw error;
  }
  const workspace = await ok<Schemas['WorkspaceDetail']>(
    api.GET('/v1/workspaces/{id}', { params: { path: { id: sessionId } } }),
  );
  return {
    session_id: workspace.id,
    session_title: workspace.title,
    cells: (workspace.tiles ?? []).map((tile, index) => ({
      seq: index + 1,
      label: tile.title ?? tile.kind,
      kept: true,
    })),
    params: [],
  };
};

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
  /** A2 skill_version.id of the version under test; :test and :publish name it. */
  version_id: string | null;
  skill_name: string;
  version: number;
  status: SkillAuthoringStatus;
  /** null until the version's suite has run */
  ran_at: string | null;
  cases: SkillTestCase[];
  failures: SkillTestFailure[];
}

export const getLatestTestRun = async (id: string): Promise<SkillTestRun> => {
  try {
    return await v1Get<SkillTestRun>(`/skills/${pathId(id)}/test-runs/latest`);
  } catch (error) {
    if (!notProposal(error)) throw error;
  }
  const raw = await ok<Schemas['SkillDetail']>(
    api.GET('/v1/skills/{id}', { params: { path: { id } } }),
  );
  // The version being worked on: the newest one not yet published or under review (versions come
  // newest first). A draft saved after a tested version is the one to test next.
  const version =
    (raw.versions ?? []).find((candidate) => WORKING.has(candidate.status)) ??
    (raw.versions ?? [])[0];
  if (!version) throw new V1Error(404, 'not_found', 'Kỹ năng chưa có phiên bản.');
  // The suite's result is the version's eval run (skill_version.eval_run_id): its results, read
  // against the set's items. Without it the page showed "0/0 đạt" in green after a failing run.
  const scored = version.eval_run_id ? await evalCases(version.eval_run_id) : null;
  return {
    skill_id: raw.id,
    version_id: version.id,
    skill_name: raw.name,
    version: version.version,
    status: version.status as SkillAuthoringStatus,
    ran_at: scored?.ran_at ?? null,
    cases: scored?.cases ?? [],
    failures: [],
  };
};

/** A result's actual value in a line: a string or number as is, an object by its main field. */
const shown = (value: unknown): string => {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string' || typeof value === 'number') return String(value);
  if (typeof value === 'object') {
    const v = value as Record<string, unknown>;
    for (const key of ['answer', 'value', 'text']) {
      if (typeof v[key] === 'string' || typeof v[key] === 'number') return String(v[key]);
    }
  }
  return JSON.stringify(value);
};

/** The version's eval run: one case per result. A2 has no read of a set's items, so a case is
 *  named by its place and shows what the run produced, whether it passed and why not. */
async function evalCases(
  runId: string,
): Promise<{ ran_at: string | null; cases: SkillTestCase[] }> {
  const run = await ok<Schemas['EvalRunDetail']>(
    api.GET('/v1/eval-runs/{id}', { params: { path: { id: runId } } }),
  );
  return {
    ran_at: run.finished_at ?? run.started_at,
    cases: (run.results ?? []).map((result, i) => ({
      id: result.eval_item_id,
      name: `Ca ${i + 1}`,
      period_text: '',
      expected: '',
      actual: shown(result.actual),
      passed: result.passed,
      note: result.cause,
    })),
  };
}

const WORKING = new Set(['draft', 'needs_review', 'testing']);
/** skill/routes.py: :test takes a draft or a version flagged needs_review, nothing else. */
export const TESTABLE = new Set<string>(['draft', 'needs_review']);

const versionOf = (run: SkillTestRun) => {
  if (!run.version_id) throw new V1Error(409, 'invalid_state', 'Kỹ năng chưa có phiên bản.');
  return run.version_id;
};

/**
 * A2: POST /v1/skill-versions/{id}:test (202) sends the version to its eval set on locked data;
 * the eval module scores it from the event. The suite is read again afterwards.
 */
export const runSkillTests = async (run: SkillTestRun): Promise<SkillTestRun> => {
  if (!TESTABLE.has(run.status))
    throw new V1Error(
      409,
      'invalid_state',
      `v${run.version} không phải bản nháp: sửa trong trình soạn để có bản nháp mới rồi chạy kiểm định.`,
    );
  await ok<Accepted>(
    api.POST('/v1/skill-versions/{id}:test', { params: { path: { id: versionOf(run) } } }),
  );
  return getLatestTestRun(run.skill_id);
};

/** PROPOSAL: POST /v1/skills/{id}/test-cases (A2 SkillTestCase). */
export const addSkillTestCase = async (
  run: SkillTestRun,
  body: { name: string; period_text: string; expected: string },
) => {
  try {
    return await v1Send<SkillTestRun>('POST', `/skills/${pathId(run.skill_id)}/test-cases`, body);
  } catch (error) {
    if (!notProposal(error)) throw error;
  }
  // A2 has no test-case route: rerunning the suite would drop the case while saying it was added.
  throw new V1Error(
    409,
    'not_supported',
    'Ca kiểm thử chưa lưu được ở đây: ca nằm trong bộ đánh giá của kỹ năng.',
  );
};

/*
 * A2 has no route that applies the agent's proposed fix (the PROPOSAL :apply-fix answered 405):
 * the screen sends the author to the editor instead, and "Chạy lại" reruns the suite.
 */

/**
 * A2: POST /v1/skill-versions/{id}:publish. A personal skill is published at once; a wider one
 * goes to review (pending_review). The notes on failing cases have no A2 field: the screen holds
 * them as the condition for sending, the reviewer reads the eval run.
 */
export const submitSkill = async (run: SkillTestRun) => {
  // skill/routes.py: :publish takes a version that has been through its tests (testing).
  if (run.status !== 'testing')
    throw new V1Error(
      409,
      'invalid_state',
      `v${run.version} chưa qua kiểm định nên chưa gửi duyệt được.`,
    );
  const published = await ok<Schemas['VersionView']>(
    api.POST('/v1/skill-versions/{id}:publish', { params: { path: { id: versionOf(run) } } }),
  );
  return { status: published.status as SkillAuthoringStatus };
};

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
  /** what the numbers are (a contribution by member, not a cause) and how far they can be trusted */
  method?: string;
  confidence?: string;
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
// A PROPOSAL verb under an existing /{id} route answers 405, not 404 (Starlette matches the GET
// route with id "…:verb"), so both mean "not served".
const notProposal = (e: unknown) => e instanceof V1Error && (e.status === 404 || e.status === 405);

/*
 * PROPOSAL: GET /v1/skills/{id}/run-form — the designed form, with the kind of view its result
 * gets. A skill it does not know is read from A2's GET /v1/skills/{id}: the published version's
 * params_schema (a JSON Schema) gives the parameters, an enum their valid choices, a default the
 * first value, and the result is shown generically.
 */
export const getSkillRunForm = async (id: string): Promise<SkillRunForm> => {
  try {
    return await v1Get<SkillRunForm>(`/skills/${pathId(id)}/run-form`);
  } catch (error) {
    if (!notProposal(error)) throw error;
  }
  const raw = await ok<Schemas['SkillDetail']>(
    api.GET('/v1/skills/{id}', { params: { path: { id } } }),
  );
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
/** worker-agent skills/decomposition.py result_of: the waterfall and contributions it drew. */
function decompositionOf(result: Record<string, unknown>): Decomposition | null {
  const d = result.decomposition as Partial<Decomposition> | undefined;
  if (!d || !d.start || !d.end || !Array.isArray(d.steps) || !Array.isArray(d.contributions))
    return null;
  return {
    summary: typeof result.summary === 'string' ? result.summary : (d.summary ?? ''),
    start: d.start,
    steps: d.steps,
    end: d.end,
    contributions: d.contributions,
    method: typeof result.method === 'string' ? result.method : undefined,
    confidence: typeof result.confidence === 'string' ? result.confidence : undefined,
  };
}

function readRun(raw: RunWire): SkillRun {
  if (raw.display) return raw.display;
  const result = (raw.result ?? {}) as Record<string, unknown>;
  const decomposition = raw.status === 'succeeded' ? decompositionOf(result) : null;
  if (decomposition)
    return {
      id: raw.id,
      skill_id: raw.skill_id,
      version: raw.version,
      kind: 'decomposition',
      params: Object.fromEntries(
        Object.entries((raw.params ?? {}) as Record<string, unknown>).map(([k, v]) => [k, text(v)]),
      ),
      ran_at: raw.started_at ?? raw.created_at,
      decomposition,
      external: null,
      outcome: null,
    };
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

/** The Data Space's decomposition skill ("Phân rã biến động") with a version to run, or null. */
export const findDecompositionSkill = async (): Promise<string | null> => {
  const skills = await ok<Page<Schemas['SkillView']>>(
    api.GET('/v1/skills', {
      params: {
        query: {
          category: 'decomposition',
          page_size: 20,
        },
      },
    }),
  );
  return skills.items.find((s) => s.current_version !== null)?.id ?? null;
};

/** GET /v1/skills/{id}/runs — the viewer's runs, newest first. */
export const listSkillRuns = async (id: string): Promise<Page<SkillRun>> => {
  try {
    const page = await ok<Page<RunWire | SkillRun>>(
      api.GET('/v1/skills/{id}/runs', {
        params: {
          path: { id },
          query: {
            page_size: 50,
          },
        },
      }),
    );
    return {
      ...page,
      items: page.items.map((run) => ('kind' in run && run.kind ? run : readRun(run as RunWire))),
    };
  } catch (error) {
    if (!notProposal(error)) throw error;
    return { items: [], next_page_token: '' };
  }
};

/**
 * A2: POST /v1/skills/{id}/runs (202), under the caller's permissions, then the run read back
 * until it finishes or about twenty seconds pass; a run still queued is returned as running.
 * Every kind runs this way: what the result is (a decomposition, a generic outcome) is read from
 * the run itself. The kind argument is kept for callers that still pass the form's.
 */
export const runSkill = async (
  id: string,
  params: Record<string, string>,
  _kind?: SkillRunKind,
): Promise<SkillRun> => {
  const accepted = await ok<Accepted>(
    api.POST('/v1/skills/{id}/runs', { params: { path: { id } }, body: { params } }),
  );
  return readRun(await awaitRun(accepted));
};

/**
 * "Thêm vào phiên": A2 has no verb on a skill run for it (the PROPOSAL :add-to-session answered
 * 405), so the result goes into a new session made from the run (POST /v1/workspaces,
 * created_from agent_run) as one skill_run tile.
 */
export const addRunToSession = async (runId: string): Promise<{ session_id: string }> => {
  const run = await ok<Schemas['RunDetail']>(
    api.GET('/v1/skill-runs/{id}', { params: { path: { id: runId } } }),
  );
  const workspace = await ok<Schemas['WorkspaceView']>(
    api.POST('/v1/workspaces', {
      body: {
        title: `Kết quả kỹ năng · ${run.version}`,
        created_from: 'agent_run',
        created_from_ref: run.id,
        params: (run.params ?? {}) as Record<string, unknown>,
      },
    }),
  );
  await ok<Schemas['TileView']>(
    api.POST('/v1/workspaces/{id}/tiles', {
      params: { path: { id: workspace.id } },
      body: {
        kind: 'skill_run',
        title: `Kết quả kỹ năng v${run.version}`,
        spec: { skill_run_id: run.id, result: run.result ?? null },
      },
    }),
  );
  return { session_id: workspace.id };
};
