import { useMemo, useRef, useState, type ComponentProps } from 'react';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { Dialog } from '../../design-system/Dialog';
import { Field } from '../../design-system/Field';
import { FilterChips } from '../../design-system/FilterChips';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { Select } from '../../design-system/Select';
import { Textarea } from '../../design-system/Textarea';
import { useToast } from '../../design-system/Toast';
import { formatDate, formatNumber } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import { listAnswerProducts } from '../../lib/v1/answerProducts';
import {
  askPrivately,
  createMemoryItem,
  createThesis,
  getTransitKey,
  listMemoryItems,
  listScores,
  PRIVATE_CONTEXT_MAX,
  privateAnswer,
  withdrawThesis,
  type MemoryKind,
  type PrivateAnswer,
} from '../../lib/v1/exec';
import { listCatalogMetrics } from '../../lib/v1/metricCatalog';
import { useV1 } from '../../lib/v1/useV1';
import { encryptCondition, uuidv7 } from '../../lib/vault/crypto';
import {
  COMPARATOR,
  openMemory,
  openSealedSafe,
  proposeCondition,
  shortDateTime,
  thresholdText,
  type Comparator,
  type OpenMemory,
  type OpenThesis,
  type SealedScore,
  type ThesisCondition,
} from './content';
import { useVault, type Frame } from './VaultPage';

/*
 * Tab Luận điểm (2425:15275, UC-16-01, UC-16-02): the leader's theses, the composer, the private
 * memory and the private-question bar. A thesis is sealed on the device before it is sent; only
 * its scoring condition is also encrypted to the vault's Transit key, the one part a workflow
 * reads to score it. Once saved it cannot be edited, only superseded (BR-188).
 */
type Filter = 'all' | 'open' | 'scored' | 'withdrawn' | 'unresolvable';

const groupOf = (t: OpenThesis): Exclude<Filter, 'all'> =>
  t.status === 'open' || t.status === 'scoring'
    ? 'open'
    : t.status === 'scored'
      ? 'scored'
      : t.status === 'withdrawn'
        ? 'withdrawn'
        : 'unresolvable';

const MEMORY_GROUPS: { label: string; kinds: MemoryKind[] }[] = [
  { label: 'Ưu tiên', kinds: ['priority'] },
  { label: 'Lý do quyết định', kinds: ['decision_reason'] },
  { label: 'Câu hỏi đã hỏi', kinds: ['question', 'answer_snapshot'] },
  { label: 'Ghi chú', kinds: ['note'] },
];

