/**
 * "Vì sao …?" (UC-03-01, AC-03-01-01): the plan proposes the decomposition skill on the answered
 * metric and month; nothing runs until the person confirms. Confirming runs the Data Space's
 * decomposition skill (POST /v1/skills/{id}/runs, worker-agent skill_runs.py) and draws its
 * waterfall, contributions, method and confidence in the answer.
 */
import { useState } from 'react';
import { Button } from '../../../design-system/Button';
import { Notice } from '../../../design-system/Notice';
import { findDecompositionSkill, runSkill, type SkillRun } from '../../../lib/v1/skillAuthoring';
import { Waterfall } from '../../skill-authoring/Waterfall';
import type { DecompositionProposal } from '../dataAnswer';

const month = (m: string) => {
  const [y, mm] = m.split('-');
  return mm && y ? `${mm}/${y}` : m;
};

export function DecompositionCard({ proposal }: { proposal: DecompositionProposal }) {
  const [state, setState] = useState<
    { kind: 'idle' } | { kind: 'running' } | { kind: 'done'; run: SkillRun } | { kind: 'error'; text: string }
  >({ kind: 'idle' });

  const run = async () => {
    setState({ kind: 'running' });
    try {
      const skill = await findDecompositionSkill();
      if (!skill) {
        setState({ kind: 'error', text: 'Data Space chưa có kỹ năng Phân rã biến động được phát hành.' });
        return;
      }
      const result = await runSkill(skill, proposal.params, 'generic');
      if (result.decomposition) setState({ kind: 'done', run: result });
      else if (result.outcome?.status === 'running')
        setState({ kind: 'error', text: 'Kỹ năng vẫn đang chạy; xem kết quả ở màn Kỹ năng khi xong.' });
      else setState({ kind: 'error', text: result.outcome?.error ?? 'Kỹ năng không trả kết quả phân rã.' });
    } catch (e) {
      setState({ kind: 'error', text: e instanceof Error ? e.message : 'Không chạy được kỹ năng.' });
    }
  };

  const d = state.kind === 'done' ? state.run.decomposition : null;
  return (
    <section aria-label="Phân rã biến động" className="flex flex-col gap-da-md rounded-md border border-border bg-card px-da-xl py-da-lg">
      <div className="flex flex-wrap items-center gap-da-md">
        <div className="flex min-w-0 flex-1 flex-col gap-da-xs">
          <span className="text-foreground da-body-strong">{proposal.explain}</span>
          <span className="text-muted-foreground da-caption">
            {month(proposal.params.base_period)} → {month(proposal.params.period)}
          </span>
        </div>
        {state.kind !== 'done' && (
          <Button kind="primary" loading={state.kind === 'running'} onClick={() => void run()}>
            Chạy phân rã
          </Button>
        )}
      </div>
      {state.kind === 'error' && <Notice tone="error">{state.text}</Notice>}
      {d && (
        <>
          <p className="text-foreground da-reading">{d.summary}</p>
          <Waterfall start={d.start} steps={d.steps} end={d.end} />
          {(d.method || d.confidence) && (
            <p className="text-muted-foreground da-body-small">
              {[d.method, d.confidence && `Mức tin cậy: ${d.confidence}.`].filter(Boolean).join(' ')}
            </p>
          )}
        </>
      )}
    </section>
  );
}
