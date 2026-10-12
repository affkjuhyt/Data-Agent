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
import { Button } from '../../../design-system/Button';
import { Icon } from '../../../design-system/Icon';
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
      className="flex flex-col gap-da-md rounded-lg border border-border bg-warning-subtle px-da-lg py-da-md"
    >
      <div className="flex items-start gap-[10px]">
        <Icon name="alert" className="mt-0.5 text-warning" />
        <p className="m-0 min-w-0 flex-1 text-foreground da-body">
          {refusal.message || 'Hệ thống chưa trả lời được câu hỏi này.'}
        </p>
      </div>

      {!!refusal.issues?.length && (
        <ul className="m-0 ml-[26px] list-disc pl-da-lg text-foreground da-body-small">
          {refusal.issues.map((issue, i) => <li key={i}>{issue}</li>)}
        </ul>
      )}

      {hint && (
        <div className="ml-[26px] flex items-center gap-da-sm text-muted-foreground da-body-small">
          {action === 'contact_steward' && <Icon name="users" size={14} />}
          {hint}
        </div>
      )}

      {((action === 'retry' && onRetry) || (action === 'rephrase' && onRephrase) || showDetail) && (
        <div className="ml-[26px] flex flex-wrap items-center gap-da-sm">
          {action === 'retry' && onRetry && (
            <Button kind="primary" size="sm" icon="refresh" onClick={onRetry} disabled={disabled}>
              Thử lại
            </Button>
          )}
          {action === 'rephrase' && onRephrase && (
            <Button size="sm" icon="edit" onClick={onRephrase} disabled={disabled}>
              Diễn đạt lại
            </Button>
          )}
          {showDetail && (
            <span data-testid="refusal-detail" className="ml-auto inline-flex items-center gap-da-sm text-muted-foreground da-caption">
              <span className="da-mono">{refusal.code}</span>
              {refusal.detail_id && (
                <>
                  <span>· Mã tra cứu</span>
                  <span className="da-mono">{refusal.detail_id}</span>
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
