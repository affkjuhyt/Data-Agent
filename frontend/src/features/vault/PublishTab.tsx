import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { Checkbox } from '../../design-system/Checkbox';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { Radio } from '../../design-system/Radio';
import { Select } from '../../design-system/Select';
import { useToast } from '../../design-system/Toast';
import { formatDate, formatDateTime, formatNumber } from '../../lib/formatVN';
import {
  addKeyWrap,
  isOffline,
  listPublishTargets,
  publishThesis,
  requestHandover,
  type HandoverReason,
  type Vault,
} from '../../lib/v1/exec';
import { useV1 } from '../../lib/v1/useV1';
import { passkeyWrap } from '../../lib/vault/session';
import { conditionTitle, type Comparator, type OpenThesis } from './content';
import { useVault, type Frame } from './VaultPage';

/*
 * Tab Công bố & khoá (2426:15808, UC-16-05, UC-16-06): publishing a thesis as a hypothesis for the
 * organisation, leaving the post under the contract's exit policy, and the vault's key wraps. A
 * publication is plaintext the leader writes and reviews word by word; it carries no number in
 * the sentence (the threshold lives in the structured condition, BR-162) and goes to a reviewer
 * other than the leader. The vault needs two key wraps, one offline, to accept a thesis (BR-189).
 */
const WILL: Record<Comparator, string> = {
  gte: 'sẽ không thấp hơn',
  lte: 'sẽ không cao hơn',
  gt: 'sẽ cao hơn',
  lt: 'sẽ thấp hơn',
};

const subjectOf = (t: OpenThesis) => {
  const c = t.content!.condition;
  const what = c.scope_label ?? c.metric_label;
  return `${what.charAt(0).toUpperCase()}${what.slice(1)}${c.period_label ? ` kỳ ${c.period_label}` : ''}`;
};

export function PublishTab({ frame }: { frame: Frame }) {
  const { theses, selected } = useVault();
  const publishable = (theses.data ?? []).filter(
    (t) => t.content && (t.status === 'open' || t.status === 'scored'),
  );
  const thesis = publishable.find((t) => t.id === selected?.id) ?? publishable[0];
  // Picking another thesis starts the form over from that thesis.
  return (
    <PublishForm
      key={thesis?.id ?? 'none'}
      frame={frame}
      thesis={thesis}
      publishable={publishable}
    />
  );
}

