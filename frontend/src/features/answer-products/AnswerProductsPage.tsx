import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { Dialog } from '../../design-system/Dialog';
import { FilterChips } from '../../design-system/FilterChips';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { StatusDot } from '../../design-system/StatusDot';
import { Tabs } from '../../design-system/Tabs';
import { Textarea } from '../../design-system/Textarea';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { cn } from '../../lib/utils';
import {
  draftProductVersion,
  getAnswerProduct,
  getProductFreshness,
  getProductMonitoring,
  listAnswerProducts,
  removeProductMember,
  submitAnswerProduct,
  type AnswerProduct,
  type AnswerProductSummary,
  type FreshnessState,
  type ProductStatus,
} from '../../lib/v1/answerProducts';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P07 · Data product" (2418:14958, SCR-67, UC-07-13): the data products of the Data Space — the
 * scope of questions the organisation commits to answer right — beside the one picked: its scope
 * (description, sample questions with answers and results, out of scope), members (datasets,
 * certified metrics, knowledge pages; primary or reference), freshness (commitment, next due, the
 * last 8 periods) and monitoring (in scope, masked answers, open Error Book entries, eval). Sending
 * for review runs the product's eval; it needs 10 questions with answers at 95% (BR-175). Changing
 * an active product makes the next version; the active one serves until that is approved.
 */
type Filter = 'all' | ProductStatus;
type Tab = 'scope' | 'members' | 'freshness' | 'monitoring';

const STATUS: Record<
  ProductStatus,
  { label: string; tone: 'success' | 'neutral' | 'warning' | 'error' }
