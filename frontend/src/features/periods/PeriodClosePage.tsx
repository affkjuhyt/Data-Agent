import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { Notice } from '../../design-system/Notice';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { cn } from '../../lib/utils';
import { resolveHitl } from '../../lib/v1/api';
import {
  cancelPeriodClose,
  getPeriod,
  listPeriods,
  lockPeriod,
  type CloseStep,
  type PeriodClose,
  type PeriodSummary,
} from '../../lib/v1/periods';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P10 · Kỳ chốt · Chốt kỳ" (2304:19277, UC-10-02, UC-10-03): the periods on the left (260px),
 * the close of the chosen one as four numbered steps, and on the right (320px) the figures that
 * locking freezes. A step stopped on a mismatch carries the agent's explanation and the
 * decision inline, answered through its HITL request. "Khoá kỳ" stays off until every step is
 * done and the caller is not the person who ran the reconciliation.
 */
type Tone = 'success' | 'warning' | 'info' | 'idle';

const PERIOD_STATUS: Record<PeriodSummary['status'], { label: string; tone: Tone }> = {
  open: { label: 'Đang mở', tone: 'idle' },
  closing: { label: 'Đang chốt', tone: 'warning' },
  locked: { label: 'Đã khoá', tone: 'success' },
  published: { label: 'Đã khoá · đã công bố', tone: 'success' },
};

const STEP_STATUS: Record<CloseStep['state'], { label: string; tone: Tone }> = {
  done: { label: 'Xong', tone: 'success' },
  running: { label: 'Đang chạy', tone: 'info' },
  needs_you: { label: 'Cần bạn', tone: 'warning' },
  pending: { label: 'Chưa', tone: 'idle' },
};

function periodLabel(p: PeriodSummary): string {
  const base = PERIOD_STATUS[p.status].label;
  return p.status === 'locked' && p.adjustment_count > 0
    ? `${base} · ${p.adjustment_count} điều chỉnh`
    : base;
}

export default function PeriodClosePage() {
  const { routeId, navigate } = useAppState();
  const periods = useV1(listPeriods, []);
  // Without an id the page opens on the period being closed, else the newest.
  const items = periods.data?.items ?? [];
  const current = routeId || (items.find((p) => p.status === 'closing') ?? items[0])?.id;
  const period = useV1(current ? () => getPeriod(current) : null, [current]);

  const reload = () => {
    period.reload();
    periods.reload();
  };

  return (
    <AsyncRegion state={period} what="kỳ chốt">
      {(p) => (
        <PeriodBody
          period={p}
          periods={items}
          onSelect={(id) => navigate('data', 'PERIOD', id)}
          onChanged={reload}
        />
      )}
    </AsyncRegion>
  );
}

function PeriodBody({
  period,
  periods,
  onSelect,
  onChanged,
}: {
  period: PeriodClose;
  periods: PeriodSummary[];
  onSelect: (id: string) => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [busy, setBusy] = useState<'lock' | 'cancel' | null>(null);
  const [confirm, setConfirm] = useState<'lock' | 'cancel' | null>(null);
  const [error, setError] = useState<string>();
  const closing = period.status === 'closing';
  const current = period.steps.findIndex((s) => s.state !== 'done');
  const status = closing
    ? `${PERIOD_STATUS.closing.label} · bước ${current + 1}/${period.steps.length}`
    : periodLabel(period);

  const act = async (verb: 'lock' | 'cancel') => {
    setBusy(verb);
    setError(undefined);
    try {
      if (verb === 'lock') {
        await lockPeriod(period.id);
        toast({ tone: 'success', message: `Đã khoá kỳ ${period.code}` });
      } else {
        await cancelPeriodClose(period.id);
        toast({ tone: 'success', message: `Đã huỷ chốt kỳ ${period.code}` });
      }
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
      setConfirm(null);
    }
  };

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Kỳ chốt', period.code]}
        title={
          <span className="flex items-center gap-[10px]">
            {period.title}
            <StatusDot tone={PERIOD_STATUS[period.status].tone}>{status}</StatusDot>
          </span>
        }
        description={period.context_text}
        actions={
          closing && (
            <>
              <Button kind="ghost" disabled={busy !== null} onClick={() => setConfirm('cancel')}>
                Huỷ chốt
              </Button>
              {period.can_lock.allowed ? (
                <Button kind="primary" loading={busy === 'lock'} onClick={() => setConfirm('lock')}>
                  Khoá kỳ
                </Button>
              ) : (
                <Tooltip content={period.can_lock.reason ?? 'Chưa khoá được.'}>
                  <Button disabled>Khoá kỳ</Button>
                </Tooltip>
              )}
            </>
          )
        }
      />
      {error && (
        <div className="px-da-3xl pt-da-lg">
          <Notice tone="error">Không thực hiện được: {error}</Notice>
        </div>
      )}
      <div className="flex min-h-0 flex-1">
        <nav
          aria-label="Các kỳ"
          className="flex w-[260px] shrink-0 flex-col border-r border-border pt-da-lg max-[1023px]:hidden"
        >
          {periods.map((p) => (
            <button
              key={p.id}
              type="button"
              aria-current={p.id === period.id ? 'page' : undefined}
              onClick={() => onSelect(p.id)}
              className={cn(
                'flex items-center gap-da-md px-[20px] py-[9px] text-left transition-colors duration-[120ms] ease-out hover:bg-subtle',
                p.id === period.id && 'bg-subtle',
              )}
            >
              <span
                className={cn(
                  'min-w-0 flex-1 text-foreground',
                  p.id === period.id ? 'da-body-strong' : 'da-body-small',
                )}
              >
                {p.code}
              </span>
              <StatusDot tone={PERIOD_STATUS[p.status].tone}>{periodLabel(p)}</StatusDot>
            </button>
          ))}
        </nav>

        <ol className="flex min-w-0 flex-1 flex-col px-da-3xl pt-da-lg">
          {period.steps.map((step, i) => (
            <Step key={step.title} n={i + 1} step={step} onAnswered={onChanged} />
          ))}
        </ol>

        {period.frozen_figures.length > 0 && (
          <aside className="flex w-[320px] shrink-0 flex-col gap-[10px] border-l border-border bg-background px-da-2xl pt-[20px] max-[1279px]:hidden">
            <h2 className="text-foreground da-heading-3">Số sẽ đóng băng</h2>
            <dl className="flex flex-col gap-[10px]">
              {period.frozen_figures.map((f) => (
                <div key={f.label} className="flex items-start gap-da-md text-foreground">
                  <dt className="min-w-0 flex-1 da-body-small">{f.label}</dt>
                  <dd className="shrink-0 da-mono">{f.value}</dd>
                </div>
              ))}
            </dl>
            <p className="text-foreground da-body-small">{period.frozen_note}</p>
            <p className="text-muted-foreground da-caption">
              Người khoá kỳ khác người chạy đối soát.
            </p>
          </aside>
        )}
      </div>
      <ConfirmDialog
        open={confirm !== null}
        title={confirm === 'lock' ? `Khoá kỳ ${period.code}?` : `Huỷ chốt kỳ ${period.code}?`}
        confirmLabel={confirm === 'lock' ? 'Khoá kỳ' : 'Huỷ chốt'}
        cancelLabel="Quay lại"
        tone={confirm === 'lock' ? 'primary' : 'danger'}
        loading={busy !== null}
        onCancel={() => setConfirm(null)}
        onConfirm={() => confirm && act(confirm)}
      >
        {confirm === 'lock'
          ? 'Số liệu của kỳ được đóng băng; sau khi khoá, mọi thay đổi phải đi qua quy trình hồi tố.'
          : 'Bản chụp và kết quả đối soát của lần chốt này bị bỏ; kỳ trở về đang mở.'}
      </ConfirmDialog>
    </div>
  );
}

