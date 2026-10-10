import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Checkbox } from '../../design-system/Checkbox';
import { Icon } from '../../design-system/Icon';
import { Notice } from '../../design-system/Notice';
import { Select } from '../../design-system/Select';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import {
  getPackVersion,
  installPack,
  type ConflictChoice,
  type PackVersion,
} from '../../lib/v1/packs';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P09 · Cài gói · Xem trước" (2284:17334, UC-09-01): what a pack version adds, the checks run
 * before installing it (compatibility, nothing certified overwritten, signature), and for every
 * metric code it shares with one the organisation already has, how to handle it — the default
 * keeps the organisation's and links it to the pack. A pack only adds definitions; it never reads
 * or changes data. Installing goes into the ticked Data Spaces and opens the onboarding.
 */
const CHOICES: { value: ConflictChoice; label: string }[] = [
  { value: 'keep_org', label: 'Giữ bản của tổ chức, liên kết với gói' },
  { value: 'use_pack', label: 'Dùng bản của gói' },
  { value: 'keep_both', label: 'Giữ cả hai, đổi mã bản của gói' },
];

export default function PackInstallPage() {
  const { routeId } = useAppState();
  const version = useV1(routeId ? () => getPackVersion(routeId) : null, [routeId]);
  if (!routeId)
    return (
      <p className="px-da-3xl pt-da-xl text-muted-foreground da-body-small">
        Chọn một gói ở Gói ngành để xem trước khi cài.
      </p>
    );
  return (
    <AsyncRegion state={version} what="phiên bản gói">
      {(v) => <InstallBody key={v.id} version={v} />}
    </AsyncRegion>
  );
}

