import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Notice } from '../../design-system/Notice';
import { Select } from '../../design-system/Select';
import { StatusDot } from '../../design-system/StatusDot';
import { Tabs } from '../../design-system/Tabs';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import {
  applyPackUpgrade,
  benchmarkPackUpgrade,
  decidePackUpgrade,
  getPackUpgrade,
  type PackUpgrade,
} from '../../lib/v1/packs';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P09 · Nâng cấp gói · Gộp ba chiều" (2286:17750, UC-09-04): a dry run of upgrading a pack. Each
 * element both the pack and the organisation changed shows the old pack, the new pack and the
 * organisation's edit, and a choice (the agent's suggestion first); the changes that merged on
 * their own and the ones skipped because the organisation removed that part are in their own
 * tabs. Applying needs every choice made and the pack's benchmark questions at 95% or more; the
 * old version is kept 30 days to roll back. Nothing the organisation edited is overwritten silently.
 */
type Tab = 'conflicts' | 'auto' | 'skipped';
const BENCHMARK_MIN = 95;

export default function PackUpgradePage() {
  const { routeId } = useAppState();
  const upgrade = useV1(routeId ? () => getPackUpgrade(routeId) : null, [routeId]);
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
  const [decisions, setDecisions] = useState<Record<string, string>>(
    Object.fromEntries(upgrade.conflicts.map((c) => [c.id, c.suggested])),
  );
  const [busy, setBusy] = useState<'benchmark' | 'apply' | null>(null);
  const [error, setError] = useState<string>();
  const chosen = upgrade.conflicts.every((c) => decisions[c.id]);
  const benchmarkOk = upgrade.benchmark_pct !== null && upgrade.benchmark_pct >= BENCHMARK_MIN;
  const blocker = !chosen
    ? 'Chọn cách xử lý cho mọi mục.'
    : upgrade.benchmark_pct === null
      ? 'Chạy bộ câu hỏi chuẩn trước khi áp dụng.'
      : !benchmarkOk
        ? `Bộ câu hỏi chuẩn dưới ${BENCHMARK_MIN}%.`
        : null;

  const run = async (key: 'benchmark' | 'apply') => {
    setBusy(key);
    setError(undefined);
    try {
      if (key === 'benchmark') {
        const result = await benchmarkPackUpgrade(upgrade.id);
        toast({
          tone: 'success',
          message: `Bộ câu hỏi chuẩn: ${String(result.benchmark_pct).replace('.', ',')}% đúng`,
        });
        onChanged();
      } else {
        await decidePackUpgrade(upgrade.id, decisions);
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
              <StatusDot tone="warning">{upgrade.conflicts.length} cần chọn</StatusDot>
            </span>
          }
          description={upgrade.summary_text}
          actions={
            <>
              <Button kind="ghost" onClick={() => navigate('data', 'PACKS')}>
                Huỷ
              </Button>
              <Button
                loading={busy === 'benchmark'}
                disabled={busy !== null}
                onClick={() => run('benchmark')}
              >
                Chạy bộ câu hỏi chuẩn
              </Button>
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
        {upgrade.benchmark_pct !== null && (
          <Notice tone={benchmarkOk ? 'success' : 'warning'}>
            Bộ câu hỏi chuẩn: {String(upgrade.benchmark_pct).replace('.', ',')}% đúng (cần từ{' '}
            {BENCHMARK_MIN}%).
          </Notice>
        )}
        {tab === 'conflicts' ? (
          <table className="w-full table-fixed border-collapse da-body-small">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground da-caption">
                <th className="px-da-md py-da-sm font-normal">Phần tử</th>
                <th className="px-da-md py-da-sm font-normal">Bản gói cũ {upgrade.from_version}</th>
                <th className="px-da-md py-da-sm font-normal">Bản gói mới {upgrade.to_version}</th>
                <th className="px-da-md py-da-sm font-normal">Phần tổ chức đã sửa</th>
                <th className="w-[214px] px-da-md py-da-sm font-normal">Chọn</th>
              </tr>
            </thead>
            <tbody>
              {upgrade.conflicts.map((c) => (
                <tr key={c.id} className="border-b border-border align-top text-foreground">
                  <td className="px-da-md py-[12px]">{c.element}</td>
                  <td className="px-da-md py-[12px] text-muted-foreground da-mono">{c.old}</td>
                  <td className="px-da-md py-[12px] da-mono">{c.new}</td>
                  <td className="px-da-md py-[12px] text-info da-mono">{c.org}</td>
                  <td className="px-da-md py-[8px]">
                    <Select
                      label={`Chọn cho ${c.element}`}
                      value={decisions[c.id] ?? null}
                      onChange={(v) => setDecisions({ ...decisions, [c.id]: v })}
                      options={c.options}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
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
          Áp dụng nâng cấp được mở khi đã chọn đủ {upgrade.conflicts.length} mục và bộ câu hỏi chuẩn
          đạt từ {BENCHMARK_MIN}% trở lên. Bản {upgrade.from_version} được giữ để quay lui trong 30
          ngày.
        </p>
        <p className="text-muted-foreground da-caption">Không tự ghi đè phần tổ chức đã sửa.</p>
      </div>
    </div>
  );
}
