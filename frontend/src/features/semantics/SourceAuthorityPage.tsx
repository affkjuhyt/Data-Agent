import { Fragment, useState } from 'react';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Icon } from '../../design-system/Icon';
import { Notice } from '../../design-system/Notice';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import {
  listSourceAuthorities,
  saveSourceAuthority,
  type SourceAuthority,
} from '../../lib/v1/authorities';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P07 · Thẩm quyền nguồn" (2302:19178, UC-07-05): for each kind of data, the order in which
 * sources win when they disagree, the gap above which the agent asks instead of choosing, and
 * who it asks. A row opens to reorder its sources and change the threshold; "Lưu" sends every
 * changed row (A2: one PUT per kind), each becoming a draft that is backtested on 90 days of
 * conflicts and applies after approval.
 */
type Edit = { ranking: string[]; materiality: string };

export default function SourceAuthorityPage() {
  const authorities = useV1(['listSourceAuthorities'], listSourceAuthorities);
  const toast = useToast();
  const [open, setOpen] = useState<string>();
  const [edits, setEdits] = useState<Record<string, Edit>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const items = authorities.data?.items ?? [];
  const view = useTableView(items, {
    search: (a) =>
      `${a.data_kind_label} ${a.decider_name} ${a.ranking.map((s) => s.name).join(' ')} ${a.materiality ?? ''}`,
    filters: [
      {
        id: 'status',
        label: 'Trạng thái',
        value: (a) => (a.status === 'draft' ? 'Bản nháp' : 'Đang áp dụng'),
      },
    ],
    sorts: [
      { id: 'kind', value: (a) => a.data_kind_label },
      { id: 'rank', value: (a) => a.ranking.map((s) => s.name).join(' → ') },
      { id: 'threshold', value: (a) => edits[a.data_kind]?.materiality ?? a.materiality },
      { id: 'decider', value: (a) => a.decider_name },
    ],
    pageSize: 50,
  });
  const changed = items.filter((a) => isChanged(a, edits[a.data_kind]));
  const example = items.find((a) => a.last_conflict_text)?.last_conflict_text;

  const editOf = (a: SourceAuthority): Edit =>
    edits[a.data_kind] ?? {
      ranking: a.ranking.map((s) => s.source_id),
      materiality: a.materiality ?? '',
    };

  const save = async () => {
    setBusy(true);
    setError(undefined);
    try {
      for (const a of changed) {
        const edit = edits[a.data_kind];
        await saveSourceAuthority({
          data_kind: a.data_kind,
          ranking: edit.ranking,
          materiality: edit.materiality.trim() || null,
        });
      }
      toast({
        tone: 'success',
        message: `Đã lưu ${changed.length} thay đổi; chạy thử trên xung đột 90 ngày rồi chờ duyệt`,
      });
      setEdits({});
      setOpen(undefined);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      // Rows saved before a failure are drafts already; show them as such.
      authorities.reload();
    }
  };

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Ngữ nghĩa', 'Thẩm quyền nguồn']}
        title="Thẩm quyền nguồn"
        description="Khi hai nguồn cho số khác nhau, agent dùng thứ tự này; lệch vượt ngưỡng trọng yếu thì không tự chọn mà hỏi qua Cần bạn"
        actions={
          <Button kind="primary" loading={busy} disabled={changed.length === 0} onClick={save}>
            Lưu
          </Button>
        }
      />
      <div className="flex flex-col gap-[14px] px-da-3xl pt-da-xl">
        {error && <Notice tone="error">Không lưu được: {error}</Notice>}
        <AsyncRegion state={authorities} what="thẩm quyền nguồn">
          {() => (
            <>
            <TableToolbar view={view} placeholder="Tìm loại dữ liệu…" searchLabel="Tìm loại dữ liệu" />
            <table className="w-full table-fixed border-collapse">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground da-caption">
                  <SortableTh view={view} column="kind" className="w-[220px] px-da-lg py-da-md font-normal">
                    Loại dữ liệu
                  </SortableTh>
                  <SortableTh view={view} column="rank" className="px-da-lg py-da-md font-normal">
                    Thứ tự ưu tiên
                  </SortableTh>
                  <SortableTh view={view} column="threshold" className="w-[200px] px-da-lg py-da-md font-normal">
                    Ngưỡng trọng yếu
                  </SortableTh>
                  <SortableTh view={view} column="decider" className="w-[180px] px-da-lg py-da-md font-normal">
                    Người quyết khi lệch
                  </SortableTh>
                </tr>
              </thead>
              <tbody>
                {view.rows.map((a) => {
                  const edit = editOf(a);
                  const names = edit.ranking.map(
                    (id) => a.ranking.find((s) => s.source_id === id)?.name ?? id,
                  );
                  const isOpen = open === a.data_kind;
                  return (
                    <Fragment key={a.id}>
                      <tr className="border-b border-border">
                        <td className="px-da-lg py-da-md">
                          <button
                            type="button"
                            aria-expanded={isOpen}
                            onClick={() => setOpen(isOpen ? undefined : a.data_kind)}
                            className="flex items-center gap-da-xs text-left text-foreground da-body-strong"
                          >
                            {a.data_kind_label}
                            <Icon
                              name="chevron-down"
                              size={12}
                              className={isOpen ? 'rotate-180' : undefined}
                            />
                          </button>
                          {a.status === 'draft' && (
                            <StatusDot tone="info">Bản nháp · chờ duyệt</StatusDot>
                          )}
                        </td>
                        <td className="px-da-lg py-da-md text-foreground da-body-small">
                          {names.map((n, i) => `${i + 1} ${n}`).join(' → ')}
                        </td>
                        <td className="px-da-lg py-da-md text-foreground da-mono">
                          {edit.materiality || '—'}
                        </td>
                        <td className="px-da-lg py-da-md text-muted-foreground da-body-small">
                          {a.decider_name}
                        </td>
                      </tr>
                      {isOpen && (
                        <tr className="border-b border-border bg-background">
                          <td colSpan={4} className="px-da-lg py-da-md">
                            <RankingEditor
                              authority={a}
                              edit={edit}
                              onChange={(next) => setEdits({ ...edits, [a.data_kind]: next })}
                            />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
            <TablePager view={view} />
            </>
          )}
        </AsyncRegion>
        {example && (
          <div className="flex flex-col gap-[6px] rounded-md bg-background px-da-xl py-da-lg text-foreground">
            <p className="da-body-strong">Ví dụ gần nhất</p>
            <p className="da-body-small">{example}</p>
          </div>
        )}
        <p className="text-muted-foreground da-caption">
          Thay đổi thứ tự ưu tiên được ghi kiểm toán và áp từ lần truy vấn tiếp theo sau khi được
          duyệt.
        </p>
      </div>
    </div>
  );
}

function isChanged(a: SourceAuthority, edit: Edit | undefined): boolean {
  if (!edit) return false;
  return (
    edit.ranking.join('|') !== a.ranking.map((s) => s.source_id).join('|') ||
    edit.materiality.trim() !== (a.materiality ?? '')
  );
}

function RankingEditor({
  authority,
  edit,
  onChange,
}: {
  authority: SourceAuthority;
  edit: Edit;
  onChange: (edit: Edit) => void;
}) {
  const move = (from: number, to: number) => {
    const ranking = [...edit.ranking];
    [ranking[from], ranking[to]] = [ranking[to], ranking[from]];
    onChange({ ...edit, ranking });
  };
  return (
    <div className="flex items-start gap-da-2xl">
      <ol
        className="flex min-w-0 flex-1 flex-col gap-da-xs"
        aria-label={`Thứ tự nguồn · ${authority.data_kind_label}`}
      >
        {edit.ranking.map((id, i) => {
          const source = authority.ranking.find((s) => s.source_id === id);
          if (!source) return null;
          return (
            <li key={id} className="flex items-center gap-da-md">
              <span className="w-5 text-muted-foreground da-mono">{i + 1}</span>
              <span className="min-w-0 flex-1 text-foreground da-body-small">
                {source.name}
                {source.manual && <span className="text-muted-foreground"> · tải tay</span>}
              </span>
              <Button
                size="sm"
                kind="ghost"
                aria-label={`Đưa ${source.name} lên`}
                disabled={i === 0}
                onClick={() => move(i, i - 1)}
              >
                ↑
              </Button>
              <Button
                size="sm"
                kind="ghost"
                aria-label={`Đưa ${source.name} xuống`}
                disabled={i === edit.ranking.length - 1}
                onClick={() => move(i, i + 1)}
              >
                ↓
              </Button>
            </li>
          );
        })}
      </ol>
      <label className="flex w-[240px] flex-col gap-da-xs">
        <span className="text-foreground da-label">Ngưỡng trọng yếu</span>
        <input
          value={edit.materiality}
          onChange={(e) => onChange({ ...edit, materiality: e.target.value })}
          placeholder="ví dụ 0,1% hoặc 1 tỷ"
          className="rounded-md border border-border-strong bg-card px-da-md py-da-sm text-foreground da-body-small"
        />
      </label>
    </div>
  );
}
