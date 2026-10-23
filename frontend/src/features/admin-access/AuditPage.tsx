import { useEffect, useState } from 'react';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { EmptyState } from '../../design-system/EmptyState';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { Select } from '../../design-system/Select';
import {
  SortableTh,
  TablePager,
  TableToolbar,
  tableEmptyRow,
  useTableView,
} from '../../design-system/tableView';
import { Tabs } from '../../design-system/Tabs';
import { useToast } from '../../design-system/Toast';
import { formatDateTime, formatNumber, formatTime } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import {
  exportAudit,
  getAuditExport,
  listSecurityEvents,
  searchAudit,
  verifyAuditChain,
  type AuditExport,
  type AuditQuery,
  type SecurityEvent,
} from '../../lib/v1/adminAccess';
import { useV1 } from '../../lib/v1/useV1';
import { dayMonth, isoDay } from './format';

/*
 * "P11 · Kiểm toán" (2306:19586, UC-11-03): the append-only audit log and the security events.
 * GET /v1/audit filters by exact action, outcome and time (no free text, actor kind or channel),
 * so the screen offers those; a blocked action is drawn in warning. "Kiểm chuỗi băm" recomputes
 * the hash chain. "Xuất cho kiểm toán" asks for a signed CSV of the current filters and follows
 * the export job until it is ready or waits for a second person's approval (BR-138).
 */
type Tab = 'log' | 'security';
type Outcome = 'all' | NonNullable<AuditQuery['outcome']>;

const TABS: { value: Tab; label: string }[] = [
  { value: 'log', label: 'Nhật ký' },
  { value: 'security', label: 'Sự kiện bảo mật' },
];

const OUTCOMES: { value: Outcome; label: string }[] = [
  { value: 'all', label: 'Kết quả: tất cả' },
  { value: 'ok', label: 'Kết quả: thành công' },
  { value: 'denied', label: 'Kết quả: bị chặn' },
  { value: 'error', label: 'Kết quả: lỗi' },
];

const SEARCH_DELAY_MS = 300;

export default function AuditPage() {
  const toast = useToast();
  const [tab, setTab] = useState<Tab>('log');
  const [text, setText] = useState('');
  const [action, setAction] = useState('');
  const [outcome, setOutcome] = useState<Outcome>('all');
  const [date, setDate] = useState(isoDay(new Date()));
  const [exporting, setExporting] = useState(false);
  const [verifying, setVerifying] = useState(false);
  const [exportId, setExportId] = useState<{ id: string; poll_after_ms: number } | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => setAction(text.trim()), SEARCH_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [text]);

  const query: AuditQuery = {
    action: action || undefined,
    outcome: outcome === 'all' ? undefined : outcome,
    date: date || undefined,
  };

  const runExport = async () => {
    setExporting(true);
    try {
      const res = await exportAudit(query);
      setExportId({ id: res.id, poll_after_ms: res.poll_after_ms ?? 2000 });
    } catch (e) {
      toast({ tone: 'error', message: `Không xuất được: ${(e as Error).message}` });
    } finally {
      setExporting(false);
    }
  };

  const runVerify = async () => {
    setVerifying(true);
    try {
      const res = await verifyAuditChain();
      toast(
        res.ok
          ? {
              tone: 'success',
              message: `Chuỗi băm nguyên vẹn · đã kiểm ${formatNumber(res.checked_rows)} dòng.`,
            }
          : {
              tone: 'error',
              message: `Chuỗi băm đứt ở dòng #${res.broken_at_seq}; đã ghi một sự kiện bảo mật.`,
            },
      );
    } catch (e) {
      toast({ tone: 'error', message: `Không kiểm được: ${(e as Error).message}` });
    } finally {
      setVerifying(false);
    }
  };

  return (
    <div className="flex flex-col">
      <PageHeader
        bordered
        breadcrumb={['Quản trị', 'Kiểm toán']}
        title="Nhật ký kiểm toán"
        description="Chỉ đọc · không ai sửa hay xoá được · giữ 7 năm · mỗi dòng ký số nối tiếp dòng trước"
        actions={
          <>
            <Button kind="secondary" loading={verifying} onClick={runVerify}>
              Kiểm chuỗi băm
            </Button>
            <Button loading={exporting} onClick={runExport}>
              Xuất cho kiểm toán
            </Button>
          </>
        }
      />
      <div className="flex flex-col gap-da-lg px-da-3xl pt-da-lg pb-da-xl">
        {exportId && <ExportStatus key={exportId.id} {...exportId} />}
        <Tabs<Tab> label="Kiểm toán" items={TABS} value={tab} onChange={setTab} />
        {tab === 'log' ? (
          <>
            <div className="flex flex-wrap items-center gap-da-md">
              <Input
                icon="search"
                aria-label="Mã hành động"
                placeholder="Mã hành động, ví dụ audit.export_requested"
                value={text}
                onChange={(e) => setText(e.target.value)}
                className="w-[360px]"
              />
              <Select<Outcome>
                label="Kết quả"
                className="w-[200px]"
                options={OUTCOMES}
                value={outcome}
                onChange={setOutcome}
              />
              <Input
                type="date"
                aria-label="Ngày"
                value={date}
                max={isoDay(new Date())}
                onChange={(e) => setDate(e.target.value)}
                className="w-[160px]"
              />
            </div>
            <AuditLog query={query} deps={[action, outcome, date]} />
          </>
        ) : (
          <SecurityEvents />
        )}
        <p className="text-muted-foreground da-caption">
          UC-11-03 · kiểm toán viên chỉ xem được nhật ký, không xem dữ liệu nghiệp vụ; câu hỏi chứa
          dữ liệu cá nhân được lưu dạng đã che
        </p>
      </div>
    </div>
  );
}

