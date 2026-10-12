import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { EmptyState } from '../../design-system/EmptyState';
import { Menu } from '../../design-system/Menu';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { disablePolicy, listAccessPolicies, type AccessPolicy } from '../../lib/v1/adminAccess';
import { useV1 } from '../../lib/v1/useV1';
import PolicyDetail from './PolicyDetail';
import { POLICY_KIND, POLICY_STATUS } from './policyLabels';

/*
 * Rail item "Chính sách dữ liệu" (UC-11-02). With an id it is "P11 · Chính sách dữ liệu"
 * (2305:19617, PolicyDetail). Without one it lists the policies in the P11 table idiom (Figma
 * has no list frame); an active policy can be turned off through "Xác nhận · policyOff".
 */
export default function PoliciesPage() {
  const { routeId } = useAppState();
  return routeId ? <PolicyDetail id={routeId} /> : <PolicyList />;
}

function PolicyList() {
  const { navigate } = useAppState();
  const toast = useToast();
  const policies = useV1(listAccessPolicies, []);
  const [turningOff, setTurningOff] = useState<AccessPolicy | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();

  const items = policies.data?.items ?? [];
  const active = items.filter((p) => p.status === 'active').length;

  const turnOff = async (p: AccessPolicy) => {
    setSending(true);
    setError(undefined);
    try {
      await disablePolicy(p.id);
      toast({ tone: 'success', message: `Đã tắt ${p.code}` });
      setTurningOff(null);
      policies.reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="flex flex-col">
      <PageHeader
        bordered
        breadcrumb={['Quản trị', 'Chính sách dữ liệu']}
        title="Chính sách dữ liệu"
        description={
          policies.data
            ? `${items.length} chính sách · ${active} đang bật · áp ở tầng truy vấn cho người, agent và mọi kênh`
            : undefined
        }
      />
      <div className="px-da-3xl pt-da-xl">
        <AsyncRegion
          state={policies}
          what="chính sách"
          skeletonRows={5}
          isEmpty={(p) => p.items.length === 0}
          empty={
            <EmptyState
              icon="shield"
              title="Chưa có chính sách dữ liệu"
              description="Chính sách giới hạn dòng, ẩn cột hoặc che giá trị theo vai trò và thuộc tính của người hỏi."
            />
          }
        >
          {() => (
            <table className="w-full table-fixed border-collapse">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground da-caption">
                  <th className="px-da-lg py-da-md font-normal">Chính sách</th>
                  <th className="w-[200px] px-da-lg py-da-md font-normal">Data Space</th>
                  <th className="w-[180px] px-da-lg py-da-md font-normal">Loại</th>
                  <th className="w-[120px] px-da-lg py-da-md font-normal">Trạng thái</th>
                  <th className="w-[40px] px-da-lg py-da-md">
                    <span className="sr-only">Thao tác</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {items.map((p) => (
                  <tr key={p.id} className="border-b border-border align-middle hover:bg-subtle">
                    <td className="px-da-lg py-da-md">
                      <button
                        type="button"
                        className="flex min-w-0 flex-col gap-[2px] text-left"
                        onClick={() => navigate('admin', 'POLICIES', p.id)}
                      >
                        <span className="truncate text-foreground da-body-strong">{p.name}</span>
                        <span className="text-muted-foreground da-mono">{p.code}</span>
                      </button>
                    </td>
                    <td className="px-da-lg py-da-md text-foreground da-body-small">{p.data_space_name}</td>
                    <td className="px-da-lg py-da-md text-muted-foreground da-body-small">{POLICY_KIND[p.kind]}</td>
                    <td className="px-da-lg py-da-md">
                      <StatusDot tone={POLICY_STATUS[p.status].tone}>{POLICY_STATUS[p.status].label}</StatusDot>
                    </td>
                    <td className="px-da-xs py-da-md text-right">
                      <Menu
                        label={`Thao tác với ${p.code}`}
                        items={[
                          { label: 'Mở', icon: 'external-link', onSelect: () => navigate('admin', 'POLICIES', p.id) },
                          ...(p.status === 'active'
                            ? [{ label: 'Tắt chính sách', danger: true, onSelect: () => setTurningOff(p) }]
                            : []),
                        ]}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </AsyncRegion>
      </div>
      <ConfirmDialog
        open={turningOff !== null}
        title="Tắt chính sách này?"
        confirmLabel="Tắt chính sách"
        loading={sending}
        error={error}
        onCancel={() => {
          setTurningOff(null);
          setError(undefined);
        }}
        onConfirm={() => turningOff && void turnOff(turningOff)}
      >
        {turningOff?.applied_this_week ?? 0} yêu cầu tuần này được tự xử lý nhờ quy tắc. Sau khi tắt,
        agent sẽ hỏi lại bạn mỗi lần.
      </ConfirmDialog>
    </div>
  );
}
