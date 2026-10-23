import { useEffect, useState } from 'react';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Checkbox } from '../../design-system/Checkbox';
import { Dialog } from '../../design-system/Dialog';
import { EmptyState } from '../../design-system/EmptyState';
import { Field } from '../../design-system/Field';
import { Notice } from '../../design-system/Notice';
import { Select } from '../../design-system/Select';
import { listDataSpaces } from '../../lib/v1/api';
import { listCatalogProducts, requestProductUse } from '../../lib/v1/dataProducts';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "Hộp thoại · importCatalog" (2095:9055): use products another Data Space publishes. Nothing is
 * copied: each picked product becomes an access request its owner decides, so a product carrying
 * personal data is flagged before sending.
 */
export function ImportCatalogDialog({
  open,
  onClose,
  onRequested,
}: {
  open: boolean;
  onClose: () => void;
  onRequested: (count: number) => void;
}) {
  const catalog = useV1(['listCatalogProducts', open], open ? listCatalogProducts : null);
  const spaces = useV1(['listDataSpaces', open], open ? listDataSpaces : null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [space, setSpace] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    if (!open) return;
    setPicked(new Set());
    setError(undefined);
  }, [open]);

  const spaceOptions = (spaces.data?.items ?? []).map((s) => ({ value: s.id, label: s.name }));
  // The first Data Space is the default target until the user picks another.
  const target = space ?? spaceOptions[0]?.value ?? null;
  const products = catalog.data?.items ?? [];
  const withPii = products.filter((p) => p.has_pii && picked.has(p.id));

  const toggle = (id: string, on: boolean) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });

  const submit = async () => {
    if (!target) return;
    setSending(true);
    setError(undefined);
    try {
      const { requests } = await requestProductUse({
        product_ids: [...picked],
        data_space_id: target,
      });
      onRequested(requests.length);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  return (
    <Dialog
      open={open}
      title="Nhập sản phẩm từ catalog"
      description="Dùng sản phẩm dữ liệu của Data Space khác; quyền theo chủ sở hữu."
      submitLabel="Yêu cầu dùng"
      submitting={sending}
      canSubmit={picked.size > 0 && target !== null}
      error={error}
      onSubmit={submit}
      onClose={onClose}
    >
      <AsyncRegion
        state={catalog}
        what="catalog sản phẩm"
        skeletonRows={2}
        isEmpty={(page) => page.items.length === 0}
        empty={
          <EmptyState
            icon="layers"
            title="Catalog trống"
            description="Chưa Data Space nào khác xuất bản sản phẩm dữ liệu."
          />
        }
      >
        {(page) => (
          <div className="flex flex-col gap-da-sm">
            {page.items.map((p) => (
              <div key={p.id} className="rounded-md border border-border px-da-md py-[7px]">
                <Checkbox checked={picked.has(p.id)} onChange={(on) => toggle(p.id, on)}>
                  {`${p.code} · ${p.owner_space.name} · ${p.version}`}
                </Checkbox>
              </div>
            ))}
          </div>
        )}
      </AsyncRegion>
      <Field label="Dùng cho" labelTone="muted">
        <Select<string>
          label="Dùng cho"
          options={spaceOptions}
          value={target}
          onChange={setSpace}
          disabled={spaceOptions.length === 0}
        />
      </Field>
      {withPii.map((p) => (
        <Notice key={p.id} tone="info">
          {`${p.code} chứa PII; quyền đọc cần chủ sở hữu ${p.owner_space.name} duyệt.`}
        </Notice>
      ))}
    </Dialog>
  );
}
