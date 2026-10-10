import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Checkbox } from '../../design-system/Checkbox';
import { Notice } from '../../design-system/Notice';
import { Radio } from '../../design-system/Radio';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { cn } from '../../lib/utils';
import { getHitl, listInbox, resolveHitl } from '../../lib/v1/api';
import type { ConflictContext, HitlRequest } from '../../lib/v1/types';
import { useV1 } from '../../lib/v1/useV1';
import { RequestList } from './RequestList';
import { detailRouteOf, dueOf } from './decision';

/*
 * "v3.1 · Cần bạn · So sánh hai nguồn" (2225:14714, UC-07-04): two sources of equal authority
 * disagree and the agent may not choose. The request list on the left; in the middle the two
 * numbers with how each was read, the gap against the materiality threshold, what the agent
 * already checked, and the choices (the agent's suggestion preselected) with "remember as a
 * rule"; on the right what the choice changes and how similar conflicts were decided.
 */
const MARK = { ok: '✓', warn: '!', unknown: '?' } as const;
const PENDING_REASSIGN = 'Chọn người nhận việc đang được làm.';
const PENDING_EVIDENCE = 'Màn truy vấn bằng chứng đang được làm.';

export default function ConflictPage() {
  const { routeId, navigate } = useAppState();
  const request = useV1(routeId ? () => getHitl(routeId) : null, [routeId]);
  const inbox = useV1(() => listInbox({ status: 'open' }), []);

  const openRequest = (r: HitlRequest) => {
    const detail = detailRouteOf(r);
    if (detail) navigate('needs-you', detail.page, detail.id);
    else navigate('needs-you');
  };

  return (
    <div className="flex min-h-full">
      <RequestList requests={inbox.data?.items ?? []} activeId={routeId} onOpen={openRequest} />
      <div className="flex min-w-0 flex-1">
        <AsyncRegion state={request} what="việc cần bạn">
          {(r) => <ConflictBody key={r.id} request={r} onDone={() => navigate('needs-you')} />}
        </AsyncRegion>
      </div>
    </div>
  );
}

