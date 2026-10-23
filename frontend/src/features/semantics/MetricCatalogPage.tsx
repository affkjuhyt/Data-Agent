import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { FilterChips } from '../../design-system/FilterChips';
import { Icon } from '../../design-system/Icon';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { StatusDot } from '../../design-system/StatusDot';
import { Tabs } from '../../design-system/Tabs';
import { cn } from '../../lib/utils';
import {
  getMetricProfile,
  listCatalogMetrics,
  type CatalogMetric,
  type MetricProfile,
} from '../../lib/v1/metricCatalog';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "v3.1 · Dữ liệu & tri thức" (2056:1150, UC-07-01): the metric catalog. Left, every metric with
 * its code, version, certification and who uses it, searched and filtered; right, the one picked:
 * its definition and lineage from source to use, the agents relying on it and a change an agent
 * drafted ("Định nghĩa & lineage"), its versions (2099:11054), where it is used (2099:11344) and
 * its quality checks (2099:11632). Editing opens the definition page (Chỉ số · Định nghĩa).
 */
type Filter = 'all' | 'certified' | 'draft' | string;
type Tab = 'definition' | 'versions' | 'usage' | 'quality';

const STATUS = {
  certified: { label: 'Chứng nhận', tone: 'success' },
  draft: { label: 'Nháp', tone: 'warning' },
} as const;
const VERSION = {
  certified: <StatusDot tone="success">Chứng nhận</StatusDot>,
  draft: <StatusDot tone="warning">Nháp</StatusDot>,
  archived: <Badge tone="neutral">Lưu trữ</Badge>,
};

export default function MetricCatalogPage() {
  const { routeId } = useAppState();
  const metrics = useV1(['listCatalogMetrics'], listCatalogMetrics);
  const [picked, setPicked] = useState<string | undefined>(routeId || undefined);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const items = metrics.data?.items ?? [];
  const id = picked ?? items[0]?.id;
  const domains = [...new Set(items.map((m) => m.domain))];
  const q = query.trim().toLowerCase();
  const shown = items.filter(
    (m) =>
      (filter === 'all' || m.status === filter || m.domain === filter) &&
      (!q || `${m.code} ${m.name}`.toLowerCase().includes(q)),
  );

  return (
    <div className="flex min-h-full max-[1023px]:flex-col">
      <div className="flex w-[360px] shrink-0 flex-col gap-da-sm border-r border-border px-da-md pt-da-md max-[1023px]:w-full">
        <Input
          icon="search"
          aria-label="Tìm chỉ số"
          placeholder="Tìm chỉ số, cột, định nghĩa…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <FilterChips<Filter>
          label="Lọc chỉ số"
          value={filter}
          onChange={setFilter}
          items={[
            { value: 'all', label: 'Tất cả' },
            { value: 'certified', label: 'Chứng nhận' },
            { value: 'draft', label: 'Nháp' },
            ...domains.map((d) => ({ value: d, label: `Domain: ${d}` })),
          ]}
        />
        <AsyncRegion state={metrics} what="chỉ số" skeletonRows={6}>
          {() => (
            <ul aria-label="Chỉ số" className="flex flex-col">
              {shown.map((m) => (
                <MetricCard
                  key={m.id}
                  metric={m}
                  selected={m.id === id}
                  onSelect={() => setPicked(m.id)}
                />
              ))}
              {shown.length === 0 && (
                <li className="py-da-md text-muted-foreground da-body-small">
                  Không có chỉ số khớp.
                </li>
              )}
            </ul>
          )}
        </AsyncRegion>
      </div>
      <div className="min-w-0 flex-1 px-da-xl pb-da-2xl pt-da-lg">
        {id && <ProfileView key={id} id={id} />}
      </div>
    </div>
  );
}

function MetricCard({
  metric,
  selected,
  onSelect,
}: {
  metric: CatalogMetric;
  selected: boolean;
  onSelect: () => void;
}) {
  const status = STATUS[metric.status];
  return (
    <li>
      <button
        type="button"
        aria-current={selected ? 'true' : undefined}
        onClick={onSelect}
        className={cn(
          'flex w-full flex-col gap-[2px] rounded-md border px-da-sm py-da-sm text-left',
          selected ? 'border-border bg-card' : 'border-transparent hover:bg-subtle',
        )}
      >
        <span className="flex items-center gap-da-sm">
          <span className="text-foreground da-mono">
            {metric.code} v{metric.version}
          </span>
          <span className="ml-auto">
            <StatusDot tone={status.tone}>{status.label}</StatusDot>
          </span>
        </span>
        <span className="text-foreground da-body-small">{metric.name}</span>
        <span className="text-muted-foreground da-caption">{metric.usage_text}</span>
      </button>
    </li>
  );
}