function PublishForm({
  frame,
  thesis,
  publishable,
}: {
  frame: Frame;
  thesis: OpenThesis | undefined;
  publishable: OpenThesis[];
}) {
  const { theses, select } = useVault();
  const { user } = useAppState();
  const toast = useToast();
  const targets = useV1(() => listPublishTargets(), []);
  const c = thesis?.content?.condition;

  const defaults = (t: OpenThesis | undefined) => ({
    title: t ? `Giả thuyết: ${subjectOf(t).replace(/ kỳ điều hành /, ' kỳ ')}` : '',
    statement: t ? `${subjectOf(t)} ${WILL[t.content!.condition.comparator]} ngưỡng dưới đây.` : '',
  });
  const [title, setTitle] = useState(defaults(thesis).title);
  const [statement, setStatement] = useState(defaults(thesis).statement);
  const [attribution, setAttribution] = useState<'named' | 'role_only'>('role_only');
  // The title signed instead of the name is the leader's own words (attribution_label, A3).
  const [roleTitle, setRoleTitle] = useState('Thành viên Ban điều hành');
  const [showConfidence, setShowConfidence] = useState(false);
  const [showRationale, setShowRationale] = useState(false);
  const [target, setTarget] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();
  const [sent, setSent] = useState<string>();

  const space = targets.data?.find((t) => t.id === (target ?? targets.data?.[0]?.id));
  const signer = attribution === 'named' ? user.name : roleTitle.trim();
  const thresholdShown = c ? `${formatNumber(c.threshold)}` : '';
  const hasNumber = Boolean(
    c && statement.replace(/(\d)[.,](?=\d)/g, '$1').includes(thresholdShown.replace(/\./g, '')),
  );
  const condLine =
    c && thesis ? `${conditionTitle(c)} · chấm ${formatDateTime(thesis.due_at)}` : '';

  const reset = () => {
    setTitle(defaults(thesis).title);
    setStatement(defaults(thesis).statement);
    setShowConfidence(false);
    setShowRationale(false);
    setError(undefined);
  };

  const send = async () => {
    if (!thesis || !c || !space || !signer) return;
    setSending(true);
    setError(undefined);
    try {
      await publishThesis(thesis.id, {
        title: title.trim(),
        statement_public: statement.trim(),
        target_data_space_id: space.id,
        attribution,
        attribution_label: signer,
        ...(showConfidence ? { confidence_public: thesis.content!.confidence } : {}),
        ...(showRationale && thesis.content!.rationale
          ? { rationale_public: thesis.content!.rationale }
          : {}),
        ref_kind: c.ref_kind,
        metric_ref: c.metric_ref,
        ...(c.scope_label ? { scope: { label: c.scope_label } } : {}),
        period_label: c.period_label,
        comparator: c.comparator,
        threshold: c.threshold,
        unit: c.unit,
      });
      // A3 answers 202 with the publication's id; the target's steward reviews it.
      setSent(`người phụ trách ${space.name}`);
      theses.reload();
      toast({ tone: 'success', message: `Đã gửi người phụ trách ${space.name} duyệt` });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
    }
  };

  const pending = Boolean(sent || thesis?.publication_id);
  const canSend = Boolean(
    thesis && space && signer && title.trim() && statement.trim() && !hasNumber && !pending,
  );
  const label = 'grid gap-da-md md:grid-cols-[130px_1fr] md:items-center';

  return frame(
    {
      breadcrumb: ['Kho riêng', 'Công bố, bàn giao & khoá'],
      title: 'Công bố, bàn giao & khoá',
      description:
        'Bản công bố là bản rõ do bạn duyệt câu chữ, qua người duyệt khác bạn · rời vị trí theo hợp đồng · kho cần ít nhất 2 khoá bọc',
      actions: (
        <>
          <Button onClick={reset}>Huỷ</Button>
          <Button kind="primary" loading={sending} disabled={!canSend} onClick={send}>
            Gửi duyệt công bố
          </Button>
        </>
      ),
    },
    <div className="flex flex-col gap-da-xl xl:flex-row xl:items-start">
      <section
        aria-label="Công bố"
        className="flex min-w-0 flex-1 flex-col gap-da-lg rounded-lg border border-border bg-card p-da-xl"
      >
        <h2 className="text-foreground da-heading-3">Công bố cho tổ chức</h2>
        {!thesis ? (
          <Notice tone="info">Chưa có luận điểm đang mở hay đã chấm để công bố.</Notice>
        ) : (
          <>
            <div className={label}>
              <span className="text-muted-foreground da-caption">Luận điểm</span>
              <Select
                label="Luận điểm"
                value={thesis.id}
                onChange={select}
                options={publishable.map((t) => ({
                  value: t.id,
                  label: `${t.content!.statement} · tự tin ${Math.round(t.content!.confidence * 100)}% · ${t.status === 'open' ? 'đang mở' : 'đã chấm'}`,
                }))}
              />
            </div>
            <div className={label}>
              <span className="text-muted-foreground da-caption">Tiêu đề</span>
              <Input
                aria-label="Tiêu đề"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </div>
            <div className={label}>
              <span className="text-muted-foreground da-caption">Câu giả thuyết</span>
              <Field
                label="Câu giả thuyết"
                hideLabel
                hint="Không chứa số; số nằm ở điều kiện có cấu trúc (BR-162)"
              >
                <Input
                  value={statement}
                  onChange={(e) => setStatement(e.target.value)}
                  error={
                    hasNumber ? 'Bỏ ngưỡng khỏi câu; ngưỡng đã nằm trong điều kiện.' : undefined
                  }
                />
              </Field>
            </div>
            {c && (
              <div className={label}>
                <span className="text-muted-foreground da-caption">Điều kiện</span>
                <div className="flex flex-wrap gap-[6px]">
                  {[
                    `${c.metric_ref}${c.scope_label ? ` · ${c.scope_label}` : ''}`,
                    conditionTitle(c)
                      .split(' ')
                      .slice(-(c.unit ? 3 : 2))
                      .join(' '),
                    c.period_label,
                    `chấm ${formatDateTime(thesis.due_at)}`,
                  ]
                    .filter(Boolean)
                    .map((text) => (
                      <span
                        key={text}
                        className="rounded-full bg-subtle px-da-md py-da-xs text-muted-foreground da-label"
                      >
                        {text}
                      </span>
                    ))}
                </div>
              </div>
            )}
            <div className={label}>
              <span className="text-muted-foreground da-caption">Ghi tên</span>
              <div role="radiogroup" aria-label="Ghi tên" className="flex flex-wrap gap-da-xl">
                <Radio
                  name="attribution"
                  value="named"
                  checked={attribution === 'named'}
                  onChange={() => setAttribution('named')}
                >
                  {user.name}
                </Radio>
                <Radio
                  name="attribution"
                  value="role_only"
                  checked={attribution === 'role_only'}
                  onChange={() => setAttribution('role_only')}
                >
                  Chức danh
                </Radio>
                {attribution === 'role_only' && (
                  <Input
                    aria-label="Chức danh"
                    value={roleTitle}
                    onChange={(e) => setRoleTitle(e.target.value)}
                    className="w-[240px] max-w-full"
                  />
                )}
              </div>
            </div>
            <div className={label}>
              <span className="text-muted-foreground da-caption">Hiện thêm</span>
              <div className="flex flex-wrap items-center gap-da-xl">
                <Checkbox checked={showConfidence} onChange={setShowConfidence}>
                  Mức tự tin
                </Checkbox>
                <Checkbox checked={showRationale} onChange={setShowRationale}>
                  Lý do
                </Checkbox>
                <span className="text-muted-foreground da-caption">mặc định tắt</span>
              </div>
            </div>
            <div className={label}>
              <span className="text-muted-foreground da-caption">Data Space đích</span>
              <Select
                label="Data Space đích"
                value={space?.id ?? null}
                onChange={setTarget}
                options={(targets.data ?? []).map((t) => ({ value: t.id, label: t.name }))}
              />
            </div>
            <div className="flex flex-col gap-da-xs rounded-lg bg-subtle p-da-lg">
              <span className="flex items-center gap-da-md">
                <span className="text-muted-foreground da-caption">Xem trước</span>
                <Badge tone="warning">Giả thuyết, chưa kiểm chứng</Badge>
              </span>
              <span className="text-foreground da-body-strong">{title}</span>
              <span className="text-foreground da-caption">
                {statement} Điều kiện: {condLine} · {signer}.
                {showConfidence && ` Mức tự tin ${Math.round(thesis.content!.confidence * 100)}%.`}
                {showRationale &&
                  thesis.content!.rationale &&
                  ` Lý do: ${thesis.content!.rationale}`}
              </span>
            </div>
            {space && (
              <p className="flex items-start gap-da-md text-foreground da-caption">
                <span aria-hidden className="mt-[5px] size-[6px] shrink-0 rounded-full bg-info" />
                Người duyệt: người phụ trách Data Space {space.name} (khác bạn) ·
                trạng thái: {pending ? 'đã gửi, chờ duyệt' : 'nháp, chưa gửi'} · sau ngày chấm trang
                tự đổi nhãn “Đã kiểm chứng: đúng/sai”
              </p>
            )}
            {error && <Notice tone="error">{error}</Notice>}
          </>
        )}
      </section>
      <div className="flex flex-col gap-da-lg xl:w-[452px] xl:shrink-0">
        <LeaveCard />
        <KeyWrapsCard />
      </div>
    </div>,
  );
}

