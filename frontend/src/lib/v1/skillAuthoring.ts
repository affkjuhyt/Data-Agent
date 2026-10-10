import { pathId, v1Get, v1Send, type Page } from './client';
import type { SkillStatus } from './skills';

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

export type SkillRunKind = 'decomposition' | 'external_factors';

export interface SkillRunParam {
  key: string;
  label: string;
  value: string;
  /** Only the valid choices: dimensions are those the metric allows. */
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
}

export const getSkillRunForm = async (id: string) =>
  v1Get<SkillRunForm>(`/skills/${pathId(id)}/run-form`);

/** PROPOSAL: GET /v1/skills/{id}/runs — the viewer's runs, newest first. */
export const listSkillRuns = async (id: string) =>
  v1Get<Page<SkillRun>>(`/skills/${pathId(id)}/runs`, { page_size: 1 });

/** PROPOSAL: POST /v1/skills/{id}:run {params} — runs under the caller's permissions. */
export const runSkill = async (id: string, params: Record<string, string>) =>
  v1Send<SkillRun>('POST', `/skills/${pathId(id)}:run`, { params });

/**
 * PROPOSAL: POST /v1/skill-runs/{id}:add-to-session — adds the result as a tile to the session
 * the run came from, or to a new session when it has none.
 */
export const addRunToSession = async (runId: string) =>
  v1Send<{ session_id: string }>('POST', `/skill-runs/${pathId(runId)}:add-to-session`, {});
