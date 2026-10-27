import type { ReactNode } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Notice } from '../../design-system/Notice';
import {
  SortableTh,
  TablePager,
  TableToolbar,
  tableEmptyRow,
  useTableView,
} from '../../design-system/tableView';
import { StatusDot } from '../../design-system/StatusDot';
import { formatNumber } from '../../lib/formatVN';
import { getSystemHealth, type ServiceHealth, type SystemHealth } from '../../lib/v1/adminPlatform';
import { useV1 } from '../../lib/v1/useV1';
import { formatLatency, formatTokens, pct } from './format';
import { MiniKpi, SectionTitle } from './parts';
import { LinkButton } from '../../components/shell/AppLink';

/*
 * "v3.1 · Quản trị · Giám sát hệ thống" (2237:15149, UC-11-04): four small KPIs, the service
 * table, the model table, and on the right the open alerts and the backup line. "Xem nhật ký"
 * opens Kiểm toán, where the platform's own events are logged.
 */
const SERVICE_STATUS: Record<
  ServiceHealth['status'],
  { label: string; tone: 'success' | 'warning' | 'error' }
> = {
  ok: { label: 'Bình thường', tone: 'success' },
  degraded: { label: 'Chậm', tone: 'warning' },
  restarted: { label: 'Đã khởi động lại', tone: 'warning' },
  down: { label: 'Ngừng', tone: 'error' },
};

const ALERT_TONE = { warning: 'warning', info: 'info', error: 'error' } as const;

export default function MonitoringPage() {
  const { navigate } = useAppState();
  const health = useV1(['getSystemHealth'], getSystemHealth);

  return (
    <div className="flex min-h-full">
      <div className="flex min-w-0 flex-1 flex-col gap-da-lg px-da-2xl py-da-xl">
        <PageHeader
          breadcrumb={['Quản trị', 'Giám sát hệ thống']}
          title="Giám sát hệ thống"
          description="Tình trạng các dịch vụ, mô hình AI và hạ tầng chạy tại trung tâm dữ liệu của ngân hàng. Cảnh báo gửi cho Quản trị hệ thống qua Teams và email."
          actions={
            <LinkButton area="admin" page="AUDIT">
              Xem nhật ký
            </LinkButton>
          }
        />
        <AsyncRegion state={health} what="tình trạng hệ thống" skeletonRows={6}>
          {(data) => <HealthBody data={data} />}
        </AsyncRegion>
      </div>
      <aside
        aria-label="Cảnh báo và sao lưu"
        className="flex w-[380px] shrink-0 flex-col gap-da-lg border-l border-border bg-card p-da-xl max-[1279px]:hidden"
      >
        {health.data && (
          <>
            <h2 className="text-muted-foreground da-overline">Cảnh báo đang mở</h2>
            {health.data.alerts.length === 0 && (
              <p className="text-muted-foreground da-caption">Không có cảnh báo nào.</p>
            )}
            {health.data.alerts.map((alert) => (
              <div
                key={alert.id}
                className="flex flex-col gap-da-xs rounded-sm border border-border p-[10px]"
              >
                <StatusDot tone={ALERT_TONE[alert.severity]}>{alert.title}</StatusDot>
                <p className="text-muted-foreground da-caption">{alert.detail}</p>
              </div>
            ))}
            <h2 className="text-muted-foreground da-overline">Sao lưu</h2>
            <p className="text-foreground da-caption">{health.data.backup_text}</p>
          </>
        )}
      </aside>
    </div>
  );
}

