import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Checkbox } from '../../design-system/Checkbox';
import { EmptyState } from '../../design-system/EmptyState';
import { Input } from '../../design-system/Input';
import { Menu } from '../../design-system/Menu';
import { Notice } from '../../design-system/Notice';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { formatTime } from '../../lib/formatVN';
import {
  listMonitors,
  muteMonitor,
  type Monitor,
  type MonitorState,
  type RuleKind,
} from '../../lib/v1/monitors';
import { useV1 } from '../../lib/v1/useV1';
import { whenText } from './whenText';

/*
 * "P08 · Theo dõi · Danh sách" (2270:15950, UC-08-01): every monitor of the Data Space, newest
 * firing first. Filters on the left (state, owner, kind of rule) combine across groups and widen
 * within one; search matches the name and the metric. A row's menu opens its latest alert or
 * pauses it (A2 pauses without an end date; "Bật lại" resumes it). A monitor on an uncertified
 * metric still alerts, but only inside the app.
 */
const STATE: Record<
  MonitorState,
  { label: string; tone: 'error' | 'warning' | 'success' | 'idle' | 'neutral' }
> = {
  alerting: { label: 'Đang cảnh báo', tone: 'error' },
  early_warning: { label: 'Cảnh báo sớm', tone: 'warning' },
  normal: { label: 'Bình thường', tone: 'success' },
  insufficient_data: { label: 'Không đủ dữ liệu', tone: 'idle' },
  muted: { label: 'Tạm dừng', tone: 'neutral' },
};
const KIND: Record<RuleKind, string> = {
  threshold: 'Ngưỡng',
  change_vs_previous: 'Thay đổi',
  seasonal_anomaly: 'Bất thường',
};

type Owner = 'me' | string;
interface Filters {
  state: Set<MonitorState>;
  owner: Set<Owner>;
  kind: Set<RuleKind>;
}

const ownerOf = (m: Monitor): Owner => (m.owner_is_me ? 'me' : m.owner_name);

export default function WatchListPage() {
  const { navigate, dataSpace } = useAppState();
  const monitors = useV1(listMonitors, []);
  const items = monitors.data?.items ?? [];
  const alerting = items.filter((m) => m.state === 'alerting').length;
  const lastCheck = items.reduce<string | null>(
    (latest, m) =>
      m.last_checked_at && (!latest || m.last_checked_at > latest) ? m.last_checked_at : latest,
    null,
  );

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Phân tích', 'Theo dõi']}
        title="Theo dõi"
        description={
          monitors.data
            ? [
                `${items.length} theo dõi trong ${dataSpace?.name ?? 'Data Space này'}`,
                `${alerting} đang cảnh báo`,
                lastCheck ? `kiểm tra gần nhất ${formatTime(lastCheck)}` : null,
              ]
                .filter(Boolean)
                .join(' · ')
            : undefined
        }
        actions={
          <>
            <Tooltip content="Thư viện mẫu theo dõi đang được làm.">
              <Button disabled>Nhập từ mẫu</Button>
            </Tooltip>
            <Button kind="primary" onClick={() => navigate('analysis', 'WATCH_NEW')}>
              Tạo theo dõi
            </Button>
          </>
        }
      />
      <div className="px-da-3xl pb-da-2xl pt-da-xl">
        <AsyncRegion state={monitors} what="theo dõi" skeletonRows={6}>
          {() => <WatchList items={items} onChanged={monitors.reload} />}
        </AsyncRegion>
      </div>
    </div>
  );
}

