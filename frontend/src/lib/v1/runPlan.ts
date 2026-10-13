import { pathId, v1Send } from './client';
import type { components } from './schema';

/*
 * "P13 · Can thiệp kế hoạch" (2070:3221, UC-13-06): editing a stopped run's plan. A2 gives
 * PUT /v1/runs/{id}/plan {steps*, edit_note} → a new plan version, rerun from the first changed
 * step; 409 invalid_state when the run is not stopped at a step boundary.
 */

/** A step of the edited plan. `id` is absent on a step added here; `removed` drops one not yet run. */
export interface PlanStepInput {
  id?: string;
  title: string;
  agent_name: string;
  removed?: boolean;
}

export interface PlanEdit {
  steps: PlanStepInput[];
  edit_note?: string;
}

/** A2 returns the Plan; the screen only needs its version. */
export const updateRunPlan = async (runId: string, edit: PlanEdit) =>
  v1Send<components['schemas']['services__api__modules__agent__schemas__PlanView']>(
    'PUT',
    `/runs/${pathId(runId)}/plan`,
    edit,
  );
