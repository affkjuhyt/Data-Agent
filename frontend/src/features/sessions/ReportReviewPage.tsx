import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { Icon } from '../../design-system/Icon';
import { Notice } from '../../design-system/Notice';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import {
  getSessionReport,
  getWorkspace,
  publishSessionReport,
  requestReportChanges,
  type SessionReport,
} from '../../lib/v1/sessions';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P02 · Phiên · Duyệt & phát hành báo cáo" (2297:18812, UC-02-07), at /analysis/report/<id>:
 * the report Agent Báo cáo drafted from a session, shown as the page recipients will get, and on
 * the right the checks it must pass (grounded numbers, match with filed figures, recipients'
 * rights, no personal data) and who it goes to. Phát hành opens only when every check passes;
 * the server also refuses a reviewer who drafted it. Cần bạn (kind report_publish) links here.
 */
const PENDING = 'Màn này đang được làm.';

const STATUS: Record<
  SessionReport['status'],
  { tone: 'warning' | 'success' | 'neutral' | 'info'; label: string }
> = {
  drafting: { tone: 'info', label: 'Đang soạn' },
  pending_review: { tone: 'warning', label: 'Chờ duyệt' },
  changes_requested: { tone: 'neutral', label: 'Đã yêu cầu sửa' },
  published: { tone: 'success', label: 'Đã phát hành' },
};

const hhmm = (iso: string) =>
  new Date(iso).toLocaleTimeString('vi-VN', { hour: '2-digit', minute: '2-digit' });

export default function ReportReviewPage() {
  const { routeId } = useAppState();
  const report = useV1(routeId ? () => getSessionReport(routeId) : null, [routeId]);
  return (
    <AsyncRegion state={report} what="báo cáo">
      {(r) => <Review report={r} />}
    </AsyncRegion>
  );
}

