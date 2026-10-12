import { useEffect, useState } from 'react';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { Field } from '../../design-system/Field';
import { Select } from '../../design-system/Select';
import { useToast } from '../../design-system/Toast';
import { listDataSpaces } from '../../lib/v1/api';
import { shareSkill, type Skill } from '../../lib/v1/skills';
import { useV1 } from '../../lib/v1/useV1';

/*
 * UC-03-07 "Chia sẻ cho Data Space khác": a certified skill to one Data Space or the whole
 * organisation. The receiver sees name, description, parameters and test results and runs it
 * under its own data permissions; organisation-wide sharing waits for an administrator.
 */
const ORGANIZATION = 'organization';

export function ShareSkillDialog({ skill, onClose }: { skill: Skill | null; onClose: () => void }) {
  const toast = useToast();
  const spaces = useV1(skill ? listDataSpaces : null, [skill?.id]);
  const [target, setTarget] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    setTarget(null);
    setError(undefined);
  }, [skill?.id]);

  const options = [
    ...(spaces.data?.items ?? [])
      .filter((s) => s.status === 'active')
      .map((s) => ({ value: s.id, label: s.name })),
    { value: ORGANIZATION, label: 'Toàn tổ chức' },
  ];

  const confirm = () => {
    if (!skill) return;
    if (!target) {
      setError('Chọn nơi chia sẻ.');
      return;
    }
    setBusy(true);
    shareSkill(skill.id, target === ORGANIZATION ? null : target)
      .then((result) => {
        toast({
          tone: 'success',
          message:
            result.status === 'pending_admin'
              ? `Đã gửi quản trị viên duyệt chia sẻ ${skill.name}`
              : `Đã chia sẻ ${skill.name}`,
        });
        onClose();
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <ConfirmDialog
      open={skill !== null}
      size="md"
      tone="primary"
      title={`Chia sẻ ${skill?.name ?? 'kỹ năng'}`}
      confirmLabel="Chia sẻ"
      loading={busy}
      error={error}
      onConfirm={confirm}
      onCancel={onClose}
    >
      <div className="flex flex-col gap-da-md">
        <p>
          Bên nhận thấy tên, mô tả, tham số và kết quả kiểm định; kỹ năng chạy theo quyền dữ liệu
          của người dùng bên nhận. Chia sẻ toàn tổ chức cần quản trị viên duyệt.
        </p>
        <Field label="Chia sẻ cho">
          <Select
            label="Chia sẻ cho"
            options={options}
            value={target}
            onChange={(value) => {
              setTarget(value);
              setError(undefined);
            }}
          />
        </Field>
      </div>
    </ConfirmDialog>
  );
}
