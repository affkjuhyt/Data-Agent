import { StatusDot } from '../../design-system/StatusDot';
import { formatElapsed } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import type { HitlRequest } from '../../lib/v1/types';
import { DECISION, LIST_LABEL, decisionKindOf } from './decision';

/*
 * The request list beside a detail page ("Danh sách" of "Cần bạn · So sánh hai nguồn",
 * 2225:14717): 320px, right border, 16×12 padding; each item 8px padding, radius-md, the kind
 * as a status dot, the title in Body Strong and "<agent> · <waiting>"; the open one is outlined.
 */
export function RequestList({
  requests,
  activeId,
  onOpen,
}: {
  requests: HitlRequest[];
  activeId?: string;
  onOpen: (request: HitlRequest) => void;
}) {
  return (
    <nav
      aria-label="Việc cần bạn"
      className="flex w-[320px] shrink-0 flex-col gap-da-xs overflow-y-auto border-r border-border bg-card px-da-lg py-da-xl max-[1279px]:hidden"
    >
      <h2 className="text-foreground da-heading-3">Cần bạn · {requests.length}</h2>
      {requests.map((request) => {
        const kind = decisionKindOf(request);
        const waited = (Date.now() - new Date(request.created_at).getTime()) / 1000;
        const active = request.id === activeId || request.source_ref === activeId;
        return (
          <button
            key={request.id}
            type="button"
            aria-current={active ? 'page' : undefined}
            onClick={() => onOpen(request)}
            className={cn(
              'flex w-full flex-col items-start gap-da-xs rounded-md border p-da-md text-left',
              active ? 'border-border' : 'border-transparent hover:bg-subtle',
            )}
          >
            <StatusDot tone={DECISION[kind].dot}>{LIST_LABEL[kind]}</StatusDot>
            <span className="text-foreground da-body-strong">{request.title}</span>
            <span className="text-muted-foreground da-caption">
              {[request.agent?.name, formatElapsed(waited)].filter(Boolean).join(' · ')}
            </span>
          </button>
        );
      })}
    </nav>
  );
}
