import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { Segmented } from '../../design-system/Segmented';
import { useToast } from '../../design-system/Toast';
import { listInbox, listMyRules, resolveHitl } from '../../lib/v1/api';
import type { HitlRequest } from '../../lib/v1/types';
import { useV1 } from '../../lib/v1/useV1';
import { DecisionCard } from './DecisionCard';
import { isExpiringSoon } from './decision';
import { RulesPanel } from './RulesPanel';

/*
 * "v3.1 · Cần bạn" (2149:15763, UC-07-07, UC-07-03): what agents wait on the viewer for.
 * Main column 16×32 padding, 16px gap: title and one line, the segmented filter, the cards;
 * a 380px right panel with the viewer's rules. Empty: "v3.1 · Cần bạn · Trống" (2165:16912).
 *
 * Answers are optimistic with a 5 s "Hoàn tác" (spec "Thẻ việc Cần bạn"): the card leaves at
 * once and the answer is sent when the undo window closes; a failure brings the card back.
 * The "Đã xử lý tuần này" segment of the design waits for a list of resolved requests in the
 * API (GET /v1/inbox returns pending ones only).
 */
type Filter = 'pending' | 'expiring';
const UNDO_MS = 5000;

export default function NeedsYouPage() {
  const { navigate } = useAppState();
  const toast = useToast();
  const inbox = useV1(() => listInbox({ status: 'open' }), []);
  const rules = useV1(listMyRules, []);
  const [filter, setFilter] = useState<Filter>('pending');
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [later, setLater] = useState<string[]>([]);
  const timers = useRef(new Map<string, number>());

  useEffect(() => () => timers.current.forEach((t) => window.clearTimeout(t)), []);

  const visible = useMemo(() => {
    const open = (inbox.data?.items ?? []).filter((r) => !hidden.has(r.id));
    // "Để sau" moves a card to the end for this visit; it stays open for the agent.
    const ordered = [
      ...open.filter((r) => !later.includes(r.id)),
      ...later.map((id) => open.find((r) => r.id === id)).filter(Boolean),
    ] as HitlRequest[];
    return filter === 'expiring' ? ordered.filter((r) => isExpiringSoon(r)) : ordered;
  }, [inbox.data, hidden, later, filter]);

  const pendingCount = (inbox.data?.items ?? []).filter((r) => !hidden.has(r.id)).length;
  const expiringCount = (inbox.data?.items ?? []).filter(
    (r) => !hidden.has(r.id) && isExpiringSoon(r),
  ).length;

  const setHiddenFlag = (id: string, value: boolean) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (value) next.add(id);
      else next.delete(id);
      return next;
    });

  const choose = useCallback(
    (request: HitlRequest, optionId: string) => {
      setHiddenFlag(request.id, true);
      const agentName = request.agent?.name ?? 'Agent';
      const timer = window.setTimeout(() => {
        timers.current.delete(request.id);
        resolveHitl(request.id, { decision: 'answer', option: optionId })
          .then(() => inbox.reload())
          .catch((e: Error) => {
            setHiddenFlag(request.id, false);
            toast({ tone: 'error', message: `Không gửi được lựa chọn: ${e.message}` });
          });
      }, UNDO_MS);
      timers.current.set(request.id, timer);
      toast({
        tone: 'success',
        message: `Đã chọn · ${agentName} chạy tiếp`,
        duration: UNDO_MS,
        action: {
          label: 'Hoàn tác',
          onClick: () => {
            window.clearTimeout(timers.current.get(request.id));
            timers.current.delete(request.id);
            setHiddenFlag(request.id, false);
          },
        },
      });
    },
    [inbox, toast],
  );

  const askMore = (request: HitlRequest) => {
    const q = `Về câu hỏi của ${request.agent?.name ?? 'agent'}: ${request.title}`;
    window.history.replaceState(null, '', `/analysis/ask?q=${encodeURIComponent(q)}`);
    navigate('analysis', 'ASK');
  };

  return (
    <div className="flex min-h-full">
      <div className="flex min-w-0 flex-1 flex-col gap-da-xl px-da-3xl py-da-xl">
        <header className="flex flex-col gap-da-xs">
          <h1 className="text-foreground da-heading-1">Cần bạn quyết định</h1>
          {inbox.data && pendingCount > 0 && (
            <p className="text-muted-foreground da-body">
              {pendingCount} việc đang chờ
              {expiringCount > 0 && ` · ${expiringCount} sắp hết hạn`} · các agent liên quan đang
              dừng ở bước an toàn, không làm gì thêm cho tới khi bạn trả lời.
            </p>
          )}
        </header>

        {inbox.data && pendingCount > 0 && (
          <Segmented<Filter>
            label="Lọc việc"
            value={filter}
            onChange={setFilter}
            items={[
              { value: 'pending', label: `Đang chờ · ${pendingCount}` },
              { value: 'expiring', label: `Sắp hết hạn · ${expiringCount}` },
            ]}
          />
        )}

        <AsyncRegion
          state={inbox}
          what="việc cần bạn"
          isEmpty={() => pendingCount === 0}
          empty={
            <EmptyState
              title="Không có việc nào cần bạn"
              description="Các agent đang tự làm việc. Khi agent cần bạn quyết định, việc sẽ hiện ở đây và trên Teams."
              action={
                <Button onClick={() => navigate('agents', 'TEAM')}>Xem agent đang làm gì</Button>
              }
            />
          }
        >
          {() => (
            <div className="flex flex-col gap-da-lg">
              {visible.map((request) => (
                <DecisionCard
                  key={request.id}
                  request={request}
                  onChoose={(optionId) => choose(request, optionId)}
                  onAskMore={() => askMore(request)}
                  onLater={() =>
                    setLater((prev) => [...prev.filter((id) => id !== request.id), request.id])
                  }
                />
              ))}
              {visible.length === 0 && (
                <p className="text-muted-foreground da-body-small">
                  Không có việc nào sắp hết hạn.
                </p>
              )}
            </div>
          )}
        </AsyncRegion>
      </div>

      <RulesPanel rules={rules} />
    </div>
  );
}