function HealthBody({ data }: { data: SystemHealth }) {
  const services = useTableView(data.services, {
    search: (service) =>
      `${service.name} ${SERVICE_STATUS[service.status].label} ${service.version} ${formatLatency(service.latency_p95_ms)} ${pct(service.error_rate_24h)} ${service.note ?? ''}`,
    filters: [
      {
        id: 'status',
        label: 'Trạng thái',
        value: (service) => SERVICE_STATUS[service.status].label,
      },
    ],
    sorts: [
      { id: 'name', value: (service) => service.name },
      { id: 'status', value: (service) => SERVICE_STATUS[service.status].label },
      { id: 'version', value: (service) => service.version },
      { id: 'latency', value: (service) => service.latency_p95_ms },
      { id: 'errors', value: (service) => service.error_rate_24h },
      { id: 'note', value: (service) => service.note },
    ],
    pageSize: 50,
  });
  const models = useTableView(data.models, {
    search: (model) =>
      `${model.name} ${model.tasks_text} ${formatTokens(model.tokens_today)} ${model.queue_unit === 'page' ? `${formatNumber(model.queue)} trang` : model.queue} ${model.gpu_cards} ${pct(model.gpu_utilization, 0)}`,
    filters: [{ id: 'tasks', label: 'Tác vụ', value: (model) => model.tasks_text }],
    sorts: [
      { id: 'name', value: (model) => model.name },
      { id: 'tasks', value: (model) => model.tasks_text },
      { id: 'tokens', value: (model) => model.tokens_today },
      { id: 'queue', value: (model) => model.queue },
      { id: 'gpu', value: (model) => model.gpu_utilization },
    ],
    pageSize: 50,
  });
  const delta = data.error_rate_delta_pp;
  const errorUp = delta !== null && delta > 0;
  const latency = data.latency_p95_ms;
  const target = data.latency_target_ms;
  return (
    <>
      {data.no_data && (
        <Notice tone="warning">
          Chưa đọc được số liệu giám sát: api chưa nối Prometheus (PROMETHEUS_URL) hoặc Prometheus
          không trả lời.
        </Notice>
      )}
      <div className="grid grid-cols-4 gap-da-lg max-[1279px]:grid-cols-2">
        <MiniKpi
          label="Dịch vụ hoạt động"
          value={`${data.services_up} / ${data.services_total}`}
          note={data.services_note ?? undefined}
          noteTone={data.services_note ? 'warning' : 'muted'}
        />
        <MiniKpi
          label="Độ trễ trả lời p95"
          value={formatLatency(latency, true)}
          note={target === null ? undefined : `mục tiêu ≤ ${formatLatency(target, true)}`}
          noteTone={
            latency === null || target === null ? 'muted' : latency <= target ? 'success' : 'error'
          }
        />
        <MiniKpi
          label="GPU đang dùng"
          value={pct(data.gpu_utilization, 0)}
          note={`${data.gpu_hosts} máy · ${data.gpu_cards} card`}
        />
        <MiniKpi
          label="Lỗi 24 giờ"
          value={pct(data.error_rate_24h)}
          note={
            delta === null
              ? undefined
              : `${errorUp ? '▲' : '▼'} ${formatNumber(Math.abs(delta), { maxFractionDigits: 1 })} điểm`
          }
          noteTone={delta === null ? 'muted' : errorUp ? 'warning' : 'success'}
        />
      </div>

      <SectionTitle>Dịch vụ</SectionTitle>
      <div className="flex flex-col gap-da-lg">
        <TableToolbar view={services} placeholder="Tìm dịch vụ…" searchLabel="Tìm dịch vụ" />
        <div className="overflow-hidden rounded-md border border-border bg-card">
          <table className="w-full table-fixed border-collapse">
            <thead>
              <tr className="bg-subtle text-left">
                <SortableTh
                  view={services}
                  column="name"
                  className="w-[150px] px-[14px] py-da-md font-semibold text-muted-foreground da-caption"
                >
                  Dịch vụ
                </SortableTh>
                <SortableTh
                  view={services}
                  column="status"
                  className="w-[140px] px-[14px] py-da-md font-semibold text-muted-foreground da-caption"
                >
                  Trạng thái
                </SortableTh>
                <SortableTh
                  view={services}
                  column="version"
                  className="w-[90px] px-[14px] py-da-md font-semibold text-muted-foreground da-caption"
                >
                  Phiên bản
                </SortableTh>
                <SortableTh
                  view={services}
                  column="latency"
                  className="w-[80px] px-[14px] py-da-md font-semibold text-muted-foreground da-caption"
                >
                  p95
                </SortableTh>
                <SortableTh
                  view={services}
                  column="errors"
                  className="w-[90px] px-[14px] py-da-md font-semibold text-muted-foreground da-caption"
                >
                  Lỗi 24 giờ
                </SortableTh>
                <SortableTh
                  view={services}
                  column="note"
                  className="px-[14px] py-da-md font-semibold text-muted-foreground da-caption"
                >
                  Ghi chú
                </SortableTh>
              </tr>
            </thead>
            <tbody>
              {services.rows.map((service) => {
                const status = SERVICE_STATUS[service.status];
                return (
                  <tr key={service.name}>
                    <Cell>{service.name}</Cell>
                    <Cell>
                      <StatusDot tone={status.tone}>{status.label}</StatusDot>
                    </Cell>
                    <Cell>{service.version}</Cell>
                    <Cell>{formatLatency(service.latency_p95_ms)}</Cell>
                    <Cell>{pct(service.error_rate_24h)}</Cell>
                    <Cell>{service.note}</Cell>
                  </tr>
                );
              })}
              {services.total === 0 && tableEmptyRow(6)}
            </tbody>
          </table>
        </div>
        <TablePager view={services} />
      </div>

      <SectionTitle>Mô hình AI</SectionTitle>
      <div className="flex flex-col gap-da-lg">
        <TableToolbar view={models} placeholder="Tìm mô hình…" searchLabel="Tìm mô hình" />
        <div className="overflow-hidden rounded-md border border-border bg-card">
          <table className="w-full table-fixed border-collapse">
            <thead>
              <tr className="bg-subtle text-left">
                <SortableTh
                  view={models}
                  column="name"
                  className="w-[200px] px-[14px] py-da-md font-semibold text-muted-foreground da-caption"
                >
                  Mô hình
                </SortableTh>
                <SortableTh
                  view={models}
                  column="tasks"
                  className="px-[14px] py-da-md font-semibold text-muted-foreground da-caption"
                >
                  Tác vụ
                </SortableTh>
                <SortableTh
                  view={models}
                  column="tokens"
                  className="w-[140px] px-[14px] py-da-md font-semibold text-muted-foreground da-caption"
                >
                  Token hôm nay
                </SortableTh>
                <SortableTh
                  view={models}
                  column="queue"
                  className="w-[100px] px-[14px] py-da-md font-semibold text-muted-foreground da-caption"
                >
                  Hàng đợi
                </SortableTh>
                <SortableTh
                  view={models}
                  column="gpu"
                  className="w-[140px] px-[14px] py-da-md font-semibold text-muted-foreground da-caption"
                >
                  GPU
                </SortableTh>
              </tr>
            </thead>
            <tbody>
              {data.models.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-[14px] py-da-md text-muted-foreground da-caption">
                    Chưa có số liệu tải theo mô hình.
                  </td>
                </tr>
              )}
              {models.rows.map((model) => (
                <tr key={model.name}>
                  <Cell>{model.name}</Cell>
                  <Cell>{model.tasks_text}</Cell>
                  <Cell>{formatTokens(model.tokens_today)}</Cell>
                  <Cell>
                    {model.queue_unit === 'page'
                      ? `${formatNumber(model.queue)} trang`
                      : model.queue}
                  </Cell>
                  <Cell>
                    {model.gpu_cards} card · {pct(model.gpu_utilization, 0)}
                  </Cell>
                </tr>
              ))}
              {data.models.length > 0 && models.total === 0 && tableEmptyRow(5)}
            </tbody>
          </table>
        </div>
        <TablePager view={models} />
      </div>
    </>
  );
}

function Cell({ children }: { children?: ReactNode }) {
  return <td className="px-[14px] py-da-md align-top text-foreground da-caption">{children}</td>;
}
