import { useRef, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Menu } from '../../design-system/Menu';
import { Notice } from '../../design-system/Notice';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { StatusDot } from '../../design-system/StatusDot';
import { importPack, listPacks, type IndustryPack } from '../../lib/v1/packs';
import { useV1 } from '../../lib/v1/useV1';
import { CreatePackDialog } from './CreatePackDialog';

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
  const [creating, setCreating] = useState(false);

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
            <Button onClick={() => setCreating(true)}>Tạo gói riêng</Button>
            <Button kind="primary" loading={busy} onClick={() => fileRef.current?.click()}>
              Cài gói
            </Button>
          </>
        }
      />
      <div className="flex flex-col gap-da-xl px-da-3xl pb-da-2xl pt-da-xl">
        {error && <Notice tone="error">Không nhập được gói: {error}</Notice>}
        {creating && (
          <CreatePackDialog
            open
            packs={packs.data?.items ?? []}
            onClose={() => setCreating(false)}
            onCreated={(draftId) => navigate('data', 'PACK_AUTHOR', draftId)}
          />
        )}
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

function installStatus(p: IndustryPack): string {
  const i = p.installed;
  if (!i) return '';
  if (i.status === 'onboarding') return 'Đang đưa vào ngành';
  if (i.upgrade) return `Có bản ${i.upgrade.to_version}`;
  return 'Đang dùng';
}

function Installed({ items }: { items: IndustryPack[] }) {
  const { navigate } = useAppState();
  const view = useTableView(items, {
    search: (p) => `${p.name} ${p.installed?.scope_text ?? ''} ${p.installed?.version ?? ''}`,
    filters: [{ id: 'status', label: 'Trạng thái', value: installStatus }],
    sorts: [
      { id: 'name', value: (p) => p.name },
      { id: 'version', value: (p) => p.installed?.version },
      {
        id: 'mapped',
        value: (p) =>
          p.installed?.mapped_total
            ? Math.round((p.installed.mapped / p.installed.mapped_total) * 100)
            : 0,
      },
      { id: 'overrides', value: (p) => p.installed?.overrides },
      { id: 'status', value: installStatus },
    ],
    pageSize: 50,
  });
  return (
    <section className="flex flex-col gap-da-md">
      <h2 className="text-foreground da-heading-3">Đã cài</h2>
      <TableToolbar view={view} placeholder="Tìm gói đã cài…" searchLabel="Tìm gói đã cài" />
      <table className="w-full table-fixed border-collapse da-body-small">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground da-caption">
            <SortableTh view={view} column="name" className="px-da-md py-da-md font-normal">
              Gói
            </SortableTh>
            <SortableTh view={view} column="version" className="w-[166px] px-da-md py-da-md font-normal">
              Phiên bản
            </SortableTh>
            <SortableTh view={view} column="mapped" className="w-[206px] px-da-md py-da-md font-normal">
              Đã ánh xạ
            </SortableTh>
            <SortableTh view={view} column="overrides" className="w-[136px] px-da-md py-da-md font-normal">
              Đã hiệu chỉnh
            </SortableTh>
            <SortableTh view={view} column="status" className="w-[200px] px-da-md py-da-md font-normal">
              Trạng thái
            </SortableTh>
            <SortableTh view={view} className="w-[40px]" />
          </tr>
        </thead>
        <tbody>
          {view.rows.map((p) => {
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
                      ...(p.latest
                        ? [
                            {
                              label: 'Xem nội dung phiên bản',
                              onSelect: () =>
                                navigate('data', 'PACK_INSTALL', p.latest?.version_id),
                            },
                          ]
                        : []),
                    ]}
                  />
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <TablePager view={view} />
    </section>
  );
}

function Available({ items }: { items: IndustryPack[] }) {
  const { navigate } = useAppState();
  const view = useTableView(items, {
    search: (p) =>
      `${p.name} ${p.latest?.version ?? ''} ${p.latest?.content_text ?? ''} ${p.publisher}`,
    filters: [{ id: 'publisher', label: 'Nhà phát hành', value: (p) => p.publisher }],
    sorts: [
      { id: 'name', value: (p) => p.name },
      { id: 'version', value: (p) => p.latest?.version },
      { id: 'content', value: (p) => p.latest?.content_text },
      { id: 'publisher', value: (p) => p.publisher },
    ],
    pageSize: 50,
  });
  if (items.length === 0) return null;
  return (
    <section className="flex flex-col gap-da-md">
      <h2 className="text-foreground da-heading-3">Có thể cài</h2>
      <TableToolbar view={view} placeholder="Tìm gói có thể cài…" searchLabel="Tìm gói có thể cài" />
      <table className="w-full table-fixed border-collapse da-body-small">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground da-caption">
            <SortableTh view={view} column="name" className="px-da-md py-da-md font-normal">
              Gói
            </SortableTh>
            <SortableTh view={view} column="version" className="w-[166px] px-da-md py-da-md font-normal">
              Phiên bản
            </SortableTh>
            <SortableTh view={view} column="content" className="px-da-md py-da-md font-normal">
              Nội dung
            </SortableTh>
            <SortableTh view={view} column="publisher" className="w-[172px] px-da-md py-da-md font-normal">
              Nhà phát hành
            </SortableTh>
          </tr>
        </thead>
        <tbody>
          {view.rows.map((p) => (
            <tr key={p.id} className="border-b border-border text-foreground">
              <td className="px-da-md py-[10px]">
                {/* GET /v1/packs carries no version yet: without one the pack is listed, not opened. */}
                {p.latest ? (
                  <button
                    type="button"
                    onClick={() => navigate('data', 'PACK_INSTALL', p.latest?.version_id)}
                    className="text-left da-body-strong hover:underline"
                  >
                    {p.name}
                  </button>
                ) : (
                  <span className="da-body-strong">{p.name}</span>
                )}
              </td>
              <td className="px-da-md py-[10px] da-mono">{p.latest?.version ?? '—'}</td>
              <td className="px-da-md py-[10px] text-muted-foreground">{p.latest?.content_text}</td>
              <td className="px-da-md py-[10px] text-muted-foreground">{p.publisher}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <TablePager view={view} />
    </section>
  );
}
