/**
 * The card shown instead of an answer when the turn was refused with a code
 * (`__refusal`, backend libs/contracts/v1/errors.py). The sentence comes from
 * the backend; what the card offers depends only on `next_action`:
 *   retry           → "Thử lại" sends the same question again
 *   rephrase        → a hint, and "Diễn đạt lại" puts the question back in the box
 *   contact_steward → who to ask; nothing to click
 * Never on the message text: wording changes, codes do not.
 *
 * The Playground also shows the code and the lookup id (`showDetail`), which
 * is what a builder quotes to whoever operates the platform. The Ask screen
 * keeps them out of the way of the person who asked.
 */
import { AlertTriangle, PenLine, RotateCcw, UserRound } from 'lucide-react';
import type { Refusal } from '../types';
import { CopyButton } from './EvidenceStrip';

interface RefusalCardProps {
  refusal: Refusal;
  onRetry?: () => void;
  onRephrase?: () => void;
  showDetail?: boolean;
  disabled?: boolean;
}

const HINTS: Record<string, string> = {
  rephrase: 'Hãy diễn đạt lại câu hỏi: nêu rõ chỉ số, kỳ thời gian và phạm vi cần xem.',
  contact_steward: 'Liên hệ người phụ trách dữ liệu hoặc agent này để được hỗ trợ.',
};

export function RefusalCard({ refusal, onRetry, onRephrase, showDetail, disabled }: RefusalCardProps) {
  const action = refusal.next_action;
  const hint = HINTS[action];

  return (
    <div
      data-testid="refusal-card"
      role="alert"
      style={{
        borderRadius: 10, border: '1px solid var(--ab-warn)', background: 'var(--ab-warn-bg)',
        padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 8,
      }}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8 }}>
        <AlertTriangle size={14} style={{ color: 'var(--ab-warn)', flexShrink: 0, marginTop: 2 }} />
        <div style={{ fontSize: 13, lineHeight: 1.5, color: 'var(--ab-fg)' }}>
          {refusal.message || 'Hệ thống chưa trả lời được câu hỏi này.'}
        </div>
      </div>

      {!!refusal.issues?.length && (
        <ul style={{ margin: '0 0 0 22px', padding: 0, fontSize: 12, color: 'var(--ab-fg-2)', lineHeight: 1.5 }}>
          {refusal.issues.map((issue, i) => <li key={i}>{issue}</li>)}
        </ul>
      )}

      {hint && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginLeft: 22, fontSize: 12, color: 'var(--ab-fg-2)' }}>
          {action === 'contact_steward' && <UserRound size={12} style={{ color: 'var(--ab-muted)' }} />}
          {hint}
        </div>
      )}

      {((action === 'retry' && onRetry) || (action === 'rephrase' && onRephrase) || showDetail) && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginLeft: 22, flexWrap: 'wrap' }}>
          {action === 'retry' && onRetry && (
            <button type="button" className="ab-btn ab-btn-primary" onClick={onRetry} disabled={disabled}>
              <RotateCcw size={11} /> Thử lại
            </button>
          )}
          {action === 'rephrase' && onRephrase && (
            <button type="button" className="ab-btn" onClick={onRephrase} disabled={disabled}>
              <PenLine size={11} /> Diễn đạt lại
            </button>
          )}
          {showDetail && (
            <span data-testid="refusal-detail" style={{ display: 'inline-flex', alignItems: 'center', gap: 6, marginLeft: 'auto', fontSize: 10.5, color: 'var(--ab-muted)' }}>
              <span style={{ fontFamily: 'var(--font-mono)' }}>{refusal.code}</span>
              {refusal.detail_id && (
                <>
                  <span>· Mã tra cứu</span>
                  <span style={{ fontFamily: 'var(--font-mono)' }}>{refusal.detail_id}</span>
                  <CopyButton text={refusal.detail_id} label="Sao chép" />
                </>
              )}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
