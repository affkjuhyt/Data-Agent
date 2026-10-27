import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Checkbox } from '../../design-system/Checkbox';
import { EmptyState } from '../../design-system/EmptyState';
import { Notice } from '../../design-system/Notice';
import { Select } from '../../design-system/Select';
import { StatusDot } from '../../design-system/StatusDot';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { useToast } from '../../design-system/Toast';
import { cn } from '../../lib/utils';
import {
  getEntityMappings,
  getPackInstall,
  listPackInstalls,
  NOT_APPLICABLE,
  saveMappings,
  type EntityMappings,
  type PackInstall,
} from '../../lib/v1/packs';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P09 · Đưa vào ngành · Ánh xạ" (2284:17560, UC-09-02): an installed pack's canonical entities
 * on the left, how far each is mapped; in the middle the attributes of one entity against the
 * organisation's columns, with the agent's confidence and note; on the right what waits on a
 * person — a required attribute with no column. A required attribute must be mapped or marked not
 * applicable. Ticked rows are approved together; "Đối chiếu số" checks the mapped figures against
 * what was reported. Opened from the rail, the install in progress. The api gives the current
 * column of each attribute, not the agent's alternatives or the code tables it would merge.
 */
const fmt = (n: number) => n.toFixed(2).replace('.', ',');

export default function PackOnboardPage() {
  const { routeId } = useAppState();
  const list = useV1(['listPackInstalls', routeId], routeId ? null : listPackInstalls);
  const id = routeId || list.data?.items[0]?.id;
  const install = useV1(['getPackInstall', id], id ? () => getPackInstall(id) : null);
  if (!routeId && list.data && list.data.items.length === 0)
    return (
      <div className="px-da-3xl pt-da-2xl">
        <EmptyState
          title="Không có gói đang đưa vào ngành"
          description="Cài một gói ở Gói ngành để bắt đầu."
        />
      </div>
    );
  return (
    <AsyncRegion state={install} what="lượt đưa vào ngành">
      {(i) => <OnboardBody install={i} onChanged={install.reload} />}
    </AsyncRegion>
  );
}

function OnboardBody({ install, onChanged }: { install: PackInstall; onChanged: () => void }) {
  const first = install.entities.find((e) => e.mapped < e.total) ?? install.entities[0];
  const [entity, setEntity] = useState(first?.key ?? '');
  const mappings = useV1(
    ['getEntityMappings', install.id, entity],
    entity ? () => getEntityMappings(install, entity) : null,
  );
  return (
    <AsyncRegion state={mappings} what="ánh xạ">
      {(m) => (
        <MappingBody
          key={m.entity}
          install={install}
          mappings={m}
          onEntity={setEntity}
          onSaved={() => {
            mappings.reload();
            onChanged();
          }}
        />
      )}
    </AsyncRegion>
  );
}