function AuditLog({ query, deps }: { query: AuditQuery; deps: unknown[] }) {
  const log = useV1(['searchAudit', query, ...deps], () => searchAudit(query));
  const items = log.data?.items ?? [];
  const view = useTableView(items, {
    search: (e) =>
      `${formatTime(e.at, true)} ${e.actor_label} ${e.action_text} ${e.data_text} ${e.service}`,
    filters: [{ id: 'service', label: 'Dịch vụ', value: (e) => e.service }],
    sorts: [
      { id: 'at', value: (e) => e.at },
      { id: 'actor', value: (e) => e.actor_label },
      { id: 'action', value: (e) => e.action_text },
      { id: 'data', value: (e) => e.data_text },
      { id: 'service', value: (e) => e.service },
    ],
    pageSize: 50,
  });
  return (
    <AsyncRegion
      state={log}
      what="nhật ký kiểm toán"
      skeletonRows={6}
      isEmpty={(p) => p.items.length === 0}
      empty={
        <EmptyState
          icon="file"
          title="Không có sự kiện nào khớp"
          description="Thử bỏ bớt bộ lọc hoặc chọn ngày khác."
        />
      }
    >
      {() => (
        <div className="flex flex-col gap-da-lg">
          <TableToolbar
            view={view}
            placeholder="Tìm trong nhật ký…"
            searchLabel="Tìm trong nhật ký"
          />
          <table className="w-full table-fixed border-collapse">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground da-caption">
                <SortableTh
                  view={view}
                  column="at"
                  className="w-[114px] px-da-lg py-da-md font-normal"
                >
                  Thời gian
                </SortableTh>
                <SortableTh
                  view={view}
                  column="actor"
                  className="w-[202px] px-da-lg py-da-md font-normal"
                >
                  Ai
                </SortableTh>
                <SortableTh view={view} column="action" className="px-da-lg py-da-md font-normal">
                  Làm gì
                </SortableTh>
                <SortableTh
                  view={view}
                  column="data"
                  className="w-[232px] px-da-lg py-da-md font-normal"
                >
                  Dữ liệu
                </SortableTh>
                <SortableTh
                  view={view}
                  column="service"
                  className="w-[120px] px-da-lg py-da-md font-normal"
                >
                  Dịch vụ
                </SortableTh>
              </tr>
            </thead>
            <tbody>
              {view.rows.map((e) => (
                <tr key={e.id} className="border-b border-border align-middle">
                  <td className="px-da-lg py-da-md text-muted-foreground da-mono">
                    {formatTime(e.at, true)}
                  </td>
                  <td className="px-da-lg py-da-md text-foreground da-body-small">
                    {e.actor_label}
                  </td>
                  <td
                    className={cn(
                      'px-da-lg py-da-md da-body-small',
                      e.outcome === 'blocked' && 'text-warning',
                      e.outcome === 'error' && 'text-error',
                      e.outcome === 'ok' && 'text-foreground',
                    )}
                  >
                    {e.action_text}
                  </td>
                  <td className="px-da-lg py-da-md text-muted-foreground da-mono">{e.data_text}</td>
                  <td className="px-da-lg py-da-md text-muted-foreground da-body-small">
                    {e.service}
                  </td>
                </tr>
              ))}
              {view.total === 0 && tableEmptyRow(5)}
            </tbody>
          </table>
          <TablePager view={view} />
        </div>
      )}
    </AsyncRegion>
  );
}

