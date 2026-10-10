import { useEffect, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Checkbox } from '../../design-system/Checkbox';
import { Dialog } from '../../design-system/Dialog';
import { Select } from '../../design-system/Select';
import { getSessionAgentDraft, type SessionAgentDraft } from '../../lib/v1/skillAuthoring';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "Biến phiên thành agent" (2070:3988 in "v3.1 · Chi tiết · Biến phiên thành agent", UC-03-03,
 * UC-13-01), opened from "Biến thành agent" in a session notebook. The cells become steps (text
 * cells are left out by default) and each run gets its own parameters. Two exits: "Lưu thành kỹ
 * năng" goes to "P03 · Tạo kỹ năng từ phiên"; "Tạo agent nháp" hands the job over to "Tạo agent
 * từ mô tả" (agents/BUILDER) as a description in ?description=, where the config agent drafts the
 * agent and a draft must pass a test run before it is activated.
 * The frame is 760px with the two lists side by side (Dialog size lg).
 */
export function SessionToAgentDialog({
  sessionId,
  open,
  onClose,
}: {
  sessionId: string;
  open: boolean;
  onClose: () => void;
}) {
  const draft = useV1(open ? () => getSessionAgentDraft(sessionId) : null, [open, sessionId]);
  return (
    <Dialog
      open={open}
      title="Biến phiên thành agent"
      description="Agent sẽ lặp lại các bước bạn đã làm trong phiên này, với tham số thay đổi theo mỗi lần chạy."
      size="lg"
      onClose={onClose}
      footer={draft.data ? null : <CloseOnly onClose={onClose} />}
    >
      <AsyncRegion state={draft} what="các ô của phiên">
        {(d) => <DraftForm key={d.session_id} draft={d} onClose={onClose} />}
      </AsyncRegion>
    </Dialog>
  );
}

function CloseOnly({ onClose }: { onClose: () => void }) {
  return (
    <Button kind="ghost" onClick={onClose}>
      Đóng
    </Button>
  );
}

function DraftForm({ draft, onClose }: { draft: SessionAgentDraft; onClose: () => void }) {
  const { navigate } = useAppState();
  const [kept, setKept] = useState(
    () => new Set(draft.cells.filter((c) => c.kept).map((c) => c.seq)),
  );
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(draft.params.map((p) => [p.key, p.value])),
  );
  const [error, setError] = useState<string>();
  useEffect(() => setError(undefined), [kept]);

  const toggle = (seq: number, on: boolean) =>
    setKept((prev) => {
      const next = new Set(prev);
      if (on) next.add(seq);
      else next.delete(seq);
      return next;
    });

  const createAgent = () => {
    const steps = draft.cells.filter((c) => kept.has(c.seq));
    if (steps.length === 0) return setError('Chọn ít nhất một ô làm bước.');
    const description = [
      `Lặp lại phiên “${draft.session_title}” theo các bước: ${steps.map((c) => c.label).join('; ')}.`,
      ...draft.params.map((p) => `${p.label}: ${values[p.key]}.`),
    ].join(' ');
    const query = new URLSearchParams({ description, from_session: draft.session_id });
    window.history.replaceState(window.history.state, '', `${window.location.pathname}?${query}`);
    onClose();
    navigate('agents', 'BUILDER');
  };

  return (
    <>
      <div className="grid grid-cols-2 gap-da-xl">
        <div className="flex min-w-0 flex-col gap-da-sm">
          <span className="text-muted-foreground da-label">Các ô sẽ thành bước</span>
          {draft.cells.map((c) => (
            <Checkbox key={c.seq} checked={kept.has(c.seq)} onChange={(v) => toggle(c.seq, v)}>
              <span className={kept.has(c.seq) ? 'text-foreground' : 'text-muted-foreground'}>
                {c.label}
              </span>
            </Checkbox>
          ))}
        </div>
        <div className="flex min-w-0 flex-col gap-da-sm">
          <span className="text-muted-foreground da-label">Tham số mỗi lần chạy</span>
          {draft.params.map((p) => (
            <div
              key={p.key}
              className="flex items-center gap-da-md border-b border-border px-da-md py-da-sm"
            >
              <span aria-hidden className="w-[80px] shrink-0 text-muted-foreground da-caption">
                {p.label}
              </span>
              <Select
                className="min-w-0 flex-1"
                label={p.label}
                options={p.options.map((o) => ({ value: o, label: o }))}
                value={values[p.key] ?? null}
                onChange={(v) => setValues((prev) => ({ ...prev, [p.key]: v }))}
              />
            </div>
          ))}
        </div>
      </div>
      {error && <p className="text-error da-caption">{error}</p>}
      <div className="flex items-center gap-da-sm">
        <span className="min-w-0 flex-1 text-muted-foreground da-caption">
          Agent nháp cần chạy thử trước khi kích hoạt
        </span>
        <Button
          kind="ghost"
          onClick={() => {
            onClose();
            navigate('agents', 'SKILL_FROM_SESSION', draft.session_id);
          }}
        >
          Lưu thành kỹ năng
        </Button>
        <Button kind="primary" onClick={createAgent}>
          Tạo agent nháp
        </Button>
      </div>
    </>
  );
}
