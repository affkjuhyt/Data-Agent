import { useRef, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Menu } from '../../design-system/Menu';
import { Notice } from '../../design-system/Notice';
import { StatusDot } from '../../design-system/StatusDot';
import { importPack, listPacks, type IndustryPack } from '../../lib/v1/packs';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P09 · Gói ngành · Danh sách" (2284:17173, UC-09-01, UC-09-04): the packs installed — version,
 * how much is mapped, how many metrics the organisation adjusted, and whether a newer version or
 * the onboarding waits — and the packs that can be installed. "Cài gói" imports a signed pack file
 * (BR-128) and opens its install preview; an available pack opens the same preview.
 */
export default function PacksPage() {
  const { navigate } = useAppState();
  const packs = useV1(listPacks, []);
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const onFile = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    setError(undefined);
    try {
      const version = await importPack(file);
      navigate('data', 'PACK_INSTALL', version.id);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Gói ngành']}
        title="Gói ngành"
        description="Gói ngành chứa mô hình dữ liệu chuẩn, chỉ số, thuật ngữ, kỹ năng và agent mẫu của một ngành. Phần đã hiệu chỉnh cho tổ chức được giữ khi nâng cấp."
        actions={
          <>
            <input
              ref={fileRef}
              type="file"
              accept=".dapack,.zip"
              aria-label="Tệp gói ngành"
              className="sr-only"
              tabIndex={-1}
              onChange={(e) => onFile(e.target.files?.[0])}
            />
            <Button kind="primary" loading={busy} onClick={() => fileRef.current?.click()}>
              Cài gói
            </Button>
          </>
        }
      />
      <div className="flex flex-col gap-da-xl px-da-3xl pb-da-2xl pt-da-xl">
        {error && <Notice tone="error">Không nhập được gói: {error}</Notice>}
        <AsyncRegion state={packs} what="gói ngành">
          {(page) => (
            <>
              <Installed items={page.items.filter((p) => p.installed)} />
              <Available items={page.items.filter((p) => !p.installed)} />
            </>
          )}
        </AsyncRegion>
      </div>
    </div>
  );
}

function Installed({ items }: { items: IndustryPack[] }) {
  const { navigate } = useAppState();
  return (
    <section className="flex flex-col gap-da-md">
      <h2 className="text-foreground da-heading-3">Đã cài</h2>
      <table className="w-full table-fixed border-collapse da-body-small">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground da-caption">
            <th className="px-da-md py-da-md font-normal">Gói</th>
            <th className="w-[166px] px-da-md py-da-md font-normal">Phiên bản</th>
            <th className="w-[206px] px-da-md py-da-md font-normal">Đã ánh xạ</th>
            <th className="w-[136px] px-da-md py-da-md font-normal">Đã hiệu chỉnh</th>
            <th className="w-[200px] px-da-md py-da-md font-normal">Trạng thái</th>
            <th className="w-[40px]" />
          </tr>
        </thead>
        <tbody>
          {items.map((p) => {
            const i = p.installed!;
            const pct = i.mapped_total ? Math.round((i.mapped / i.mapped_total) * 100) : 0;
            return (
              <tr key={p.id} className="border-b border-border text-foreground">
                <td className="px-da-md py-[10px]">
                  <p className="truncate">{p.name}</p>
                  <p className="truncate text-muted-foreground da-caption">{i.scope_text}</p>
                </td>
                <td className="px-da-md py-[10px] da-mono">{i.version}</td>
                <td className="px-da-md py-[10px]">
                  <span className="flex flex-col gap-[4px]">
                    <span className="da-mono">
                      {pct}% · {i.mapped}/{i.mapped_total} thực thể
                    </span>
                    <span className="block h-1 w-[160px] rounded-full bg-subtle">
                      <span
                        className="block h-full rounded-full bg-primary"
                        style={{ width: `${pct}%` }}
                      />
                    </span>
                  </span>
                </td>
                <td className="px-da-md py-[10px]">
                  {i.first_override_id ? (
                    <button
                      type="button"
                      onClick={() =>
                        navigate('data', 'METRIC_OVERRIDE', i.first_override_id ?? undefined)
                      }
                      className="hover:underline"
                    >
                      {i.overrides} chỉ số
                    </button>
                  ) : (
                    `${i.overrides} chỉ số`
                  )}
                </td>
                <td className="px-da-md py-[10px]">
                  {i.status === 'onboarding' ? (
                    <StatusDot tone="info">Đang đưa vào ngành</StatusDot>
                  ) : i.upgrade ? (
                    <StatusDot tone="warning">Có bản {i.upgrade.to_version}</StatusDot>
                  ) : (
                    <StatusDot tone="success">Đang dùng</StatusDot>
                  )}
                </td>
                <td className="py-[10px]">
                  <Menu
                    label={`Thao tác với ${p.name}`}
                    items={[
                      ...(i.status === 'onboarding'
                        ? [
                            {
                              label: 'Tiếp tục đưa vào ngành',
                              onSelect: () => navigate('data', 'PACK_ONBOARD', i.install_id),
                            },
                          ]
                        : []),
                      ...(i.upgrade
                        ? [
                            {
                              label: `Nâng cấp lên ${i.upgrade.to_version}`,
                              onSelect: () =>
                                navigate('data', 'PACK_UPGRADE', i.upgrade?.upgrade_id),
                            },
                          ]
                        : []),
                      {
                        label: 'Xem nội dung phiên bản',
                        onSelect: () => navigate('data', 'PACK_INSTALL', p.latest.version_id),
                      },
                    ]}
                  />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

function Available({ items }: { items: IndustryPack[] }) {
  const { navigate } = useAppState();
  if (items.length === 0) return null;
  return (
    <section className="flex flex-col gap-da-md">
      <h2 className="text-foreground da-heading-3">Có thể cài</h2>
      <table className="w-full table-fixed border-collapse da-body-small">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground da-caption">
            <th className="px-da-md py-da-md font-normal">Gói</th>
            <th className="w-[166px] px-da-md py-da-md font-normal">Phiên bản</th>
            <th className="px-da-md py-da-md font-normal">Nội dung</th>
            <th className="w-[172px] px-da-md py-da-md font-normal">Nhà phát hành</th>
          </tr>
        </thead>
        <tbody>
          {items.map((p) => (
            <tr key={p.id} className="border-b border-border text-foreground">
              <td className="px-da-md py-[10px]">
                <button
                  type="button"
                  onClick={() => navigate('data', 'PACK_INSTALL', p.latest.version_id)}
                  className="text-left da-body-strong hover:underline"
                >
                  {p.name}
                </button>
              </td>
              <td className="px-da-md py-[10px] da-mono">{p.latest.version}</td>
              <td className="px-da-md py-[10px] text-muted-foreground">{p.latest.content_text}</td>
              <td className="px-da-md py-[10px] text-muted-foreground">{p.publisher}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
