import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Notice } from '../../design-system/Notice';
import { Select } from '../../design-system/Select';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { StatusDot } from '../../design-system/StatusDot';
import { Tabs } from '../../design-system/Tabs';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import {
  applyPackUpgrade,
  decidePackUpgrade,
  getPackUpgrade,
  type PackUpgrade,
  type UpgradeChoice,
} from '../../lib/v1/packs';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P09 · Nâng cấp gói · Gộp ba chiều" (2286:17750, UC-09-04): upgrading a pack. Each element both
 * the pack and the organisation changed needs a choice; the changes that merged on their own and
 * the ones skipped because the new pack dropped them are in their own tabs. With every choice made
 * the upgrade runs against the filed numbers (step 5), then someone other than who started it
 * applies it; the old version is kept 30 days to roll back. Nothing the organisation edited is
 * overwritten silently. The merge items carry codes only, so the old, new and organisation's text
 * show "—".
 */
type Tab = 'conflicts' | 'auto' | 'skipped';

const STATUS: Record<string, string> = {
  needs_decision: 'Cần chọn',
  testing: 'Đang chạy thử',
  pending_approval: 'Chờ áp dụng',
  applied: 'Đã áp dụng',
  rolled_back: 'Đã quay lui',
};

export default function PackUpgradePage() {
  const { routeId } = useAppState();
  const upgrade = useV1(
    ['getPackUpgrade', routeId],
    routeId ? () => getPackUpgrade(routeId) : null,
  );
  if (!routeId)
    return (
      <p className="px-da-3xl pt-da-xl text-muted-foreground da-body-small">
        Mở nâng cấp từ một gói có phiên bản mới.
      </p>
    );
  return (
    <AsyncRegion state={upgrade} what="nâng cấp gói">
      {(u) => <UpgradeBody upgrade={u} onChanged={upgrade.reload} />}
    </AsyncRegion>
  );
}

