import { AsyncRegion } from '../../design-system/AsyncRegion';
import { StatusDot } from '../../design-system/StatusDot';
import {
  SortableTh,
  TablePager,
  TableToolbar,
  tableEmptyRow,
  useTableView,
} from '../../design-system/tableView';
import { getChannelRules, type ChannelRules } from '../../lib/v1/adminPlatform';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "v3.1 · Kênh · Quy tắc" (2112:12987, UC-08-07 step 2): what each outside channel may do per
 * kind of request. The risk of a request decides the channel; outside channels only receive and
 * answer quickly, Data Agent stays the source of truth. Green = allowed, orange = limited,
 * red = link only. Read only: the rules are the organisation's, opened from "Thông báo".
 */
export default function ChannelRulesPage() {
  const rules = useV1(['getChannelRules'], getChannelRules);
  return (
    <div className="flex flex-col gap-da-xl px-da-2xl py-da-xl">
      <header className="flex flex-col gap-da-xl">
        <h1 className="text-foreground da-heading-1">Quy tắc gửi và xử lý ngoài ứng dụng</h1>
        <p className="text-muted-foreground da-body-small">
          Mức rủi ro của yêu cầu quyết định kênh được phép xử lý. Kênh ngoài chỉ là nơi nhận và trả
          lời nhanh; nguồn sự thật luôn là Data Agent.
        </p>
      </header>
      <AsyncRegion state={rules} what="quy tắc kênh" skeletonRows={6}>
        {(data) => (
          <>
            <RulesTable data={data} />
            <div className="grid grid-cols-4 gap-da-lg max-[1279px]:grid-cols-2">
              {data.principles.map((p) => (
                <section
                  key={p.title}
                  className="flex flex-col gap-da-sm rounded-lg border border-border bg-card px-da-lg py-da-md"
                >
                  <h2 className="text-foreground da-body-strong">{p.title}</h2>
                  <p className="text-muted-foreground da-caption">{p.body}</p>
                </section>
              ))}
            </div>
          </>
        )}
      </AsyncRegion>
    </div>
  );
}

function RulesTable({ data }: { data: ChannelRules }) {
  const first = data.channels[0];
  const view = useTableView(data.rows, {
    search: (row) =>
      `${row.label} ${data.channels.map((c) => row.cells[c.key]?.text ?? '').join(' ')} ${row.note}`,
    filters: [
      first
        ? {
            id: 'channel',
            label: first.label,
            value: (row: ChannelRules['rows'][number]) => row.cells[first.key]?.text ?? '',
          }
        : {
            id: 'kind',
            label: 'Loại yêu cầu',
            value: (row: ChannelRules['rows'][number]) => row.label,
          },
    ],
    sorts: [
      { id: 'kind', value: (row) => row.label },
      ...data.channels.map((c) => ({
        id: c.key,
        value: (row: ChannelRules['rows'][number]) => row.cells[c.key]?.text ?? '',
      })),
      { id: 'note', value: (row) => row.note },
    ],
    pageSize: 50,
  });
  return (
    <div className="flex flex-col gap-da-lg">
      <TableToolbar view={view} placeholder="Tìm loại yêu cầu…" searchLabel="Tìm loại yêu cầu" />
      <div className="overflow-hidden rounded-lg border border-border bg-card">
        <table className="w-full border-collapse">
          <thead>
            <tr className="bg-subtle text-left text-muted-foreground da-label">
              <SortableTh
                view={view}
                column="kind"
                className="w-[272px] py-da-sm pl-da-lg font-medium"
              >
                Loại yêu cầu
              </SortableTh>
              {data.channels.map((c) => (
                <SortableTh
                  key={c.key}
                  view={view}
                  column={c.key}
                  className="py-da-sm pl-da-lg font-medium"
                >
                  {c.label}
                </SortableTh>
              ))}
              <SortableTh view={view} column="note" className="py-da-sm px-da-lg font-medium">
                Ghi chú
              </SortableTh>
            </tr>
          </thead>
          <tbody>
            {view.rows.map((row) => (
              <tr key={row.kind} className="border-t border-border">
                <td className="py-da-md pl-da-lg text-foreground da-body-strong">{row.label}</td>
                {data.channels.map((c) => {
                  const cell = row.cells[c.key];
                  return (
                    <td key={c.key} className="py-da-md pl-da-lg">
                      {cell && <StatusDot tone={cell.tone}>{cell.text}</StatusDot>}
                    </td>
                  );
                })}
                <td className="py-da-md px-da-lg text-foreground da-body-small">{row.note}</td>
              </tr>
            ))}
            {view.total === 0 && tableEmptyRow(2 + data.channels.length)}
          </tbody>
        </table>
      </div>
      <TablePager view={view} />
    </div>
  );
}
