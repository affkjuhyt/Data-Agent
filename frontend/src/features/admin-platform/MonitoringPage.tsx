import type { ReactNode } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { StatusDot } from '../../design-system/StatusDot';
import { formatNumber } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import { getSystemHealth, type ServiceHealth, type SystemHealth } from '../../lib/v1/adminPlatform';
import { useV1 } from '../../lib/v1/useV1';
import { formatLatency, formatTokens, pct } from './format';
import { MiniKpi, SectionTitle } from './parts';

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
  const health = useV1(getSystemHealth, []);

  return (
    <div className="flex min-h-full">
      <div className="flex min-w-0 flex-1 flex-col gap-da-lg px-da-2xl py-da-xl">
        <PageHeader
          breadcrumb={['Quản trị', 'Giám sát hệ thống']}
          title="Giám sát hệ thống"
          description="Tình trạng các dịch vụ, mô hình AI và hạ tầng chạy tại trung tâm dữ liệu của ngân hàng. Cảnh báo gửi cho Quản trị hệ thống qua Teams và email."
          actions={<Button onClick={() => navigate('admin', 'AUDIT')}>Xem nhật ký</Button>}
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
  const errorUp = data.error_rate_delta_pp > 0;
  return (
    <>
      <div className="grid grid-cols-4 gap-da-lg max-[1279px]:grid-cols-2">
        <MiniKpi
          label="Dịch vụ hoạt động"
          value={`${data.services_up} / ${data.services_total}`}
          note={data.services_note ?? undefined}
          noteTone={data.services_note ? 'warning' : 'muted'}
        />
        <MiniKpi
          label="Độ trễ trả lời p95"
          value={formatLatency(data.latency_p95_ms, true)}
          note={`mục tiêu ≤ ${formatLatency(data.latency_target_ms, true)}`}
          noteTone={data.latency_p95_ms <= data.latency_target_ms ? 'success' : 'error'}
        />
        <MiniKpi
          label="GPU đang dùng"
          value={pct(data.gpu_utilization, 0)}
          note={`${data.gpu_hosts} máy · ${data.gpu_cards} card`}
        />
        <MiniKpi
          label="Lỗi 24 giờ"
          value={pct(data.error_rate_24h)}
          note={`${errorUp ? '▲' : '▼'} ${formatNumber(Math.abs(data.error_rate_delta_pp), { maxFractionDigits: 1 })} điểm`}
          noteTone={errorUp ? 'warning' : 'success'}
        />
      </div>

      <SectionTitle>Dịch vụ</SectionTitle>
      <Table
        head={['Dịch vụ', 'Trạng thái', 'Phiên bản', 'p95', 'Lỗi 24 giờ', 'Ghi chú']}
        widths={['w-[150px]', 'w-[140px]', 'w-[90px]', 'w-[80px]', 'w-[90px]', '']}
      >
        {data.services.map((service) => {
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
      </Table>

      <SectionTitle>Mô hình AI</SectionTitle>
      <Table
        head={['Mô hình', 'Tác vụ', 'Token hôm nay', 'Hàng đợi', 'GPU']}
        widths={['w-[200px]', '', 'w-[140px]', 'w-[100px]', 'w-[140px]']}
      >
        {data.models.map((model) => (
          <tr key={model.name}>
            <Cell>{model.name}</Cell>
            <Cell>{model.tasks_text}</Cell>
            <Cell>{formatTokens(model.tokens_today)}</Cell>
            <Cell>
              {model.queue_unit === 'page' ? `${formatNumber(model.queue)} trang` : model.queue}
            </Cell>
            <Cell>
              {model.gpu_cards} card · {pct(model.gpu_utilization, 0)}
            </Cell>
          </tr>
        ))}
      </Table>
    </>
  );
}

/* The bordered tables of this frame: subtle head row, 14px side padding, 12px text. */
function Table({
  head,
  widths,
  children,
}: {
  head: string[];
  widths: string[];
  children: ReactNode;
}) {
  return (
    <div className="overflow-hidden rounded-md border border-border bg-card">
      <table className="w-full table-fixed border-collapse">
        <thead>
          <tr className="bg-subtle text-left">
            {head.map((label, i) => (
              <th
                key={label}
                className={cn(
                  'px-[14px] py-da-md font-semibold text-muted-foreground da-caption',
                  widths[i],
                )}
              >
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

function Cell({ children }: { children?: ReactNode }) {
  return <td className="px-[14px] py-da-md align-top text-foreground da-caption">{children}</td>;
}
