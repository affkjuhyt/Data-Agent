import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Avatar } from '../../design-system/Avatar';
import { EmptyState } from '../../design-system/EmptyState';
import { Field } from '../../design-system/Field';
import { Select } from '../../design-system/Select';
import { StatusDot } from '../../design-system/StatusDot';
import { cn } from '../../lib/utils';
import {
  listMembers,
  listRoles,
  listSpaceDatasets,
  simulatePermissions,
  type PermissionView,
} from '../../lib/v1/adminAccess';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "v3.1 · Cộng tác · Cùng bảng dưới hai vai trò" (2084:6905, UC-11-02/08), opened from
 * "Mô phỏng quyền" of a Data Space: one of its tables as two roles or people read it
 * (POST /v1/permissions:simulate). The api answers per column — seen, masked, hidden — and with
 * the row conditions of the active policies; it returns no rows, so none are drawn here: rows
 * filtered in the browser would not be the ones the query engine lets through.
 */
type Subject = `role:${string}` | `user:${string}`;

// AccessPolicy.mask_method (governance/policies.py).
const MASK: Record<string, string> = {
  hash: 'băm',
  partial: 'che một phần',
  last4: 'chỉ 4 số cuối',
  null: 'để trống',
};

export default function PermissionComparePage() {
  const { routeId } = useAppState();
  const space = routeId ?? null;
  const datasets = useV1(
    ['listSpaceDatasets', space],
    space ? () => listSpaceDatasets(space) : null,
  );
  const roles = useV1(['listRoles'], listRoles);
  const members = useV1(['listMembers', space], space ? () => listMembers(space) : null);
  const [dataset, setDataset] = useState<string | null>(null);
  const [left, setLeft] = useState<Subject | null>(null);
  const [right, setRight] = useState<Subject | null>(null);

  const subjects = [
    ...(roles.data?.items ?? []).map((r) => ({
      value: `role:${r.id}` as Subject,
      label: `Vai trò · ${r.name}`,
    })),
    ...(members.data?.items ?? [])
      .filter((m) => m.principal_type === 'user')
      .map((m) => ({
        value: `user:${m.principal_id}` as Subject,
        label: `Người · ${m.display_name}`,
      })),
  ];
  const nameOf = (kind: string, id: string) =>
    subjects.find((s) => s.value === `${kind}:${id}`)?.label ?? id;
  const chosen = [left, right].filter((s): s is Subject => s !== null);
  const sim = useV1(
    ['simulatePermissions', dataset, ...chosen],
    dataset && chosen.length > 0
      ? () =>
          simulatePermissions({
            dataset_id: dataset,
            role_ids: chosen.filter((s) => s.startsWith('role:')).map((s) => s.slice(5)),
            user_ids: chosen.filter((s) => s.startsWith('user:')).map((s) => s.slice(5)),
          })
      : null,
  );
  const tableName = datasets.data?.items.find((d) => d.id === dataset)?.display_name ?? 'một bảng';

  return (
    <div className="flex flex-col gap-da-md px-da-2xl py-da-xl">
      <PageHeader
        breadcrumb={['Quản trị', 'Data Space', 'Mô phỏng quyền']}
        title={dataset ? `Cùng bảng ${tableName} · hai vai trò` : 'Cùng bảng · hai vai trò'}
        description="Cột nào được thấy, bị che hay bị ẩn và dòng nào được giữ lại, theo các chính sách truy cập đang hiệu lực của bảng."
      />
      <div className="grid grid-cols-3 gap-da-md max-[1023px]:grid-cols-1">
        <Field label="Bảng">
          <Select
            label="Bảng"
            placeholder={datasets.loading ? 'Đang tải bảng…' : 'Chọn bảng'}
            options={(datasets.data?.items ?? []).map((d) => ({
              value: d.id,
              label: d.display_name ?? d.table_name,
            }))}
            value={dataset}
            onChange={setDataset}
          />
        </Field>
        <Field label="Bên trái">
          <Select<Subject>
            label="Bên trái"
            placeholder="Chọn vai trò hoặc người"
            options={subjects.filter((s) => s.value !== right)}
            value={left}
            onChange={setLeft}
          />
        </Field>
        <Field label="Bên phải">
          <Select<Subject>
            label="Bên phải"
            placeholder="Chọn vai trò hoặc người"
            options={subjects.filter((s) => s.value !== left)}
            value={right}
            onChange={setRight}
          />
        </Field>
      </div>
      {!dataset || chosen.length === 0 ? (
        <EmptyState
          icon="search"
          title="Chọn một bảng và tối đa hai vai trò hoặc người"
          description="Kết quả lấy từ chính sách truy cập đang hiệu lực của bảng, như khi truy vấn chạy thật."
        />
      ) : (
        <AsyncRegion state={sim} what="mô phỏng quyền" skeletonRows={4}>
          {(s) => (
            <div className="grid grid-cols-2 gap-da-md max-[1023px]:grid-cols-1">
              {s.views.map((view) => (
                <ViewCard
                  key={`${view.subject_kind}:${view.subject_id}`}
                  view={view}
                  name={nameOf(view.subject_kind, view.subject_id)}
                />
              ))}
            </div>
          )}
        </AsyncRegion>
      )}
    </div>
  );
}

function ViewCard({ view, name }: { view: PermissionView; name: string }) {
  const columns = [
    ...view.visible_columns.map((c) => ({
      name: c,
      state:
        c in view.masked_columns
          ? `Che · ${MASK[view.masked_columns[c]] ?? view.masked_columns[c]}`
          : 'Thấy',
      muted: c in view.masked_columns,
    })),
    ...view.hidden_columns.map((c) => ({ name: c, state: 'Ẩn', muted: true })),
  ];
  const limited = view.row_conditions.length > 0;
  return (
    <article
      aria-label={name}
      className="flex min-w-0 flex-col gap-da-md rounded-lg border border-border bg-card p-da-lg"
    >
      <div className="flex items-center gap-da-sm">
        <Avatar name={name.replace(/^(Vai trò|Người) · /, '')} />
        <h2 className="flex-1 text-foreground da-body-strong">{name}</h2>
      </div>
      <StatusDot tone={limited ? 'warning' : 'primary'}>
        {limited ? `Chỉ các dòng thoả ${view.row_conditions.join(' và ')}` : 'Mọi dòng'}
        {view.policy_ids.length > 0 && ` · ${view.policy_ids.length} chính sách áp dụng`}
      </StatusDot>
      <table className="w-full table-fixed border-collapse border-b border-border">
        <thead>
          <tr className="bg-subtle text-left text-muted-foreground da-caption">
            <th className="px-da-md py-da-sm font-normal">Cột</th>
            <th className="w-50 px-da-md py-da-sm font-normal">Thấy như thế nào</th>
          </tr>
        </thead>
        <tbody>
          {columns.map((c) => (
            <tr key={c.name} className="border-t border-border">
              <td className="truncate px-da-md py-da-sm text-foreground da-mono">{c.name}</td>
              <td
                className={cn(
                  'px-da-md py-da-sm da-body-small',
                  c.muted ? 'text-muted-foreground' : 'text-foreground',
                )}
              >
                {c.state}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </article>
  );
}