function Step({ n, step, onAnswered }: { n: number; step: CloseStep; onAnswered: () => void }) {
  const look = STEP_STATUS[step.state];
  return (
    <li className="flex items-start gap-[14px] border-b border-border py-[14px]">
      <span className="w-5 shrink-0 text-muted-foreground da-mono">{n}</span>
      <div className="flex min-w-0 flex-1 flex-col gap-da-xs">
        <p className="text-foreground da-body-strong">{step.title}</p>
        {step.detail && <p className="text-muted-foreground da-body-small">{step.detail}</p>}
        {step.issue && <Issue issue={step.issue} onAnswered={onAnswered} />}
      </div>
      <span className="w-[110px] shrink-0">
        <StatusDot tone={look.tone}>{look.label}</StatusDot>
      </span>
    </li>
  );
}

function Issue({
  issue,
  onAnswered,
}: {
  issue: NonNullable<CloseStep['issue']>;
  onAnswered: () => void;
}) {
  const toast = useToast();
  const [noteFor, setNoteFor] = useState<string>();
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const answer = async (option: string, withNote?: string) => {
    setBusy(true);
    setError(undefined);
    try {
      await resolveHitl(issue.hitl_id, { decision: 'answer', option, note: withNote });
      toast({ tone: 'success', message: 'Đã ghi nhận quyết định đối soát' });
      onAnswered();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-da-sm rounded-sm bg-warning-subtle px-da-lg py-[10px]">
      <p className="text-foreground da-body-small">{issue.text}</p>
      {noteFor ? (
        <form
          className="flex flex-col gap-da-sm"
          onSubmit={(event) => {
            event.preventDefault();
            if (note.trim()) answer(noteFor, note.trim());
          }}
        >
          <label className="flex flex-col gap-da-xs">
            <span className="text-foreground da-label">Ghi chú kèm theo</span>
            <textarea
              value={note}
              onChange={(event) => setNote(event.target.value)}
              rows={2}
              className="rounded-md border border-border-strong bg-card px-da-md py-da-sm text-foreground da-body-small"
            />
          </label>
          <div className="flex gap-da-md">
            <Button type="submit" kind="primary" loading={busy} disabled={!note.trim()}>
              Ghi nhận
            </Button>
            <Button kind="ghost" disabled={busy} onClick={() => setNoteFor(undefined)}>
              Quay lại
            </Button>
          </div>
        </form>
      ) : (
        <div className="flex flex-wrap gap-da-md">
          {issue.options.map((option, i) => (
            <Button
              key={option.id}
              kind={i === 0 ? 'secondary' : 'ghost'}
              disabled={busy}
              onClick={() => (option.needs_note ? setNoteFor(option.id) : answer(option.id))}
            >
              {option.label}
            </Button>
          ))}
        </div>
      )}
      {error && <Notice tone="error">Không ghi nhận được: {error}</Notice>}
    </div>
  );
}
