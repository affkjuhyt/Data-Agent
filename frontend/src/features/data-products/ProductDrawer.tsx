import { useState } from 'react';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Badge } from '../../design-system/Badge';
import { Button } from '../../design-system/Button';
import { Icon } from '../../design-system/Icon';
import { StatusDot } from '../../design-system/StatusDot';
import { Tabs } from '../../design-system/Tabs';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { useToast } from '../../design-system/Toast';
import { formatTime } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import {
  draftNextVersion,
  getDataProduct,
  type CheckState,
  type DataProductDetail,
  type VersionState,
} from '../../lib/v1/dataProducts';
import { useV1 } from '../../lib/v1/useV1';
import { versionLabel } from './format';

/*
 * "Drawer · Chi tiết" of the product catalog, 420px on the right, with its four tabs: Contract
 * (columns, the last checks, the agent's reading of a pending change), Chất lượng (2101:9809),
 * Người dùng (2101:10164) and Phiên bản (2101:10529). "Xem ảnh hưởng" opens Người dùng, which
 * lists who the next version would affect.
 */
type Tab = 'contract' | 'quality' | 'consumers' | 'versions';

const CHECK: Record<CheckState, { tone: 'success' | 'warning' | 'error'; label: string }> = {
  pass: { tone: 'success', label: 'Đạt' },
  warn: { tone: 'warning', label: 'Cảnh báo' },
  fail: { tone: 'error', label: 'Vi phạm' },
};

export function ProductDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const detail = useV1(['getDataProduct', id], () => getDataProduct(id));
  const [tab, setTab] = useState<Tab>('contract');
  const p = detail.data;

  return (
    <aside
      aria-label="Chi tiết sản phẩm dữ liệu"
      className="flex w-[420px] shrink-0 flex-col gap-da-lg border-l border-border bg-card p-da-xl"
    >
      <div className="flex items-center gap-da-md">
        <div className="flex min-w-0 flex-1 flex-col">
          <h2 className="text-foreground da-heading-2">{p?.name ?? ' '}</h2>
          {p && (
            <p className="text-muted-foreground da-mono">
              {versionLabel(p)}
              {p.next_version ? ' (nháp)' : ''}
            </p>
          )}
        </div>
        <button
          type="button"
          aria-label="Đóng"
          onClick={onClose}
          className="rounded-sm text-foreground hover:bg-subtle"
        >
          <Icon name="x" />
        </button>
      </div>
      <Tabs<Tab>
        label="Chi tiết sản phẩm"
        value={tab}
        onChange={setTab}
        items={[
          { value: 'contract', label: 'Contract' },
          { value: 'quality', label: 'Chất lượng' },
          { value: 'consumers', label: 'Người dùng' },
          { value: 'versions', label: 'Phiên bản' },
        ]}
      />
      <AsyncRegion state={detail} what="sản phẩm dữ liệu" skeletonRows={5}>
        {(d) => (
          <div role="tabpanel" className="flex flex-col gap-da-lg">
            {tab === 'contract' && (
              <ContractTab
                product={d}
                onImpact={() => setTab('consumers')}
                onChanged={detail.reload}
              />
            )}
            {tab === 'quality' && <QualityTab product={d} />}
            {tab === 'consumers' && <ConsumersTab product={d} />}
            {tab === 'versions' && <VersionsTab product={d} />}
          </div>
        )}
      </AsyncRegion>
    </aside>
  );
}

