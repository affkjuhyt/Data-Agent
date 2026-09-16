/**
 * The clarification card — the smallest, highest-value piece of the roadmap.
 *
 * Two sources ask before answering: deterministic rules in the runtime
 * (backend/libs/semantic/ambiguity.py, options are slot values) and the
 * orchestrator's input check (options suggested by the model). Either way each
 * question gets its options as buttons plus an "Other…" that opens a box for
 * the user's own answer — a question without options shows the box directly.
 * What happens on answer depends on `clarification.kind` — see types.ts.
 *
 * One answer per question is collected and the reply goes out once the last
 * one is given — answering only the first used to leave the others to be asked
 * again. A pick is sent as `{reason, value}` so the backend writes it straight
 * onto the slots; an "Other" answer is free text and travels as text only.
 */
import { useState } from 'react';
import { HelpCircle, PenLine, Send } from 'lucide-react';
import { optionLabel, reasonLabel } from '../labels';
import type { Clarification, ClarificationAnswer, ClarificationQuestion, ClarificationReply } from '../types';

interface Pick {
  value: string;
  /** typed under "Other" rather than one of the options */
  custom: boolean;
}

export function ClarificationCard({
  clarification,
  disabled,
  onAnswer,
}: {
  clarification: Clarification;
  disabled?: boolean;
  onAnswer: (reply: ClarificationReply) => void;
}) {
  const listed = clarification.questions.filter(q => q.text || q.options?.length);
  // A card with nothing listed still needs somewhere to type the answer.
  const questions: ClarificationQuestion[] = listed.length ? listed : [{ text: '' }];
  const many = questions.length > 1;

  const [picked, setPicked] = useState<Record<number, Pick>>({});
  const [otherOpen, setOtherOpen] = useState<Record<number, boolean>>({});
  const [drafts, setDrafts] = useState<Record<number, string>>({});

  const labelOf = (p: Pick) => (p.custom ? p.value : optionLabel(p.value));

  const reply = (picks: Record<number, Pick>): ClarificationReply => {
    const answers: ClarificationAnswer[] = questions.flatMap((q, i) => {
      const p = picks[i];
      return p && !p.custom && q.reason ? [{ reason: q.reason, value: p.value }] : [];
    });
    // Free-form questions answered together: say which answer is which, or the
    // agent gets "A, B" with no way to tell what each one answers.
    if (clarification.kind === 'resume' && many) {
      return { text: questions.map((q, i) => `${q.text} → ${labelOf(picks[i])}`).join('; '), answers };
    }
    return { text: questions.map((_, i) => labelOf(picks[i])).join(', '), answers };
  };

  const choose = (index: number, pick: Pick) => {
    if (disabled) return;
    const next = { ...picked, [index]: pick };
    if (questions.every((_, i) => next[i])) {
      setPicked({});
      setOtherOpen({});
      setDrafts({});
      onAnswer(reply(next));
      return;
    }
    setPicked(next);
    if (pick.custom) setOtherOpen(prev => ({ ...prev, [index]: false }));
  };

  const submitDraft = (index: number) => {
    const text = (drafts[index] ?? '').trim();
    if (!text || disabled) return;
    setDrafts(prev => ({ ...prev, [index]: '' }));
    choose(index, { value: text, custom: true });
  };

  return (
    <div
      data-testid="clarification-card"
      role="group"
      aria-label="Needs clarification before answering"
      style={{ marginTop: 10, borderRadius: 10, border: '1px solid var(--ab-accent)', background: 'var(--ab-accent-bg)', padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 12 }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <HelpCircle size={13} style={{ color: 'var(--ab-accent)' }} />
        <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--ab-accent)' }}>
          {clarification.kind === 'resume' ? 'Waiting for your answer' : 'Needs clarification before running'}
        </span>
        <span style={{ fontSize: 10.5, color: 'var(--ab-muted)', marginLeft: 'auto' }}>
          {many ? `One answer per question · ${Object.keys(picked).length}/${questions.length}` : 'No query has run yet'}
        </span>
      </div>

      {questions.map((q, i) => {
        const options = q.options ?? [];
        const pick = picked[i];
        const boxOpen = !options.length || otherOpen[i];
        return (
          <div key={i} data-testid="clarification-question" style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {q.reason && <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--ab-muted)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{reasonLabel(q.reason)}</span>}
            {q.text && <div style={{ fontSize: 13, color: 'var(--ab-fg)', lineHeight: 1.5 }}>{q.text}</div>}

            {options.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {options.map(opt => {
                  const selected = !!pick && !pick.custom && pick.value === opt;
                  return (
                    <button
                      key={opt}
                      type="button"
                      disabled={disabled}
                      onClick={() => choose(i, { value: opt, custom: false })}
                      className="ab-option"
                      aria-pressed={selected}
                      title={opt !== optionLabel(opt) ? opt : undefined}
                      style={selected ? { borderColor: 'var(--ab-accent)', color: 'var(--ab-accent)', fontWeight: 600 } : undefined}
                    >
                      {optionLabel(opt)}
                    </button>
                  );
                })}
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => setOtherOpen(prev => ({ ...prev, [i]: !prev[i] }))}
                  className="ab-option"
                  aria-expanded={!!otherOpen[i]}
                  aria-pressed={!!pick?.custom}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 4, ...(pick?.custom ? { borderColor: 'var(--ab-accent)', color: 'var(--ab-accent)', fontWeight: 600 } : {}) }}
                >
                  <PenLine size={10} />
                  {pick?.custom ? `Other: ${pick.value}` : 'Other…'}
                </button>
              </div>
            )}

            {boxOpen && (
              <div style={{ display: 'flex', gap: 6, alignItems: 'center', background: 'var(--ab-bg)', border: '1px solid var(--ab-border)', borderRadius: 8, padding: '4px 4px 4px 10px' }}>
                <input
                  value={drafts[i] ?? ''}
                  disabled={disabled}
                  autoFocus={options.length > 0}
                  onChange={e => { const v = e.target.value; setDrafts(prev => ({ ...prev, [i]: v })); }}
                  onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); submitDraft(i); } }}
                  placeholder={pick?.custom ? pick.value : options.length ? 'Type your own answer…' : 'Answer in your own words…'}
                  aria-label={many ? `Custom answer ${i + 1}` : 'Custom answer'}
                  style={{ flex: 1, background: 'transparent', border: 'none', outline: 'none', fontSize: 12.5, color: 'var(--ab-fg)', fontFamily: 'inherit' }}
                />
                <button
                  type="button"
                  className="ab-btn ab-btn-primary"
                  onClick={() => submitDraft(i)}
                  disabled={disabled || !(drafts[i] ?? '').trim()}
                  aria-label={many ? `Send answer ${i + 1}` : 'Send answer'}
                >
                  <Send size={11} />
                </button>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
