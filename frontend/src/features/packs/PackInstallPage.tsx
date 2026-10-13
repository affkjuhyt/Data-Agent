import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Icon } from '../../design-system/Icon';
import { Notice } from '../../design-system/Notice';
import { useToast } from '../../design-system/Toast';
import { getPackVersion, installPack, type PackVersion } from '../../lib/v1/packs';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P09 · Cài gói · Xem trước" (2284:17334, UC-09-01): what a pack version adds and the checks on it
 * (the publisher's signature, breaking changes). A pack only adds definitions; it never reads or
 * changes data. Installing goes into the current Data Space (POST /v1/pack-installs takes one
 * version, in X-Data-Space) and opens the onboarding. Choosing per Data Space and resolving metric
 * codes the organisation already has are not in the api, so the screen does not offer them.
 */

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
  const { navigate, dataSpace } = useAppState();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const passed = version.checks.filter((c) => c.ok).length;
  const pending = version.checks.length - passed;

  const install = async () => {
    setBusy(true);
    setError(undefined);
    try {
      const created = await installPack(version.id);
      toast({ tone: 'success', message: `Đã cài ${version.pack_name} ${version.version}` });
      navigate('data', 'PACK_ONBOARD', created.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const installButton = (
    <Button kind="primary" loading={busy} disabled={busy} onClick={install}>
      Cài vào Data Space này
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
            {installButton}
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
            <dl className="grid grid-cols-5 overflow-hidden rounded-md border border-border max-[1279px]:grid-cols-3">
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
                {passed} đạt{pending ? ` · ${pending} cần xem` : ''}
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
                        label={c.ok ? 'Đạt' : 'Cần xem'}
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
        </div>
        <aside className="flex w-[360px] shrink-0 flex-col gap-da-md border-l border-border bg-background px-da-xl pt-da-xl max-[1023px]:w-full max-[1023px]:border-l-0">
          <h2 className="text-foreground da-heading-3">Cài vào</h2>
          <p className="text-foreground da-body-small">
            {dataSpace?.name ?? 'Data Space đang chọn'}
          </p>
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
