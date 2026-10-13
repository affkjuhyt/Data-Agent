import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { EmptyState } from '../../design-system/EmptyState';
import { Notice } from '../../design-system/Notice';
import { StatusDot } from '../../design-system/StatusDot';
import { Textarea } from '../../design-system/Textarea';
import { useToast } from '../../design-system/Toast';
import { formatDate } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import {
  activateDecisionRule,
  backtestDecisionRule,
  discardDecisionRule,
  getDecisionRule,
  listDraftRules,
  type DecisionRule,
} from '../../lib/v1/rules';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P07 · Quy tắc từ quyết định" (2302:19305, UC-07-08): a rule drafted from a decision in Cần
 * bạn. Bordered header (where it came from), the rule in KHI … THÌ form, what it would have done
 * over the last 30 days — including how many cases it would have decided differently from the
 * person, which is what the owner must judge — and on the right how long it holds. The draft is
 * already in its owner's Cần bạn; it is dropped (with a reason), backtested again, or switched on
 * by the owner. It never applies itself.
 */
const STATUS: Record<
  DecisionRule['status'],
  { label: string; tone: 'warning' | 'info' | 'success' | 'idle' }
> = {
  draft: { label: 'Bản nháp', tone: 'warning' },
  active: { label: 'Đang áp dụng', tone: 'success' },
  disabled: { label: 'Đã bỏ', tone: 'idle' },
  expired: { label: 'Hết hạn', tone: 'idle' },
};

export default function DecisionRulePage() {
  const { routeId } = useAppState();
  // From the rail without an id: the first draft waiting to be sent to its owner.
  const drafts = useV1(routeId ? null : listDraftRules, [routeId]);
  const id = routeId || drafts.data?.items[0]?.id;
  const rule = useV1(id ? () => getDecisionRule(id) : null, [id]);
  if (!routeId && drafts.data && drafts.data.items.length === 0) {
    return (
      <div className="px-da-3xl pt-da-xl">
        <EmptyState
          icon="check"
          title="Không có quy tắc nháp nào"
          description="Chọn “Ghi nhớ thành quy tắc” khi trả lời một việc Cần bạn để tạo quy tắc từ quyết định."
        />
      </div>
    );
  }
  return (
    <AsyncRegion state={rule} what="quy tắc">
      {(r) => <RuleBody rule={r} onChanged={rule.reload} />}
    </AsyncRegion>
  );
}

