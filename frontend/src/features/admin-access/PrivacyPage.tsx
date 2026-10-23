import { useEffect, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Checkbox } from '../../design-system/Checkbox';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { EmptyState } from '../../design-system/EmptyState';
import { Notice } from '../../design-system/Notice';
import { StatusDot } from '../../design-system/StatusDot';
import {
  SortableTh,
  TablePager,
  TableToolbar,
  tableEmptyRow,
  useTableView,
} from '../../design-system/tableView';
import { Textarea } from '../../design-system/Textarea';
import { useToast } from '../../design-system/Toast';
import { cn } from '../../lib/utils';
import {
  executeDsr,
  getDsr,
  listDsr,
  rejectDsr,
  type DsrItem,
  type DsrRequest,
} from '../../lib/v1/adminAccess';
import { csvCell } from '../../lib/csv';
import { useV1 } from '../../lib/v1/useV1';
import { CreateDsrDialog, DSR_KINDS } from './CreateDsrDialog';
import { dayMonth } from './format';

/*
 * Rail item "Quyền riêng tư" (UC-11-06). With an id it is "P11 · Yêu cầu của chủ thể dữ liệu"
 * (2306:19909): where the agent found the person's data, what can be deleted and what must be
 * kept (with the legal basis), then "Xoá đã chọn" through "Xác nhận · dsr". Without one it lists
 * the requests (no Figma list frame) and opens "Hộp thoại · createDSR".
 */
const STATUS: Record<
  DsrRequest['status'],
  { tone: 'warning' | 'success' | 'neutral' | 'info'; label: string }
> = {
  open: { tone: 'info', label: 'Mới nhận' },
  searching: { tone: 'info', label: 'Đang tìm dữ liệu' },
  review: { tone: 'warning', label: 'Đang xử lý' },
  executing: { tone: 'warning', label: 'Đang thực hiện' },
  done: { tone: 'success', label: 'Đã xong' },
  rejected: { tone: 'neutral', label: 'Đã từ chối' },
};

export default function PrivacyPage() {
  const { routeId } = useAppState();
  return routeId ? <DsrDetail id={routeId} /> : <DsrList />;
}