function ContractTab({
  product,
  onImpact,
  onChanged,
}: {
  product: DataProductDetail;
  onImpact: () => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const [drafting, setDrafting] = useState(false);
  const contract = useTableView(product.contract, {
    search: (row) => `${row.name} ${row.type} ${row.description}`,
    filters: [{ id: 'type', label: 'Kiểu', value: (row) => row.type }],
    sorts: [
      { id: 'name', value: (row) => row.name },
      { id: 'type', value: (row) => row.type },
      { id: 'description', value: (row) => row.description },
    ],
    pageSize: 50,
  });

  const draft = async () => {
    setDrafting(true);
    try {
      await draftNextVersion(product.id);
      // Figma Toast · draft (2094:8183). The new draft is the product's next version, shown in its Phiên bản tab.
      toast({ tone: 'success', message: 'Đã tạo bản nháp · mở để chỉnh sửa' });
      onChanged();
    } catch (e) {
      toast({ tone: 'error', message: `Không tạo được bản nháp: ${(e as Error).message}` });
    } finally {
      setDrafting(false);
    }
  };

  return (
    <>
      {product.contract.length > 0 ? (
        <>
        <TableToolbar view={contract} placeholder="Tìm trường…" searchLabel="Tìm trường" />
        <table className="w-full table-fixed border-separate border-spacing-0 overflow-hidden rounded-lg border border-border da-caption">
          <thead>
            <tr className="bg-subtle text-left text-muted-foreground da-label">
              <SortableTh view={contract} column="name" className="w-[118px] px-da-md py-da-sm font-medium">
                Trường
              </SortableTh>
              <SortableTh view={contract} column="type" className="w-[100px] px-da-md py-da-sm font-medium">
                Kiểu
              </SortableTh>
              <SortableTh view={contract} column="description" className="px-da-md py-da-sm font-medium">
                Mô tả
              </SortableTh>
            </tr>
          </thead>
          <tbody>
            {contract.rows.map((c) => (
              <tr key={c.name}>
                <td
                  className={cn(
                    'break-words border-t border-border px-da-md py-da-sm da-mono',
                    c.changed ? 'text-warning' : 'text-foreground',
                  )}
                >
                  {c.name}
                </td>
                <td className="break-words border-t border-border px-da-md py-da-sm text-muted-foreground da-mono">
                  {c.type}
                </td>
                <td className="border-t border-border px-da-md py-da-sm text-foreground">
                  {c.description}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <TablePager view={contract} />
        </>
      ) : (
        <p className="text-muted-foreground da-body-small">Chưa có contract.</p>
      )}
      {product.checks.length > 0 && (
        <div className="flex flex-col gap-da-sm">
          <p className="text-muted-foreground da-label">
            Kiểm tra chất lượng · lần chạy {formatTime(product.checked_at)}
          </p>
          <ul className="flex flex-col gap-da-sm">
            {product.checks.map((c) => (
              <li
                key={c.text}
                className="flex items-center gap-da-sm text-foreground da-body-small"
              >
                <Icon
                  name={c.state === 'pass' ? 'check' : 'alert'}
                  label={CHECK[c.state].label}
                  className={
                    c.state === 'pass'
                      ? 'text-success'
                      : c.state === 'warn'
                        ? 'text-warning'
                        : 'text-error'
                  }
                />
                {c.text}
              </li>
            ))}
          </ul>
        </div>
      )}
      {product.agent_note && (
        <div className="flex flex-col gap-da-md rounded-lg border border-border p-da-lg">
          <p className="flex items-center gap-da-sm text-primary da-label">
            <Icon name="agent" />
            Data Agent
          </p>
          <p className="text-foreground da-body-small">{product.agent_note.text}</p>
          <div className="flex gap-da-sm">
            <Button kind="primary" loading={drafting} onClick={draft}>
              {product.agent_note.action_label}
            </Button>
            <Button kind="ghost" icon="compare" onClick={onImpact}>
              Xem ảnh hưởng
            </Button>
          </div>
        </div>
      )}
    </>
  );
}

function QualityTab({ product }: { product: DataProductDetail }) {
  if (product.checks.length === 0)
    return <p className="text-muted-foreground da-body-small">Chưa có lần kiểm tra nào.</p>;
  return (
    <div className="flex flex-col gap-da-lg">
      <p className="text-muted-foreground da-label">
        Kiểm tra mỗi lần làm mới · {formatTime(product.checked_at)}
      </p>
      {product.checks.map((c) => (
        <div key={c.text} className="flex items-center gap-da-md">
          <StatusDot tone={CHECK[c.state].tone}>{CHECK[c.state].label}</StatusDot>
          <span className="min-w-0 flex-1 text-foreground da-body-small">{c.text}</span>
        </div>
      ))}
    </div>
  );
}

function ConsumersTab({ product }: { product: DataProductDetail }) {
  const view = useTableView(product.consumers, {
    search: (row) => `${row.data_space_name} ${row.used_for} ${row.version}`,
    filters: [{ id: 'use', label: 'Dùng cho', value: (row) => row.used_for }],
    sorts: [
      { id: 'space', value: (row) => row.data_space_name },
      { id: 'use', value: (row) => row.used_for },
      { id: 'version', value: (row) => row.version },
    ],
    pageSize: 50,
  });
  return (
    <div className="flex flex-col gap-da-lg">
      {product.consumers.length > 0 && (
        <>
        <TableToolbar view={view} placeholder="Tìm Data Space…" searchLabel="Tìm Data Space" />
        <table className="w-full table-fixed border-separate border-spacing-0 overflow-hidden rounded-md border border-border bg-card">
          <thead>
            <tr className="bg-subtle text-left text-muted-foreground da-label">
              <SortableTh view={view} column="space" className="px-da-md py-da-sm font-medium">
                Data Space
              </SortableTh>
              <SortableTh view={view} column="use" className="w-[140px] px-da-md py-da-sm font-medium">
                Dùng cho
              </SortableTh>
              <SortableTh view={view} column="version" className="w-[82px] px-da-md py-da-sm font-medium">
                Phiên bản
              </SortableTh>
            </tr>
          </thead>
          <tbody>
            {view.rows.map((c) => (
              <tr key={c.data_space_name}>
                <td className="border-t border-border px-da-md py-da-sm text-foreground da-body-strong">
                  {c.data_space_name}
                </td>
                <td className="border-t border-border px-da-md py-da-sm text-foreground da-body-small">
                  {c.used_for}
                </td>
                <td className="border-t border-border px-da-md py-da-sm text-foreground da-mono">
                  {c.version}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <TablePager view={view} />
        </>
      )}
      <p className="text-muted-foreground da-caption">
        {product.consumer_spaces_total} Data Space
        {product.next_version &&
          ` · ${product.affected_by_next} sẽ bị ảnh hưởng nếu phát hành v${product.next_version}`}
      </p>
    </div>
  );
}

const VERSION: Record<VersionState, { dot?: 'warning' | 'success'; label: string }> = {
  draft: { dot: 'warning', label: 'Nháp' },
  current: { dot: 'success', label: 'Đang dùng' },
  supported: { label: 'Còn hỗ trợ' },
  retired: { label: 'Ngừng' },
};

function VersionsTab({ product }: { product: DataProductDetail }) {
  const view = useTableView(product.versions, {
    search: (row) => `${row.version} ${row.change} ${VERSION[row.state].label}`,
    filters: [{ id: 'state', label: 'Trạng thái', value: (row) => VERSION[row.state].label }],
    sorts: [
      { id: 'version', value: (row) => row.version },
      { id: 'change', value: (row) => row.change },
      { id: 'state', value: (row) => VERSION[row.state].label },
    ],
    pageSize: 50,
  });
  return (
    <div className="flex flex-col gap-da-md">
    <TableToolbar view={view} placeholder="Tìm phiên bản…" searchLabel="Tìm phiên bản" />
    <table className="w-full table-fixed border-separate border-spacing-0 overflow-hidden rounded-md border border-border bg-card">
      <thead>
        <tr className="bg-subtle text-left text-muted-foreground da-label">
          <SortableTh view={view} column="version" className="w-[92px] px-da-md py-da-sm font-medium">
            Phiên bản
          </SortableTh>
          <SortableTh view={view} column="change" className="px-da-md py-da-sm font-medium">
            Thay đổi
          </SortableTh>
          <SortableTh view={view} column="state" className="w-[122px] px-da-md py-da-sm font-medium">
            Trạng thái
          </SortableTh>
        </tr>
      </thead>
      <tbody>
        {view.rows.map((v) => {
          const state = VERSION[v.state];
          return (
            <tr key={v.version}>
              <td className="border-t border-border px-da-md py-da-sm text-foreground da-mono">
                {v.version}
              </td>
              <td className="border-t border-border px-da-md py-da-sm text-foreground da-body-small">
                {v.change}
              </td>
              <td className="border-t border-border px-da-md py-da-sm">
                {state.dot ? (
                  <StatusDot tone={state.dot}>{state.label}</StatusDot>
                ) : (
                  <Badge>{state.label}</Badge>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
    <TablePager view={view} />
    </div>
  );
}