function MappingBody({
  install,
  mappings,
  onEntity,
  onSaved,
}: {
  install: PackInstall;
  mappings: EntityMappings;
  onEntity: (key: string) => void;
  onSaved: () => void;
}) {
  const { navigate } = useAppState();
  const toast = useToast();
  const installId = install.id;
  const [columns, setColumns] = useState<Record<string, string | null>>(
    Object.fromEntries(mappings.rows.map((r) => [r.attr, r.column])),
  );
  const [picked, setPicked] = useState<Set<string>>(
    new Set(mappings.rows.filter((r) => r.approved || !r.unsure).map((r) => r.attr)),
  );
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  const save = async () => {
    const rows = mappings.rows.filter((r) => picked.has(r.attr));
    const missing = rows.filter((r) => !columns[r.attr]);
    if (missing.length) {
      setError(`Chọn cột hoặc “Không áp dụng” cho ${missing.map((r) => r.attr).join(', ')}.`);
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      const done = await saveMappings(
        installId,
        mappings.entity,
        rows.flatMap((r) =>
          columns[r.attr] === NOT_APPLICABLE || !r.dataset_id
            ? []
            : [
                {
                  attribute_id: r.attribute_id,
                  dataset_id: r.dataset_id,
                  column_expr: columns[r.attr] as string,
                  confidence: r.confidence,
                },
              ],
        ),
      );
      toast({ tone: 'success', message: `Đã duyệt ${done.approved} ánh xạ của ${mappings.name}` });
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const waiting = mappings.rows.filter((r) => r.required && !columns[r.attr]);

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Gói ngành', install.title]}
        title={`Đưa vào ngành · ${install.title}`}
        description={install.summary_text}
        actions={
          <>
            <Button onClick={() => navigate('data', 'PACK_CALIBRATE', install.id)}>
              Đối chiếu số
            </Button>
            <Button kind="primary" loading={busy} disabled={busy} onClick={() => save()}>
              Duyệt ánh xạ đã chọn
            </Button>
          </>
        }
      />
      <div className="flex min-h-0 flex-1 max-[1023px]:flex-col">
        <nav
          aria-label="Thực thể của gói"
          className="flex w-[260px] shrink-0 flex-col border-r border-border pt-da-md max-[1023px]:w-full"
        >
          {install.entities.map((e) => (
            <button
              key={e.key}
              type="button"
              aria-current={e.key === mappings.entity ? 'true' : undefined}
              onClick={() => onEntity(e.key)}
              className={cn(
                'flex items-center gap-da-sm px-da-xl py-[8px] text-left da-body-small',
                e.key === mappings.entity ? 'bg-subtle' : 'hover:bg-subtle',
              )}
            >
              <StatusDot tone={e.mapped === e.total ? 'success' : 'warning'}>
                <span className="text-foreground">{e.name}</span>
              </StatusDot>
              <span className="ml-auto text-muted-foreground da-mono">
                {e.mapped}/{e.total}
              </span>
            </button>
          ))}
        </nav>
        <div className="flex min-w-0 flex-1 flex-col gap-da-md px-da-2xl pb-da-2xl pt-da-xl">
          <div className="flex items-baseline gap-da-md">
            <h2 className="text-foreground da-heading-2">{mappings.name}</h2>
            <span className="ml-auto text-muted-foreground da-mono">
              Bảng chính: {mappings.main_table}
            </span>
          </div>
          {error && <Notice tone="error">{error}</Notice>}
          {mappings.rows.length === 0 ? (
            <p className="text-muted-foreground da-body-small">
              Agent Chuẩn bị dữ liệu chưa đề xuất ánh xạ cho thực thể này.
            </p>
          ) : (
            <MappingTable
              rows={mappings.rows}
              columns={columns}
              picked={picked}
              onPicked={setPicked}
              onColumn={(attr, value) => setColumns({ ...columns, [attr]: value })}
            />
          )}
        </div>
        <aside className="flex w-[320px] shrink-0 flex-col gap-da-md border-l border-border bg-background px-da-xl pt-da-xl max-[1023px]:w-full max-[1023px]:border-l-0">
          <h2 className="text-foreground da-heading-3">Cần bạn quyết định</h2>
          {waiting.length === 0 && (
            <p className="text-muted-foreground da-body-small">Không có gì chờ ở thực thể này.</p>
          )}
          {waiting.map((r) => (
            <p key={r.attr} className="text-foreground da-body-small">
              {r.attr} là thuộc tính bắt buộc nhưng chưa có cột. Chọn cột hoặc đánh dấu “Không áp
              dụng”.
            </p>
          ))}
          <p className="text-muted-foreground da-caption">
            Thuộc tính bắt buộc phải có ánh xạ hoặc được đánh dấu không áp dụng.
          </p>
        </aside>
      </div>
    </div>
  );
}

function MappingTable({
  rows,
  columns,
  picked,
  onPicked,
  onColumn,
}: {
  rows: EntityMappings['rows'];
  columns: Record<string, string | null>;
  picked: Set<string>;
  onPicked: (next: Set<string>) => void;
  onColumn: (attr: string, value: string) => void;
}) {
  const view = useTableView(rows, {
    search: (r) => `${r.attr} ${columns[r.attr] ?? ''} ${r.note}`,
    filters: [
      { id: 'required', label: 'Bắt buộc', value: (r) => (r.required ? 'Bắt buộc' : 'Tuỳ chọn') },
    ],
    sorts: [
      { id: 'attr', value: (r) => r.attr },
      { id: 'column', value: (r) => columns[r.attr] },
      { id: 'confidence', value: (r) => r.confidence },
      { id: 'note', value: (r) => r.note },
    ],
    pageSize: 50,
  });
  return (
    <>
      <TableToolbar view={view} placeholder="Tìm thuộc tính…" searchLabel="Tìm thuộc tính" />
      <table className="w-full table-fixed border-collapse da-body-small">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground da-caption">
            <SortableTh view={view} className="w-[40px]">
              <span className="sr-only">Chọn</span>
            </SortableTh>
            <SortableTh view={view} column="attr" className="py-da-sm font-normal">
              Thuộc tính chuẩn
            </SortableTh>
            <SortableTh view={view} column="column" className="w-[182px] py-da-sm font-normal">
              Cột của tổ chức
            </SortableTh>
            <SortableTh
              view={view}
              column="confidence"
              className="w-[62px] px-da-md py-da-sm font-normal"
            >
              Độ tin
            </SortableTh>
            <SortableTh view={view} column="note" className="w-[170px] py-da-sm font-normal">
              Ghi chú
            </SortableTh>
          </tr>
        </thead>
        <tbody>
          {view.rows.map((r) => {
            const column = columns[r.attr];
            return (
              <tr key={r.attr} className="border-b border-border text-foreground">
                <td className="px-da-sm py-[10px]">
                  <Checkbox
                    checked={picked.has(r.attr)}
                    onChange={(on) => {
                      const next = new Set(picked);
                      if (on) next.add(r.attr);
                      else next.delete(r.attr);
                      onPicked(next);
                    }}
                  >
                    <span className="sr-only">Chọn {r.attr}</span>
                  </Checkbox>
                </td>
                <td className="py-[10px]">
                  {r.attr}
                  {r.required && ' · bắt buộc'}
                </td>
                <td className="py-[6px] pr-da-sm">
                  <Select
                    label={`Cột cho ${r.attr}`}
                    value={column}
                    placeholder="—"
                    error={r.required && !column ? 'Bắt buộc' : undefined}
                    onChange={(v) => onColumn(r.attr, v)}
                    options={[
                      ...r.options.map((o) => ({ value: o, label: o })),
                      { value: NOT_APPLICABLE, label: 'Không áp dụng' },
                    ]}
                  />
                </td>
                <td
                  className={cn(
                    'px-da-md py-[10px] da-mono',
                    r.confidence !== null && r.confidence < 0.8 ? 'text-warning' : '',
                  )}
                >
                  {r.confidence === null ? '—' : fmt(r.confidence)}
                </td>
                <td
                  className={cn(
                    'py-[10px] da-caption',
                    !r.column && !column
                      ? 'text-error'
                      : r.unsure
                        ? 'text-warning'
                        : 'text-muted-foreground',
                  )}
                >
                  {r.note}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <TablePager view={view} />
    </>
  );
}