function DsrList() {
  const { navigate } = useAppState();
  const toast = useToast();
  const list = useV1(['listDsr'], listDsr);
  const [creating, setCreating] = useState(false);
  const items = list.data?.items ?? [];
  const open = items.filter((d) => d.status !== 'done' && d.status !== 'rejected').length;
  const kindOf = (d: DsrRequest) => DSR_KINDS.find((k) => k.value === d.request_kind)?.label ?? '';
  const view = useTableView(items, {
    search: (d) =>
      `${d.title} ${d.code} ${kindOf(d)} ${dayMonth(d.due_at)} ${d.handler_name} ${STATUS[d.status].label}`,
    filters: [{ id: 'status', label: 'Trạng thái', value: (d) => STATUS[d.status].label }],
    sorts: [
      { id: 'title', value: (d) => d.title },
      { id: 'kind', value: (d) => kindOf(d) },
      { id: 'due', value: (d) => d.due_at },
      { id: 'handler', value: (d) => d.handler_name },
      { id: 'status', value: (d) => STATUS[d.status].label },
    ],
    pageSize: 50,
  });

  const create = (
    <Button kind="primary" onClick={() => setCreating(true)}>
      Tạo yêu cầu
    </Button>
  );

  return (
    <div className="flex flex-col">
      <PageHeader
        bordered
        breadcrumb={['Quản trị', 'Quyền riêng tư']}
        title="Yêu cầu của chủ thể dữ liệu"
        description={
          list.data
            ? `${open} yêu cầu đang mở · hạn theo Nghị định 13, nhắc trước 5 ngày làm việc`
            : undefined
        }
        actions={create}
      />
      <div className="px-da-3xl pt-da-xl">
        <AsyncRegion
          state={list}
          what="yêu cầu của chủ thể dữ liệu"
          isEmpty={(p) => p.items.length === 0}
          empty={
            <EmptyState
              icon="eye"
              title="Chưa có yêu cầu nào"
              description="Khi khách hàng xin biết, nhận bản sao, sửa hoặc xoá dữ liệu cá nhân, tạo yêu cầu ở đây để agent tìm mọi nơi có dữ liệu đó."
              action={create}
            />
          }
        >
          {() => (
            <div className="flex flex-col gap-da-lg">
              <TableToolbar view={view} placeholder="Tìm yêu cầu…" searchLabel="Tìm yêu cầu" />
              <table className="w-full table-fixed border-collapse">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground da-caption">
                    <SortableTh
                      view={view}
                      column="title"
                      className="px-da-lg py-da-md font-normal"
                    >
                      Yêu cầu
                    </SortableTh>
                    <SortableTh
                      view={view}
                      column="kind"
                      className="w-[160px] px-da-lg py-da-md font-normal"
                    >
                      Loại
                    </SortableTh>
                    <SortableTh
                      view={view}
                      column="due"
                      className="w-[100px] px-da-lg py-da-md font-normal"
                    >
                      Hạn
                    </SortableTh>
                    <SortableTh
                      view={view}
                      column="handler"
                      className="w-[200px] px-da-lg py-da-md font-normal"
                    >
                      Người xử lý
                    </SortableTh>
                    <SortableTh
                      view={view}
                      column="status"
                      className="w-[170px] px-da-lg py-da-md font-normal"
                    >
                      Trạng thái
                    </SortableTh>
                  </tr>
                </thead>
                <tbody>
                  {view.rows.map((d) => (
                    <tr key={d.id} className="border-b border-border align-middle hover:bg-subtle">
                      <td className="px-da-lg py-da-md">
                        <button
                          type="button"
                          className="flex min-w-0 flex-col gap-[2px] text-left"
                          onClick={() => navigate('admin', 'PRIVACY', d.id)}
                        >
                          <span className="truncate text-foreground da-body-strong">{d.title}</span>
                          <span className="text-muted-foreground da-mono">{d.code}</span>
                        </button>
                      </td>
                      <td className="px-da-lg py-da-md text-muted-foreground da-body-small">
                        {kindOf(d)}
                      </td>
                      <td className="px-da-lg py-da-md text-muted-foreground da-mono">
                        {dayMonth(d.due_at)}
                      </td>
                      <td className="px-da-lg py-da-md text-foreground da-body-small">
                        {d.handler_name}
                      </td>
                      <td className="px-da-lg py-da-md">
                        <StatusDot tone={STATUS[d.status].tone}>{STATUS[d.status].label}</StatusDot>
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
      </div>
      <CreateDsrDialog
        open={creating}
        onClose={() => setCreating(false)}
        onCreated={(dsr) => {
          setCreating(false);
          toast({
            tone: 'success',
            message: `Đã tạo ${dsr.code}. Agent đang tìm dữ liệu của chủ thể.`,
          });
          navigate('admin', 'PRIVACY', dsr.id);
        }}
      />
    </div>
  );
}

type Dialog = 'execute' | 'reject' | null;

function DsrDetail({ id }: { id: string }) {
  const toast = useToast();
  const dsr = useV1(['getDsr', id], () => getDsr(id));
  const [selected, setSelected] = useState<string[]>([]);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [reason, setReason] = useState('');
  const [missing, setMissing] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();

  // Everything that may be deleted starts selected; what must be kept cannot be.
  useEffect(() => {
    if (dsr.data)
      setSelected(
        dsr.data.items
          .filter((i) => i.proposed_action === 'delete' && i.decision === 'pending')
          .map((i) => i.id),
      );
  }, [dsr.data]);

  const act = async (d: DsrRequest) => {
    setSending(true);
    setError(undefined);
    try {
      if (dialog === 'reject') {
        await rejectDsr(d.id, reason.trim());
        toast({ tone: 'success', message: `Đã từ chối ${d.code}` });
      } else {
        await executeDsr(d.id, selected);
        toast({
          tone: 'success',
          message: `Đã xoá dữ liệu của ${d.code}. Thư trả lời chờ Pháp chế duyệt.`,
        });
      }
      setDialog(null);
      dsr.reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  const exportList = (d: DsrRequest) => {
    const rows = [
      ['Nơi', 'Loại', 'Số mục', 'Xoá được?'],
      ...d.items.map((i) => [
        i.location_label,
        i.kind_label,
        String(i.entry_count),
        deletableText(i),
      ]),
    ];
    const csv = rows.map((r) => r.map(csvCell).join(',')).join('\n');
    const url = URL.createObjectURL(new Blob([`﻿${csv}`], { type: 'text/csv;charset=utf-8' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `${d.code}.csv`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex min-h-full flex-col">
      <AsyncRegion state={dsr} what="yêu cầu" skeletonRows={5}>
        {(d) => {
          const openReq = d.status !== 'done' && d.status !== 'rejected';
          const chosen = d.items.filter((i) => selected.includes(i.id));
          const kept = d.items.filter((i) => i.proposed_action === 'retain');
          return (
            <>
              <PageHeader
                bordered
                breadcrumb={['Quản trị', 'Quyền riêng tư', d.code]}
                title={
                  <span className="flex items-center gap-[10px]">
                    {d.title}
                    <StatusDot tone={STATUS[d.status].tone}>{STATUS[d.status].label}</StatusDot>
                  </span>
                }
                description={`Nhận ${dayMonth(d.received_at)} qua ${d.channel_label} · hạn xử lý ${dayMonth(d.due_at)} theo ${d.legal_ref} · người xử lý ${d.handler_name}`}
                actions={
                  openReq && (
                    <>
                      <Button
                        kind="ghost"
                        onClick={() => {
                          setReason('');
                          setMissing(false);
                          setDialog('reject');
                        }}
                      >
                        Từ chối có lý do
                      </Button>
                      <Button disabled={d.items.length === 0} onClick={() => exportList(d)}>
                        Xuất danh sách
                      </Button>
                      <Button
                        kind="primary"
                        disabled={chosen.length === 0}
                        onClick={() => setDialog('execute')}
                      >
                        Xoá đã chọn
                      </Button>
                    </>
                  )
                }
              />
              {error && (
                <div className="px-da-3xl pt-da-xl">
                  <Notice tone="error">{error}</Notice>
                </div>
              )}
              <div className="flex min-h-0 flex-1">
                <div className="flex min-w-0 flex-1 flex-col gap-[14px] pl-da-3xl pr-da-2xl pt-da-xl pb-da-xl">
                  <h2 className="text-foreground da-heading-3">
                    Agent tìm thấy dữ liệu của người này ở
                  </h2>
                  {d.items.length === 0 ? (
                    <EmptyState
                      icon="search"
                      title="Agent đang tìm dữ liệu"
                      description="Kết quả hiện ở đây khi agent quét xong các nguồn, tài liệu, phiên hỏi đáp và bộ nhớ agent."
                    />
                  ) : (
                    <FoundItemsTable
                      items={d.items}
                      openReq={openReq}
                      selected={selected}
                      setSelected={setSelected}
                    />
                  )}
                </div>
                <aside className="flex w-[320px] shrink-0 flex-col gap-[10px] border-l border-border bg-background px-da-2xl pt-[20px] max-[1279px]:hidden">
                  <h2 className="text-foreground da-heading-3">Trả lời khách hàng</h2>
                  <p className="text-foreground da-body-small">
                    Sau khi xoá, hệ thống soạn thư trả lời nêu phần đã xoá và phần phải giữ kèm căn
                    cứ pháp lý. Pháp chế duyệt trước khi gửi.
                  </p>
                  <p className="text-muted-foreground da-caption">
                    UC-11-06 · xoá cần hai người: người xử lý và Pháp chế
                  </p>
                </aside>
              </div>
              <ConfirmDialog
                open={dialog !== null}
                title={dialog === 'reject' ? `Từ chối ${d.code}?` : `Duyệt kế hoạch xoá ${d.code}?`}
                confirmLabel={dialog === 'reject' ? 'Từ chối' : 'Duyệt và thực hiện'}
                loading={sending}
                error={missing ? 'Ghi lý do từ chối; khách hàng nhận lý do này.' : undefined}
                onCancel={() => setDialog(null)}
                onConfirm={() => {
                  if (dialog === 'reject' && !reason.trim()) {
                    setMissing(true);
                    return;
                  }
                  void act(d);
                }}
              >
                {dialog === 'reject' ? (
                  <div className="flex flex-col gap-da-sm">
                    <span>Yêu cầu sẽ đóng và khách hàng nhận thư nêu lý do.</span>
                    <Textarea
                      value={reason}
                      onChange={(e) => setReason(e.target.value)}
                      aria-label="Lý do từ chối (bắt buộc)"
                      placeholder="Lý do từ chối (bắt buộc)"
                      rows={3}
                      className="w-full"
                    />
                  </div>
                ) : (
                  <>
                    Xoá {chosen.reduce((sum, i) => sum + i.entry_count, 0)} mục ở {chosen.length}{' '}
                    nơi ({chosen.map((i) => i.kind_label.toLowerCase()).join(', ')}). Không hoàn tác
                    được.
                    {kept.length > 0 &&
                      ` ${kept.map((i) => i.location_label).join(', ')} được giữ theo nghĩa vụ pháp lý.`}
                  </>
                )}
              </ConfirmDialog>
            </>
          );
        }}
      </AsyncRegion>
    </div>
  );
}

function FoundItemsTable({
  items,
  openReq,
  selected,
  setSelected,
}: {
  items: DsrItem[];
  openReq: boolean;
  selected: string[];
  setSelected: (update: (list: string[]) => string[]) => void;
}) {
  const view = useTableView(items, {
    search: (i) => `${i.location_label} ${i.kind_label} ${i.entry_count} ${deletableText(i)}`,
    filters: [{ id: 'kind', label: 'Loại', value: (i) => i.kind_label }],
    sorts: [
      { id: 'where', value: (i) => i.location_label },
      { id: 'kind', value: (i) => i.kind_label },
      { id: 'count', value: (i) => i.entry_count },
      { id: 'deletable', value: (i) => deletableText(i) },
    ],
    pageSize: 50,
  });
  return (
    <div className="flex flex-col gap-da-lg">
      <TableToolbar view={view} placeholder="Tìm nơi lưu…" searchLabel="Tìm nơi lưu" />
      <table className="w-full table-fixed border-collapse">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground da-caption">
            <SortableTh view={view} className="w-[40px] px-da-lg py-da-md">
              <span className="sr-only">Chọn</span>
            </SortableTh>
            <SortableTh view={view} column="where" className="px-da-lg py-da-md font-normal">
              Nơi
            </SortableTh>
            <SortableTh
              view={view}
              column="kind"
              className="w-[162px] px-da-lg py-da-md font-normal"
            >
              Loại
            </SortableTh>
            <SortableTh
              view={view}
              column="count"
              className="w-[92px] px-da-lg py-da-md font-normal"
            >
              Số mục
            </SortableTh>
            <SortableTh
              view={view}
              column="deletable"
              className="w-[242px] px-da-lg py-da-md font-normal"
            >
              Xoá được?
            </SortableTh>
          </tr>
        </thead>
        <tbody>
          {view.rows.map((i) => {
            const can = i.proposed_action === 'delete' && i.decision === 'pending' && openReq;
            return (
              <tr key={i.id} className="border-b border-border align-middle">
                <td className="px-da-lg py-da-md">
                  <Checkbox
                    checked={selected.includes(i.id)}
                    disabled={!can}
                    onChange={(on) =>
                      setSelected((list) => (on ? [...list, i.id] : list.filter((x) => x !== i.id)))
                    }
                  >
                    <span className="sr-only">{i.location_label}</span>
                  </Checkbox>
                </td>
                <td className="px-da-lg py-da-md text-foreground da-body-small">
                  {i.location_label}
                </td>
                <td className="px-da-lg py-da-md text-muted-foreground da-body-small">
                  {i.kind_label}
                </td>
                <td className="px-da-lg py-da-md text-foreground da-mono">{i.entry_count}</td>
                <td
                  className={cn(
                    'px-da-lg py-da-md da-body-small',
                    i.proposed_action === 'retain' ? 'text-warning' : 'text-foreground',
                  )}
                >
                  {deletableText(i)}
                </td>
              </tr>
            );
          })}
          {view.total === 0 && tableEmptyRow(5)}
        </tbody>
      </table>
      <TablePager view={view} />
    </div>
  );
}

function deletableText(i: DsrRequest['items'][number]): string {
  if (i.decision === 'done') return 'Đã xoá';
  if (i.proposed_action === 'retain') return `Không · ${i.legal_basis ?? 'phải giữ'}`;
  if (i.proposed_action === 'provide') return 'Cung cấp bản sao';
  return 'Được';
}
