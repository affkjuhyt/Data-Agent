import { useState } from 'react';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { Notice } from '../../design-system/Notice';
import { Toggle } from '../../design-system/Toggle';
import { useToast } from '../../design-system/Toast';
import { useAuth } from '../../lib/AuthProvider';
import { getOrganization, setSelfApproval, type Organization } from '../../lib/v1/adminAccess';
import { useV1 } from '../../lib/v1/useV1';

/*
 * The organisation's self-approval switch (org.organization.allow_self_approval), on top of
 * "Người dùng và vai trò". Off, every approval needs someone other than the creator; on, the
 * creator may approve their own work and each time is written to the audit log. Everyone may see
 * it; only an organisation-wide `admin` (GET /v1/me, as the shell's rail reads it) turns it.
 * Turning it on widens who may approve, so it is confirmed first; turning it off only narrows
 * that and applies at once. Either change is acknowledged with a toast.
 */
export function SelfApprovalSwitch() {
  const organization = useV1(['getOrganization'], getOrganization);
  return (
    <AsyncRegion state={organization} what="thiết lập tự duyệt" skeletonRows={1}>
      {(org) => <Switch organization={org} onChanged={organization.reload} />}
    </AsyncRegion>
  );
}

function Switch({ organization, onChanged }: { organization: Organization; onChanged: () => void }) {
  const toast = useToast();
  const { me } = useAuth();
  const [saving, setSaving] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState<string>();
  const isAdmin = me?.capabilities.includes('admin') ?? false;

  const turn = async (on: boolean) => {
    setSaving(true);
    setError(undefined);
    try {
      await setSelfApproval(on);
      toast({
        tone: 'success',
        message: on ? 'Đã bật tự duyệt cho tổ chức' : 'Đã tắt tự duyệt cho tổ chức',
      });
      setConfirming(false);
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col gap-da-xs">
      <div className="flex items-start gap-[10px] rounded-md bg-subtle px-[10px] py-da-md">
        <Toggle
          checked={organization.allow_self_approval}
          disabled={!isAdmin || saving}
          label="Cho phép tự duyệt"
          onChange={(on) => (on ? setConfirming(true) : turn(false))}
        />
        <div className="flex min-w-0 flex-1 flex-col gap-da-xs">
          <span className="text-foreground da-body-strong">Cho phép tự duyệt</span>
          <span className="text-muted-foreground da-body-small">
            Khi bật, người tạo được tự duyệt việc của mình. Mỗi lần tự duyệt được ghi vào nhật ký
            kiểm toán.
          </span>
        </div>
        <span className="text-muted-foreground da-caption">
          {organization.allow_self_approval ? 'Đang bật' : 'Đang tắt'}
        </span>
      </div>
      {error && !confirming && <Notice tone="error">Không đổi được: {error}</Notice>}
      <ConfirmDialog
        open={confirming}
        title="Bật tự duyệt cho tổ chức?"
        confirmLabel="Bật tự duyệt"
        tone="primary"
        loading={saving}
        error={error && `Không đổi được: ${error}`}
        onCancel={() => {
          setConfirming(false);
          setError(undefined);
        }}
        onConfirm={() => turn(true)}
      >
        Từ giờ người tạo được tự duyệt việc của mình, không cần người thứ hai. Mỗi lần tự duyệt
        được ghi vào nhật ký kiểm toán.
      </ConfirmDialog>
    </div>
  );
}