function ConflictBody({ request, onDone }: { request: HitlRequest; onDone: () => void }) {
  const toast = useToast();
  const ctx = request.context as ConflictContext;
  const [option, setOption] = useState(
    () => request.options.find((o) => o.is_default)?.id ?? request.options[0]?.id ?? '',
  );
  const [remember, setRemember] = useState(Boolean(ctx.rule_suggestion));
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();
  const due = dueOf(request);
  const open = request.status === 'open';

  const submit = async () => {
    setSending(true);
    setError(undefined);
    try {
      await resolveHitl(request.id, { decision: 'answer', option, remember_as_rule: remember });
      const label = request.options.find((o) => o.id === option)?.label ?? 'phương án';
      toast({
        tone: 'success',
        message: `Đã chọn: ${label}${remember ? ' · đã lưu thành quy tắc' : ''}`,
      });
      onDone();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  return (
    <>
      <div className="flex min-w-0 flex-1 flex-col gap-da-lg px-da-2xl py-da-xl">
        <header className="flex flex-col gap-da-sm">
          <div className="flex flex-wrap gap-da-sm">
            <StatusDot tone="error">Xung đột nguồn</StatusDot>
            {request.risk === 'high' && (
              <StatusDot tone="warning">Rủi ro cao · chỉ xử lý trong ứng dụng</StatusDot>
            )}
          </div>
          <h1 className="text-foreground da-heading-1">{request.title}</h1>
          <p className="text-muted-foreground da-caption">
            {[ctx.subtitle, due?.text].filter(Boolean).join(' · ')}
          </p>
        </header>

        {!open && <Notice tone="info">Việc này đã được xử lý.</Notice>}

        <div className="flex gap-da-lg max-[1023px]:flex-col">
          {ctx.sources.map((source) => (
            <section
              key={source.name}
              className="flex min-w-0 flex-1 flex-col gap-da-md rounded-md border border-border bg-card p-da-xl"
            >
              <div className="flex items-center gap-da-md">
                <h2 className="text-foreground da-heading-3">{source.name}</h2>
                <StatusDot tone={source.authority_tone}>{source.authority}</StatusDot>
              </div>
              <span className="text-muted-foreground da-caption">{source.detail}</span>
              <span className="text-foreground da-display">{source.value}</span>
              <span className="text-muted-foreground da-caption">{source.value_label}</span>
              <code className="block rounded-sm bg-subtle p-[10px] text-[11px] leading-4 text-muted-foreground">
                {source.query}
              </code>
            </section>
          ))}
        </div>

        <div className="flex flex-wrap items-baseline gap-da-xl rounded-md border border-border p-[14px]">
          <span className="text-error da-heading-3">{ctx.gap.headline}</span>
          <span className="text-foreground da-caption">{ctx.gap.detail}</span>
        </div>

        <section className="flex flex-col gap-da-sm rounded-md border border-border bg-card p-da-xl">
          <h2 className="text-foreground da-body-strong">Agent đã kiểm tra</h2>
          {ctx.checks.map((check) => (
            <p key={check.text} className="flex gap-da-md text-foreground da-caption">
              <span
                aria-hidden
                className={cn(
                  'w-3 shrink-0',
                  check.mark === 'ok'
                    ? 'text-success'
                    : check.mark === 'warn'
                      ? 'text-warning'
                      : 'text-muted-foreground',
                )}
              >
                {MARK[check.mark]}
              </span>
              {check.text}
            </p>
          ))}
        </section>

        {open && (
          <fieldset className="flex flex-col gap-da-md rounded-md border border-border bg-card p-da-xl">
            <legend className="sr-only">Chọn số dùng cho câu trả lời và báo cáo</legend>
            <p className="text-foreground da-body-strong" aria-hidden>
              Chọn số dùng cho câu trả lời và báo cáo
            </p>
            {request.options.map((o) => (
              <Radio
                key={o.id}
                name={`conflict-${request.id}`}
                value={o.id}
                checked={o.id === option}
                onChange={setOption}
              >
                {o.consequence ? `${o.label} (${o.consequence})` : o.label}
              </Radio>
            ))}
            {ctx.rule_suggestion && (
              <Checkbox checked={remember} onChange={setRemember}>
                Ghi nhớ thành quy tắc: {ctx.rule_suggestion}
              </Checkbox>
            )}
          </fieldset>
        )}

        {error && <Notice tone="error">Không gửi được lựa chọn: {error}</Notice>}

        {open && (
          <div className="flex gap-da-md">
            <Button kind="primary" loading={sending} disabled={!option} onClick={submit}>
              Chọn phương án
            </Button>
            <Tooltip content={PENDING_REASSIGN}>
              <Button disabled>Giao người khác</Button>
            </Tooltip>
            <Tooltip content={PENDING_EVIDENCE}>
              <Button kind="ghost" disabled>
                Mở truy vấn bằng chứng
              </Button>
            </Tooltip>
          </div>
        )}
      </div>

      <aside className="flex w-[340px] shrink-0 flex-col gap-da-lg border-l border-border bg-card p-da-xl max-[1279px]:hidden">
        <h2 className="text-muted-foreground da-overline">{ctx.impact_title}</h2>
        {ctx.impact.map((item) => (
          <div
            key={item.title}
            className="flex flex-col gap-[2px] rounded-sm border border-border p-[10px]"
          >
            <span className="text-foreground da-body-strong">{item.title}</span>
            <span className="text-muted-foreground da-caption">{item.detail}</span>
          </div>
        ))}
        <h2 className="text-muted-foreground da-overline">Thẩm quyền nguồn hiện có</h2>
        <p className="text-foreground da-caption">{ctx.authority_note}</p>
        <h2 className="text-muted-foreground da-overline">Xung đột tương tự</h2>
        <p className="text-foreground da-caption">{ctx.similar_note}</p>
      </aside>
    </>
  );
}