function UpgradeBody({ upgrade, onChanged }: { upgrade: PackUpgrade; onChanged: () => void }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [tab, setTab] = useState<Tab>('conflicts');
  const [decisions, setDecisions] = useState<Record<string, UpgradeChoice>>(
    Object.fromEntries(upgrade.conflicts.flatMap((c) => (c.decision ? [[c.id, c.decision]] : []))),
  );
  const [busy, setBusy] = useState<'decide' | 'apply' | null>(null);
  const [error, setError] = useState<string>();
  const deciding = upgrade.status === 'needs_decision';
  const chosen = upgrade.conflicts.every((c) => decisions[c.id]);
  const testOk = upgrade.test !== null && upgrade.test.matched === upgrade.test.total;
  const blocker =
    upgrade.status === 'pending_approval'
      ? null
      : deciding
        ? 'Chọn cách xử lý cho mọi mục rồi chạy thử.'
        : upgrade.status === 'testing'
          ? 'Chờ chạy thử với số đã nộp xong.'
          : `Lượt nâng cấp ${STATUS[upgrade.status]?.toLowerCase() ?? upgrade.status}.`;

  const run = async (key: 'decide' | 'apply') => {
    setBusy(key);
    setError(undefined);
    try {
      if (key === 'decide') {
        await decidePackUpgrade(
          upgrade.id,
          upgrade.conflicts.map((c) => ({ code: c.id, kind: c.kind, decision: decisions[c.id] })),
        );
        toast({ tone: 'success', message: 'Đã lưu lựa chọn; đang chạy thử với số đã nộp' });
        onChanged();
      } else {
        await applyPackUpgrade(upgrade.id);
        toast({
          tone: 'success',
          message: `Đã nâng cấp ${upgrade.pack_name} lên ${upgrade.to_version}`,
        });
        navigate('data', 'PACKS');
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const applyButton = (
    <Button
      kind="primary"
      loading={busy === 'apply'}
      disabled={busy !== null || blocker !== null}
      onClick={() => run('apply')}
    >
      Áp dụng nâng cấp
    </Button>
  );

  return (
    <div className="flex min-h-full flex-col">
      {/* Unbordered: the tabs below draw the line under the header. */}
      <div className="px-da-3xl pb-da-md pt-[20px]">
        <PageHeader
          breadcrumb={['Dữ liệu', 'Gói ngành', upgrade.pack_name, 'Nâng cấp']}
          title={
            <span className="flex items-center gap-[10px]">
              Nâng cấp {upgrade.from_version} → {upgrade.to_version}
              <StatusDot tone={upgrade.status === 'pending_approval' ? 'success' : 'warning'}>
                {STATUS[upgrade.status] ?? upgrade.status}
              </StatusDot>
            </span>
          }
          description={upgrade.summary_text}
          actions={
            <>
              <Button kind="ghost" onClick={() => navigate('data', 'PACKS')}>
                Huỷ
              </Button>
              {deciding && (
                <Button
                  loading={busy === 'decide'}
                  disabled={busy !== null || !chosen}
                  onClick={() => run('decide')}
                >
                  Lưu lựa chọn và chạy thử
                </Button>
              )}
              {blocker ? <Tooltip content={blocker}>{applyButton}</Tooltip> : applyButton}
            </>
          }
        />
      </div>
      <div className="px-da-3xl">
        <Tabs<Tab>
          label="Thay đổi trong gói"
          value={tab}
          onChange={setTab}
          items={[
            { value: 'conflicts', label: 'Cần chọn', count: upgrade.conflicts.length },
            { value: 'auto', label: 'Tự gộp', count: upgrade.auto.length },
            { value: 'skipped', label: 'Bỏ qua', count: upgrade.skipped.length },
          ]}
        />
      </div>
      <div className="flex flex-col gap-da-md px-da-3xl pb-da-2xl pt-da-lg">
        {error && <Notice tone="error">Không thực hiện được: {error}</Notice>}
        {upgrade.test && (
          <Notice tone={testOk ? 'success' : 'warning'}>
            Chạy thử với số đã nộp: {upgrade.test.matched}/{upgrade.test.total} khớp.
          </Notice>
        )}
        {tab === 'conflicts' ? (
          <ConflictTable
            conflicts={upgrade.conflicts}
            fromVersion={upgrade.from_version}
            toVersion={upgrade.to_version}
            decisions={decisions}
            deciding={deciding}
            onDecide={(id, value) => setDecisions({ ...decisions, [id]: value })}
          />
        ) : (
          <ul className="flex flex-col border-t border-border">
            {(tab === 'auto'
              ? upgrade.auto.map((a) => ({ element: a.element, text: a.text }))
              : upgrade.skipped.map((s) => ({ element: s.element, text: s.reason }))
            ).map((row, i) => (
              <li
                key={`${row.element}-${i}`}
                className="flex gap-da-lg border-b border-border py-[9px] da-body-small"
              >
                <span className="w-[260px] shrink-0 text-foreground">{row.element}</span>
                <span className="text-muted-foreground">{row.text}</span>
              </li>
            ))}
          </ul>
        )}
        <p className="text-muted-foreground da-body-small">
          Áp dụng nâng cấp được mở khi đã chọn đủ {upgrade.conflicts.length} mục và chạy thử với số
          đã nộp xong. Bản {upgrade.from_version} được giữ để quay lui trong 30 ngày.
        </p>
        <p className="text-muted-foreground da-caption">Không tự ghi đè phần tổ chức đã sửa.</p>
      </div>
    </div>
  );
}

function ConflictTable({
  conflicts,
  fromVersion,
  toVersion,
  decisions,
  deciding,
  onDecide,
}: {
  conflicts: PackUpgrade['conflicts'];
  fromVersion: string;
  toVersion: string;
  decisions: Record<string, UpgradeChoice>;
  deciding: boolean;
  onDecide: (id: string, value: UpgradeChoice) => void;
}) {
  const view = useTableView(conflicts, {
    search: (c) => `${c.element} ${c.old ?? ''} ${c.new ?? ''} ${c.org ?? ''}`,
    filters: [
      {
        id: 'kind',
        label: 'Loại',
        value: (c) => (c.kind === 'metric' ? 'Chỉ số' : 'Ánh xạ'),
      },
    ],
    sorts: [
      { id: 'element', value: (c) => c.element },
      { id: 'old', value: (c) => c.old },
      { id: 'new', value: (c) => c.new },
      { id: 'org', value: (c) => c.org },
    ],
    pageSize: 50,
  });
  return (
    <>
      <TableToolbar view={view} placeholder="Tìm phần tử…" searchLabel="Tìm phần tử" />
      <table className="w-full table-fixed border-collapse da-body-small">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground da-caption">
            <SortableTh view={view} column="element" className="px-da-md py-da-sm font-normal">
              Phần tử
            </SortableTh>
            <SortableTh view={view} column="old" className="px-da-md py-da-sm font-normal">
              Bản gói cũ {fromVersion}
            </SortableTh>
            <SortableTh view={view} column="new" className="px-da-md py-da-sm font-normal">
              Bản gói mới {toVersion}
            </SortableTh>
            <SortableTh view={view} column="org" className="px-da-md py-da-sm font-normal">
              Phần tổ chức đã sửa
            </SortableTh>
            <SortableTh view={view} className="w-[214px] px-da-md py-da-sm font-normal">
              Chọn
            </SortableTh>
          </tr>
        </thead>
        <tbody>
          {view.rows.map((c) => (
            <tr key={c.id} className="border-b border-border align-top text-foreground">
              <td className="px-da-md py-[12px]">{c.element}</td>
              <td className="px-da-md py-[12px] text-muted-foreground da-mono">{c.old ?? '—'}</td>
              <td className="px-da-md py-[12px] da-mono">{c.new ?? '—'}</td>
              <td className="px-da-md py-[12px] text-info da-mono">{c.org ?? '—'}</td>
              <td className="px-da-md py-[8px]">
                <Select<UpgradeChoice>
                  label={`Chọn cho ${c.element}`}
                  value={decisions[c.id] ?? null}
                  placeholder="Chọn"
                  disabled={!deciding}
                  onChange={(v) => onDecide(c.id, v)}
                  options={c.options}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <TablePager view={view} />
    </>
  );
}