export function ThesesTab({ frame }: { frame: Frame }) {
  const { session, theses, selected, select } = useVault();
  const [filter, setFilter] = useState<Filter>('all');
  const [composerKey, setComposerKey] = useState(0);
  const [noting, setNoting] = useState(false);
  const statementRef = useRef<HTMLInputElement>(null);
  const memory = useV1(
    ['ThesesTab.openMemory', session !== null],
    async () => openMemory(session, (await listMemoryItems()).items),
    { cache: false },
  );
  const scores = useV1(['listScores.items'], async () => (await listScores()).items);
  const observed = useMemo(() => {
    const out = new Map<string, { outcome: string; score: SealedScore | null }>();
    for (const s of scores.data ?? [])
      if (s.kind === 'final')
        out.set(s.thesis_id, {
          outcome: s.outcome,
          score: openSealedSafe<SealedScore>(session, s.sealed_ct),
        });
    return out;
  }, [scores.data, session]);

  const items = theses.data ?? [];
  const count = (f: Exclude<Filter, 'all'>) => items.filter((t) => groupOf(t) === f).length;
  const shown = items
    .filter((t) => filter === 'all' || groupOf(t) === filter)
    .sort((a, b) => {
      const ao = groupOf(a) === 'open';
      const bo = groupOf(b) === 'open';
      if (ao !== bo) return ao ? -1 : 1;
      return ao ? a.due_at.localeCompare(b.due_at) : b.due_at.localeCompare(a.due_at);
    });
  const open = count('open');

  return frame(
    {
      breadcrumb: ['Kho riêng', 'Luận điểm'],
      title: 'Luận điểm của tôi',
      description: `${items.length} luận điểm · ${open} đang mở · nội dung mã hoá trên thiết bị, máy chủ và quản trị chỉ thấy bản mã · điều kiện chấm mã hoá bằng khoá riêng của kho trong OpenBao`,
      actions: (
        <>
          <Button onClick={() => setNoting(true)}>Ghi lý do quyết định</Button>
          <Button
            kind="primary"
            onClick={() => {
              setComposerKey((k) => k + 1);
              window.setTimeout(() => statementRef.current?.focus(), 0);
            }}
          >
            Luận điểm mới
          </Button>
        </>
      ),
    },
    <>
      {theses.error && (
        <Notice tone="error">Không tải được luận điểm. {theses.error.message}</Notice>
      )}
      <div className="flex flex-col gap-da-xl xl:flex-row xl:items-stretch">
        <section
          aria-label="Danh sách luận điểm"
          className="flex flex-col gap-[10px] xl:w-[330px] xl:shrink-0"
        >
          <FilterChips<Filter>
            label="Lọc luận điểm"
            value={filter}
            onChange={setFilter}
            items={[
              { value: 'all', label: `Tất cả ${items.length}` },
              { value: 'open', label: `Đang mở ${open}` },
              { value: 'scored', label: `Đã chấm ${count('scored')}` },
              { value: 'withdrawn', label: `Rút ${count('withdrawn')}` },
              { value: 'unresolvable', label: `Không chấm được ${count('unresolvable')}` },
            ]}
          />
          <ul className="flex max-h-[640px] flex-col gap-[10px] overflow-y-auto">
            {shown.map((t) => (
              <li key={t.id}>
                <ThesisCard
                  thesis={t}
                  active={t.id === selected?.id}
                  onSelect={() => select(t.id)}
                  result={observed.get(t.id)}
                />
              </li>
            ))}
            {shown.length === 0 && (
              <li className="text-muted-foreground da-caption">
                Không có luận điểm nào ở nhóm này.
              </li>
            )}
          </ul>
        </section>
        <Composer key={composerKey} statementRef={statementRef} onSaved={select} />
        <MemoryCard memory={memory.data ?? []} error={memory.error?.message} />
      </div>
      <AskPrivately
        context={(memory.data ?? [])
          .flatMap((m) => (m.content ? [{ memory_item_id: m.id, text: m.content.text }] : []))
          .slice(0, PRIVATE_CONTEXT_MAX)}
        onSaved={memory.reload}
      />
      <MemoryDialog open={noting} onClose={() => setNoting(false)} onSaved={memory.reload} />
    </>,
  );
}

type BadgeTone = NonNullable<ComponentProps<typeof Badge>['tone']>;

const STATUS: Record<string, { label: string; tone: BadgeTone }> = {
  open: { label: 'Đang mở', tone: 'info' },
  scoring: { label: 'Đang chấm', tone: 'info' },
  correct: { label: 'Đúng', tone: 'success' },
  incorrect: { label: 'Sai', tone: 'error' },
  unresolvable: { label: 'Không chấm được', tone: 'warning' },
  withdrawn: { label: 'Rút', tone: 'neutral' },
  invalid_condition: { label: 'Điều kiện lỗi', tone: 'error' },
};

