import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { StatusDot } from '../../design-system/StatusDot';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { Tabs } from '../../design-system/Tabs';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { formatDate } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import {
  getKnowledgePage,
  listKnowledgePages,
  listKnowledgePageVersions,
  recompileKnowledgePage,
  type KnowledgeClaim,
  type KnowledgeKind,
  type KnowledgePage,
  type KnowledgePageStatus,
  type KnowledgePageSummary,
} from '../../lib/v1/knowledge';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P07 · Trang tri thức" (2405:14761, UC-07-10, UC-07-12): the compiled knowledge pages. The
 * index on the left (search, kind filter, each page with its kind and state), the page in the
 * middle under four tabs, its policy and links on the right. Figures in the summary are filled by
 * the system from a query when the page opens, under the reader's permissions — never written by
 * the model (BR-162) — and say which query and source they came from.
 */
type Tab = 'content' | 'versions' | 'links' | 'policy';
type Filter = 'all' | KnowledgeKind;
type Tone = 'success' | 'info' | 'warning' | 'neutral' | 'error';

const KIND: Record<KnowledgeKind, string> = {
  metric: 'Chỉ số',
  entity: 'Thực thể',
  source: 'Nguồn',
  topic: 'Chủ đề',
};

const STATUS: Record<KnowledgePageStatus, { label: string; tone: Tone }> = {
  certified: { label: 'đã chứng nhận', tone: 'success' },
  approved: { label: 'đã duyệt', tone: 'info' },
  decertified: { label: 'tạm mất chứng nhận', tone: 'warning' },
  needs_review: { label: 'cần xem lại', tone: 'warning' },
  pending_review: { label: 'chờ duyệt', tone: 'info' },
  draft: { label: 'nháp', tone: 'neutral' },
  retired: { label: 'đã ngừng', tone: 'neutral' },
};

export default function KnowledgePageView() {
  const { routeId, navigate } = useAppState();
  const pages = useV1(listKnowledgePages, []);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const items = pages.data?.items ?? [];
  const id = routeId || items[0]?.id;
  const page = useV1(id ? () => getKnowledgePage(id) : null, [id]);

  const shown = items.filter(
    (p) =>
      (filter === 'all' || p.kind === filter) &&
      p.title.toLowerCase().includes(query.trim().toLowerCase()),
  );
  const count = (kind: KnowledgeKind) => items.filter((p) => p.kind === kind).length;

  return (
    <div className="flex min-h-full flex-col">
      <AsyncRegion state={page} what="trang tri thức">
        {(p) => <Header page={p} />}
      </AsyncRegion>
      <div className="flex min-h-0 flex-1 gap-da-xl px-da-3xl pb-da-2xl pt-da-xl">
        <nav aria-label="Trang tri thức" className="flex w-[300px] shrink-0 flex-col gap-[10px]">
          <input
            type="search"
            aria-label="Tìm trang"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Tìm trang, chỉ số, ngân hàng…"
            className="rounded-sm border border-border-strong bg-card px-[10px] py-da-md text-foreground da-body-small"
          />
          <div role="radiogroup" aria-label="Loại trang" className="flex flex-wrap gap-[6px]">
            {(['all', 'metric', 'entity', 'source', 'topic'] as Filter[]).map((f) => (
              <button
                key={f}
                type="button"
                role="radio"
                aria-checked={filter === f}
                onClick={() => setFilter(f)}
                className={cn(
                  'rounded-full px-da-md py-da-xs da-label',
                  filter === f ? 'bg-accent text-primary' : 'bg-subtle text-muted-foreground',
                )}
              >
                {f === 'all' ? `Tất cả ${items.length}` : `${KIND[f]} ${count(f)}`}
              </button>
            ))}
          </div>
          <ul className="flex flex-col gap-[10px]">
            {shown.map((p) => (
              <li key={p.id}>
                <PageCard
                  page={p}
                  active={p.id === id}
                  onOpen={() => navigate('data', 'KNOWLEDGE', p.id)}
                />
              </li>
            ))}
            {shown.length === 0 && (
              <li className="text-muted-foreground da-body-small">Không có trang nào khớp.</li>
            )}
          </ul>
        </nav>
        {page.data && <Body key={page.data.id} page={page.data} />}
      </div>
    </div>
  );
}

function PageCard({
  page,
  active,
  onOpen,
}: {
  page: KnowledgePageSummary;
  active: boolean;
  onOpen: () => void;
}) {
  const status = STATUS[page.status];
  return (
    <button
      type="button"
      aria-current={active ? 'page' : undefined}
      onClick={onOpen}
      className={cn(
        'flex w-full flex-col items-start gap-da-xs rounded-md border px-da-lg py-[10px] text-left',
        active ? 'border-ring bg-accent' : 'border-border bg-card hover:bg-subtle',
      )}
    >
      <span className="text-foreground da-body-strong">{page.title}</span>
      <Badge tone={status.tone}>
        {KIND[page.kind]} · {status.label}
      </Badge>
    </button>
  );
}

