import type { VerificationFrame } from '../types';

/*
 * ConfidenceMeter (docs/v3/01-agent-core-and-ui.md §5.5, R1-14): beside the trust label of an answer
 * whose query the model wrote, how far its ways of writing it agreed. That share is the
 * confidence of the quality gate (sqlgen vote): at least 4 of 6 answers, 3 of 6 asks to check it.
 * A governed or verified answer has no vote, so it shows none.
 */
const SEGMENTS = 6;

export function ConfidenceMeter({ verification }: { verification: VerificationFrame }) {
  const { confidence, consensus } = verification;
  if (confidence === null) return null;
  const shown = Math.round(confidence * 100);
  const filled = Math.round(confidence * SEGMENTS);
  const tone = confidence >= 4 / 6 ? 'bg-success' : confidence >= 3 / 6 ? 'bg-warning' : 'bg-error';
  const said = consensus
    ? `${consensus.agreeing}/${consensus.written} cách viết truy vấn cho cùng kết quả`
    : `Độ tin cậy ${shown}%`;
  return (
    <span
      data-testid="confidence-meter"
      role="meter"
      aria-label="Độ tin cậy"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={shown}
      aria-valuetext={said}
      title={said}
      className="inline-flex items-center gap-da-sm text-muted-foreground da-caption"
    >
      <span className="inline-flex gap-[2px]" aria-hidden>
        {Array.from({ length: SEGMENTS }, (_, i) => (
          <span key={i} className={`h-[8px] w-[6px] rounded-[1px] ${i < filled ? tone : 'bg-border'}`} />
        ))}
      </span>
      Độ tin cậy {shown}%
    </span>
  );
}
