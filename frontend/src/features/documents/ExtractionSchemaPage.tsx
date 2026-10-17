import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { EmptyState } from '../../design-system/EmptyState';
import { Icon } from '../../design-system/Icon';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { formatNumber, formatPercent } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import {
  activateSchema,
  getSchema,
  listSchemas,
  testSchema,
  type ExtractionSchema,
  type SchemaField,
} from '../../lib/v1/documents';
import { useV1 } from '../../lib/v1/useV1';
import { HEAD_ROW, KeyValue, ROW, SidePanel } from './parts';

/*
 * "v3.1 · Phi cấu trúc · Lược đồ trích xuất" (2069:3224, UC-05-06): the fields a collection's
 * documents are read into, their accuracy on the sample and the column each lands in. Publishing
 * ("Xác nhận · publishSchema", 2094:8538) makes the result table a structured source agents query.
 * From the rail without an id it opens the first draft, else the first schema.
 */
/* Figma's rows: 12px side padding, 8px between cells, 6px top and bottom. */
const CELL = 'px-da-xs py-da-sm first:pl-da-lg last:pr-da-lg';

const ACCURACY_TONE: Record<SchemaField['accuracy_state'], 'success' | 'warning' | 'info'> = {
  ok: 'success',
  low: 'warning',
  new: 'info',
};

export default function ExtractionSchemaPage() {
  const { routeId } = useAppState();
  const schemas = useV1(routeId ? null : listSchemas, [routeId]);
  if (!routeId)
    return (
      <AsyncRegion
        state={schemas}
        what="lược đồ trích xuất"
        isEmpty={(p) => p.items.length === 0}
        empty={
          <EmptyState
            className="m-da-3xl"
            icon="table"
            title="Chưa có lược đồ trích xuất"
            description="Agent Tài liệu đề xuất lược đồ khi một bộ sưu tập có đủ 20 tệp mẫu."
          />
        }
      >
        {(p) => <SchemaLoader id={(p.items.find((s) => s.status === 'draft') ?? p.items[0]).id} />}
      </AsyncRegion>
    );
  return <SchemaLoader id={routeId} />;
}

function SchemaLoader({ id }: { id: string }) {
  const schema = useV1(() => getSchema(id), [id]);
  return (
    <AsyncRegion state={schema} what="lược đồ trích xuất" skeletonRows={8}>
      {(s) => <SchemaView key={s.id} schema={s} onChanged={schema.reload} />}
    </AsyncRegion>
  );
}