function ThesisCard({
  thesis: t,
  active,
  onSelect,
  result,
}: {
  thesis: OpenThesis;
  active: boolean;
  onSelect: () => void;
  result: { outcome: string; score: SealedScore | null } | undefined;
}) {
  const { theses } = useVault();
  const toast = useToast();
  const [withdrawing, setWithdrawing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const status =
    STATUS[t.status === 'scored' ? (result?.outcome ?? 'correct') : t.status] ?? STATUS.open;
  const when =
    t.status === 'withdrawn' && t.withdrawn_at
      ? `rút ${formatDate(t.withdrawn_at).slice(0, 5)}`
      : `${t.status === 'scored' ? 'đã chấm' : 'chấm'} ${shortDateTime(t.due_at)}`;
  const note =
    t.status === 'withdrawn'
      ? 'Không vào điểm hiệu chỉnh, vẫn đếm số lần rút'
      : t.status === 'unresolvable'
        ? 'Không đủ dữ liệu để chấm; không vào điểm hiệu chỉnh'
        : t.status === 'scored'
          ? result?.score
            ? `Quan sát ${result.score.observed}`
            : 'Đã chấm'
          : t.evidence_counts
            ? `Bằng chứng: ${t.evidence_counts.support} ủng hộ · ${t.evidence_counts.oppose} phản bác`
            : 'Bằng chứng: chưa có lô đầu tiên';

  const withdraw = async () => {
    setBusy(true);
    setError(undefined);
    try {
      await withdrawThesis(t.id);
      setWithdrawing(false);
      theses.reload();
      toast({ tone: 'success', message: 'Đã rút luận điểm' });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className={cn(
        'flex flex-col gap-[6px] rounded-lg border px-da-lg py-[10px]',
        active ? 'border-ring bg-accent' : 'border-border bg-card',
      )}
    >
      <button
        type="button"
        onClick={onSelect}
        aria-pressed={active}
        className="flex flex-col gap-[6px] text-left"
      >
        <span className="text-foreground da-body-strong">
          {t.content?.statement ?? 'Không giải mã được bằng khoá đang mở'}
        </span>
        <span className="flex w-full items-center gap-da-md">
          {t.content && <Badge tone="accent">{Math.round(t.content.confidence * 100)}%</Badge>}
          <span className="text-foreground da-caption">{when}</span>
          <span className="flex-1" />
          <Badge tone={status.tone}>{status.label}</Badge>
        </span>
        <span className="text-muted-foreground da-caption">{note}</span>
      </button>
      {active && t.status === 'open' && (
        <Button size="sm" className="self-start" onClick={() => setWithdrawing(true)}>
          Rút luận điểm
        </Button>
      )}
      <ConfirmDialog
        open={withdrawing}
        title="Rút luận điểm này?"
        confirmLabel="Rút"
        loading={busy}
        error={error}
        onConfirm={withdraw}
        onCancel={() => setWithdrawing(false)}
      >
        Luận điểm rút không vào điểm hiệu chỉnh nhưng vẫn được đếm số lần rút; không mở lại được.
      </ConfirmDialog>
    </div>
  );
}

interface Target {
  value: string;
  label: string;
  ref_kind: ThesisCondition['ref_kind'];
  certified: boolean;
}

const tomorrowLocal = () => {
  const d = new Date(Date.now() + 2 * 86_400_000);
  d.setHours(18, 0, 0, 0);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
};

function Composer({
  statementRef,
  onSaved,
}: {
  statementRef: React.RefObject<HTMLInputElement | null>;
  onSaved: (id: string) => void;
}) {
  const { session, theses } = useVault();
  const toast = useToast();
  const targets = useV1(['ThesesTab.targets'], async () => {
    const [metrics, products] = await Promise.all([
      listCatalogMetrics(),
      listAnswerProducts('active'),
    ]);
    return [
      ...products.items.map<Target>((p) => ({
        value: `data_product:${p.code}`,
        label: `Data product: ${p.name}`,
        ref_kind: 'data_product',
        certified: true,
      })),
      ...metrics.items.map<Target>((m) => ({
        value: `metric:${m.code}`,
        label: `Chỉ số: ${m.name}`,
        ref_kind: 'metric',
        certified: m.status === 'certified',
      })),
    ];
  });
  const [statement, setStatement] = useState('');
  const [rationale, setRationale] = useState('');
  const [confidence, setConfidence] = useState(70);
  const [target, setTarget] = useState<string | null>(null);
  const [scope, setScope] = useState('');
  const [comparator, setComparator] = useState<Comparator>('gte');
  const [threshold, setThreshold] = useState('');
  const [unit, setUnit] = useState('');
  const [period, setPeriod] = useState('');
  const [due, setDue] = useState(tomorrowLocal);
  const [editing, setEditing] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();

  const picked = targets.data?.find((t) => t.value === target);
  const value = Number(threshold.replace(/\./g, '').replace(',', '.'));
  const dueAt = due ? new Date(due) : null;
  const tooSoon = !dueAt || dueAt.getTime() - Date.now() < 86_400_000;
  const ready =
    statement.trim() && picked && threshold && Number.isFinite(value) && period.trim() && !tooSoon;
  const condition: ThesisCondition | null = picked
    ? {
        ref_kind: picked.ref_kind,
        metric_ref: picked.value.split(':')[1],
        metric_label: picked.label.replace(/^(Data product|Chỉ số): /, ''),
        ...(scope.trim() ? { scope_label: scope.trim() } : {}),
        comparator,
        threshold: value,
        unit: unit.trim(),
        period_label: period.trim(),
      }
    : null;

  const propose = () => {
    const p = proposeCondition(statement);
    if (p.comparator) setComparator(p.comparator);
    if (p.threshold !== undefined) setThreshold(formatNumber(p.threshold));
    if (p.unit) setUnit(p.unit);
  };

  const save = async () => {
    if (!ready || !condition || !dueAt) return;
    setSaving(true);
    setError(undefined);
    try {
      const id = uuidv7();
      const sealed = session.encrypt(
        {
          statement: statement.trim(),
          rationale: rationale.trim(),
          confidence: confidence / 100,
          tags: [],
          condition,
        },
        id,
        1,
      );
      const transit = await getTransitKey();
      const condCt = await encryptCondition(transit.public_key_pem, {
        ...condition,
        due_at: dueAt.toISOString(),
      });
      await createThesis({
        id,
        ...sealed,
        content_version: 1,
        cond_ct: condCt,
        cond_key_version: transit.key_version,
        due_at: dueAt.toISOString(),
      });
      theses.reload();
      onSaved(id);
      toast({ tone: 'success', message: 'Đã lưu luận điểm' });
      setStatement('');
      setRationale('');
      setThreshold('');
      setPeriod('');
      setScope('');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const chip = 'rounded-full bg-subtle px-da-md py-da-xs text-muted-foreground da-label';
  return (
    <form
      aria-label="Soạn luận điểm"
      className="flex flex-col gap-da-md rounded-lg border border-border bg-card p-da-xl xl:w-[480px] xl:shrink-0"
      onSubmit={(e) => {
        e.preventDefault();
        void save();
      }}
    >
      <h2 className="text-foreground da-heading-3">Soạn luận điểm</h2>
      <Field label="Nhận định" labelTone="muted">
        <Input
          ref={statementRef}
          value={statement}
          onChange={(e) => setStatement(e.target.value)}
          onBlur={() => !threshold && statement && propose()}
          placeholder="Giá xăng E5 kỳ 22/10 sẽ trên 20 nghìn"
        />
      </Field>
      <div className="flex items-center gap-da-md">
        <span className="text-muted-foreground da-caption">
          Điều kiện chấm · đề xuất trên thiết bị, bạn sửa được
        </span>
        <span className="flex-1" />
        <Button size="sm" onClick={() => setEditing((v) => !v)}>
          {editing ? 'Xong' : 'Sửa'}
        </Button>
      </div>
      {editing ? (
        <div className="grid grid-cols-2 gap-da-md">
          <div className="col-span-2">
            <Select
              label="Chỉ số hoặc data product đã chứng nhận"
              placeholder="Chọn chỉ số hoặc data product…"
              options={(targets.data ?? []).map((t) => ({ value: t.value, label: t.label }))}
              value={target}
              onChange={setTarget}
            />
          </div>
          <Input
            aria-label="Chỉ tiêu"
            placeholder="Chỉ tiêu (vd. giá trần E5RON92)"
            value={scope}
            onChange={(e) => setScope(e.target.value)}
          />
          <Select<Comparator>
            label="Phép so sánh"
            options={(Object.keys(COMPARATOR) as Comparator[]).map((c) => ({
              value: c,
              label: `Phép so sánh: ${COMPARATOR[c]}`,
            }))}
            value={comparator}
            onChange={setComparator}
          />
          <Input
            aria-label="Giá trị"
            placeholder="Giá trị"
            inputMode="decimal"
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
          />
          <Input
            aria-label="Đơn vị"
            placeholder="Đơn vị (đ/lít, %)"
            value={unit}
            onChange={(e) => setUnit(e.target.value)}
          />
          <Input
            aria-label="Kỳ"
            placeholder="Kỳ (vd. điều hành 22/10/2026)"
            value={period}
            onChange={(e) => setPeriod(e.target.value)}
          />
          <Input
            aria-label="Thời điểm chấm"
            type="datetime-local"
            value={due}
            onChange={(e) => setDue(e.target.value)}
            error={due && tooSoon ? 'Chấm sau hôm nay ít nhất 1 ngày (BR-188)' : undefined}
          />
        </div>
      ) : (
        <div className="flex flex-wrap gap-[6px]">
          {picked ? (
            <span className={chip}>{picked.label}</span>
          ) : (
            <span className={chip}>Chưa chọn chỉ số</span>
          )}
          {picked?.certified && <Badge tone="success">Đã chứng nhận</Badge>}
          {scope && <span className={chip}>Chỉ tiêu: {scope}</span>}
          <span className={chip}>Phép so sánh: {COMPARATOR[comparator]}</span>
          {condition && threshold && (
            <span className={chip}>Giá trị: {thresholdText(condition).slice(2)}</span>
          )}
          {period && <span className={chip}>Kỳ: {period}</span>}
          {dueAt && (
            <span className={chip}>
              Chấm: {formatDate(dueAt)} {due.slice(11)}
            </span>
          )}
        </div>
      )}
      <div className="flex items-center">
        <span className="text-muted-foreground da-caption">Mức tự tin</span>
        <span className="flex-1" />
        <span className="text-foreground da-body-strong">{confidence}%</span>
      </div>
      <input
        type="range"
        min={50}
        max={99}
        value={confidence}
        onChange={(e) => setConfidence(Number(e.target.value))}
        aria-label="Mức tự tin"
        className="w-full accent-[var(--info)]"
      />
      <div className="flex text-muted-foreground da-overline normal-case tracking-normal">
        <span>50%</span>
        <span className="flex-1" />
        <span>99%</span>
      </div>
      <Field label="Lý do" labelTone="muted">
        <Input value={rationale} onChange={(e) => setRationale(e.target.value)} />
      </Field>
      <p className="flex items-start gap-[6px] text-foreground da-caption">
        <span aria-hidden className="mt-[5px] size-[6px] shrink-0 rounded-full bg-warning" />
        Sau khi lưu không sửa được, chỉ tạo luận điểm thay; luận điểm cũ vẫn được chấm.
      </p>
      {error && <Notice tone="error">{error}</Notice>}
      <div className="flex justify-end gap-da-md">
        <Button
          onClick={() => {
            setStatement('');
            setRationale('');
            setThreshold('');
            setError(undefined);
          }}
        >
          Huỷ
        </Button>
        <Button type="submit" kind="primary" loading={saving} disabled={!ready}>
          Lưu luận điểm
        </Button>
      </div>
    </form>
  );
}

function MemoryCard({ memory, error }: { memory: OpenMemory[]; error?: string }) {
  const [query, setQuery] = useState('');
  const q = query.trim().toLowerCase();
  // Search runs here, over what this device decrypted: there is no server index of the memory.
  const live = memory.filter(
    (m) =>
      m.content &&
      !m.shredded_at &&
      (!q || m.content.text.toLowerCase().includes(q) || m.content.tag.toLowerCase().includes(q)),
  );
  return (
    <section
      aria-label="Bộ nhớ riêng"
      className="flex min-w-0 flex-1 flex-col gap-da-md rounded-lg border border-border bg-card p-da-xl"
    >
      <h2 className="text-foreground da-heading-3">Bộ nhớ riêng</h2>
      <Input
        aria-label="Tìm trong bộ nhớ riêng"
        placeholder="Tìm nhãn… · tìm trên thiết bị"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      {error && <Notice tone="error">{error}</Notice>}
      {MEMORY_GROUPS.map((g) => {
        const inGroup = live.filter((m) => g.kinds.includes(m.kind));
        if (inGroup.length === 0) return null;
        return (
          <div key={g.label} className="flex flex-col gap-da-md">
            <span className="text-muted-foreground da-caption">{g.label}</span>
            {inGroup.map((m) => (
              <div
                key={m.id}
                className="flex flex-col gap-da-xs rounded-md bg-subtle px-[10px] py-da-md"
              >
                <span className="text-foreground da-caption">{m.content!.text}</span>
                <span className="text-[11px] leading-4 text-muted-foreground">
                  {m.content!.tag}
                </span>
              </div>
            ))}
          </div>
        );
      })}
      {live.length === 0 && !error && (
        <span className="text-muted-foreground da-caption">
          {q ? 'Không có mục nào khớp.' : 'Chưa có mục nào.'}
        </span>
      )}
      <p className="text-muted-foreground da-caption">
        Chỉ dùng để chọn cái cần trình bày; số vẫn lấy từ dữ liệu lớp dưới, không đổi theo ưu tiên
        của bạn.
      </p>
    </section>
  );
}

function MemoryDialog({
  open,
  onClose,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const { session } = useVault();
  const [kind, setKind] = useState<MemoryKind>('decision_reason');
  const [text, setText] = useState('');
  const [tag, setTag] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();
  const submit = async () => {
    setSaving(true);
    setError(undefined);
    try {
      const id = uuidv7();
      const stamp = formatDate(new Date()).slice(0, 5);
      await createMemoryItem({
        id,
        kind,
        ...session.encrypt(
          { text: text.trim(), tag: tag.trim() ? `${tag.trim()} · ghi ${stamp}` : `ghi ${stamp}` },
          id,
          1,
        ),
        content_version: 1,
      });
      setText('');
      setTag('');
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };
  return (
    <Dialog
      open={open}
      title="Ghi vào bộ nhớ riêng"
      description="Mã hoá trên thiết bị trước khi gửi; chỉ bạn đọc được."
      onClose={onClose}
      onSubmit={submit}
      submitLabel="Lưu"
      submitting={saving}
      canSubmit={Boolean(text.trim())}
      error={error}
    >
      <div className="flex flex-col gap-da-lg">
        <Field label="Loại">
          <Select<MemoryKind>
            label="Loại"
            value={kind}
            onChange={setKind}
            options={[
              { value: 'decision_reason', label: 'Lý do quyết định' },
              { value: 'priority', label: 'Ưu tiên' },
              { value: 'note', label: 'Ghi chú' },
            ]}
          />
        </Field>
        <Field label="Nội dung">
          <Textarea value={text} onChange={(e) => setText(e.target.value)} rows={3} />
        </Field>
        <Field label="Nhãn" hint="Ví dụ Q4/2026, từ Cần bạn.">
          <Input value={tag} onChange={(e) => setTag(e.target.value)} />
        </Field>
      </div>
    </Dialog>
  );
}

function AskPrivately({
  context,
  onSaved,
}: {
  context: { memory_item_id: string; text: string }[];
  onSaved: () => void;
}) {
  const { session } = useVault();
  const [text, setText] = useState('');
  const [asking, setAsking] = useState(false);
  const [answer, setAnswer] = useState<{ question: string; reply: PrivateAnswer } | null>(null);
  const [error, setError] = useState<string>();
  const [saved, setSaved] = useState(false);

  const ask = async () => {
    const question = text.trim();
    if (!question) return;
    setAsking(true);
    setError(undefined);
    setSaved(false);
    try {
      const { id } = await askPrivately({ text: question, context_items: context });
      setAnswer({ question, reply: await privateAnswer(id) });
      setText('');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setAsking(false);
    }
  };

  const keep = async () => {
    if (!answer) return;
    const id = uuidv7();
    try {
      await createMemoryItem({
        id,
        kind: 'question',
        ...session.encrypt(
          {
            text: answer.question,
            tag: `${formatDate(new Date()).slice(0, 5)} · kèm bản chụp câu trả lời`,
            answer: answer.reply,
          },
          id,
          1,
        ),
        content_version: 1,
      });
      setSaved(true);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="flex flex-col gap-da-md">
      {answer && (
        <section
          aria-label="Câu trả lời riêng"
          className="flex flex-col gap-da-md rounded-lg border border-border bg-card p-da-lg"
        >
          <span className="text-muted-foreground da-caption">{answer.question}</span>
          <p className="text-foreground da-body-small">{answer.reply.text}</p>
          {answer.reply.figures.length > 0 && (
            <span className="text-muted-foreground da-caption">
              Số lấy từ:{' '}
              {answer.reply.figures.map((f) => `${f.value} · ${f.query_run_id}`).join(' · ')}
            </span>
          )}
          <Button size="sm" className="self-start" disabled={saved} onClick={keep}>
            {saved ? 'Đã lưu vào bộ nhớ' : 'Lưu vào bộ nhớ riêng'}
          </Button>
        </section>
      )}
      {error && <Notice tone="error">{error}</Notice>}
      <form
        className="flex items-center gap-da-md rounded-lg border border-border-strong bg-card py-[6px] pl-da-lg pr-[6px]"
        onSubmit={(e) => {
          e.preventDefault();
          void ask();
        }}
      >
        <input
          aria-label="Hỏi riêng"
          placeholder="Hỏi riêng…"
          value={text}
          onChange={(e) => setText(e.target.value)}
          className="min-w-0 flex-1 bg-transparent text-foreground outline-none placeholder:text-muted-foreground da-body-small"
        />
        <Badge tone="info">Mô hình tại chỗ · không lưu bản rõ</Badge>
        <Button type="submit" kind="primary" loading={asking} disabled={!text.trim()}>
          Gửi
        </Button>
      </form>
    </div>
  );
}
