import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Checkbox } from '../../design-system/Checkbox';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { EmptyState } from '../../design-system/EmptyState';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { useToast } from '../../design-system/Toast';
import { formatNumber } from '../../lib/formatVN';
import {
  discardErrorGroups,
  listErrorGroups,
  reloadErrorGroups,
  type ErrorRecordGroup,
} from '../../lib/v1/sources';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P04 · Bản ghi lỗi" (2281:17467, UC-04-07): records a job could not load, grouped by cause,
 * with the suggested fix. Selected groups are loaded again (in time order, without duplicates)
 * or dropped with a reason that goes to the audit log. A group whose cause is a structure change
 * loads only after that change is accepted; the right panel links to it. Figma's "Bỏ qua lô"
 * (skipBatch) is not drawn: skipping a batch is doing nothing, its records stay in the DLQ until
 * retention removes them (BR-57).
 */
export default function ErrorRecordsPage() {
  const groups = useV1(listErrorGroups, []);
  return (
    <AsyncRegion
      state={groups}
      what="bản ghi lỗi"
      skeletonRows={6}
      isEmpty={(page) => page.items.length === 0}
      empty={
        <div className="flex flex-col">
          <PageHeader bordered breadcrumb={['Dữ liệu', 'Bản ghi lỗi']} title="Bản ghi lỗi" />
          <EmptyState
            className="m-da-3xl"
            title="Không có bản ghi lỗi"
            description="Bản ghi mà job nạp không ghi được vào bảng đích sẽ hiện ở đây, gom theo nguyên nhân, và được giữ 30 ngày."
          />
        </div>
      }
    >
      {(page) => <ErrorGroups groups={page.items} onChanged={groups.reload} />}
    </AsyncRegion>
  );
}