/* What a running export says; the worker moves it queued → running → ready (or failed). */
const POLLED = ['queued', 'running'];

function ExportStatus({ id, poll_after_ms }: { id: string; poll_after_ms: number }) {
  const [job, setJob] = useState<AuditExport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    // The first read is immediate: the job may already wait for approval.
    const timer = window.setTimeout(
      () =>
        getAuditExport(id)
          .then((next) => {
            if (!alive) return;
            setJob(next);
            setError(null);
            if (POLLED.includes(next.status)) setTick((t) => t + 1);
          })
          .catch((e: unknown) => alive && setError((e as Error).message)),
      tick === 0 ? 0 : poll_after_ms,
    );
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [id, poll_after_ms, tick]);

  if (error) return <Notice tone="error">Không đọc được bản xuất: {error}</Notice>;
  if (!job) return <Notice tone="info">Đang tạo bản xuất…</Notice>;
  const rows = `${formatNumber(job.row_count ?? 0)} dòng`;
  switch (job.status) {
    case 'waiting_approval':
      return (
        <Notice
          tone="warning"
          action={
            <Button kind="secondary" size="sm" onClick={() => setTick((t) => t + 1)}>
              Xem lại
            </Button>
          }
        >
          Bản xuất {rows} vượt 100.000 dòng trong ngày, chờ một người khác có quyền kiểm toán duyệt
          trong Cần bạn.
        </Notice>
      );
    case 'ready':
      return (
        <Notice tone="success">
          Tệp CSV {rows} đã ký
          {job.signature ? ` (chữ ký ${job.signature.slice(0, 12)}…)` : ''}
          {job.expires_at ? `, giữ đến ${dayMonth(job.expires_at)}` : ''}. Liên kết tải được gửi cho
          bạn.
        </Notice>
      );
    case 'failed':
      return <Notice tone="error">Không tạo được tệp xuất {rows}. Thử xuất lại.</Notice>;
    case 'expired':
      return <Notice tone="warning">Bản xuất {rows} đã hết hạn tải. Xuất lại nếu cần.</Notice>;
    default:
      return <Notice tone="info">Đang tạo tệp CSV có chữ ký ({rows})…</Notice>;
  }
}

/* security_event.kind and severity in words; an unknown kind is shown by its code. */
const EVENT_KIND: Record<string, string> = {
  prompt_injection: 'Chèn lệnh vào tài liệu',
  pii_leak_blocked: 'Chặn lộ dữ liệu cá nhân',
  hash_chain_broken: 'Chuỗi băm nhật ký bị đứt',
  denied_spike: 'Bị từ chối tăng đột biến',
  signature_invalid: 'Chữ ký không hợp lệ',
  egress_blocked: 'Chặn gửi dữ liệu ra ngoài',
};

const SEVERITY: Record<string, { label: string; tone: 'neutral' | 'info' | 'warning' | 'error' }> =
  {
    low: { label: 'Thấp', tone: 'neutral' },
    medium: { label: 'Trung bình', tone: 'info' },
    high: { label: 'Cao', tone: 'warning' },
    critical: { label: 'Nghiêm trọng', tone: 'error' },
  };

const SEVERITY_ORDER: Record<string, number> = { low: 0, medium: 1, high: 2, critical: 3 };

const EVENT_STATUS: Record<string, string> = {
  open: 'Chưa xử lý',
  investigating: 'Đang điều tra',
  closed: 'Đã đóng',
};