function Header({ page }: { page: KnowledgePage }) {
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const status = STATUS[page.status];
  const valid = page.claims.filter((c) => c.status === 'valid').length;
  const expired = page.claims.length - valid;

  const recompile = async () => {
    setBusy(true);
    try {
      await recompileKnowledgePage(page.id);
      toast({ tone: 'info', message: 'Đang biên soạn lại; bản mới chờ duyệt trước khi thay.' });
    } catch (e) {
      toast({ tone: 'error', message: `Không biên soạn lại được: ${(e as Error).message}` });
    } finally {
      setBusy(false);
    }
  };

  return (
    <PageHeader
      bordered
      breadcrumb={['Dữ liệu', 'Trang tri thức', KIND[page.kind], page.title]}
      title={
        <span className="flex items-center gap-[10px]">
          {page.title}
          <Badge tone={status.tone}>
            {status.label[0].toUpperCase() + status.label.slice(1)} · v{page.version}
          </Badge>
          {page.external_source && <Badge tone="warning">Nguồn ngoài</Badge>}
        </span>
      }
      description={[
        `Trang ${KIND[page.kind].toLowerCase()}`,
        page.certified_at
          ? `v${page.version} chứng nhận ${formatDate(page.certified_at)} bởi ${page.certified_by_name ?? '—'}`
          : `v${page.version}`,
        `${valid} khẳng định còn hiệu lực`,
        expired > 0 ? `${expired} hết hiệu lực` : null,
        'số điền theo quyền của bạn lúc mở trang',
      ]
        .filter(Boolean)
        .join(' · ')}
      actions={
        <>
          <Button loading={busy} onClick={recompile}>
            Biên soạn lại
          </Button>
          <Tooltip content="Trình sửa trang tri thức đang được làm; dùng “Biên soạn lại” để agent viết bản mới.">
            <Button kind="primary" disabled>
              Đề xuất sửa
            </Button>
          </Tooltip>
        </>
      }
    />
  );
}

function Body({ page }: { page: KnowledgePage }) {
  const [tab, setTab] = useState<Tab>('content');
  return (
    <>
      <div className="flex min-w-0 flex-1 flex-col gap-da-lg">
        <Tabs<Tab>
          label="Nội dung trang"
          value={tab}
          onChange={setTab}
          items={[
            { value: 'content', label: 'Nội dung' },
            { value: 'versions', label: 'Phiên bản', count: page.version_count },
            { value: 'links', label: 'Liên kết', count: page.links.length },
            { value: 'policy', label: 'Chính sách' },
          ]}
        />
        {tab === 'content' && <Content page={page} />}
        {tab === 'versions' && <Versions pageId={page.id} />}
        {tab === 'links' && <LinksCard page={page} />}
        {tab === 'policy' && <PolicyCard page={page} />}
      </div>
      <aside className="flex w-[272px] shrink-0 flex-col gap-da-lg max-[1279px]:hidden">
        <PolicyCard page={page} />
        <LinksCard page={page} />
      </aside>
    </>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-da-lg rounded-md border border-border bg-card p-da-xl">
      <h2 className="text-foreground da-heading-3">{title}</h2>
      {children}
    </section>
  );
}

function Content({ page }: { page: KnowledgePage }) {
  const valid = page.claims.filter((c) => c.status === 'valid').length;
  return (
    <>
      <Card title="Tóm tắt">
        <p className="max-w-[520px] text-foreground da-body-small">
          {page.summary.map((segment, i) =>
            'figure' in segment ? (
              <Tooltip
                key={i}
                content={`Điền từ lượt truy vấn ${segment.query_run_id} · ${segment.source_title}`}
              >
                <button type="button" className="text-info da-body-strong hover:underline">
                  {segment.figure}
                </button>
              </Tooltip>
            ) : (
              <span key={i}>{segment.text}</span>
            ),
          )}
        </p>
        <p className="max-w-[520px] text-muted-foreground da-caption">
          Số màu xanh do hệ thống điền từ truy vấn lúc mở trang, không do mô hình viết; trỏ vào số
          để xem lượt truy vấn và nguồn.
        </p>
      </Card>
      {page.claims.length > 0 && (
        <Card title={`Khẳng định · ${valid} còn hiệu lực`}>
          <ClaimsTable claims={page.claims} />
        </Card>
      )}
    </>
  );
}

