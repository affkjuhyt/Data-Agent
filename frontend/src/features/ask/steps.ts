/**
 * The steps of an Ask turn (docs/superpowers/specs/2026-11-02-ask-chat-redesign-design.md, "Step
 * contract"): the worker publishes each step as an `activity` event of kind "step" while the turn
 * runs, and GET /v1/questions/{id}/steps serves the same steps once it has ended. Both become
 * TurnStep; a model call is drawn under the step it served.
 */
import type { components } from '../../lib/v1/schema';

type QuestionStepsView = components['schemas']['QuestionStepsView'];
type StepView = components['schemas']['TurnStepView'];

export type StepStatus = 'running' | 'done' | 'failed' | 'skipped';

export interface TurnStep {
  stepId: string;
  seq: number;
  action: string;
  title: string;
  target: string;
  chips: { kind: string; label: string }[];
  status: StepStatus;
  startedAt: string;
  durationMs: number | null;
  detail: Record<string, unknown> | null;
  /** Why a step failed or was set aside, in words for the reader. */
  summary: string | null;
  parent: string | null;
  children: TurnStep[];
}

const STATUSES: readonly StepStatus[] = ['running', 'done', 'failed', 'skipped'];

const str = (value: unknown, fallback = '') => (typeof value === 'string' ? value : fallback);
const record = (value: unknown) =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
const chipsOf = (value: unknown) =>
  Array.isArray(value)
    ? value.flatMap((chip) => {
        const c = record(chip);
        return c ? [{ kind: str(c.kind), label: str(c.label) }] : [];
      })
    : [];

function fromWire(data: Record<string, unknown>): TurnStep {
  const status = STATUSES.includes(data.status as StepStatus) ? (data.status as StepStatus) : 'done';
  return {
    stepId: str(data.step_id),
    seq: typeof data.seq === 'number' ? data.seq : 0,
    action: str(data.action),
    title: str(data.title, str(data.action)),
    target: str(data.target),
    chips: chipsOf(data.chips),
    status,
    startedAt: str(data.started_at),
    durationMs: typeof data.duration_ms === 'number' ? data.duration_ms : null,
    detail: record(data.detail),
    summary: typeof data.summary === 'string' ? data.summary : null,
    parent: typeof data.parent === 'string' ? data.parent : null,
    children: [],
  };
}

const bySeq = (a: TurnStep, b: TurnStep) => a.seq - b.seq;

/** One `activity` event applied to the turn's steps: a step is added when first seen and updated
 *  in place when it ends. Events of any other kind leave the steps as they are. */
export function applyStepEvent(steps: TurnStep[], data: Record<string, unknown>): TurnStep[] {
  if (data.kind !== 'step' || typeof data.step_id !== 'string') return steps;
  const next = fromWire(data);
  const at = steps.findIndex((s) => s.stepId === next.stepId);
  if (at < 0) return [...steps, next].sort(bySeq);
  const merged = steps.slice();
  merged[at] = { ...steps[at], ...next, children: steps[at].children };
  return merged;
}

/** The persisted steps of an ended turn, model calls nested under the step they served. */
export function stepsFromView(view: QuestionStepsView): TurnStep[] {
  const all = (view.steps ?? []).map((s: StepView) => fromWire(s as unknown as Record<string, unknown>));
  const top = all.filter((s) => !s.parent || !all.some((p) => p.stepId === s.parent));
  for (const step of top) step.children = all.filter((c) => c.parent === step.stepId).sort(bySeq);
  return top.sort(bySeq);
}