> = {
  active: { label: 'Đang dùng', tone: 'success' },
  draft: { label: 'Nháp', tone: 'neutral' },
  pending_review: { label: 'Chờ duyệt', tone: 'warning' },
  suspended: { label: 'Tạm ngưng', tone: 'error' },
  retired: { label: 'Ngừng', tone: 'neutral' },
};
const FRESH: Record<FreshnessState, { label: string; tone: 'success' | 'error' | 'idle' }> = {
  fresh: { label: 'Còn hạn', tone: 'success' },
  overdue: { label: 'Quá hạn', tone: 'error' },
  missing: { label: 'Chưa có dữ liệu', tone: 'idle' },
};
/** A share in Vietnamese notation; "—" while the api does not send it (PROPOSAL). */
const pct = (n: number | null) => (n === null ? '—' : `${String(n).replace('.', ',')}%`);
const dueText = (iso: string) => {
  const d = new Date(iso);
  return `${String(d.getDate()).padStart(2, '0')}/${String(d.getMonth() + 1).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
};

export default function AnswerProductsPage() {
  const { routeId } = useAppState();
  const list = useV1(() => listAnswerProducts(), []);
  const [picked, setPicked] = useState<string | undefined>(routeId || undefined);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const items = list.data?.items ?? [];
  const id = picked ?? items[0]?.id;
  const q = query.trim().toLowerCase();
  const shown = items.filter(
    (p) =>
      (filter === 'all' || p.status === filter) &&
      (!q || `${p.name} ${p.code}`.toLowerCase().includes(q)),
  );
  const count = (s: ProductStatus) => items.filter((p) => p.status === s).length;

  return (
    <div className="flex min-h-full max-[1023px]:flex-col">
      <div className="flex w-[320px] shrink-0 flex-col gap-da-sm border-r border-border px-da-xl pt-da-xl max-[1023px]:w-full">
        <Input
          aria-label="Tìm data product"
          placeholder="Tìm data product…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <FilterChips<Filter>
          label="Lọc data product"
          value={filter}
          onChange={setFilter}
          items={[
            { value: 'all', label: `Tất cả ${items.length}` },
            { value: 'active', label: `Đang dùng ${count('active')}` },
            { value: 'draft', label: `Nháp ${count('draft')}` },
          ]}
        />
        <AsyncRegion state={list} what="data product">
          {() => (
            <ul aria-label="Data product" className="flex flex-col gap-da-sm">
              {shown.map((p) => (
                <ProductCard
                  key={p.id}
                  product={p}
                  selected={p.id === id}
                  onSelect={() => setPicked(p.id)}
                />
              ))}
            </ul>
          )}
        </AsyncRegion>
        <p className="text-muted-foreground da-caption">
          Data product là phạm vi trả lời có cam kết: câu hỏi trong phạm vi có cổng độ tươi và biên
          nhận; ngoài phạm vi mang nhãn “không có biên nhận” (BR-175).
        </p>
      </div>
      <div className="min-w-0 flex-1">
        {id && <ProductView key={id} id={id} onChanged={list.reload} />}
      </div>
    </div>
  );
}

function ProductCard({
  product,
  selected,
  onSelect,
}: {
  product: AnswerProductSummary;
  selected: boolean;
  onSelect: () => void;
}) {
  const status = STATUS[product.status];
  const fresh = FRESH[product.freshness_state];
  return (
    <li>
      <button
        type="button"
        aria-current={selected ? 'true' : undefined}
        onClick={onSelect}
        className={cn(
          'flex w-full flex-col gap-[2px] rounded-md border px-da-md py-da-sm text-left',
          selected
            ? 'border-primary bg-accent'
            : 'border-border bg-card hover:border-border-strong',
        )}
      >
        <span className="flex items-start gap-da-sm">
          <span className="min-w-0 flex-1 text-foreground da-body-strong">{product.name}</span>
          <Badge tone={status.tone}>
            {status.label}
            {product.status === 'active' ? ` · v${product.version}` : ''}
          </Badge>
        </span>
        <span className="text-muted-foreground da-caption">
          Owner {product.owner_name} · steward {product.steward_name}
        </span>
        <span className="text-foreground da-caption">{product.freshness_statement}</span>
        {product.readiness_text ? (
          <StatusDot tone="warning">{product.readiness_text}</StatusDot>
        ) : (
          <StatusDot tone={fresh.tone}>
            {fresh.label}
            {product.next_due_at ? ` · hạn kế ${dueText(product.next_due_at)}` : ''}
          </StatusDot>
        )}
      </button>
    </li>
  );
}

function ProductView({ id, onChanged }: { id: string; onChanged: () => void }) {
  const product = useV1(() => getAnswerProduct(id), [id]);
  return (
    <AsyncRegion state={product} what="data product">
      {(p) => (
        <ProductBody
          product={p}
          onChanged={() => {
            product.reload();
            onChanged();
          }}
        />
      )}
    </AsyncRegion>
  );
}

function ProductBody({ product, onChanged }: { product: AnswerProduct; onChanged: () => void }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [tab, setTab] = useState<Tab>('scope');
  const [submitting, setSubmitting] = useState(false);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string>();
  const status = STATUS[product.status];
  const fresh = FRESH[product.freshness_state];
  const evalPct = product.stats.eval
    ? (product.stats.eval.passed / product.stats.eval.total) * 100
    : null;

  const act = async (key: string, call: () => Promise<unknown>, message: string) => {
    setBusy(key);
    setError(undefined);
    try {
      await call();
      toast({ tone: 'success', message });
      setSubmitting(false);
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Data product', product.name]}
        title={
          <span className="flex flex-wrap items-center gap-[10px]">
            {product.name}
            <Badge tone={status.tone}>
              {status.label}
              {product.status === 'active' ? ` · v${product.version}` : ''}
            </Badge>
            {product.status === 'active' && (
              <Badge
                tone={
                  fresh.tone === 'success'
                    ? 'success'
                    : fresh.tone === 'error'
                      ? 'error'
                      : 'neutral'
                }
              >
                {fresh.label}
              </Badge>
            )}
          </span>
        }
        description={[
          product.code,
          product.status === 'active'
            ? `v${product.version} đang dùng`
            : `v${product.version} nháp`,
          `Data Space ${product.data_space_name}`,
          `owner ${product.owner_name}`,
          `steward ${product.steward_name}`,
          product.freshness_statement.toLowerCase(),
        ].join(' · ')}
        actions={
          <>
            {product.status === 'active' && (
              <Button
                loading={busy === 'draft'}
                disabled={busy !== null || product.draft_version !== null}
                onClick={() =>
                  act(
                    'draft',
                    () => draftProductVersion(product.id),
                    `Đã tạo bản nháp v${product.version + 1}; v${product.version} vẫn phục vụ tới khi duyệt`,
                  )
                }
              >
                {product.draft_version
                  ? `Đang soạn v${product.draft_version}`
                  : 'Tạo phiên bản mới'}
              </Button>
            )}
            <Button
              kind="primary"
              disabled={busy !== null || product.status === 'pending_review'}
              onClick={() => {
                setError(undefined);
                setSubmitting(true);
              }}
            >
              Gửi duyệt
            </Button>
          </>
        }
      />
      <div className="flex flex-col gap-da-md px-da-3xl pb-da-2xl pt-da-md">
        <Tabs<Tab>
          label="Data product"
          value={tab}
          onChange={setTab}
          items={[
            { value: 'scope', label: 'Phạm vi' },
            { value: 'members', label: 'Thành phần', count: product.members.length },
            { value: 'freshness', label: 'Độ tươi' },
            { value: 'monitoring', label: 'Theo dõi' },
          ]}
        />
        {error && !submitting && <Notice tone="error">Không thực hiện được: {error}</Notice>}
        <dl className="grid grid-cols-5 gap-da-md max-[1279px]:grid-cols-3 max-[767px]:grid-cols-2">
          <Kpi
            label="Độ tươi"
            value={fresh.label}
            note={product.next_due_at ? `hạn kế ${dueText(product.next_due_at)}` : '—'}
            tone={fresh.tone === 'success' ? 'success' : 'muted'}
          />
          <Kpi
            label="Trong phạm vi"
            value={pct(product.stats.in_scope_pct)}
            note="câu hỏi 30 ngày"
          />
          <Kpi
            label="Bị che do quá hạn"
            value={pct(product.stats.masked_pct)}
            note={product.stats.masked_note}
            tone={(product.stats.masked_pct ?? 0) > 0 ? 'warning' : 'muted'}
          />
          <Kpi
            label="Error Book mở"
            value={String(product.stats.error_book_count)}
            note={
              product.stats.error_book_open.map((e) => e.code).join(', ') ||
              (product.stats.error_book_count
                ? `${product.stats.error_book_count} mục mở`
                : 'không có')
            }
            tone={product.stats.error_book_count ? 'error' : 'muted'}
            onClick={
              product.stats.error_book_open.length
                ? () => navigate('data', 'ERROR_BOOK', product.stats.error_book_open[0].id)
                : undefined
            }
          />
          <Kpi
            label="Đánh giá"
            value={evalPct === null ? '—' : pct(Math.round(evalPct))}
            note={
              product.stats.eval
                ? `${product.stats.eval.passed}/${product.stats.eval.total} · ngưỡng ${product.stats.eval.threshold_pct}%`
                : 'chưa chạy'
            }
            tone={evalPct !== null && evalPct >= 95 ? 'success' : 'muted'}
          />
        </dl>
        {tab === 'scope' && <ScopeTab product={product} />}
        {tab === 'members' && (
          <MembersTab
            product={product}
            busy={busy}
            onRemove={(memberId, label) =>
              act(
                `rm-${memberId}`,
                () => removeProductMember(product.id, memberId),
                `Đã gỡ ${label}`,
              )
            }
          />
        )}
        {tab === 'freshness' && <FreshnessTab id={product.id} />}
        {tab === 'monitoring' && <MonitoringTab id={product.id} />}
      </div>
      <Dialog
        open={submitting}
        size="sm"
        title={`Gửi duyệt ${product.name}`}
        description={product.reviewer_text}
        submitLabel="Gửi duyệt"
        submitting={busy === 'submit'}
        error={submitting ? error : undefined}
        onClose={() => setSubmitting(false)}
        onSubmit={() =>
          act(
            'submit',
            () => submitAnswerProduct(product.id, note.trim() || undefined),
            'Đã chạy đánh giá và gửi duyệt vào Cần bạn',
          )
        }
      >
        <div className="flex flex-col gap-da-sm">
          <p className="text-muted-foreground da-body-small">
            Hệ thống chạy bộ đánh giá của data product; cần ít nhất 10 câu hỏi mẫu có đáp án và đạt
            từ 95% (BR-175).
          </p>
          <Textarea
            aria-label="Ghi chú cho người duyệt"
            placeholder="Ghi chú cho người duyệt"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={3}
          />
        </div>
      </Dialog>
    </div>
  );
}

function Kpi({
  label,
  value,
  note,
  tone = 'muted',
  onClick,
}: {
  label: string;
  value: string;
  note: string;
  tone?: 'success' | 'warning' | 'error' | 'muted';
  onClick?: () => void;
}) {
  const Root = onClick ? 'button' : 'div';
  return (
    <Root
      type={onClick ? 'button' : undefined}
      onClick={onClick}
      className="flex flex-col gap-[4px] rounded-md border border-border bg-card px-da-lg py-da-md text-left"
    >
      <dt className="text-muted-foreground da-caption">{label}</dt>
      <dd className="text-foreground da-metric">{value}</dd>
      <dd
        className={cn(
          'da-caption',
          tone === 'success'
            ? 'text-success'
            : tone === 'warning'
              ? 'text-warning'
              : tone === 'error'
                ? 'text-error'
                : 'text-muted-foreground',
        )}
      >
        {note}
      </dd>
    </Root>
  );
}

const RESULT = {
  pass: { label: 'Đạt', tone: 'success' },
  fail: { label: 'Trượt', tone: 'error' },
  pending: { label: 'Chưa chạy', tone: 'idle' },
} as const;

function ScopeTab({ product }: { product: AnswerProduct }) {
  const primary = product.members.slice(0, 3);
  return (
    <div className="flex items-start gap-da-md max-[1279px]:flex-col">
      <section className="flex min-w-0 flex-1 flex-col gap-da-md rounded-md border border-border bg-card p-da-lg max-[1279px]:w-full">
        <h2 className="text-foreground da-heading-3">Phạm vi câu hỏi</h2>
        <p className="text-foreground da-body-small">{product.description}</p>
        <ul aria-label="Phạm vi" className="flex flex-wrap gap-da-xs">
          {product.scope_chips.map((c) => (
            <li
              key={c}
              className="rounded-md bg-subtle px-da-sm py-[2px] text-foreground da-body-small"
            >
              {c}
            </li>
          ))}
        </ul>
        <table className="w-full table-fixed border-collapse overflow-hidden rounded-md border border-border da-body-small">
          <thead>
            <tr className="border-b border-border bg-subtle text-left text-muted-foreground da-caption">
              <th className="px-da-md py-da-sm font-normal">
                Câu hỏi mẫu · {product.sample_total} câu có đáp án
              </th>
              <th className="w-[160px] px-da-md py-da-sm font-normal">Đáp án</th>
              <th className="w-[100px] px-da-md py-da-sm font-normal">Kết quả</th>
            </tr>
          </thead>
          <tbody>
            {product.sample_questions.map((s) => (
              <tr
                key={s.question}
                className="border-b border-border text-foreground last:border-b-0"
              >
                <td className="px-da-md py-[9px]">{s.question}</td>
                <td className="px-da-md py-[9px]">{s.answer}</td>
                <td className="px-da-md py-[9px]">
                  <StatusDot tone={RESULT[s.result].tone}>{RESULT[s.result].label}</StatusDot>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {product.sample_total > product.sample_questions.length && (
          <p className="text-muted-foreground da-caption">
            +{product.sample_total - product.sample_questions.length} câu khác. Câu trượt mở cách
            hiểu sai để sửa trước khi gửi duyệt.
          </p>
        )}
      </section>
      <div className="flex w-[330px] shrink-0 flex-col gap-da-md max-[1279px]:w-full">
        <section className="flex flex-col gap-da-sm rounded-md border border-border bg-subtle p-da-lg">
          <h2 className="text-foreground da-heading-3">Ngoài phạm vi</h2>
          <p className="text-foreground da-body-small">{product.out_of_scope_note}</p>
        </section>
        <section className="flex flex-col gap-da-sm rounded-md border border-border bg-card p-da-lg">
          <h2 className="text-foreground da-heading-3">Thành phần</h2>
          <ul className="flex flex-col gap-da-sm">
            {primary.map((m) => (
              <li key={m.id} className="flex items-center gap-da-sm">
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate text-foreground da-mono">{m.ref}</span>
                  <span className="text-muted-foreground da-caption">{m.detail}</span>
                </span>
                <MemberBadge member={m} />
              </li>
            ))}
          </ul>
        </section>
        <section className="flex flex-col gap-da-sm rounded-md border border-border bg-card p-da-lg">
          <h2 className="text-foreground da-heading-3">Gửi duyệt</h2>
          {product.stats.eval ? (
            <StatusDot
              tone={
                product.stats.eval.passed / product.stats.eval.total >= 0.95 ? 'success' : 'warning'
              }
            >
              Đánh giá {product.stats.eval.passed}/{product.stats.eval.total} đạt ·{' '}
              {pct(Math.round((product.stats.eval.passed / product.stats.eval.total) * 100))}{' '}
              (ngưỡng {product.stats.eval.threshold_pct}%)
            </StatusDot>
          ) : (
            <StatusDot tone="idle">Chưa chạy đánh giá</StatusDot>
          )}
          <p className="text-muted-foreground da-caption">{product.reviewer_text}</p>
        </section>
      </div>
    </div>
  );
}

function MemberBadge({ member }: { member: AnswerProduct['members'][number] }) {
  if (member.role === 'primary') return <Badge tone="success">Chính</Badge>;
  if (member.member_kind === 'metric' && member.certified)
    return <Badge tone="success">Chứng nhận</Badge>;
  return <Badge tone="neutral">Tham chiếu</Badge>;
}

const KIND_LABEL = {
  dataset: 'Dataset',
  metric: 'Chỉ số',
  knowledge_page: 'Trang tri thức',
  shared_product: 'Sản phẩm dữ liệu dùng chung',
};

function MembersTab({
  product,
  busy,
  onRemove,
}: {
  product: AnswerProduct;
  busy: string | null;
  onRemove: (memberId: string, label: string) => void;
}) {
  return (
    <table className="w-full table-fixed border-collapse overflow-hidden rounded-md border border-border da-body-small">
      <thead>
        <tr className="border-b border-border bg-subtle text-left text-muted-foreground da-caption">
          <th className="px-da-md py-da-sm font-normal">Thành phần</th>
          <th className="w-[200px] px-da-md py-da-sm font-normal">Loại</th>
          <th className="px-da-md py-da-sm font-normal">Chi tiết</th>
          <th className="w-[130px] px-da-md py-da-sm font-normal">Vai trò</th>
          <th className="w-[90px]" />
        </tr>
      </thead>
      <tbody>
        {product.members.map((m) => {
          const locked = product.status === 'active' && m.role === 'primary';
          const remove = (
            <Button
              size="sm"
              kind="ghost"
              disabled={busy !== null || locked}
              loading={busy === `rm-${m.id}`}
              onClick={() => onRemove(m.id, m.ref)}
            >
              Gỡ
            </Button>
          );
          return (
            <tr key={m.id} className="border-b border-border text-foreground last:border-b-0">
              <td className="truncate px-da-md py-[9px] da-mono">{m.ref}</td>
              <td className="px-da-md py-[9px]">{KIND_LABEL[m.member_kind]}</td>
              <td className="px-da-md py-[9px] text-muted-foreground">{m.detail}</td>
              <td className="px-da-md py-[9px]">
                <MemberBadge member={m} />
              </td>
              <td className="px-da-sm py-[6px]">
                {locked ? (
                  <Tooltip content="Gỡ thành phần chính của bản đang dùng phải qua phiên bản mới.">
                    {remove}
                  </Tooltip>
                ) : (
                  remove
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

const PERIOD = {
  shown: { label: 'Đúng hạn', tone: 'success' },
  masked_stale: { label: 'Bị che', tone: 'error' },
  not_available: { label: 'Chưa có', tone: 'idle' },
} as const;

function FreshnessTab({ id }: { id: string }) {
  const freshness = useV1(() => getProductFreshness(id), [id]);
  return (
    <AsyncRegion state={freshness} what="độ tươi">
      {(f) => (
        <div className="flex flex-col gap-da-md">
          {f.datasets.map((d) => (
            <section
              key={d.ref}
              className="flex flex-col gap-da-sm rounded-md border border-border bg-card p-da-lg"
            >
              <h2 className="flex items-center gap-da-sm text-foreground da-heading-3">
                <span className="da-mono">{d.ref}</span>
                <StatusDot tone={FRESH[d.state].tone}>{FRESH[d.state].label}</StatusDot>
              </h2>
              <dl className="grid grid-cols-[160px_1fr] gap-x-da-md gap-y-da-xs da-body-small">
                <dt className="text-muted-foreground">Cam kết</dt>
                <dd className="text-foreground">
                  {d.commitment_text}, {d.grace_text}
                </dd>
                <dt className="text-muted-foreground">Hạn kế tiếp</dt>
                <dd className="text-foreground">{dueText(d.next_due_at)}</dd>
                <dt className="text-muted-foreground">Lần nạp gần nhất</dt>
                <dd className="text-foreground">
                  {dueText(d.last_loaded_at)} · dữ liệu đến{' '}
                  {d.last_as_of.split('-').reverse().join('/')}
                </dd>
              </dl>
            </section>
          ))}
          <section className="flex flex-col gap-da-sm rounded-md border border-border bg-card p-da-lg">
            <h2 className="text-foreground da-heading-3">8 kỳ gần nhất</h2>
            <ol
              aria-label="8 kỳ gần nhất"
              className="grid grid-cols-8 gap-da-sm max-[1023px]:grid-cols-4"
            >
              {f.periods.map((p) => (
                <li
                  key={p.period}
                  className="flex flex-col gap-[2px] rounded-md border border-border px-da-sm py-da-xs"
                >
                  <span className="text-foreground da-mono">{p.period}</span>
                  <StatusDot tone={PERIOD[p.state].tone}>{PERIOD[p.state].label}</StatusDot>
                  <span className="text-muted-foreground da-caption">{p.note}</span>
                </li>
              ))}
            </ol>
            <p className="text-muted-foreground da-caption">
              Kỳ quá hạn cập nhật bị che trong câu trả lời: “dữ liệu đến &lt;ngày&gt;, đã quá hạn
              cập nhật” (BR-178).
            </p>
          </section>
        </div>
      )}
    </AsyncRegion>
  );
}

function MonitoringTab({ id }: { id: string }) {
  const monitoring = useV1(() => getProductMonitoring(id), [id]);
  return (
    <AsyncRegion state={monitoring} what="theo dõi">
      {(m) => (
        <section className="flex flex-col gap-da-sm rounded-md border border-border bg-card p-da-lg">
          <h2 className="text-foreground da-heading-3">{m.questions_30d} câu hỏi trong 30 ngày</h2>
          <table className="w-full border-collapse da-body-small">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground da-caption">
                <th className="py-da-sm font-normal">Tuần</th>
                <th className="py-da-sm font-normal">Trong phạm vi</th>
                <th className="py-da-sm font-normal">Ngoài phạm vi</th>
                <th className="py-da-sm font-normal">Bị che</th>
              </tr>
            </thead>
            <tbody>
              {m.weeks.map((w) => (
                <tr
                  key={w.label}
                  className="border-b border-border text-foreground last:border-b-0"
                >
                  <td className="py-[7px]">{w.label}</td>
                  <td className="py-[7px]">{pct(w.in_scope_pct)}</td>
                  <td className="py-[7px]">{pct(100 - w.in_scope_pct)}</td>
                  <td className={cn('py-[7px]', w.masked_pct > 0 && 'text-warning')}>
                    {pct(w.masked_pct)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </AsyncRegion>
  );
}
