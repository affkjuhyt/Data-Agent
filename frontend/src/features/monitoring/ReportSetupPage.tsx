import { useState, type KeyboardEvent } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Checkbox } from '../../design-system/Checkbox';
import { Notice } from '../../design-system/Notice';
import { StatusDot } from '../../design-system/StatusDot';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { useToast } from '../../design-system/Toast';
import { Toggle } from '../../design-system/Toggle';
import { cn } from '../../lib/utils';
import {
  getReport,
  listReports,
  pauseReport,
  sendReportTest,
  updateReport,
  type ReportSchedule,
  type ReportSection,
} from '../../lib/v1/reports';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P08 · Báo cáo định kỳ · Thiết lập" (2271:16289, UC-08-03, UC-02-04, UC-02-07): one scheduled
 * report. Left, its sections — ticked ones go out, dragged (or moved with the arrow keys on the
 * handle) into order; a section with personal data is only ever shown inside the app — and its
 * parameters. Right, schedule and recipients, whether someone must approve before it is sent (late
 * means held, never sent unchecked), and the recent sends. Changes wait for "Lưu thay đổi"; a test
 * goes to the caller alone. Opened from the rail without an id, the page shows the first report.
 */
const STATUS = {
  active: { label: 'Đang chạy', tone: 'success' },
  paused: { label: 'Tạm dừng', tone: 'idle' },
  draft: { label: 'Nháp', tone: 'neutral' },
} as const;

const runLook = (r: ReportSchedule['recent_runs'][number]) =>
  r.status === 'sent'
    ? r.corrected
      ? { label: 'Đã gửi, có đính chính', tone: 'warning' as const }
      : { label: 'Đã gửi', tone: 'success' as const }
    : r.status === 'blocked_by_checks'
      ? { label: 'Giữ lại', tone: 'error' as const }
      : r.status === 'partially_sent'
        ? { label: 'Gửi một phần', tone: 'warning' as const }
        : r.status === 'failed'
          ? { label: 'Lỗi', tone: 'error' as const }
          : { label: 'Đang chạy', tone: 'info' as const };

export default function ReportSetupPage() {
  const { routeId } = useAppState();
  const list = useV1(routeId ? null : listReports, [routeId]);
  const id = routeId || list.data?.items[0]?.id;
  const report = useV1(id ? () => getReport(id) : null, [id]);
  return (
    <AsyncRegion state={report} what="báo cáo định kỳ">
      {(r) => <ReportBody key={r.id} report={r} onChanged={report.reload} />}
    </AsyncRegion>
  );
}