function WatchList({ items, onChanged }: { items: Monitor[]; onChanged: () => void }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [filters, setFilters] = useState<Filters>({
    state: new Set(),
    owner: new Set(),
    kind: new Set(),
  });
  const [query, setQuery] = useState('');
  const [error, setError] = useState<string>();

  const q = query.trim().toLowerCase();
  const shown = items
    .filter(
      (m) =>
        (filters.state.size === 0 || filters.state.has(m.state)) &&
        (filters.owner.size === 0 || filters.owner.has(ownerOf(m))) &&
        (filters.kind.size === 0 || filters.kind.has(m.rule_kind)) &&
        (!q || `${m.name} ${m.metric_text}`.toLowerCase().includes(q)),
    )
    .sort((a, b) => (b.last_fired_at ?? '').localeCompare(a.last_fired_at ?? ''));

  const toggle = <K extends keyof Filters>(group: K, value: string, on: boolean) =>
    setFilters((f) => {
      const next = new Set<string>(f[group]);
      if (on) next.add(value);
      else next.delete(value);
      return { ...f, [group]: next };
    });

  const owners = [
    ['me', 'Tôi'] as const,
    ...[...new Set(items.filter((m) => !m.owner_is_me).map((m) => m.owner_name))].map(
      (name) => [name, name] as const,
    ),
  ];

  const mute = async (m: Monitor, on: boolean) => {
    setError(undefined);
    try {
      await muteMonitor(m.id, on);
      toast({
        tone: 'success',
        message: on ? `Đã tạm dừng ${m.name}` : `Đã bật lại ${m.name}`,
      });
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="flex items-start gap-da-xl max-[1023px]:flex-col">
      <div className="flex w-[172px] shrink-0 flex-col gap-da-lg max-[1023px]:w-full">
        <FilterGroup title="Trạng thái">
          {(Object.keys(STATE) as MonitorState[]).map((s) => (
            <FilterRow
              key={s}
              count={items.filter((m) => m.state === s).length}
              checked={filters.state.has(s)}
              onChange={(on) => toggle('state', s, on)}
              label={STATE[s].label}
            >
              <StatusDot tone={STATE[s].tone}>{STATE[s].label}</StatusDot>
            </FilterRow>
          ))}
        </FilterGroup>
        <FilterGroup title="Chủ sở hữu">
          {owners.map(([value, label]) => (
            <FilterRow
              key={value}
              count={items.filter((m) => ownerOf(m) === value).length}
              checked={filters.owner.has(value)}
              onChange={(on) => toggle('owner', value, on)}
              label={label}
            />
          ))}
        </FilterGroup>
        <FilterGroup title="Kiểu điều kiện">
          {(Object.keys(KIND) as RuleKind[]).map((k) => (
            <FilterRow
              key={k}
              count={items.filter((m) => m.rule_kind === k).length}
              checked={filters.kind.has(k)}
              onChange={(on) => toggle('kind', k, on)}
              label={KIND[k]}
            />
          ))}
        </FilterGroup>
      </div>
      <div className="flex min-w-0 flex-1 flex-col gap-da-md">
        <div className="flex items-center gap-da-lg">
          <Input
            icon="search"
            aria-label="Tìm theo dõi"
            placeholder="Tìm theo tên, chỉ số…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="w-[320px] max-w-full"
          />
          <span className="ml-auto text-muted-foreground da-body-small">
            Sắp xếp: Kích hoạt gần nhất
          </span>
        </div>
        {error && <Notice tone="error">Không thực hiện được: {error}</Notice>}
        {shown.length === 0 ? (
          <EmptyState
            title="Không có theo dõi khớp bộ lọc"
            description="Bỏ bớt bộ lọc hoặc đổi từ khoá tìm."
          />
        ) : (
          <table className="w-full table-fixed border-collapse da-body-small">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground da-caption">
                <th className="px-da-md py-da-md font-normal">Tên</th>
                <th className="w-[236px] px-da-md py-da-md font-normal">Điều kiện</th>
                <th className="w-[166px] px-da-md py-da-md font-normal">Trạng thái</th>
                <th className="w-[156px] px-da-md py-da-md font-normal">Kích hoạt gần nhất</th>
                <th className="w-[136px] px-da-md py-da-md font-normal">Gửi tới</th>
                <th className="w-[40px]" />
              </tr>
            </thead>
            <tbody>
              {shown.map((m) => (
                <tr key={m.id} className="border-b border-border text-foreground">
                  <td className="px-da-md py-[10px]">
                    <p className="truncate">{m.name}</p>
                    <p className="truncate text-muted-foreground da-caption">{m.metric_text}</p>
                  </td>
                  <td className="px-da-md py-[10px]">{m.condition_text}</td>
                  <td className="px-da-md py-[10px]">
                    <StatusDot tone={STATE[m.state].tone}>{STATE[m.state].label}</StatusDot>
                    {m.noise_warning && (
                      <p className="text-warning da-caption">Dễ gây nhiễu (BR-32)</p>
                    )}
                  </td>
                  <td className="px-da-md py-[10px]">
                    {m.last_fired_at ? whenText(m.last_fired_at) : '—'}
                  </td>
                  <td className="truncate px-da-md py-[10px]">{m.channels_text}</td>
                  <td className="py-[10px]">
                    <Menu
                      label={`Thao tác với ${m.name}`}
                      items={[
                        ...(m.last_alert_id
                          ? [
                              {
                                label: 'Mở cảnh báo gần nhất',
                                icon: 'bell' as const,
                                onSelect: () =>
                                  navigate('analysis', 'ALERTS', m.last_alert_id ?? undefined),
                              },
                            ]
                          : []),
                        m.state === 'muted'
                          ? { label: 'Bật lại', onSelect: () => mute(m, false) }
                          : { label: 'Tạm dừng', onSelect: () => mute(m, true) },
                      ]}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="text-muted-foreground da-caption">
          Theo dõi trên chỉ số chưa chứng nhận vẫn tạo được nhưng chỉ báo trong ứng dụng, không gửi
          ra Teams, Zalo hay email.
        </p>
      </div>
    </div>
  );
}

function FilterGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <fieldset className="flex flex-col gap-da-xs">
      <legend className="pb-da-xs text-muted-foreground da-caption">{title}</legend>
      {children}
    </fieldset>
  );
}

// The label falls back to plain text when the row has no richer content (the state's dot).
function FilterRow({
  label,
  count,
  checked,
  onChange,
  children,
}: {
  label: string;
  count: number;
  checked: boolean;
  onChange: (on: boolean) => void;
  children?: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-da-sm da-body-small">
      <Checkbox checked={checked} onChange={onChange}>
        {children ?? label}
      </Checkbox>
      <span className="ml-auto text-muted-foreground da-caption">{count}</span>
    </div>
  );
}