const EXIT: { value: Vault['exit_policy']; label: string; hint: string }[] = [
  {
    value: 'seal',
    label: 'Niêm phong (mặc định)',
    hint: 'Chỉ bạn mở, chỉ đọc, hạn mở lại kho 24 tháng rồi tự huỷ khoá',
  },
  {
    value: 'selective_handover',
    label: 'Bàn giao có chọn lọc',
    hint: 'Bạn chọn mục (ví dụ 3 ưu tiên, 2 lý do quyết định) và người kế nhiệm; luận điểm và điểm không bàn giao',
  },
  {
    value: 'shred',
    label: 'Huỷ khoá',
    hint: 'Xác nhận bằng passkey · biên bản SHA-256 · AC06 duyệt khi bạn không tham gia',
  },
];

// A3 keeps the reason as one of these, not as the leader's words (nothing about them is stored).
const REASONS: { value: HandoverReason; label: string }[] = [
  { value: 'leave_position', label: 'Rời vị trí' },
  { value: 'contract_end', label: 'Hết hợp đồng' },
  { value: 'owner_request', label: 'Theo yêu cầu của tôi' },
];

function LeaveCard() {
  const { vault } = useVault();
  const toast = useToast();
  const [kind, setKind] = useState<Vault['exit_policy']>(vault.exit_policy);
  const [asking, setAsking] = useState(false);
  const [reason, setReason] = useState<HandoverReason | null>(null);
  const [successor, setSuccessor] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const confirm = async () => {
    setBusy(true);
    setError(undefined);
    try {
      await requestHandover({
        kind,
        reason: reason as HandoverReason,
        ...(kind === 'selective_handover' && successor.trim()
          ? { successor_id: successor.trim() }
          : {}),
      });
      setAsking(false);
      toast({
        tone: 'success',
        message: kind === 'seal' ? 'Đã niêm phong kho' : 'Đã gửi yêu cầu rời vị trí',
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section
      aria-label="Rời vị trí"
      className="flex flex-col gap-da-md rounded-lg border border-border bg-card p-da-xl"
    >
      <div className="flex items-center gap-da-md">
        <h2 className="flex-1 text-foreground da-heading-3">
          Rời vị trí{vault.contract_ref ? ` · hợp đồng ${vault.contract_ref}` : ''}
        </h2>
        <Button onClick={() => setAsking(true)}>Rời vị trí…</Button>
      </div>
      <div role="radiogroup" aria-label="Khi rời vị trí" className="flex flex-col gap-da-md">
        {EXIT.map((e) => (
          <div key={e.value} className="flex flex-col gap-[2px]">
            <Radio
              name="exit"
              value={e.value}
              checked={kind === e.value}
              onChange={() => setKind(e.value)}
            >
              {e.label}
            </Radio>
            <span className="pl-[22px] text-muted-foreground da-caption">{e.hint}</span>
          </div>
        ))}
      </div>
      <ConfirmDialog
        open={asking}
        size="md"
        title={kind === 'shred' ? 'Huỷ khoá kho?' : 'Rời vị trí?'}
        confirmLabel={
          kind === 'shred' ? 'Huỷ khoá' : kind === 'seal' ? 'Niêm phong' : 'Gửi yêu cầu'
        }
        tone={kind === 'shred' ? 'danger' : 'primary'}
        loading={busy}
        error={error}
        onConfirm={() => reason && void confirm()}
        onCancel={() => setAsking(false)}
      >
        <div className="flex flex-col gap-da-lg">
          <span>{EXIT.find((e) => e.value === kind)!.hint}.</span>
          <Select<HandoverReason>
            label="Lý do"
            value={reason}
            placeholder="Chọn lý do"
            onChange={setReason}
            options={REASONS}
          />
          {kind === 'selective_handover' && (
            <Field
              label="Người kế nhiệm"
              hint="Mã người dùng; mục bàn giao được niêm phong lại cho khoá của họ."
            >
              <Input value={successor} onChange={(e) => setSuccessor(e.target.value)} />
            </Field>
          )}
        </div>
      </ConfirmDialog>
    </section>
  );
}

function KeyWrapsCard() {
  const { vault, session, reloadVault } = useVault();
  const { user } = useAppState();
  const toast = useToast();
  const [adding, setAdding] = useState(false);
  const wraps = vault.key_wraps;
  const offline = wraps.filter((w) => isOffline(w.kind)).length;
  const short = wraps.length < 2 || offline < 1;

  const add = async () => {
    setAdding(true);
    try {
      const wrap = await passkeyWrap(
        session,
        { id: vault.owner_id, name: user.name },
        `Passkey · ${navigator.platform || 'thiết bị này'}`,
      );
      await addKeyWrap(wrap);
      reloadVault();
      toast({ tone: 'success', message: 'Đã thêm passkey' });
    } catch (e) {
      toast({ tone: 'error', message: e instanceof Error ? e.message : String(e) });
    } finally {
      setAdding(false);
    }
  };

  return (
    <section
      aria-label="Khoá bọc"
      className="flex flex-col gap-da-md rounded-lg border border-border bg-card p-da-xl"
    >
      <div className="flex items-center gap-da-md">
        <h2 className="flex-1 text-foreground da-heading-3">Khoá bọc · {wraps.length}</h2>
        <Button loading={adding} onClick={add}>
          Thêm khoá
        </Button>
      </div>
      <ul className="flex flex-col gap-da-md">
        {wraps.map((w) => (
          <li key={w.id} className="flex items-center gap-da-md">
            <span className="flex min-w-0 flex-1 flex-col">
              <span className="text-foreground da-body-small">{w.label}</span>
              <span className="text-muted-foreground da-caption">
                {w.last_used_at
                  ? `dùng lần cuối ${formatDateTime(w.last_used_at)}`
                  : `chưa dùng · ${w.kind === 'recovery_code' ? 'in' : 'thêm'} ngày ${formatDate(w.created_at)}`}
              </span>
            </span>
            <Badge tone={isOffline(w.kind) ? 'neutral' : 'info'}>
              {isOffline(w.kind) ? 'Ngoại tuyến' : 'Trực tuyến'}
            </Badge>
          </li>
        ))}
      </ul>
      <p className="flex items-start gap-da-md text-foreground da-caption">
        <span aria-hidden className="mt-[5px] size-[6px] shrink-0 rounded-full bg-warning" />
        Kho cần ít nhất 2 khoá còn hiệu lực, một khoá ngoại tuyến; dưới 2 khoá thì không ghi được
        luận điểm.
      </p>
      {short && (
        <Notice tone="warning">Kho đang thiếu khoá bọc; thêm khoá trước khi ghi luận điểm.</Notice>
      )}
      <p className="text-error da-caption">
        Mất mọi khoá thì mất nội dung; quản trị không khôi phục được.
      </p>
    </section>
  );
}