function Review({ report: loaded }: { report: SessionReport }) {
  const toast = useToast();
  const { navigate } = useAppState();
  const workspace = useV1(() => getWorkspace(loaded.workspace_id), [loaded.workspace_id]);
  const [report, setReport] = useState(loaded);
  const [dialog, setDialog] = useState(false);
  const [note, setNote] = useState('');
  const [noteMissing, setNoteMissing] = useState(false);
  const [busy, setBusy] = useState<'publish' | 'changes' | null>(null);
  const [error, setError] = useState<string>();

  const title = workspace.data?.title ?? report.document.heading;
  const failing = report.checks.filter((c) => !c.ok).length;
  const open = report.status === 'pending_review';
  const status = STATUS[report.status];

  const act = async (
    kind: 'publish' | 'changes',
    call: () => Promise<SessionReport>,
    done: string,
  ) => {
    setBusy(kind);
    setError(undefined);
    try {
      setReport(await call());
      toast({ tone: 'success', message: done });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
      setDialog(false);
    }
  };

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Phân tích', 'Phiên', title, 'Báo cáo']}
        title={
          <span className="flex items-center gap-[10px]">
            Báo cáo · {title}
            <StatusDot tone={status.tone}>{status.label}</StatusDot>
          </span>
        }
        description={`${report.drafted_by} soạn từ ${report.tile_count} ô của phiên lúc ${hhmm(report.drafted_at)} · người duyệt ${report.reviewer_name}`}
        actions={
          <>
            <Button
              kind="ghost"
              onClick={() => navigate('analysis', 'SESSIONS', report.workspace_id)}
            >
              Mở phiên
            </Button>
            {open && (
              <>
                <Tooltip content={PENDING}>
                  <Button kind="ghost" disabled>
                    Sửa nội dung
                  </Button>
                </Tooltip>
                <Button onClick={() => setDialog(true)}>Yêu cầu sửa</Button>
                {failing > 0 ? (
                  <Tooltip content={`Còn ${failing} kiểm tra chưa đạt`}>
                    <Button kind="primary" disabled>
                      Phát hành
                    </Button>
                  </Tooltip>
                ) : (
                  <Button
                    kind="primary"
                    loading={busy === 'publish'}
                    onClick={() =>
                      act(
                        'publish',
                        () => publishSessionReport(report.id),
                        `Đã phát hành báo cáo “${title}”`,
                      )
                    }
                  >
                    Phát hành
                  </Button>
                )}
              </>
            )}
          </>
        }
      />

      {error && (
        <div className="px-da-3xl pt-da-lg">
          <Notice tone="error">{error}</Notice>
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col items-center bg-background px-da-2xl pt-da-2xl pb-da-3xl">
          <article
            aria-label="Xem trước báo cáo"
            className="flex w-[640px] max-w-full flex-col gap-[14px] rounded-sm border border-border bg-card px-[40px] py-da-3xl"
          >
            <h2 className="text-foreground da-heading-1">{report.document.heading}</h2>
            <p className="text-foreground da-reading">{report.document.summary}</p>
            <div className="grid grid-cols-4 gap-da-md">
              {report.document.figures.map((f) => (
                <div key={f.value} className="flex flex-col gap-[2px]">
                  <span className="text-foreground da-metric-small">{f.value}</span>
                  <StatusDot tone={f.grounded ? 'success' : 'warning'}>
                    {f.grounded ? 'có căn cứ' : 'chưa gắn nguồn'}
                  </StatusDot>
                </div>
              ))}
            </div>
            <p className="text-foreground da-body">{report.document.recommendation}</p>
          </article>
        </div>

        <aside
          aria-label="Kiểm tra trước khi phát hành"
          className="flex w-[380px] shrink-0 flex-col gap-[10px] border-l border-border px-da-2xl pt-[20px]"
        >
          <h2 className="text-foreground da-heading-3">Kiểm tra trước khi phát hành</h2>
          <ul className="flex flex-col gap-[10px]">
            {report.checks.map((c) => (
              <li key={c.title} className="flex items-start gap-da-md">
                <Icon
                  name={c.ok ? 'check' : 'alert'}
                  size={14}
                  label={c.ok ? 'Đạt' : 'Chưa đạt'}
                  className={c.ok ? 'mt-[2px] text-success' : 'mt-[2px] text-warning'}
                />
                <span className="flex min-w-0 flex-1 flex-col gap-[2px]">
                  <span className="text-foreground da-body-strong">{c.title}</span>
                  <span className="text-muted-foreground da-caption">{c.detail}</span>
                </span>
              </li>
            ))}
          </ul>
          <h2 className="text-foreground da-heading-3">Gửi tới</h2>
          <p className="text-foreground da-body-small">{report.recipients_text}</p>
          <p className="text-muted-foreground da-caption">
            Nút Phát hành mở khi mọi kiểm tra đạt; người duyệt khác người soạn.
          </p>
        </aside>
      </div>

      <ConfirmDialog
        open={dialog}
        tone="primary"
        title="Yêu cầu sửa báo cáo"
        confirmLabel="Gửi yêu cầu"
        loading={busy === 'changes'}
        error={noteMissing ? 'Ghi điều cần sửa để Agent Báo cáo soạn lại.' : undefined}
        onCancel={() => {
          setDialog(false);
          setNoteMissing(false);
        }}
        onConfirm={() => {
          if (!note.trim()) {
            setNoteMissing(true);
            return;
          }
          setNoteMissing(false);
          act(
            'changes',
            () => requestReportChanges(report.id, note),
            'Đã gửi yêu cầu sửa cho Agent Báo cáo',
          );
        }}
      >
        <label className="flex flex-col gap-da-xs">
          <span>Agent Báo cáo soạn lại theo ghi chú này rồi gửi bạn duyệt lần nữa.</span>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={3}
            aria-label="Điều cần sửa"
            className="rounded-md border border-border bg-card px-da-lg py-da-md text-foreground da-body-small outline-none focus:border-ring"
          />
        </label>
      </ConfirmDialog>
    </div>
  );
}
