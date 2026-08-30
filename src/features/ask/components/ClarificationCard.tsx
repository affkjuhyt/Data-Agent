/**
 * The clarification card — the smallest, highest-value piece of the roadmap.
 *
 * The backend already decides deterministically when a question cannot be
 * answered without guessing (libs/semantic/ambiguity.py) and sends the
 * question WITH clickable options. Until now the UI blanked the message.
 * This renders the question(s), one button per option (slot keys mapped to
 * Vietnamese), and a free-text fallback. What happens on answer depends on
 * `clarification.kind` — see types.ts.
 */
import { useState } from 'react';
import { HelpCircle, Send } from 'lucide-react';
import { optionLabel, reasonLabel } from '../labels';
import type { Clarification } from '../types';

export function ClarificationCard({
  clarification,
  disabled,
  onAnswer,
}: {
  clarification: Clarification;
  disabled?: boolean;
  onAnswer: (answer: string) => void;
}) {
  const [custom, setCustom] = useState('');
  const questions = clarification.questions.filter(q => q.text || q.options?.length);
  const submitCustom = () => {
    const t = custom.trim();
    if (!t || disabled) return;
    setCustom('');
    onAnswer(t);
  };

  return (
    <div
      data-testid="clarification-card"
      role="group"
      aria-label="Cần làm rõ trước khi trả lời"
      style={{ marginTop: 10, borderRadius: 10, border: '1px solid var(--ab-accent)', background: 'var(--ab-accent-bg)', padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 10 }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <HelpCircle size={13} style={{ color: 'var(--ab-accent)' }} />
        <span style={{ fontSize: 10.5, fontWeight: 700, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--ab-accent)' }}>
          {clarification.kind === 'resume' ? 'Đang chờ bạn trả lời' : 'Cần làm rõ trước khi chạy'}
        </span>
        <span style={{ fontSize: 10.5, color: 'var(--ab-muted)', marginLeft: 'auto' }}>Chưa chạy truy vấn nào</span>
      </div>

      {questions.map((q, i) => (
        <div key={i} style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          {q.reason && <span style={{ fontSize: 10, fontWeight: 700, color: 'var(--ab-muted)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{reasonLabel(q.reason)}</span>}
          {q.text && <div style={{ fontSize: 13, color: 'var(--ab-fg)', lineHeight: 1.5 }}>{q.text}</div>}
          {q.options && q.options.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {q.options.map(opt => (
                <button
                  key={opt}
                  type="button"
                  disabled={disabled}
                  onClick={() => onAnswer(optionLabel(opt))}
                  className="ab-option"
                  title={opt !== optionLabel(opt) ? opt : undefined}
                >
                  {optionLabel(opt)}
                </button>
              ))}
            </div>
          )}
        </div>
      ))}

      <div style={{ display: 'flex', gap: 6, alignItems: 'center', background: 'var(--ab-bg)', border: '1px solid var(--ab-border)', borderRadius: 8, padding: '4px 4px 4px 10px' }}>
        <input
          value={custom}
          disabled={disabled}
          onChange={e => setCustom(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); submitCustom(); } }}
          placeholder="Hoặc trả lời theo cách khác…"
          aria-label="Trả lời khác"
          style={{ flex: 1, background: 'transparent', border: 'none', outline: 'none', fontSize: 12.5, color: 'var(--ab-fg)', fontFamily: 'inherit' }}
        />
        <button type="button" className="ab-btn ab-btn-primary" onClick={submitCustom} disabled={disabled || !custom.trim()} aria-label="Gửi câu trả lời">
          <Send size={11} />
        </button>
      </div>
    </div>
  );
}