function InstallBody({ version }: { version: PackVersion }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [targets, setTargets] = useState<Set<string>>(
    new Set(version.targets.filter((t) => t.default).map((t) => t.id)),
  );
  const [choices, setChoices] = useState<Record<string, ConflictChoice>>(
    Object.fromEntries(version.code_conflicts.map((c) => [c.code, c.suggested])),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const passed = version.checks.filter((c) => c.ok).length;
  const pending = version.checks.length - passed;

  const install = async () => {
    setBusy(true);
    setError(undefined);
    try {
      const created = await installPack({
        pack_version_id: version.id,
        data_space_ids: [...targets],
        conflict_choices: choices,
      });
      toast({ tone: 'success', message: `Đã cài ${version.pack_name} ${version.version}` });
      navigate('data', 'PACK_ONBOARD', created.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const installButton = (
    <Button kind="primary" loading={busy} disabled={busy || targets.size === 0} onClick={install}>
      Cài vào {targets.size} Data Space
    </Button>
  );

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Gói ngành', 'Cài gói']}
        title={`Cài “${version.pack_name}” ${version.version}`}
        description="Kiểm tra trước khi cài: gói chỉ thêm định nghĩa, không đọc hay sửa dữ liệu của bạn"
        actions={
          <>
            <Button kind="ghost" onClick={() => navigate('data', 'PACKS')}>
              Huỷ
            </Button>
            {targets.size === 0 ? (
              <Tooltip content="Chọn ít nhất một Data Space.">{installButton}</Tooltip>
            ) : (
              installButton
            )}
          </>
        }
      />
      <div className="flex min-h-0 flex-1 max-[1023px]:flex-col">
        <div className="flex min-w-0 flex-1 flex-col px-da-3xl pb-da-2xl">
          {error && (
            <div className="pt-da-lg">
              <Notice tone="error">Không cài được: {error}</Notice>
            </div>
          )}
          <section className="flex flex-col gap-da-md border-b border-border py-da-xl">
            <h2 className="text-foreground da-heading-3">Gói này thêm gì</h2>
            <dl className="grid grid-cols-6 overflow-hidden rounded-md border border-border max-[1279px]:grid-cols-3">
              {version.counts.map((c) => (
                <div
                  key={c.label}
                  className="flex flex-col-reverse gap-[2px] border-r border-border px-da-lg py-da-md last:border-r-0"
                >
                  <dt className="text-muted-foreground da-caption">{c.label}</dt>
                  <dd className="text-foreground da-metric-small">
                    {c.value.toLocaleString('vi-VN')}
                  </dd>
                </div>
              ))}
            </dl>
          </section>
          <section className="flex flex-col gap-da-md border-b border-border py-da-xl">
            <div className="flex items-baseline gap-da-md">
              <h2 className="text-foreground da-heading-3">Kiểm tra</h2>
              <span className="ml-auto text-muted-foreground da-caption">
                {passed} đạt{pending ? ` · ${pending} cần quyết định` : ''}
              </span>
            </div>
            <table className="w-full table-fixed border-collapse da-body-small">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground da-caption">
                  <th className="w-[42px]">
                    <span className="sr-only">Kết quả kiểm tra</span>
                  </th>
                  <th className="py-da-sm font-normal">Mục</th>
                  <th className="py-da-sm font-normal">Kết quả</th>
                </tr>
              </thead>
              <tbody>
                {version.checks.map((c) => (
                  <tr key={c.item} className="border-b border-border text-foreground">
                    <td className="px-da-md py-[9px]">
                      <Icon
                        name={c.ok ? 'check' : 'alert'}
                        className={c.ok ? 'text-success' : 'text-warning'}
                        label={c.ok ? 'Đạt' : 'Cần quyết định'}
                      />
                    </td>
                    <td className="py-[9px]">{c.item}</td>
                    <td
                      className={c.ok ? 'py-[9px] text-muted-foreground' : 'py-[9px] text-warning'}
                    >
                      {c.result}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
          {version.code_conflicts.length > 0 && (
            <section className="flex flex-col gap-da-md border-b border-border py-da-xl">
              <h2 className="text-foreground da-heading-3">Chỉ số trùng mã</h2>
              <table className="w-full table-fixed border-collapse da-body-small">
                <thead>
                  <tr className="border-b border-border text-left text-muted-foreground da-caption">
                    <th className="w-[156px] px-da-md py-da-sm font-normal">Mã</th>
                    <th className="px-da-md py-da-sm font-normal">Của tổ chức</th>
                    <th className="px-da-md py-da-sm font-normal">Trong gói</th>
                    <th className="w-[236px] px-da-md py-da-sm font-normal">Cách xử lý</th>
                  </tr>
                </thead>
                <tbody>
                  {version.code_conflicts.map((c) => (
                    <tr key={c.code} className="border-b border-border text-foreground">
                      <td className="px-da-md py-[10px] da-mono">{c.code}</td>
                      <td className="px-da-md py-[10px]">{c.org_text}</td>
                      <td className="px-da-md py-[10px] text-muted-foreground">{c.pack_text}</td>
                      <td className="px-da-md py-[8px]">
                        <Select<ConflictChoice>
                          label={`Cách xử lý ${c.code}`}
                          value={choices[c.code]}
                          onChange={(v) => setChoices({ ...choices, [c.code]: v })}
                          options={CHOICES}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
        </div>
        <aside className="flex w-[360px] shrink-0 flex-col gap-da-md border-l border-border bg-background px-da-xl pt-da-xl max-[1023px]:w-full max-[1023px]:border-l-0">
          <h2 className="text-foreground da-heading-3">Cài vào</h2>
          <div role="group" aria-label="Cài vào Data Space" className="flex flex-col gap-da-sm">
            {version.targets.map((t) => (
              <Checkbox
                key={t.id}
                checked={targets.has(t.id)}
                onChange={(on) => {
                  const next = new Set(targets);
                  if (on) next.add(t.id);
                  else next.delete(t.id);
                  setTargets(next);
                }}
              >
                {t.name}
              </Checkbox>
            ))}
          </div>
          <h2 className="pt-da-sm text-foreground da-heading-3">Sau khi cài</h2>
          <p className="text-foreground da-body-small">
            Định nghĩa ở trạng thái “từ gói, chưa ánh xạ”. Bước tiếp theo là đưa dữ liệu của tổ chức
            vào ngành để các chỉ số tính được.
          </p>
          <p className="text-muted-foreground da-caption">
            Cài gói không bao giờ ghi đè định nghĩa đã chứng nhận.
          </p>
        </aside>
      </div>
    </div>
  );
}
