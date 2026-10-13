import { Icon, type IconName } from '../../design-system/Icon';
import type { V1State } from '../../lib/v1/useV1';
import type { ExploreExplanation, ExploreResult, NextStep } from '../../lib/v1/explore';

/*
 * "Agent giải thích" (2140:15691), the 340px right column of Khám phá: what the agent reads in
 * the result (a bordered box; "Agent đang đọc kết quả…" while it reads, as in "Khám phá · Đang
 * tải"), the next steps it suggests, and a success line when the figures match a filed report.
 * The reading is the decomposition skill run on the result (SCR-06); A3 has no next steps nor
 * reconciliation for an explorer result yet, so those two blocks stay hidden until it does.
 */
const STEP_ICON: Record<NextStep['kind'], IconName> = {
  drill: 'layers',
  rows: 'file',
  lineage: 'eye',
  watch: 'bell',
};

export function ExplainPanel({
  result,
  explanation,
  onStep,
}: {
  result: ExploreResult | undefined;
  /** null: no decomposition skill in the Data Space, or its run had nothing to say. */
  explanation: V1State<ExploreExplanation | null>;
  onStep: (step: NextStep) => void;
}) {
  return (
    <div className="flex flex-col gap-da-lg">
      <h2 className="flex items-center gap-da-md text-foreground da-heading-3">
        <Icon name="agent" />
        Agent giải thích
      </h2>

      <div
        aria-live="polite"
        className="flex flex-col gap-da-sm rounded-md border border-border p-da-md text-foreground da-body-small"
      >
        {!result ? (
          <p className="text-muted-foreground">Thêm chỉ số và chiều để agent đọc kết quả.</p>
        ) : explanation.data === null && !explanation.loading ? (
          <p className="text-muted-foreground">
            Data Space này chưa có kỹ năng phân rã biến động để agent đọc kết quả.
          </p>
        ) : explanation.data && !explanation.loading ? (
          <>
            <p className="da-body-strong">{explanation.data.headline}</p>
            <p>{explanation.data.body}</p>
          </>
        ) : explanation.error && !explanation.loading ? (
          <p className="text-muted-foreground">
            Agent chưa đọc được kết quả.{' '}
            <button
              type="button"
              onClick={explanation.reload}
              className="text-primary da-body-strong"
            >
              Thử lại
            </button>
          </p>
        ) : (
          <p className="text-muted-foreground">Agent đang đọc kết quả…</p>
        )}
      </div>

      {result && result.next_steps.length > 0 && (
        <>
          <p className="text-muted-foreground da-label">Bước tiếp theo</p>
          <ul className="flex flex-col gap-da-lg">
            {result.next_steps.map((step) => (
              <li key={`${step.kind}-${step.title}`}>
                <button
                  type="button"
                  onClick={() => onStep(step)}
                  className="flex w-full items-start gap-[10px] rounded-md border border-border px-da-md py-da-sm text-left hover:border-border-strong"
                >
                  <Icon name={STEP_ICON[step.kind]} className="mt-[1px] text-foreground" />
                  <span className="flex min-w-0 flex-1 flex-col">
                    <span className="text-foreground da-body-strong">{step.title}</span>
                    <span className="text-muted-foreground da-caption">{step.detail}</span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </>
      )}

      {result?.reconciliation_text && (
        <p className="flex items-start gap-da-md rounded-md bg-success-subtle px-da-md py-da-sm text-foreground da-caption">
          <Icon name="check" className="text-success" />
          {result.reconciliation_text}
        </p>
      )}
    </div>
  );
}