function ErrorGroups({ groups, onChanged }: { groups: ErrorRecordGroup[]; onChanged: () => void }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<'reload' | 'discard' | null>(null);
  const [discarding, setDiscarding] = useState(false);
  const [reason, setReason] = useState('');
  const [discardError, setDiscardError] = useState<string>();

  // Groups emptied by a reload or discard cannot be chosen again.
  const chosen = groups.filter((g) => selected.has(g.id) && g.count > 0);
  const chosenCount = chosen.reduce((n, g) => n + g.count, 0);
  const total = groups.reduce((n, g) => n + g.count, 0);
  const jobs = new Set(groups.map((g) => g.job_id)).size;
  const retention = groups[0]?.retention_days ?? 30;
  const blocked = groups.filter((g) => g.blocked_by_drift_id && g.count > 0);
  const blockedCount = blocked.reduce((n, g) => n + g.count, 0);
  const sampleGroup =
    chosen.find((g) => g.sample) ?? groups.find((g) => g.sample && g.count > 0) ?? null;
  const view = useTableView(groups, {
    search: (group) =>
      `${group.cause} ${group.table_name} ${formatNumber(group.count)} ${group.suggestion ?? ''}`,
    filters: [{ id: 'job', label: 'Job', value: (group) => group.table_name }],
    sorts: [
      { id: 'cause', value: (group) => group.cause },
      { id: 'job', value: (group) => group.table_name },
      { id: 'count', value: (group) => group.count },
      { id: 'suggestion', value: (group) => group.suggestion },
    ],
    pageSize: 50,
  });

  const toggle = (id: string, on: boolean) =>
    setSelected((s) => {
      const next = new Set(s);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });

  const reload = async () => {
    setBusy('reload');
    try {
      const { reloaded } = await reloadErrorGroups(chosen);
      toast({ tone: 'success', message: `Đã nạp lại ${formatNumber(reloaded)} bản ghi.` });
      setSelected(new Set());
      onChanged();
    } catch (e) {
      toast({ tone: 'error', message: `Không nạp lại được. ${(e as Error).message}` });
    } finally {
      setBusy(null);
    }
  };

  const discard = async () => {
    if (!reason.trim()) {
      setDiscardError('Ghi lý do để lưu vào nhật ký kiểm toán.');
      return;
    }
    setBusy('discard');
    try {
      const { discarded } = await discardErrorGroups(chosen, reason.trim());
      toast({ tone: 'success', message: `Đã bỏ ${formatNumber(discarded)} bản ghi.` });
      setDiscarding(false);
      setSelected(new Set());
      onChanged();
    } catch (e) {
      setDiscardError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Bản ghi lỗi']}
        title="Bản ghi lỗi"
        description={`${formatNumber(total)} bản ghi từ ${jobs} job · giữ ${retention} ngày · bản ghi đã che dữ liệu cá nhân`}
        actions={
          <>
            <Button
              kind="ghost"
              disabled={chosen.length === 0 || busy !== null}
              onClick={() => {
                setReason('');
                setDiscardError(undefined);
                setDiscarding(true);
              }}
            >
              Bỏ đã chọn
            </Button>
            <Button
              kind="primary"
              loading={busy === 'reload'}
              disabled={chosen.length === 0 || busy !== null}
              onClick={() => void reload()}
            >
              Nạp lại đã chọn
            </Button>
          </>
        }
      />
      <div className="flex flex-1 max-[1023px]:flex-col">
        <div className="flex min-w-0 flex-1 flex-col gap-da-lg pl-da-3xl pr-da-2xl pt-da-xl">
          <h2 className="text-foreground da-heading-3">Gom theo nguyên nhân</h2>
          <TableToolbar view={view} placeholder="Tìm nguyên nhân…" searchLabel="Tìm nguyên nhân" />
          <table className="w-full table-fixed border-collapse">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground da-caption">
                <SortableTh view={view} className="w-[40px] py-da-md pl-da-lg font-normal">
                  <span className="sr-only">Chọn</span>
                </SortableTh>
                <SortableTh view={view} column="cause" className="px-da-md py-da-md font-normal">
                  Nguyên nhân
                </SortableTh>
                <SortableTh view={view} column="job" className="w-[186px] px-da-md py-da-md font-normal">
                  Job
                </SortableTh>
                <SortableTh view={view} column="count" className="w-[116px] px-da-md py-da-md font-normal">
                  Số bản ghi
                </SortableTh>
                <SortableTh
                  view={view}
                  column="suggestion"
                  className="w-[276px] px-da-md py-da-md font-normal max-[1279px]:hidden"
                >
                  Cách xử lý gợi ý
                </SortableTh>
              </tr>
            </thead>
            <tbody>
              {view.rows.map((group) => (
                <tr key={group.id} className="border-b border-border">
                  <td className="py-[9px] pl-da-lg">
                    <Checkbox
                      checked={selected.has(group.id) && group.count > 0}
                      disabled={group.count === 0}
                      onChange={(on) => toggle(group.id, on)}
                    >
                      <span className="sr-only">{group.cause}</span>
                    </Checkbox>
                  </td>
                  <td className="truncate px-da-md py-[9px] text-foreground da-body-strong">
                    {group.cause}
                  </td>
                  <td className="truncate px-da-md py-[9px] text-muted-foreground da-mono">
                    {group.table_name}
                  </td>
                  <td className="px-da-md py-[9px] text-foreground da-mono">
                    {formatNumber(group.count)}
                  </td>
                  <td className="px-da-md py-[9px] text-foreground da-body-small max-[1279px]:hidden">
                    {group.suggestion ?? '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <TablePager view={view} />
          {sampleGroup && (
            <>
              <h2 className="text-foreground da-heading-3">Mẫu bản ghi · {sampleGroup.cause}</h2>
              <pre className="overflow-x-auto whitespace-pre-wrap rounded-md border border-border bg-background px-da-lg py-[10px] text-foreground da-mono">
                {sampleGroup.sample}
              </pre>
            </>
          )}
        </div>
        <aside
          aria-label="Trước khi nạp lại"
          className="flex w-[340px] shrink-0 flex-col gap-[10px] border-l border-border bg-background px-da-2xl pt-[20px] max-[1023px]:w-full max-[1023px]:border-l-0 max-[1023px]:border-t max-[1023px]:pb-da-xl"
        >
          <h2 className="text-foreground da-heading-3">Trước khi nạp lại</h2>
          {blockedCount > 0 && (
            <p className="text-foreground da-body-small">
              · {formatNumber(blockedCount)} bản ghi chỉ nạp được sau khi thay đổi cấu trúc được
              chấp nhận.
            </p>
          )}
          <p className="text-foreground da-body-small">
            · Nạp lại giữ đúng thứ tự thời gian; không tạo dòng trùng.
          </p>
          <p className="text-foreground da-body-small">
            · Bản ghi bị bỏ được ghi kiểm toán kèm lý do.
          </p>
          {blocked[0]?.blocked_by_drift_id && (
            <button
              type="button"
              className="self-start text-primary da-body-strong hover:underline"
              onClick={() => navigate('data', 'DRIFT', blocked[0].blocked_by_drift_id!)}
            >
              Mở thay đổi cấu trúc
            </button>
          )}
          <p className="text-muted-foreground da-caption">
            UC-04-07 · bản ghi lỗi giữ {retention} ngày rồi tự xoá
          </p>
        </aside>
      </div>
      <ConfirmDialog
        open={discarding}
        size="md"
        title={`Bỏ ${formatNumber(chosenCount)} bản ghi?`}
        confirmLabel="Bỏ bản ghi"
        loading={busy === 'discard'}
        error={discardError}
        onConfirm={() => void discard()}
        onCancel={() => setDiscarding(false)}
      >
        <div className="flex flex-col gap-da-md">
          <p>Bản ghi bị bỏ không nạp vào bảng đích. Lý do được ghi vào nhật ký kiểm toán.</p>
          <Field label="Lý do">
            <Input
              className="w-full"
              value={reason}
              placeholder="Ví dụ: nguồn đã xác nhận là giao dịch thử"
              onChange={(e) => setReason(e.target.value)}
            />
          </Field>
        </div>
      </ConfirmDialog>
    </div>
  );
}
