import { useState } from 'react';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Dialog } from '../../design-system/Dialog';
import { Select } from '../../design-system/Select';
import { listWorkspaces } from '../../lib/v1/api';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "Lưu vào phiên": the design has no dashboards; a result or an answer is kept as a tile of a
 * session (P02, POST /v1/workspaces/{id}/tiles). The caller does the save for the chosen session.
 */
export function SaveToSessionDialog({
  onClose,
  onSave,
  onSaved,
}: {
  onClose: () => void;
  onSave: (workspaceId: string) => Promise<unknown>;
  onSaved: (workspaceId: string, title: string) => void;
}) {
  const sessions = useV1(['listWorkspaces'], listWorkspaces);
  const [target, setTarget] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<string>();
  const save = async () => {
    if (!target) return;
    setBusy(true);
    try {
      await onSave(target);
      onSaved(target, sessions.data?.items.find((w) => w.id === target)?.title ?? 'phiên');
    } catch (e) {
      setFailure((e as Error).message);
      setBusy(false);
    }
  };
  return (
    <Dialog
      open
      title="Lưu vào phiên"
      onClose={onClose}
      footer={
        <>
          <Button kind="ghost" onClick={onClose}>
            Huỷ
          </Button>
          <Button kind="primary" disabled={!target} loading={busy} onClick={() => void save()}>
            Lưu
          </Button>
        </>
      }
    >
      <AsyncRegion state={sessions} what="danh sách phiên" skeletonRows={2}>
        {(page) => (
          <Select
            label="Phiên đích"
            placeholder="Chọn phiên…"
            value={target}
            onChange={setTarget}
            options={page.items.map((w) => ({ value: w.id, label: w.title }))}
          />
        )}
      </AsyncRegion>
      {failure && <p className="text-error da-caption">Không lưu được: {failure}</p>}
    </Dialog>
  );
}
