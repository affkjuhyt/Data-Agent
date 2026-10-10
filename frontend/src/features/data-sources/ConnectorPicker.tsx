import { useMemo, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { FilterChips } from '../../design-system/FilterChips';
import { Icon, type IconName } from '../../design-system/Icon';
import { Input } from '../../design-system/Input';
import { StatusDot } from '../../design-system/StatusDot';
import { Tooltip } from '../../design-system/Tooltip';
import { cn } from '../../lib/utils';
import {
  listConnectors,
  listSourceRows,
  type Connector,
  type ConnectorCategory,
  type SourceRow,
} from '../../lib/v1/sources';
import { useV1 } from '../../lib/v1/useV1';
import { freshnessText, sourceTone } from './sourceFormat';

/*
 * "v3.1 · Kết nối dữ liệu · Chưa có nguồn" (2165:17285, UC-04-01): step 1 of connecting, and
 * what the source list shows while there is no source. The connector catalogue on the left
 * (search, category chips, 235px cards), the connected sources on the right (400px).
 * A database connector opens the connect flow, REST API opens Kết nối API, CSV / Excel opens
 * Tải tệp; connectors without a set-up screen yet are shown but not clickable.
 */
const CATEGORY: Record<ConnectorCategory, string> = {
  database: 'Cơ sở dữ liệu',
  warehouse: 'Kho dữ liệu',
  app: 'Ứng dụng & API',
  file: 'Tệp',
  document: 'Tài liệu & phi cấu trúc',
};

type Filter = 'all' | ConnectorCategory;

type Mark = {
  text?: string;
  icon?: IconName;
  tone: 'info' | 'subtle' | 'accent' | 'success' | 'warning';
};

/* "ConnectorIcon" (2243:871): the vendor's initials until official logos are in the repo. */
const MARK: Record<string, Mark> = {
  oracle: { text: 'ORA', tone: 'info' },
  postgresql: { text: 'PG', tone: 'info' },
  sqlserver: { text: 'SQL', tone: 'info' },
  mysql: { text: 'MY', tone: 'info' },
  snowflake: { text: 'SF', tone: 'info' },
  bigquery: { text: 'BQ', tone: 'info' },
  databricks: { text: 'DBX', tone: 'info' },
  clickhouse: { text: 'CH', tone: 'info' },
  rest: { icon: 'api', tone: 'subtle' },
  kafka: { icon: 'stream', tone: 'accent' },
  csv: { icon: 'sheet', tone: 'success' },
  sharepoint: { text: 'SP', tone: 'success' },
  folder: { icon: 'folder', tone: 'success' },
  email: { icon: 'mail', tone: 'warning' },
  recording: { icon: 'phone', tone: 'warning' },
  custom: { icon: 'plus', tone: 'subtle' },
};

const MARK_TONE: Record<Mark['tone'], string> = {
  info: 'bg-info-subtle text-info',
  subtle: 'bg-subtle text-foreground',
  accent: 'bg-accent text-primary',
  success: 'bg-success-subtle text-success',
  warning: 'bg-warning-subtle text-warning',
};

function ConnectorIcon({ code, size = 40 }: { code: string; size?: 32 | 40 }) {
  const mark = MARK[code] ?? { icon: 'database' as const, tone: 'subtle' as const };
  return (
    <span
      aria-hidden
      className={cn(
        'inline-flex shrink-0 items-center justify-center rounded-md font-bold da-label',
        size === 40 ? 'size-10' : 'size-8',
        MARK_TONE[mark.tone],
      )}
    >
      {mark.icon ? <Icon name={mark.icon} /> : mark.text}
    </span>
  );
}

/** Where a connector card leads, or null when it has no set-up screen yet. */
function targetOf(connector: Connector): { page: string; id?: string } | null {
  if (connector.code === 'csv') return { page: 'UPLOAD' };
  if (connector.code === 'rest') return { page: 'CONNECT_API' };
  if (connector.form) return { page: 'CONNECT', id: connector.code };
  return null;
}

export function ConnectorPicker() {
  const { navigate } = useAppState();
  const connectors = useV1(listConnectors, []);
  const sources = useV1(listSourceRows, []);
  const [filter, setFilter] = useState<Filter>('all');
  const [query, setQuery] = useState('');

  const all = connectors.data?.items ?? [];
  const visible = useMemo(() => {
    const q = query.trim().toLowerCase();
    return all
      .filter((c) => filter === 'all' || c.category === filter)
      .filter(
        (c) =>
          !q || c.name.toLowerCase().includes(q) || CATEGORY[c.category].toLowerCase().includes(q),
      );
  }, [all, filter, query]);

  return (
    <div className="flex min-h-full max-[1023px]:flex-col">
      <div className="flex min-w-0 flex-1 flex-col gap-da-xl px-da-3xl py-da-xl">
        <PageHeader
          title="Kết nối dữ liệu"
          description="Chọn nơi dữ liệu của bạn đang nằm. Data Agent chỉ đọc, kiểm tra kết nối trước khi dùng và che dữ liệu cá nhân tự động."
          actions={
            <Tooltip content="Gửi yêu cầu connector đang được làm.">
              <Button disabled>Yêu cầu connector mới</Button>
            </Tooltip>
          }
        />
        <div className="flex flex-wrap items-center gap-da-lg">
          <Input
            className="w-[360px]"
            icon="search"
            placeholder="Tìm nguồn: Oracle, SharePoint, API…"
            aria-label="Tìm connector"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <FilterChips<Filter>
            label="Loại connector"
            value={filter}
            onChange={setFilter}
            items={[
              { value: 'all', label: `Tất cả · ${all.length}` },
              ...(Object.keys(CATEGORY) as ConnectorCategory[]).map((c) => ({
                value: c,
                label: CATEGORY[c],
              })),
            ]}
          />
        </div>
        <AsyncRegion state={connectors} what="danh sách connector" skeletonRows={4}>
          {() => (
            <ul className="grid grid-cols-[repeat(auto-fill,minmax(235px,1fr))] gap-da-lg">
              {visible.map((connector) => (
                <li key={connector.code}>
                  <ConnectorCard
                    connector={connector}
                    onOpen={(target) => navigate('data', target.page, target.id)}
                  />
                </li>
              ))}
              {visible.length === 0 && (
                <li className="text-muted-foreground da-body-small">
                  Không có connector nào khớp.
                </li>
              )}
            </ul>
          )}
        </AsyncRegion>
      </div>
      <aside
        aria-label="Nguồn đã kết nối"
        className="flex w-[400px] shrink-0 flex-col gap-da-lg border-l border-border bg-card p-da-xl max-[1023px]:w-full max-[1023px]:border-l-0 max-[1023px]:border-t"
      >
        <ConnectedList sources={sources.data?.items} />
      </aside>
    </div>
  );
}

function ConnectorCard({
  connector,
  onOpen,
}: {
  connector: Connector;
  onOpen: (target: { page: string; id?: string }) => void;
}) {
  const target = targetOf(connector);
  const body = (
    <>
      <ConnectorIcon code={connector.code} />
      <span className="flex min-w-0 flex-1 flex-col items-start gap-[2px]">
        <span className="text-foreground da-body-strong">{connector.name}</span>
        <span className="text-muted-foreground da-caption">
          {connector.subtitle ?? CATEGORY[connector.category]}
        </span>
        {connector.connected_count > 0 && (
          <StatusDot tone="success">Đã kết nối · {connector.connected_count}</StatusDot>
        )}
        {connector.badge === 'new' && <StatusDot tone="info">Mới</StatusDot>}
        {connector.badge === 'upload' && <Badge>Tải lên</Badge>}
        {connector.badge === 'engineering' && <Badge>Cho đội kỹ thuật</Badge>}
      </span>
    </>
  );
  const card =
    'flex h-full w-full items-center gap-da-lg rounded-lg border border-border bg-card p-da-md text-left';
  if (!target)
    return (
      <Tooltip content="Connector này chưa có màn thiết lập." className="flex h-full">
        <div className={card}>{body}</div>
      </Tooltip>
    );
  return (
    <button
      type="button"
      onClick={() => onOpen(target)}
      className={cn(card, 'hover:border-border-strong hover:bg-subtle')}
    >
      {body}
    </button>
  );
}

function ConnectedList({ sources }: { sources: SourceRow[] | undefined }) {
  const live = (sources ?? []).filter((s) => s.status !== 'draft' && s.status !== 'archived');
  const broken = live.filter((s) => s.status === 'needs_fix').length;
  return (
    <>
      <div className="flex items-start gap-da-md">
        <h2 className="flex-1 text-foreground da-heading-2">Đã kết nối · {live.length}</h2>
        {live.length > 0 && (
          <span className="text-muted-foreground da-caption">
            {live.length - broken} ổn · {broken} cần sửa
          </span>
        )}
      </div>
      {sources && live.length === 0 && (
        <p className="text-muted-foreground da-body">
          Chưa có nguồn nào. Chọn một connector bên trái để bắt đầu — thường mất khoảng 5 phút.
        </p>
      )}
      <ul className="flex flex-col gap-da-md">
        {live.map((source) => (
          <li
            key={source.id}
            className="flex flex-col gap-da-md rounded-lg border border-border p-da-md"
          >
            <div className="flex items-center gap-[10px]">
              <ConnectorIcon code={source.connector} size={32} />
              <span className="min-w-0 flex-1 truncate text-foreground da-body-strong">
                {source.name}
              </span>
              <StatusDot tone={sourceTone(source)}>{source.status_label}</StatusDot>
            </div>
            <p className="text-foreground da-body-small">
              {source.status_reason ?? `Mới nhất ${freshnessText(source)}`}
            </p>
          </li>
        ))}
      </ul>
    </>
  );
}