function SchemaView({ schema, onChanged }: { schema: ExtractionSchema; onChanged: () => void }) {
  const toast = useToast();
  const [busy, setBusy] = useState<'test' | 'publish' | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string>();
  const published = schema.status === 'active' || schema.status === 'superseded';
  const v = `v${schema.version}`;
  const fields = useTableView(schema.fields, {
    search: (f) => `${f.name} ${f.type_label} ${f.description} ${f.target_column}`,
    filters: [{ id: 'required', label: 'Bắt buộc', value: (f) => (f.required ? 'Có' : 'Không') }],
    sorts: [
      { id: 'name', value: (f) => f.name },
      { id: 'type', value: (f) => f.type_label },
      { id: 'desc', value: (f) => f.description },
      { id: 'required', value: (f) => (f.required ? 1 : 0) },
      { id: 'accuracy', value: (f) => f.accuracy },
      { id: 'target', value: (f) => f.target_column },
    ],
    pageSize: 50,
  });

  const runTest = async () => {
    setBusy('test');
    try {
      await testSchema(schema.id);
      toast({
        tone: 'success',
        message: `Agent Tài liệu đang chạy thử ${v} trên ${formatNumber(schema.test_size)} tệp.`,
      });
      onChanged();
    } catch (e) {
      toast({ tone: 'error', message: `Không chạy thử được. ${(e as Error).message}` });
    } finally {
      setBusy(null);
    }
  };

  const publish = async () => {
    setBusy('publish');
    setError(undefined);
    try {
      await activateSchema(schema.id);
      setConfirming(false);
      toast({ tone: 'success', message: `Đã xuất bản ${schema.collection.name} · ${v}.` });
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex min-h-full">
      <div className="flex min-w-0 flex-1 flex-col gap-da-lg px-da-2xl py-da-xl">
        <PageHeader
          breadcrumb={['Phi cấu trúc › Lược đồ trích xuất']}
          title={`${schema.collection.name} · ${v}`}
          description="Lược đồ biến tài liệu thành bảng có cấu trúc. Sau khi xuất bản, bảng kết quả xuất hiện như một nguồn có cấu trúc để agent truy vấn và đối chiếu."
          actions={
            published ? (
              <StatusDot tone="success">Đã xuất bản</StatusDot>
            ) : (
              <>
                <Button
                  icon="play"
                  loading={busy === 'test'}
                  disabled={busy === 'publish' || schema.status === 'testing'}
                  onClick={() => void runTest()}
                >
                  Chạy thử trên {formatNumber(schema.test_size)} tệp
                </Button>
                {schema.status === 'testing' ? (
                  <Tooltip content="Chờ lượt chạy thử xong rồi mới xuất bản.">
                    <Button kind="primary" disabled>
                      Xuất bản {v}
                    </Button>
                  </Tooltip>
                ) : (
                  <Button
                    kind="primary"
                    disabled={busy !== null}
                    onClick={() => setConfirming(true)}
                  >
                    Xuất bản {v}
                  </Button>
                )}
              </>
            )
          }
        />
        {schema.proposal_text && (
          <p className="flex items-center gap-da-md rounded-lg border border-border px-da-lg py-da-md text-foreground da-body-small">
            <Icon name="agent" className="shrink-0 text-info" />
            {schema.proposal_text}
          </p>
        )}
        {schema.status === 'testing' && (
          <StatusDot tone="info">
            Đang chạy thử trên {formatNumber(schema.test_size)} tệp mẫu
          </StatusDot>
        )}
        <TableToolbar view={fields} placeholder="Tìm trường…" searchLabel="Tìm trường" />
        <div className="overflow-x-auto rounded-lg border border-border bg-card">
          <table className="w-full min-w-[720px] table-fixed border-collapse">
            <thead>
              <tr className={HEAD_ROW}>
                <SortableTh view={fields} column="name" className={cn(CELL, 'w-[156px] font-medium')}>
                  Trường
                </SortableTh>
                <SortableTh view={fields} column="type" className={cn(CELL, 'w-[98px] font-medium')}>
                  Kiểu
                </SortableTh>
                <SortableTh view={fields} column="desc" className={cn(CELL, 'font-medium')}>
                  Mô tả
                </SortableTh>
                <SortableTh view={fields} column="required" className={cn(CELL, 'w-[78px] font-medium')}>
                  Bắt buộc
                </SortableTh>
                <SortableTh view={fields} column="accuracy" className={cn(CELL, 'w-[88px] font-medium')}>
                  Chính xác
                </SortableTh>
                <SortableTh view={fields} column="target" className={cn(CELL, 'w-[166px] font-medium')}>
                  Cột đích
                </SortableTh>
              </tr>
            </thead>
            <tbody>
              {fields.rows.map((f) => (
                <tr key={f.name} className={ROW}>
                  <td className={cn(CELL, 'truncate text-foreground da-mono')}>{f.name}</td>
                  <td className={cn(CELL, 'text-foreground da-body-small')}>{f.type_label}</td>
                  <td className={cn(CELL, 'text-foreground da-body-small')}>{f.description}</td>
                  <td className={cn(CELL, 'text-foreground da-body-small')}>
                    {f.required ? 'Có' : 'Không'}
                  </td>
                  <td className={CELL}>
                    {f.accuracy === null ? (
                      <span className="text-muted-foreground da-body-small">—</span>
                    ) : (
                      <StatusDot tone={ACCURACY_TONE[f.accuracy_state]}>
                        {formatPercent(f.accuracy, 0)}
                      </StatusDot>
                    )}
                  </td>
                  <td className={CELL}>
                    <StatusDot tone="primary" className="max-w-full truncate">
                      {f.target_column}
                    </StatusDot>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <TablePager view={fields} />
      </div>
      <SidePanel label="Kết quả có cấu trúc" width="w-[360px]">
        <h2 className="text-foreground da-heading-3">Kết quả có cấu trúc</h2>
        <div className="flex flex-col gap-da-xs rounded-md border border-border px-da-lg py-da-md">
          <span className="text-primary da-mono">{schema.output.table}</span>
          <span className="text-foreground da-caption">{schema.output.rows_text}</span>
        </div>
        <KeyValue label="Dùng bởi" labelWidth="w-[80px]">
          {schema.output.used_by}
        </KeyValue>
        {schema.output.sample_text && (
          <KeyValue label="Mẫu thử" labelWidth="w-[80px]">
            {schema.output.sample_text}
          </KeyValue>
        )}
        {schema.output.core_diff_text && (
          <KeyValue label="Lệch Core" labelWidth="w-[80px]">
            {schema.output.core_diff_text}
          </KeyValue>
        )}
      </SidePanel>
      <ConfirmDialog
        open={confirming}
        title={`Xuất bản lược đồ ${v}?`}
        confirmLabel="Xuất bản"
        tone="primary"
        loading={busy === 'publish'}
        error={error}
        onConfirm={() => void publish()}
        onCancel={() => setConfirming(false)}
      >
        Bảng {schema.output.table} sẽ được trích lại theo {v} và dùng như một nguồn có cấu trúc để
        agent truy vấn, đối chiếu. Phiên bản đang dùng chuyển sang bị thay thế.
      </ConfirmDialog>
    </div>
  );
}