function ClaimsTable({ claims }: { claims: KnowledgeClaim[] }) {
  const view = useTableView(claims, {
    search: (row) => `${row.text} ${row.source_title} ${row.source_detail}`,
    filters: [
      {
        id: 'status',
        label: 'Trạng thái',
        value: (row) => row.status,
        text: (status) => (status === 'expired' ? 'Hết hạn' : 'Hiệu lực'),
      },
    ],
    sorts: [
      { id: 'text', value: (row) => row.text },
      { id: 'source', value: (row) => row.source_title },
      { id: 'valid', value: (row) => row.valid_text },
      { id: 'status', value: (row) => row.status },
    ],
    pageSize: 50,
  });
  return (
    <div className="flex flex-col gap-da-md">
      <TableToolbar view={view} placeholder="Tìm khẳng định…" searchLabel="Tìm khẳng định" />
      <table className="w-full table-fixed border-collapse overflow-hidden rounded-md border border-border">
        <thead>
          <tr className="border-b border-border bg-subtle text-left text-muted-foreground da-caption">
            <SortableTh view={view} column="text" className="px-da-lg py-da-md font-normal">
              Khẳng định
            </SortableTh>
            <SortableTh view={view} column="source" className="px-da-lg py-da-md font-normal">
              Nguồn
            </SortableTh>
            <SortableTh view={view} column="valid" className="w-[120px] px-da-lg py-da-md font-normal">
              Hiệu lực
            </SortableTh>
            <SortableTh view={view} column="status" className="w-[100px] px-da-lg py-da-md font-normal">
              Trạng thái
            </SortableTh>
          </tr>
        </thead>
        <tbody>
          {view.rows.map((c) => {
            const expired = c.status === 'expired';
            return (
              <tr key={c.text} className="border-b border-border last:border-b-0 da-body-small">
                <td className={cn('px-da-lg py-[10px]', expired ? 'text-muted-foreground' : 'text-foreground')}>
                  {c.text}
                </td>
                <td className="px-da-lg py-[10px]">
                  <p className="text-foreground da-body-strong">{c.source_title}</p>
                  <p className="text-muted-foreground da-caption">{c.source_detail}</p>
                </td>
                <td className={cn('px-da-lg py-[10px]', expired ? 'text-muted-foreground' : 'text-foreground')}>
                  {c.valid_text}
                </td>
                <td className="px-da-lg py-[10px]">
                  <StatusDot tone={expired ? 'neutral' : 'success'}>{expired ? 'Hết hạn' : 'Hiệu lực'}</StatusDot>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <TablePager view={view} />
    </div>
  );
}

function Versions({ pageId }: { pageId: string }) {
  const versions = useV1(() => listKnowledgePageVersions(pageId), [pageId]);
  return (
    <Card title="Phiên bản">
      <AsyncRegion state={versions} what="phiên bản">
        {(page) => (
          <ul className="flex flex-col">
            {page.items.map((v) => (
              <li
                key={v.id}
                className="flex items-center gap-[10px] border-b border-border py-[6px] last:border-b-0"
              >
                <span className="w-[30px] text-foreground da-mono">v{v.version}</span>
                <span className="min-w-0 flex-1 text-foreground da-body-small">
                  {[v.change_note, formatDate(v.created_at)].filter(Boolean).join(' · ')}
                </span>
                <StatusDot tone={v.status === 'certified' ? 'success' : 'neutral'}>
                  {v.status === 'certified' ? 'Đang phục vụ' : 'Đã thay'}
                </StatusDot>
              </li>
            ))}
          </ul>
        )}
      </AsyncRegion>
    </Card>
  );
}

function PolicyCard({ page }: { page: KnowledgePage }) {
  return (
    <Card title="Chính sách trang">
      <dl className="flex flex-col gap-da-lg">
        {[
          ['Data Space nguồn', page.policy.data_spaces_text],
          ['Mức phân loại', page.policy.classification],
          ['Người xem', page.policy.viewer_rule],
        ].map(([label, value]) => (
          <div key={label} className="flex flex-col gap-[2px]">
            <dt className="text-muted-foreground da-caption">{label}</dt>
            <dd className="text-foreground da-body-small">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="text-muted-foreground da-caption">
        Chính sách tính lại khi một nguồn đổi quyền; thiếu quyền thì trang bị ẩn cả ở mục lục.
      </p>
    </Card>
  );
}

function LinksCard({ page }: { page: KnowledgePage }) {
  const { navigate } = useAppState();
  return (
    <Card title="Liên kết">
      {page.links.length === 0 ? (
        <p className="text-muted-foreground da-body-small">Chưa có liên kết.</p>
      ) : (
        <ul className="flex flex-col gap-da-lg">
          {page.links.map((l) => (
            <li key={`${l.direction}-${l.title}`} className="flex items-start gap-da-md da-caption">
              <span className="w-7 shrink-0 text-muted-foreground">
                {l.direction === 'out' ? 'Đi' : 'Đến'}
              </span>
              {l.page_id ? (
                <button
                  type="button"
                  onClick={() => navigate('data', 'KNOWLEDGE', l.page_id ?? undefined)}
                  className="text-left text-foreground hover:underline"
                >
                  {l.title}
                </button>
              ) : (
                <span className="text-foreground">{l.title}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
