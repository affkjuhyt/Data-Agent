import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Checkbox } from '../../design-system/Checkbox';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { EmptyState } from '../../design-system/EmptyState';
import { Notice } from '../../design-system/Notice';
import { Select } from '../../design-system/Select';
import { StatusDot } from '../../design-system/StatusDot';
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
 * person — a code table to merge, a required attribute with no column. A required attribute must
 * be mapped or marked not applicable. Ticked rows are approved together; "Đối chiếu số" checks
 * the mapped figures against what was reported. Opened from the rail, the install in progress.
 */
const fmt = (n: number) => n.toFixed(2).replace('.', ',');

export default function PackOnboardPage() {
  const { routeId } = useAppState();
  const list = useV1(routeId ? null : listPackInstalls, [routeId]);
  const id = routeId || list.data?.items[0]?.id;
  const install = useV1(id ? () => getPackInstall(id) : null, [id]);
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
  const mappings = useV1(entity ? () => getEntityMappings(install.id, entity) : null, [
    install.id,
    entity,
  ]);
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
  const [codeTable, setCodeTable] =
    useState<EntityMappings['decisions'][number]['code_mapping']>(null);
  const [error, setError] = useState<string>();
  const [busy, setBusy] = useState(false);

  const save = async (extra?: { attr: string; rows: { from: string; to: string }[] }) => {
    const rows = mappings.rows.filter((r) => picked.has(r.attr) || r.attr === extra?.attr);
    const missing = rows.filter((r) => !columns[r.attr]);
    if (missing.length) {
      setError(`Chọn cột hoặc “Không áp dụng” cho ${missing.map((r) => r.attr).join(', ')}.`);
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      const done = await saveMappings(installId, {
        entity_mappings: rows.map((r) => ({
          entity: mappings.entity,
          attr: r.attr,
          column: columns[r.attr] === NOT_APPLICABLE ? null : columns[r.attr],
        })),
        ...(extra ? { code_mappings: [extra] } : {}),
      });
      toast({ tone: 'success', message: `Đã duyệt ${done.approved} ánh xạ của ${mappings.name}` });
      setCodeTable(null);
      onSaved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

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
            <Button
              kind="primary"
              loading={busy && !codeTable}
              disabled={busy}
              onClick={() => save()}
            >
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
            <table className="w-full table-fixed border-collapse da-body-small">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground da-caption">
                  <th className="w-[40px]">
                    <span className="sr-only">Chọn</span>
                  </th>
                  <th className="py-da-sm font-normal">Thuộc tính chuẩn</th>
                  <th className="w-[182px] py-da-sm font-normal">Cột của tổ chức</th>
                  <th className="w-[62px] px-da-md py-da-sm font-normal">Độ tin</th>
                  <th className="w-[170px] py-da-sm font-normal">Ghi chú</th>
                </tr>
              </thead>
              <tbody>
                {mappings.rows.map((r) => {
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
                            setPicked(next);
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
                          onChange={(v) => setColumns({ ...columns, [r.attr]: v })}
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
          )}
        </div>
        <aside className="flex w-[320px] shrink-0 flex-col gap-da-md border-l border-border bg-background px-da-xl pt-da-xl max-[1023px]:w-full max-[1023px]:border-l-0">
          <h2 className="text-foreground da-heading-3">Cần bạn quyết định</h2>
          {mappings.decisions.length === 0 && (
            <p className="text-muted-foreground da-body-small">Không có gì chờ ở thực thể này.</p>
          )}
          {mappings.decisions.map((d) => (
            <div key={d.text} className="flex flex-col gap-da-sm">
              <p className="text-foreground da-body-small">{d.text}</p>
              {d.code_mapping && (
                <div>
                  <Button onClick={() => setCodeTable(d.code_mapping)}>Xem bảng gộp</Button>
                </div>
              )}
            </div>
          ))}
          <p className="text-muted-foreground da-caption">
            Thuộc tính bắt buộc phải có ánh xạ hoặc được đánh dấu không áp dụng.
          </p>
        </aside>
        <ConfirmDialog
          open={codeTable !== null}
          title={`Bảng gộp ${codeTable?.attr ?? ''}`}
          confirmLabel="Dùng bảng gộp này"
          cancelLabel="Quay lại"
          loading={busy}
          error={codeTable ? error : undefined}
          onCancel={() => setCodeTable(null)}
          onConfirm={() => codeTable && save(codeTable)}
        >
          <table className="w-full border-collapse da-body-small">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground da-caption">
                <th className="py-da-xs font-normal">Nhóm của tổ chức</th>
                <th className="py-da-xs font-normal">Nhóm của gói</th>
              </tr>
            </thead>
            <tbody>
              {codeTable?.rows.map((r) => (
                <tr key={r.from} className="border-b border-border">
                  <td className="py-da-xs">{r.from}</td>
                  <td className="py-da-xs da-mono">{r.to}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </ConfirmDialog>
      </div>
    </div>
  );
}
