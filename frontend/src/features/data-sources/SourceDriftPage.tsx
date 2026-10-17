import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { EmptyState } from '../../design-system/EmptyState';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { formatNumber, formatTime } from '../../lib/formatVN';
import {
  getSourceDrift,
  listSourceDrifts,
  resolveSourceDrift,
  type ColumnChange,
  type SourceDrift,
} from '../../lib/v1/sources';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P04 · Lệch cấu trúc · Duyệt thay đổi" (2281:17287, UC-04-03): a connected source changed its
 * structure, the job behind it paused and holds records back. Column changes, what they touch
 * (assessed by the Data Quality agent), the agent's proposal, who approves and what rejecting
 * does. /data/drift/<id> opens one; from the rail (no id) the first open one.
 * "Sửa ánh xạ" has no mapping editor yet. Crawled sources have their own screen (P14, features/crawl).
 */
export default function SourceDriftPage() {
  const { routeId } = useAppState();
  return routeId ? <DriftById id={routeId} /> : <FirstOpenDrift />;
}

function DriftById({ id }: { id: string }) {
  const drift = useV1(() => getSourceDrift(id), [id]);
  return (
    <AsyncRegion state={drift} what="thay đổi cấu trúc" skeletonRows={8}>
      {(data) => <DriftView drift={data} onChanged={drift.reload} />}
    </AsyncRegion>
  );
}

function FirstOpenDrift() {
  const drifts = useV1(listSourceDrifts, []);
  return (
    <AsyncRegion
      state={drifts}
      what="thay đổi cấu trúc"
      skeletonRows={8}
      isEmpty={(page) => page.items.length === 0}
      empty={
        <div className="flex flex-col">
          <PageHeader bordered breadcrumb={['Dữ liệu']} title="Lệch cấu trúc" />
          <EmptyState
            className="m-da-3xl"
            icon="layers"
            title="Không có thay đổi cấu trúc nào chờ duyệt"
            description="Khi lần quét thấy một nguồn đổi cấu trúc, job liên quan tạm dừng theo chính sách và thay đổi hiện ở đây để bạn duyệt."
          />
        </div>
      }
    >
      {(page) => <DriftView drift={page.items[0]} onChanged={drifts.reload} />}
    </AsyncRegion>
  );
}

type StatusTone = Parameters<typeof StatusDot>[0]['tone'];

const CHANGE: Record<ColumnChange['kind'], { label: string; tone: StatusTone }> = {
  add_column: { label: 'Thêm cột', tone: 'info' },
  widen_type: { label: 'Mở rộng kiểu', tone: 'success' },
  change_type: { label: 'Đổi kiểu', tone: 'warning' },
  drop_column: { label: 'Xoá cột', tone: 'error' },
};

function driftStatus(drift: SourceDrift): { label: string; tone: StatusTone } {
  if (drift.status === 'accepted') return { label: 'Đã chấp nhận', tone: 'success' };
  if (drift.status === 'rejected') return { label: 'Đã từ chối', tone: 'idle' };
  if (drift.approvals.length > 0)
    return {
      label: `Chờ duyệt · ${drift.approvals.length}/${drift.approvals_required} người`,
      tone: 'warning',
    };
  return { label: 'Chờ duyệt', tone: 'warning' };
}

