import { useState } from 'react';
import { Dialog } from '../../design-system/Dialog';
import { Field } from '../../design-system/Field';
import { Input } from '../../design-system/Input';
import { Select } from '../../design-system/Select';
import { createPack, type IndustryPack } from '../../lib/v1/packs';

/*
 * "Hộp thoại · createPack" (2095:8808): the organisation's own domain pack, from an existing pack
 * version or from its current model. Creating it opens the first draft in Biên soạn gói. The pack
 * stays in the organisation: PackCreate has no sharing with other tenants, so the dialog does not
 * offer it.
 */
const CURRENT = '__current_model__';
const DEFAULT_INCLUDES = 'Thực thể, chỉ số, từ điển, kỹ năng, agent mẫu';

export function CreatePackDialog({
  open,
  packs,
  onClose,
  onCreated,
}: {
  open: boolean;
  packs: IndustryPack[];
  onClose: () => void;
  onCreated: (draftVersionId: string) => void;
}) {
  const [name, setName] = useState('');
  // Only a pack whose version is known can be a base (GET /v1/packs lists no version yet).
  const bases = packs.flatMap((p) => (p.latest ? [{ name: p.name, ...p.latest }] : []));
  const [base, setBase] = useState<string>(bases[0]?.version_id ?? CURRENT);
  const [includes, setIncludes] = useState(DEFAULT_INCLUDES);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();

  const submit = async () => {
    setSaving(true);
    setError(undefined);
    try {
      const created = await createPack({
        name: name.trim(),
        based_on_version_id: base === CURRENT ? null : base,
        includes: includes
          .split(',')
          .map((part) => part.trim())
          .filter(Boolean),
      });
      onCreated(created.draft_version_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog
      open={open}
      title="Tạo gói domain riêng"
      description="Bắt đầu từ một gói có sẵn hoặc từ mô hình hiện tại của tổ chức."
      onClose={onClose}
      onSubmit={submit}
      submitLabel="Tạo gói"
      submitting={saving}
      canSubmit={Boolean(name.trim() && includes.trim())}
      error={error}
    >
      <div className="flex flex-col gap-da-lg">
        <Field label="Tên gói" labelTone="muted">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Tín dụng tiêu dùng · nội bộ"
          />
        </Field>
        <Field label="Dựa trên" labelTone="muted">
          <Select
            label="Dựa trên"
            value={base}
            onChange={setBase}
            options={[
              ...bases.map((b) => ({
                value: b.version_id,
                label: `${b.name} ${b.version}`,
              })),
              { value: CURRENT, label: 'Mô hình hiện tại của tổ chức' },
            ]}
          />
        </Field>
        <Field label="Gồm" labelTone="muted">
          <Input value={includes} onChange={(e) => setIncludes(e.target.value)} />
        </Field>
      </div>
    </Dialog>
  );
}