/** detail is free jsonb: only its plain values are shown. */
function detailText(event: SecurityEvent): string {
  const detail: unknown = event.detail;
  const parts =
    typeof detail === 'object' && detail !== null && !Array.isArray(detail)
      ? Object.entries(detail)
          .filter(([, v]) => ['string', 'number', 'boolean'].includes(typeof v))
          .map(([k, v]) => `${k}: ${String(v)}`)
      : [];
  return [event.source_ref, ...parts].filter(Boolean).join(' · ') || '—';
}

function SecurityEvents() {
  const events = useV1(['listSecurityEvents', {}], () => listSecurityEvents({}));
  const items = events.data?.items ?? [];
  const view = useTableView(items, {
    search: (e) =>
      `${formatDateTime(e.occurred_at)} ${EVENT_KIND[e.kind] ?? e.kind} ${SEVERITY[e.severity]?.label ?? e.severity} ${detailText(e)} ${EVENT_STATUS[e.status] ?? e.status}`,
    filters: [
      {
        id: 'severity',
        label: 'Mức độ',
        value: (e) => SEVERITY[e.severity]?.label ?? e.severity,
      },
    ],
    sorts: [
      { id: 'at', value: (e) => e.occurred_at },
      { id: 'kind', value: (e) => EVENT_KIND[e.kind] ?? e.kind },
      { id: 'severity', value: (e) => SEVERITY_ORDER[e.severity] },
      { id: 'detail', value: (e) => detailText(e) },
      { id: 'status', value: (e) => EVENT_STATUS[e.status] ?? e.status },
    ],
    pageSize: 50,
  });
  return (
    <AsyncRegion
      state={events}
      what="sự kiện bảo mật"
      skeletonRows={4}
      isEmpty={(p) => p.items.length === 0}
      empty={
        <EmptyState
          icon="check"
          title="Chưa có sự kiện bảo mật"
          description="Chèn lệnh, lộ dữ liệu cá nhân, chuỗi băm đứt… được ghi ở đây."
        />
      }
    >
      {() => (
        <div className="flex flex-col gap-da-lg">
          <TableToolbar view={view} placeholder="Tìm sự kiện…" searchLabel="Tìm sự kiện" />
          <table className="w-full table-fixed border-collapse">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground da-caption">
                <SortableTh
                  view={view}
                  column="at"
                  className="w-[150px] px-da-lg py-da-md font-normal"
                >
                  Thời gian
                </SortableTh>
                <SortableTh
                  view={view}
                  column="kind"
                  className="w-[240px] px-da-lg py-da-md font-normal"
                >
                  Sự kiện
                </SortableTh>
                <SortableTh
                  view={view}
                  column="severity"
                  className="w-[130px] px-da-lg py-da-md font-normal"
                >
                  Mức độ
                </SortableTh>
                <SortableTh view={view} column="detail" className="px-da-lg py-da-md font-normal">
                  Chi tiết
                </SortableTh>
                <SortableTh
                  view={view}
                  column="status"
                  className="w-[130px] px-da-lg py-da-md font-normal"
                >
                  Trạng thái
                </SortableTh>
              </tr>
            </thead>
            <tbody>
              {view.rows.map((e) => {
                const severity = SEVERITY[e.severity] ?? { label: e.severity, tone: 'neutral' };
                return (
                  <tr key={e.id} className="border-b border-border align-middle">
                    <td className="px-da-lg py-da-md text-muted-foreground da-mono">
                      {formatDateTime(e.occurred_at)}
                    </td>
                    <td className="px-da-lg py-da-md text-foreground da-body-small">
                      {EVENT_KIND[e.kind] ?? e.kind}
                    </td>
                    <td className="px-da-lg py-da-md">
                      <Badge tone={severity.tone}>{severity.label}</Badge>
                    </td>
                    <td className="px-da-lg py-da-md text-muted-foreground da-mono">
                      {detailText(e)}
                    </td>
                    <td className="px-da-lg py-da-md text-muted-foreground da-body-small">
                      {EVENT_STATUS[e.status] ?? e.status}
                    </td>
                  </tr>
                );
              })}
              {view.total === 0 && tableEmptyRow(5)}
            </tbody>
          </table>
          <TablePager view={view} />
        </div>
      )}
    </AsyncRegion>
  );
}
