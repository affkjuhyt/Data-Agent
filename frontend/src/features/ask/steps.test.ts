import { describe, expect, it } from 'vitest';
import { applyStepEvent, stepsFromView } from './steps';

const running = { kind: 'step', step_id: 's1', seq: 1, action: 'query', title: 'Truy vấn', target: 'Kho cho: Doanh thu', chips: [{ kind: 'source', label: 'Kho' }], status: 'running', started_at: '2026-11-02T10:00:00Z', duration_ms: null, detail: null, parent: null };

describe('applyStepEvent', () => {
  it('adds a running step, then updates it in place when it ends', () => {
    const one = applyStepEvent([], running);
    expect(one).toHaveLength(1);
    expect(one[0]).toMatchObject({ stepId: 's1', status: 'running', title: 'Truy vấn', detail: null });
    const done = applyStepEvent(one, { ...running, status: 'done', duration_ms: 120, detail: { row_count: 3 } });
    expect(done).toHaveLength(1);
    expect(done[0]).toMatchObject({ status: 'done', durationMs: 120, detail: { row_count: 3 } });
  });

  it('keeps steps in seq order, even when an end arrives before another start', () => {
    const later = applyStepEvent([], { ...running, step_id: 's2', seq: 2, status: 'done', action: 'validate', title: 'Kiểm tra kết quả' });
    const both = applyStepEvent(later, running);
    expect(both.map((s) => s.stepId)).toEqual(['s1', 's2']);
  });

  it('ignores events that are not steps', () => {
    expect(applyStepEvent([], { kind: 'stage', stage: 'planning' })).toEqual([]);
  });
});

describe('stepsFromView', () => {
  it('nests a model call under the step it served', () => {
    const steps = stepsFromView({
      question_id: 'q', run_id: 'r', status: 'succeeded', duration_ms: 900, tokens: 40,
      steps: [
        { step_id: 's1', seq: 1, action: 'write_answer', title: 'Viết câu trả lời', target: '', chips: [], status: 'done', started_at: '2026-11-02T10:00:00Z', duration_ms: 800, detail: { by: 'model' }, summary: null, parent: null },
        { step_id: 's2', seq: 2, action: 'model_call', title: 'Gọi mô hình responder', target: '', chips: [], status: 'done', started_at: '2026-11-02T10:00:00Z', duration_ms: 700, detail: { model: 'responder', input_tokens: 200, output_tokens: 40, latency_ms: 700 }, summary: null, parent: 's1' },
      ],
    });
    expect(steps).toHaveLength(1);
    expect(steps[0].children.map((c) => c.action)).toEqual(['model_call']);
  });

  it('keeps a model call made outside any step at the top level', () => {
    const steps = stepsFromView({
      question_id: 'q', run_id: 'r', status: 'succeeded', duration_ms: null, tokens: 0,
      steps: [{ step_id: 's1', seq: 1, action: 'model_call', title: 'Gọi mô hình planner', target: '', chips: [], status: 'done', started_at: '2026-11-02T10:00:00Z', duration_ms: 10, detail: null, summary: null, parent: null }],
    });
    expect(steps.map((s) => s.action)).toEqual(['model_call']);
  });
});