function DriftView({ drift, onChanged }: { drift: SourceDrift; onChanged: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState<'accept' | 'reject' | null>(null);
  const [confirmReject, setConfirmReject] = useState(false);
  const [rejectError, setRejectError] = useState<string>();
  const status = driftStatus(drift);
  const pending = drift.status === 'pending';
  const changesView = useTableView(drift.changes, {
    search: (change) => {
      const kind = CHANGE[change.kind];
      const label = change.note ? `${kind.label} · ${change.note}` : kind.label;
      return `${change.column} ${change.before ?? ''} ${change.after ?? ''} ${label}`;
    },
    filters: [
      {
        id: 'kind',
        label: 'Loại',
        control: 'Lọc thay đổi theo loại',
        value: (change) => CHANGE[change.kind].label,
      },
    ],
    sorts: [
      { id: 'column', value: (change) => change.column },
      { id: 'before', value: (change) => change.before },
      { id: 'after', value: (change) => change.after },
      {
        id: 'kind',
        value: (change) => {
          const kind = CHANGE[change.kind];
          return change.note ? `${kind.label} · ${change.note}` : kind.label;
        },
      },
    ],
    pageSize: 50,
  });
  const impactView = useTableView(drift.impact, {
    search: (item) => `${item.object} ${item.kind_label} ${item.effect}`,
    filters: [
      {
        id: 'kind',
        label: 'Loại',
        control: 'Lọc ảnh hưởng theo loại',
        value: (item) => item.kind_label,
      },
    ],
    sorts: [
      { id: 'object', value: (item) => item.object },
      { id: 'kind', value: (item) => item.kind_label },
      { id: 'effect', value: (item) => item.effect },
    ],
    pageSize: 50,
  });

  const resolve = async (resolution: 'accept' | 'reject') => {
    setBusy(resolution);
    try {
      const next = await resolveSourceDrift(drift.id, { resolution });
      setConfirmReject(false);
      toast({
        tone: 'success',
        message:
          resolution === 'reject'
            ? `Đã từ chối. Job ${drift.table_name} chạy tiếp với cấu trúc cũ.`
            : next.status === 'accepted'
              ? `Đã chấp nhận. Job ${drift.table_name} nạp tiếp và nạp lại bản ghi đang giữ.`
              : `Đã ghi duyệt của bạn. Cần thêm ${next.approvals_required - next.approvals.length} người duyệt.`,
      });
      onChanged();
    } catch (e) {
      const message = (e as Error).message;
      if (resolution === 'reject') setRejectError(message);
      else toast({ tone: 'error', message: `Không chấp nhận được. ${message}` });
    } finally {
      setBusy(null);
    }
  };

  const facts = [
    `Phát hiện ${drift.detected_text}`,
    drift.job_paused ? `job ${drift.table_name} đang tạm dừng` : null,
    drift.held_records > 0 ? `${formatNumber(drift.held_records)} bản ghi giữ lại` : null,
  ].filter(Boolean);

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Lệch cấu trúc', drift.source_name]}
        title={
          <span className="flex items-center gap-[10px]">
            {drift.title}
            <StatusDot tone={status.tone} className="da-body-small font-normal">
              {status.label}
            </StatusDot>
          </span>
        }
        description={facts.join(' · ')}
        actions={
          pending && (
            <>
              <Button
                kind="ghost"
                disabled={busy !== null}
                onClick={() => {
                  setRejectError(undefined);
                  setConfirmReject(true);
                }}
              >
                Từ chối, giữ cấu trúc cũ
              </Button>
              <Tooltip content="Màn sửa ánh xạ đang được làm.">
                <Button disabled>Sửa ánh xạ</Button>
              </Tooltip>
              <Button
                kind="primary"
                loading={busy === 'accept'}
                disabled={busy !== null}
                onClick={() => void resolve('accept')}
              >
                Chấp nhận và nạp tiếp
              </Button>
            </>
          )
        }
      />
      <div className="flex flex-1 max-[1023px]:flex-col">
        <div className="min-w-0 flex-1 pl-da-3xl pr-da-2xl">
          <section
            aria-labelledby="drift-changes"
            className="flex flex-col gap-[10px] border-b border-border py-da-xl"
          >
            <div className="flex items-center gap-da-md">
              <h2 id="drift-changes" className="flex-1 text-foreground da-heading-3">
                Thay đổi
              </h2>
              <span className="text-muted-foreground da-caption">
                {drift.changes.length} thay đổi trên {drift.table_name}
              </span>
            </div>
            <TableToolbar
              view={changesView}
              placeholder="Tìm cột…"
              searchLabel="Tìm cột thay đổi"
            />
            <table className="w-full table-fixed border-collapse">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground da-caption">
                  <SortableTh view={changesView} column="column" className="w-[196px] px-da-lg py-da-md font-normal">
                    Cột
                  </SortableTh>
                  <SortableTh view={changesView} column="before" className="w-[216px] px-da-md py-da-md font-normal">
                    Trước
                  </SortableTh>
                  <SortableTh view={changesView} column="after" className="w-[216px] px-da-md py-da-md font-normal">
                    Sau
                  </SortableTh>
                  <SortableTh view={changesView} column="kind" className="px-da-md py-da-md font-normal">
                    Loại
                  </SortableTh>
                </tr>
              </thead>
              <tbody>
                {changesView.rows.map((change) => {
                  const kind = CHANGE[change.kind];
                  return (
                    <tr key={change.column} className="border-b border-border">
                      <td className="truncate px-da-lg py-[9px] text-foreground da-mono">
                        {change.column}
                      </td>
                      <td className="truncate px-da-md py-[9px] text-muted-foreground da-mono">
                        {change.before ?? '—'}
                      </td>
                      <td className="truncate px-da-md py-[9px] text-foreground da-mono">
                        {change.after ?? '—'}
                      </td>
                      <td className="px-da-md py-[9px]">
                        <StatusDot tone={kind.tone}>
                          {change.note ? `${kind.label} · ${change.note}` : kind.label}
                        </StatusDot>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <TablePager view={changesView} />
          </section>
          {drift.impact.length > 0 && (
            <section
              aria-labelledby="drift-impact"
              className="flex flex-col gap-[10px] border-b border-border py-da-xl"
            >
              <div className="flex items-center gap-da-md">
                <h2 id="drift-impact" className="flex-1 text-foreground da-heading-3">
                  Ảnh hưởng
                </h2>
                {drift.impact_by && (
                  <span className="text-muted-foreground da-caption">
                    {drift.impact_by.name} đánh giá lúc {drift.impact_by.at_text}
                  </span>
                )}
              </div>
              <TableToolbar
                view={impactView}
                placeholder="Tìm đối tượng…"
                searchLabel="Tìm đối tượng"
              />
              <table className="w-full table-fixed border-collapse">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground da-caption">
                    <SortableTh view={impactView} column="object" className="px-da-lg py-da-md font-normal">
                      Đối tượng
                    </SortableTh>
                    <SortableTh view={impactView} column="kind" className="w-[136px] px-da-md py-da-md font-normal">
                      Loại
                    </SortableTh>
                    <SortableTh view={impactView} column="effect" className="w-[316px] px-da-md py-da-md font-normal">
                      Ảnh hưởng
                    </SortableTh>
                  </tr>
                </thead>
                <tbody>
                  {impactView.rows.map((item) => (
                    <tr key={item.object} className="border-b border-border">
                      <td className="truncate px-da-lg py-[9px] text-foreground da-body-strong">
                        {item.object}
                      </td>
                      <td className="px-da-md py-[9px] text-muted-foreground da-body-small">
                        {item.kind_label}
                      </td>
                      <td className="px-da-md py-[9px] text-foreground da-body-small">
                        {item.effect}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <TablePager view={impactView} />
            </section>
          )}
        </div>
        <aside
          aria-label="Đề xuất và người duyệt"
          className="flex w-[360px] shrink-0 flex-col gap-[10px] border-l border-border bg-background px-da-2xl pt-[20px] max-[1023px]:w-full max-[1023px]:border-l-0 max-[1023px]:border-t max-[1023px]:pb-da-xl"
        >
          {drift.proposal && (
            <>
              <h2 className="text-foreground da-heading-3">Đề xuất của agent</h2>
              <p className="text-foreground da-body-small">{drift.proposal}</p>
            </>
          )}
          <h2 className="text-foreground da-heading-3">Người duyệt</h2>
          <p className="text-foreground da-body-small">{drift.reviewers_text}</p>
          {drift.approvals.length > 0 && (
            <p className="text-muted-foreground da-caption">
              Đã duyệt: {drift.approvals.map((a) => `${a.name} lúc ${formatTime(a.at)}`).join(', ')}
            </p>
          )}
          <h2 className="text-foreground da-heading-3">Nếu từ chối</h2>
          <p className="text-foreground da-body-small">{drift.if_rejected_text}</p>
          <p className="text-muted-foreground da-caption">
            UC-04-03 · thay đổi làm hỏng chỉ số cần duyệt hai người
          </p>
        </aside>
      </div>
      <ConfirmDialog
        open={confirmReject}
        title="Từ chối thay đổi cấu trúc?"
        confirmLabel="Từ chối, giữ cấu trúc cũ"
        loading={busy === 'reject'}
        error={rejectError}
        onConfirm={() => void resolve('reject')}
        onCancel={() => setConfirmReject(false)}
      >
        {drift.if_rejected_text}
      </ConfirmDialog>
    </div>
  );
}