function ProfileView({ id }: { id: string }) {
  const { navigate } = useAppState();
  const profile = useV1(['getMetricProfile', id], () => getMetricProfile(id));
  const [tab, setTab] = useState<Tab>('definition');

  const ask = (p: MetricProfile) => {
    // The ask page reads ?q= once and asks it, as from Trang chủ.
    window.history.replaceState(
      null,
      '',
      `/analysis/ask?q=${encodeURIComponent(`Giải thích chỉ số ${p.code}`)}`,
    );
    navigate('analysis', 'ASK');
  };

  return (
    <AsyncRegion state={profile} what="chỉ số">
      {(p) => (
        <div className="flex flex-col gap-da-md">
          <div className="flex items-start gap-da-md">
            <div className="flex min-w-0 flex-1 flex-col gap-da-xs">
              <h1 className="flex items-center gap-[10px] text-foreground da-heading-1">
                {p.name}
                <StatusDot tone={STATUS[p.status].tone}>{STATUS[p.status].label}</StatusDot>
              </h1>
              <p className="text-muted-foreground da-mono">{p.meta_line}</p>
            </div>
            <Button icon="agent" onClick={() => ask(p)}>
              Hỏi agent về chỉ số này
            </Button>
          </div>
          <Notice tone="info">
            Chỉ số là định nghĩa dùng chung: hỏi bằng lời, kéo thả hay agent báo cáo đều tính theo
            cùng công thức này.
          </Notice>
          <Tabs<Tab>
            label="Thông tin chỉ số"
            value={tab}
            onChange={setTab}
            items={[
              { value: 'definition', label: 'Định nghĩa & lineage' },
              { value: 'versions', label: 'Phiên bản', count: p.versions.length },
              { value: 'usage', label: 'Sử dụng' },
              { value: 'quality', label: 'Chất lượng' },
            ]}
          />
          {tab === 'definition' && (
            <DefinitionTab profile={p} onOpen={() => navigate('data', 'METRIC', p.id)} />
          )}
          {tab === 'versions' && (
            <>
              <VersionTable versions={p.versions} />
              {p.versions.length > 1 && (
                <div>
                  <Button onClick={() => navigate('data', 'METRIC', p.id)}>
                    So sánh v{p.versions[1].version} ↔ v{p.versions[0].version}
                  </Button>
                </div>
              )}
            </>
          )}
          {tab === 'usage' &&
            (p.usage.length ? (
              <>
                <UsageTable rows={p.usage} />
                <p className="text-muted-foreground da-caption">{p.usage_note}</p>
              </>
            ) : (
              <p className="text-muted-foreground da-body-small">{p.usage_note}</p>
            ))}
          {tab === 'quality' &&
            (p.quality.length ? (
              <ul className="flex flex-col gap-da-sm da-body-small">
                {p.quality.map((q) => (
                  <li key={q.text} className="flex items-center gap-da-md text-foreground">
                    <StatusDot tone={q.ok ? 'success' : 'warning'}>{q.label}</StatusDot>
                    {q.text}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-muted-foreground da-body-small">Chưa có kiểm tra chất lượng.</p>
            ))}
        </div>
      )}
    </AsyncRegion>
  );
}

function DefinitionTab({ profile, onOpen }: { profile: MetricProfile; onOpen: () => void }) {
  return (
    <>
      <Card title="Định nghĩa">
        <pre className="m-0 whitespace-pre-wrap border-b border-border pb-da-md text-foreground da-mono">
          {profile.definition.expression}
        </pre>
        <p className="text-muted-foreground da-body-small">{profile.definition.note}</p>
      </Card>
      {profile.lineage.length > 0 && (
        <Card title="Lineage">
          <ol aria-label="Lineage" className="flex items-stretch gap-da-xs max-[1279px]:flex-wrap">
            {profile.lineage.map((l, i) => (
              <li key={`${i}-${l.title}`} className="flex items-center gap-da-xs">
                {i > 0 && <Icon name="chevron-right" className="text-muted-foreground" />}
                <span
                  className={cn(
                    'flex w-[136px] flex-col gap-[2px] rounded-md border px-da-sm py-da-sm',
                    l.current ? 'border-primary bg-card' : 'border-border bg-subtle',
                  )}
                >
                  <span className="text-muted-foreground da-overline">{l.kind}</span>
                  <span
                    className={cn('da-body-small', l.current ? 'text-primary' : 'text-foreground')}
                  >
                    {l.title}
                  </span>
                  {l.detail && (
                    <span className="break-all text-muted-foreground da-mono">{l.detail}</span>
                  )}
                </span>
              </li>
            ))}
          </ol>
        </Card>
      )}
      {profile.agents.length > 0 && (
        <Card title="Agent đang dùng">
          <ul className="flex flex-wrap gap-da-xl">
            {profile.agents.map((a) => (
              <li key={a.name} className="flex items-center gap-da-sm">
                <Icon name="agent" className="text-muted-foreground" />
                <span className="flex flex-col">
                  <span className="text-foreground da-body-small">{a.name}</span>
                  <StatusDot tone={a.tone}>{a.state}</StatusDot>
                </span>
              </li>
            ))}
          </ul>
        </Card>
      )}
      {profile.change_notice && (
        <section className="flex items-center gap-da-md rounded-md border border-border bg-card p-da-md">
          <Icon name="agent" className="text-info" />
          <div className="flex min-w-0 flex-1 flex-col gap-[2px]">
            <span className="text-info da-body-small">{profile.change_notice.title}</span>
            <span className="text-foreground da-caption">{profile.change_notice.text}</span>
          </div>
          <Button kind="ghost" onClick={onOpen}>
            Xem so sánh
          </Button>
          <Button kind="primary" onClick={onOpen}>
            Mở bản v{profile.change_notice.draft_version} nháp
          </Button>
        </section>
      )}
    </>
  );
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-da-md rounded-md border border-border bg-card p-da-md">
      <h2 className="text-foreground da-heading-3">{title}</h2>
      {children}
    </section>
  );
}

const VERSION_LABEL = { certified: 'Chứng nhận', draft: 'Nháp', archived: 'Lưu trữ' } as const;

function VersionTable({ versions }: { versions: MetricProfile['versions'] }) {
  const view = useTableView(versions, {
    search: (v) => `v${v.version} ${v.change} ${v.effective} ${VERSION_LABEL[v.status]}`,
    filters: [{ id: 'status', label: 'Trạng thái', value: (v) => VERSION_LABEL[v.status] }],
    sorts: [
      { id: 'version', value: (v) => v.version },
      { id: 'change', value: (v) => v.change },
      { id: 'effective', value: (v) => v.effective },
      { id: 'status', value: (v) => VERSION_LABEL[v.status] },
    ],
    pageSize: 50,
  });
  return (
    <>
      <TableToolbar view={view} placeholder="Tìm phiên bản…" searchLabel="Tìm phiên bản chỉ số" />
      <table className="w-full border-collapse overflow-hidden rounded-md border border-border da-body-small">
        <thead>
          <tr className="border-b border-border bg-subtle text-left text-muted-foreground da-caption">
            <SortableTh view={view} column="version" className="px-da-sm py-da-sm font-normal">
              Phiên bản
            </SortableTh>
            <SortableTh view={view} column="change" className="px-da-sm py-da-sm font-normal">
              Thay đổi
            </SortableTh>
            <SortableTh view={view} column="effective" className="px-da-sm py-da-sm font-normal">
              Hiệu lực
            </SortableTh>
            <SortableTh view={view} column="status" className="px-da-sm py-da-sm font-normal">
              Trạng thái
            </SortableTh>
          </tr>
        </thead>
        <tbody>
          {view.rows.map((v) => (
            <tr key={v.version} className="border-b border-border text-foreground last:border-b-0">
              <td className="px-da-sm py-[7px]">{`v${v.version}`}</td>
              <td className="px-da-sm py-[7px]">{v.change}</td>
              <td className="px-da-sm py-[7px]">{v.effective}</td>
              <td className="px-da-sm py-[7px]">{VERSION[v.status]}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <TablePager view={view} />
    </>
  );
}

function UsageTable({ rows }: { rows: MetricProfile['usage'] }) {
  const view = useTableView(rows, {
    search: (u) => `${u.place} ${u.kind} ${u.version} ${u.last}`,
    filters: [{ id: 'kind', label: 'Loại', value: (u) => u.kind }],
    sorts: [
      { id: 'place', value: (u) => u.place },
      { id: 'kind', value: (u) => u.kind },
      { id: 'version', value: (u) => u.version },
      { id: 'last', value: (u) => u.last },
    ],
    pageSize: 50,
  });
  return (
    <>
      <TableToolbar view={view} placeholder="Tìm nơi dùng…" searchLabel="Tìm nơi dùng" />
      <table className="w-full border-collapse overflow-hidden rounded-md border border-border da-body-small">
        <thead>
          <tr className="border-b border-border bg-subtle text-left text-muted-foreground da-caption">
            <SortableTh view={view} column="place" className="px-da-sm py-da-sm font-normal">
              Nơi dùng
            </SortableTh>
            <SortableTh view={view} column="kind" className="px-da-sm py-da-sm font-normal">
              Loại
            </SortableTh>
            <SortableTh view={view} column="version" className="px-da-sm py-da-sm font-normal">
              Phiên bản
            </SortableTh>
            <SortableTh view={view} column="last" className="px-da-sm py-da-sm font-normal">
              Lần cuối
            </SortableTh>
          </tr>
        </thead>
        <tbody>
          {view.rows.map((u) => (
            <tr key={`${u.place}-${u.kind}`} className="border-b border-border text-foreground last:border-b-0">
              <td className="px-da-sm py-[7px]">{u.place}</td>
              <td className="px-da-sm py-[7px]">{u.kind}</td>
              <td className="px-da-sm py-[7px]">{u.version}</td>
              <td className="px-da-sm py-[7px]">{u.last}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <TablePager view={view} />
    </>
  );
}