function ReportBody({ report, onChanged }: { report: ReportSchedule; onChanged: () => void }) {
  const toast = useToast();
  const [sections, setSections] = useState<ReportSection[]>(report.sections);
  const [check, setCheck] = useState(report.require_publish_check);
  const [dragging, setDragging] = useState<string>();
  const [busy, setBusy] = useState<'save' | 'pause' | 'resume' | 'test' | null>(null);
  const [error, setError] = useState<string>();
  const dirty =
    JSON.stringify(sections) !== JSON.stringify(report.sections) ||
    check !== report.require_publish_check;
  const status = STATUS[report.status];

  const act = async (
    key: NonNullable<typeof busy>,
    call: () => Promise<unknown>,
    message: string,
  ) => {
    setBusy(key);
    setError(undefined);
    try {
      await call();
      toast({ tone: 'success', message });
      if (key !== 'test') onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const move = (key: string, to: number) =>
    setSections((all) => {
      const from = all.findIndex((s) => s.key === key);
      if (from < 0 || to < 0 || to >= all.length || from === to) return all;
      const next = [...all];
      const [section] = next.splice(from, 1);
      next.splice(to, 0, section);
      return next;
    });

  const onHandleKey = (event: KeyboardEvent, key: string, i: number) => {
    if (event.key === 'ArrowUp') {
      event.preventDefault();
      move(key, i - 1);
    } else if (event.key === 'ArrowDown') {
      event.preventDefault();
      move(key, i + 1);
    }
  };

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Phân tích', 'Báo cáo định kỳ', report.title]}
        title={
          <span className="flex items-center gap-[10px]">
            {report.title}
            <StatusDot tone={status.tone}>{status.label}</StatusDot>
          </span>
        }
        description={report.summary_text}
        actions={
          <>
            <Button
              kind="ghost"
              loading={busy === 'test'}
              disabled={busy !== null}
              onClick={() =>
                act('test', () => sendReportTest(report.id), 'Đang soạn bản thử; gửi riêng cho bạn')
              }
            >
              Gửi thử cho tôi
            </Button>
            {report.status === 'paused' ? (
              <Button
                loading={busy === 'resume'}
                disabled={busy !== null}
                onClick={() =>
                  act(
                    'resume',
                    () => updateReport(report.id, { status: 'active' }),
                    `${report.title} chạy lại theo lịch`,
                  )
                }
              >
                Tiếp tục
              </Button>
            ) : (
              <Button
                loading={busy === 'pause'}
                disabled={busy !== null}
                onClick={() =>
                  act('pause', () => pauseReport(report.id), `Đã tạm dừng ${report.title}`)
                }
              >
                Tạm dừng
              </Button>
            )}
            <Button
              kind="primary"
              loading={busy === 'save'}
              disabled={busy !== null || !dirty}
              onClick={() =>
                act(
                  'save',
                  () => updateReport(report.id, { sections, require_publish_check: check }),
                  'Đã lưu; lần gửi sau dùng thiết lập mới',
                )
              }
            >
              Lưu thay đổi
            </Button>
          </>
        }
      />
      <div className="flex min-h-0 flex-1 max-[1023px]:flex-col">
        <div className="flex w-[560px] shrink-0 flex-col px-da-3xl pb-da-2xl max-[1023px]:w-full">
          {error && (
            <div className="pt-da-lg">
              <Notice tone="error">Không thực hiện được: {error}</Notice>
            </div>
          )}
          <section className="flex flex-col gap-da-md border-b border-border py-da-xl">
            <div className="flex items-baseline gap-da-md">
              <h2 className="text-foreground da-heading-3">Nội dung</h2>
              <span className="ml-auto text-muted-foreground da-caption">kéo để đổi thứ tự</span>
            </div>
            <ol aria-label="Các mục của báo cáo" className="flex flex-col gap-da-lg">
              {sections.map((s, i) => (
                <li
                  key={s.key}
                  onDragOver={(e) => dragging && e.preventDefault()}
                  onDrop={() => dragging && move(dragging, i)}
                  className={cn('flex items-start gap-da-sm', dragging === s.key && 'opacity-50')}
                >
                  <button
                    type="button"
                    draggable
                    aria-label={`Đổi thứ tự ${s.title} (mũi tên lên, xuống)`}
                    onDragStart={() => setDragging(s.key)}
                    onDragEnd={() => setDragging(undefined)}
                    onKeyDown={(e) => onHandleKey(e, s.key, i)}
                    className="mt-[2px] cursor-grab rounded-sm px-[2px] text-muted-foreground da-caption"
                  >
                    ⠿
                  </button>
                  <div className="flex flex-col gap-[2px]">
                    <Checkbox
                      checked={s.enabled}
                      onChange={(on) =>
                        setSections((all) =>
                          all.map((x) => (x.key === s.key ? { ...x, enabled: on } : x)),
                        )
                      }
                    >
                      {s.title}
                    </Checkbox>
                    <span className="pl-[26px] text-muted-foreground da-caption">{s.hint}</span>
                  </div>
                </li>
              ))}
            </ol>
          </section>
          <section className="flex flex-col gap-da-md border-b border-border py-da-xl">
            <h2 className="text-foreground da-heading-3">Tham số</h2>
            <Pairs rows={report.params} />
          </section>
        </div>
        <aside className="flex min-w-0 flex-1 flex-col border-l border-border bg-background px-da-2xl max-[1023px]:border-l-0 max-[1023px]:border-t">
          <section className="flex flex-col gap-da-md border-b border-border py-da-xl">
            <h2 className="text-foreground da-heading-3">Lịch và người nhận</h2>
            <Pairs rows={report.delivery} />
          </section>
          <section className="flex flex-col gap-da-md border-b border-border py-da-xl">
            <h2 className="text-foreground da-heading-3">Duyệt trước khi gửi</h2>
            <div className="flex items-center gap-da-md">
              <Toggle checked={check} onChange={setCheck} label="Duyệt trước khi gửi" />
              <span className="text-foreground da-body-small">
                {check
                  ? report.publish_check_text
                  : 'Gửi ngay khi soạn xong; vẫn chặn khi số liệu không khớp số đã nộp.'}
              </span>
            </div>
          </section>
          <section className="flex flex-col gap-da-md border-b border-border py-da-xl">
            <h2 className="text-foreground da-heading-3">Lần gửi gần đây</h2>
            <RecentRuns runs={report.recent_runs} />
            <p className="text-muted-foreground da-caption">
              Báo cáo chỉ phát hành khi mọi số có căn cứ và khớp số đã nộp.
            </p>
          </section>
        </aside>
      </div>
    </div>
  );
}

function RecentRuns({ runs }: { runs: ReportSchedule['recent_runs'] }) {
  const view = useTableView(runs, {
    search: (r) => `${r.at_text} ${runLook(r).label} ${r.detail}`,
    filters: [{ id: 'status', label: 'Trạng thái', value: (r) => runLook(r).label }],
    sorts: [
      { id: 'at', value: (r) => r.at_text },
      { id: 'status', value: (r) => runLook(r).label },
      { id: 'detail', value: (r) => r.detail },
    ],
    pageSize: 50,
  });
  return (
    <>
      <TableToolbar view={view} placeholder="Tìm lần gửi…" searchLabel="Tìm lần gửi" />
      <table className="w-full border-collapse da-body-small">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground da-caption">
            <SortableTh view={view} column="at" className="w-[112px] py-da-sm font-normal">
              Thời điểm
            </SortableTh>
            <SortableTh view={view} column="status" className="py-da-sm font-normal">
              Trạng thái
            </SortableTh>
            <SortableTh view={view} column="detail" className="py-da-sm font-normal">
              Chi tiết
            </SortableTh>
          </tr>
        </thead>
        <tbody>
          {view.rows.map((r) => {
            const look = runLook(r);
            return (
              <tr key={r.id} className="border-b border-border text-foreground">
                <td className="w-[112px] py-[9px] da-mono">{r.at_text}</td>
                <td className="py-[9px]">
                  <StatusDot tone={look.tone}>{look.label}</StatusDot>
                </td>
                <td className="py-[9px] text-muted-foreground">{r.detail}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <TablePager view={view} />
    </>
  );
}

function Pairs({ rows }: { rows: { label: string; value: string }[] }) {
  return (
    <dl className="flex flex-col gap-da-sm da-body-small">
      {rows.map((r) => (
        <div key={r.label} className="flex gap-da-md">
          <dt className="w-[100px] shrink-0 text-muted-foreground">{r.label}</dt>
          <dd className="text-foreground">{r.value}</dd>
        </div>
      ))}
    </dl>
  );
}