function RuleBody({ rule, onChanged }: { rule: DecisionRule; onChanged: () => void }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [busy, setBusy] = useState<'backtest' | 'activate' | 'discard' | null>(null);
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [reason, setReason] = useState('');
  const [reasonMissing, setReasonMissing] = useState(false);
  const [error, setError] = useState<string>();
  const status = STATUS[rule.status];
  const draft = rule.status === 'draft';

  const act = async (verb: 'backtest' | 'activate' | 'discard') => {
    setBusy(verb);
    setError(undefined);
    try {
      if (verb === 'backtest') {
        await backtestDecisionRule(rule.id, rule.backtest?.window_days ?? 30);
        toast({ tone: 'info', message: 'Đang chạy thử lại; kết quả hiện khi xong.' });
      } else if (verb === 'activate') {
        await activateDecisionRule(rule.id);
        toast({ tone: 'success', message: 'Đã bật quy tắc' });
      } else {
        await discardDecisionRule(rule.id, reason.trim());
        toast({ tone: 'success', message: 'Đã bỏ bản nháp quy tắc' });
        navigate('needs-you');
        return;
      }
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
      setConfirmDiscard(false);
    }
  };

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Ngữ nghĩa', 'Quy tắc từ quyết định', status.label]}
        title={
          <span className="flex items-center gap-[10px]">
            {rule.title}
            <StatusDot tone={status.tone}>{status.label}</StatusDot>
          </span>
        }
        description={`${rule.origin_label} · ${formatDate(rule.created_at)}`}
        actions={
          draft && (
            <>
              <Button kind="ghost" disabled={busy !== null} onClick={() => setConfirmDiscard(true)}>
                Bỏ
              </Button>
              <Button
                loading={busy === 'backtest'}
                disabled={busy !== null}
                onClick={() => act('backtest')}
              >
                Chạy thử lại
              </Button>
              <Button
                kind="primary"
                loading={busy === 'activate'}
                disabled={busy !== null}
                onClick={() => act('activate')}
              >
                Bật quy tắc
              </Button>
            </>
          )
        }
      />
      {draft && (
        <div className="px-da-3xl pt-da-lg">
          <Notice tone="info">
            Bản nháp đã nằm trong Cần bạn của chủ sở hữu; quy tắc chỉ áp dụng khi chủ sở hữu bật.
          </Notice>
        </div>
      )}
      {error && (
        <div className="px-da-3xl pt-da-lg">
          <Notice tone="error">Không thực hiện được: {error}</Notice>
        </div>
      )}
      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col gap-[14px] pl-da-3xl pr-da-2xl pt-da-xl">
          <h2 className="text-foreground da-heading-3">Quy tắc</h2>
          <pre className="whitespace-pre-wrap rounded-sm border border-border bg-background px-[14px] py-da-lg text-foreground da-mono">
            {rule.rule_text}
          </pre>
          <Backtest rule={rule} />
        </div>
        <aside className="flex w-[340px] shrink-0 flex-col gap-[10px] border-l border-border bg-background px-da-2xl pt-[20px] max-[1279px]:hidden">
          <h2 className="text-foreground da-heading-3">Hiệu lực</h2>
          <dl className="flex flex-col gap-[10px]">
            {[
              ['Phạm vi', rule.scope_label],
              ['Tự hết hạn', rule.expires_at ? expiryText(rule.expires_at) : 'Không hết hạn'],
            ].map(([label, value]) => (
              <div key={label} className="flex flex-col gap-[2px] pb-da-sm">
                <dt className="text-muted-foreground da-caption">{label}</dt>
                <dd className="text-foreground da-body-small">{value}</dd>
              </div>
            ))}
          </dl>
          <p className="text-foreground da-body-small">
            Mỗi lần quy tắc tự xử lý được ghi vào Cần bạn · Quy tắc tự xử lý; ai cũng có thể mở lại
            một vụ.
          </p>
        </aside>
      </div>
      <ConfirmDialog
        open={confirmDiscard}
        title="Bỏ bản nháp quy tắc?"
        confirmLabel="Bỏ quy tắc"
        cancelLabel="Giữ lại"
        loading={busy === 'discard'}
        onCancel={() => setConfirmDiscard(false)}
        error={reasonMissing ? 'Ghi lý do bỏ quy tắc.' : undefined}
        onConfirm={() => {
          setReasonMissing(!reason.trim());
          if (reason.trim()) act('discard');
        }}
      >
        <span className="flex flex-col gap-da-sm">
          Quyết định gốc vẫn giữ nguyên. Các vụ tương tự sẽ tiếp tục được hỏi qua Cần bạn.
          <Textarea
            aria-label="Lý do bỏ"
            placeholder="Lý do bỏ (bắt buộc)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={2}
          />
        </span>
      </ConfirmDialog>
    </div>
  );
}

/** "90 ngày · 06/01/2027": how long from today, then the date. */
function expiryText(expiresAt: string, now = new Date()): string {
  const days = Math.max(
    0,
    Math.round((new Date(expiresAt).getTime() - now.getTime()) / 86_400_000),
  );
  return `${days} ngày · ${formatDate(expiresAt)}`;
}

function Backtest({ rule }: { rule: DecisionRule }) {
  const result = rule.backtest;
  if (!result) {
    return (
      <p className="text-muted-foreground da-body-small">
        Chưa chạy thử. Bấm “Chạy thử lại” để xem quy tắc sẽ xử lý các vụ gần đây thế nào.
      </p>
    );
  }
  const cells: { label: string; value: string; warn?: boolean }[] = [
    { label: 'Vụ khớp điều kiện', value: String(result.cases) },
    { label: 'Quy tắc tự xử lý', value: String(result.auto_handled) },
    { label: 'Vẫn hỏi người', value: String(result.asked_people) },
    {
      label: 'Khác quyết định người đã làm',
      value: String(result.changed),
      warn: result.changed > 0,
    },
  ];
  return (
    <>
      <h2 className="text-foreground da-heading-3">Chạy thử trên {result.window_days} ngày qua</h2>
      <dl className="flex overflow-hidden rounded-md border border-border">
        {cells.map((cell) => (
          <div
            key={cell.label}
            className="flex min-w-0 flex-1 flex-col gap-[2px] border-r border-border px-[14px] py-[10px] last:border-r-0"
          >
            <dt className="truncate text-muted-foreground da-caption">{cell.label}</dt>
            <dd
              className={cn(
                'truncate da-body-strong',
                cell.warn ? 'text-warning' : 'text-foreground',
              )}
            >
              {cell.value}
            </dd>
          </div>
        ))}
      </dl>
    </>
  );
}
