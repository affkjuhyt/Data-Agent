/**
 * The clarification card — the smallest, highest-value piece of the roadmap.
 *
 * Two sources ask before answering: deterministic rules in the runtime
 * (backend/libs/semantic/ambiguity.py, options are slot values) and the
 * orchestrator's input check (options suggested by the model). Either way each
 * question gets its options as buttons plus a "Khác…" that opens a box for
 * the user's own answer — a question without options shows the box directly.
 * What happens on answer depends on `clarification.kind` — see types.ts.
 *
 * One answer per question is collected and the reply goes out once the last
 * one is given — answering only the first used to leave the others to be asked
 * again. A pick is sent as `{reason, value}` so the backend writes it straight
 * onto the slots; an "Other" answer is free text and travels as text only.
 */
import { useState } from 'react';
import { Icon } from '../../../design-system/Icon';
import { cn } from '../../../lib/utils';
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

  const optionClass = (selected: boolean) => cn(
    'inline-flex items-center gap-da-xs rounded-sm border bg-card px-[10px] py-[5px] da-body-small transition-colors duration-[120ms] ease-out',
    'disabled:cursor-not-allowed disabled:opacity-50',
    selected ? 'border-primary bg-accent text-primary' : 'border-border text-foreground hover:border-border-strong hover:bg-subtle',
  );

  return (
    <div
      data-testid="clarification-card"
      role="group"
      aria-label="Cần làm rõ trước khi trả lời"
      className="flex flex-col gap-da-lg rounded-lg border border-border bg-accent px-da-lg py-da-md"
    >
      <div className="flex items-center gap-da-sm">
        <Icon name="info" size={14} className="text-primary" />
        <span className="text-primary da-label">
          {clarification.kind === 'resume' ? 'Đang chờ bạn trả lời' : 'Cần làm rõ trước khi chạy'}
        </span>
        <span className="ml-auto text-muted-foreground da-caption">
          {many ? `Mỗi câu một trả lời · ${Object.keys(picked).length}/${questions.length}` : 'Chưa chạy truy vấn nào'}
        </span>
      </div>

      {questions.map((q, i) => {
        const options = q.options ?? [];
        const pick = picked[i];
        const boxOpen = !options.length || otherOpen[i];
        return (
          <div key={i} data-testid="clarification-question" className="flex flex-col gap-da-sm">
            {q.reason && <span className="text-muted-foreground da-caption">{reasonLabel(q.reason)}</span>}
            {q.text && <p className="m-0 text-foreground da-body">{q.text}</p>}

            {options.length > 0 && (
              <div className="flex flex-wrap gap-da-sm">
                {options.map(opt => {
                  const selected = !!pick && !pick.custom && pick.value === opt;
                  return (
                    <button
                      key={opt}
                      type="button"
                      disabled={disabled}
                      onClick={() => choose(i, { value: opt, custom: false })}
                      className={optionClass(selected)}
                      aria-pressed={selected}
                      title={opt !== optionLabel(opt) ? opt : undefined}
                    >
                      {optionLabel(opt)}
                    </button>
                  );
                })}
                <button
                  type="button"
                  disabled={disabled}
                  onClick={() => setOtherOpen(prev => ({ ...prev, [i]: !prev[i] }))}
                  className={optionClass(!!pick?.custom)}
                  aria-expanded={!!otherOpen[i]}
                  aria-pressed={!!pick?.custom}
                >
                  <Icon name="edit" size={12} />
                  {pick?.custom ? `Khác: ${pick.value}` : 'Khác…'}
                </button>
              </div>
            )}

            {boxOpen && (
              <div className="flex items-center gap-da-sm rounded-md border border-border bg-card py-da-xs pl-da-lg pr-da-xs focus-within:border-ring">
                <input
                  value={drafts[i] ?? ''}
                  disabled={disabled}
                  autoFocus={options.length > 0}
                  onChange={e => { const v = e.target.value; setDrafts(prev => ({ ...prev, [i]: v })); }}
                  onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); submitDraft(i); } }}
                  placeholder={pick?.custom ? pick.value : options.length ? 'Tự nhập câu trả lời…' : 'Trả lời theo cách của bạn…'}
                  aria-label={many ? `Câu trả lời khác ${i + 1}` : 'Câu trả lời khác'}
                  className="min-w-0 flex-1 bg-transparent text-foreground outline-none da-body-small placeholder:text-muted-foreground focus-visible:shadow-none"
                />
                <button
                  type="button"
                  onClick={() => submitDraft(i)}
                  disabled={disabled || !(drafts[i] ?? '').trim()}
                  aria-label={many ? `Gửi câu trả lời ${i + 1}` : 'Gửi câu trả lời'}
                  className="inline-flex size-7 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground hover:bg-primary-hover disabled:bg-subtle disabled:text-muted-foreground"
                >
                  <Icon name="arrow-up" size={14} />
                </button>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
