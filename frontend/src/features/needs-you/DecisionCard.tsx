import { useState } from 'react';
import { AgentChip } from '../../design-system/AgentChip';
import { Button } from '../../design-system/Button';
import { Icon } from '../../design-system/Icon';
import { StatusDot } from '../../design-system/StatusDot';
import { Tooltip } from '../../design-system/Tooltip';
import { cn } from '../../lib/utils';
import type { HitlRequest } from '../../lib/v1/types';
import { DECISION, decisionKindOf, dueOf } from './decision';

/*
 * One request on "v3.1 · Cần bạn" (2149:15824): card radius 10, 12×16 padding, 16px gap; a 40px
 * slot with the kind's icon; then the kind as a status dot and the deadline, the question in
 * DA/Heading 2, "Vì sao agent hỏi: …", the choices with their consequence (the agent's default
 * preselected, 1.5px primary border), and the asking agent with the actions on the right.
 *
 * Approve and compare open their detail page (Duyệt đề xuất, So sánh hai nguồn); review opens
 * the report screen, not built yet, so its button says so instead of doing nothing.
 */
const DETAIL_PENDING = 'Màn chi tiết của việc này đang được làm.';

export function DecisionCard({
  request,
  onChoose,
  onAskMore,
  onLater,
  onOpenDetail,
}: {
  request: HitlRequest;
  onChoose: (optionId: string) => void;
  onAskMore: () => void;
  onLater: () => void;
  /** Opens the request's detail page; absent when it has none yet. */
  onOpenDetail?: () => void;
}) {
  const kind = decisionKindOf(request);
  const look = DECISION[kind];
  const due = dueOf(request);
  const [chosen, setChosen] = useState(
    () => request.options.find((o) => o.is_default)?.id ?? request.options[0]?.id ?? null,
  );

  return (
    <article
      aria-labelledby={`decision-${request.id}`}
      className="flex w-full items-start gap-da-xl rounded-lg border border-border bg-card px-da-xl py-da-lg"
    >
      <span className="inline-flex size-10 shrink-0 items-center justify-center text-muted-foreground">
        <Icon name={look.icon} />
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-da-sm">
        <div className="flex items-center gap-da-md">
          <StatusDot tone={look.dot}>{look.label(request)}</StatusDot>
          {due && (
            <span
              className={cn('da-caption', due.urgent ? 'text-warning' : 'text-muted-foreground')}
            >
              {due.text}
            </span>
          )}
        </div>
        <h2 id={`decision-${request.id}`} className="text-foreground da-heading-2">
          {request.title}
        </h2>
        {request.context.why && (
          <p className="text-muted-foreground da-body-small">
            Vì sao agent hỏi: {request.context.why}
          </p>
        )}

        {kind === 'choose' && (
          <div role="radiogroup" aria-label="Phương án" className="flex w-full gap-da-md">
            {request.options.map((option) => {
              const active = option.id === chosen;
              return (
                <button
                  key={option.id}
                  type="button"
                  role="radio"
                  aria-checked={active}
                  onClick={() => setChosen(option.id)}
                  className={cn(
                    'flex min-w-0 flex-1 flex-col gap-[2px] rounded-md px-da-md py-da-sm text-left',
                    active
                      ? 'border-[1.5px] border-primary'
                      : 'border border-border hover:border-border-strong',
                  )}
                >
                  <span
                    className={cn('da-body-strong', active ? 'text-primary' : 'text-foreground')}
                  >
                    {option.label}
                  </span>
                  <span className="text-muted-foreground da-caption">{option.consequence}</span>
                </button>
              );
            })}
          </div>
        )}

        <div className="flex w-full items-center gap-da-md">
          {request.agent && <AgentChip name={request.agent.name} status="waiting" />}
          <span className="flex-1" />
          {kind === 'choose' && (
            <>
              <Button kind="ghost" onClick={onAskMore}>
                Hỏi thêm
              </Button>
              <Button kind="primary" disabled={!chosen} onClick={() => chosen && onChoose(chosen)}>
                Xác nhận lựa chọn
              </Button>
            </>
          )}
          {kind === 'approve' && (
            <>
              <Button kind="ghost" onClick={onLater}>
                Để sau
              </Button>
              <DetailButton label="Xem thay đổi" onOpen={onOpenDetail} />
            </>
          )}
          {kind === 'compare' && <DetailButton label="So sánh nguồn" onOpen={onOpenDetail} />}
          {kind === 'review' && (
            <DetailButton
              label={request.kind === 'report_publish' ? 'Mở báo cáo' : 'Mở'}
              onOpen={onOpenDetail}
            />
          )}
        </div>
      </div>
    </article>
  );
}

function DetailButton({ label, onOpen }: { label: string; onOpen?: () => void }) {
  if (onOpen)
    return (
      <Button kind="primary" onClick={onOpen}>
        {label}
      </Button>
    );
  return <PendingDetail label={label} />;
}

function PendingDetail({ label }: { label: string }) {
  return (
    <Tooltip content={DETAIL_PENDING}>
      <Button kind="primary" disabled>
        {label}
      </Button>
    </Tooltip>
  );
}
