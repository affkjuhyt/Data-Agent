import { useState } from 'react';
import { ConfirmDialog } from '../../design-system/ConfirmDialog';
import { Select } from '../../design-system/Select';
import { useToast } from '../../design-system/Toast';
import {
  getKnowledgeDraft,
  listKnowledgePages,
  saveAnswerToKnowledge,
} from '../../lib/v1/knowledge';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "P07 · Lưu câu trả lời thành tri thức" (2405:15399, UC-07-12): a dialog opened from an answer.
 * It shows the answer and the target page the knowledge index suggests (changeable), and sends it
 * for review. The server turns the answer into a paragraph whose figures are placeholders bound
 * to the query rather than fixed numbers (BR-162); nothing is edited here, since changing the
 * words could unbind a figure — the reviewer edits the page version instead.
 */
const box =
  'rounded-sm border border-border-strong bg-card px-[10px] py-da-md text-foreground da-body-small';

export function SaveToKnowledgeDialog({
  answerId,
  onClose,
}: {
  answerId: string;
  onClose: () => void;
}) {
  const toast = useToast();
  const draft = useV1(() => getKnowledgeDraft(answerId), [answerId]);
  const pages = useV1(listKnowledgePages, []);
  const [target, setTarget] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const proposed = draft.data?.target_page ?? null;
  const targetId = target ?? proposed?.id ?? null;
  const options = [
    ...(proposed ? [{ value: proposed.id, label: proposed.title }] : []),
    ...(pages.data?.items ?? [])
      .filter((p) => p.id !== proposed?.id)
      .map((p) => ({ value: p.id, label: p.title })),
  ];

  const send = async () => {
    if (!targetId) {
      setError('Chọn trang đích.');
      return;
    }
    setBusy(true);
    setError(undefined);
    try {
      await saveAnswerToKnowledge({
        answer_id: answerId,
        mode: 'paragraph',
        target_page_id: targetId,
      });
      toast({ tone: 'success', message: 'Đã gửi đề xuất; người phụ trách trang sẽ duyệt' });
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <ConfirmDialog
      open
      title="Lưu thành tri thức"
      confirmLabel="Gửi đề xuất"
      cancelLabel="Huỷ"
      tone="primary"
      size="md"
      loading={busy}
      error={
        error ?? (draft.error ? `Không đọc được câu trả lời: ${draft.error.message}` : undefined)
      }
      onCancel={onClose}
      onConfirm={send}
    >
      <div className="flex flex-col gap-[14px]">
        <p>
          Câu trả lời được đề xuất thành đoạn trên một trang tri thức. Số trong câu trả lời được giữ
          dưới dạng chỗ giữ số ràng buộc tới truy vấn, không lưu con số cố định.
        </p>
        <div className="flex flex-col gap-da-xs">
          <span className="text-muted-foreground da-caption">Trang đích</span>
          <Select
            label="Trang đích"
            value={targetId}
            onChange={setTarget}
            placeholder="Chọn trang…"
            options={options}
          />
        </div>
        {draft.data && <Field label="Câu trả lời" value={draft.data.answer_text} />}
        {/* StatusDot does not wrap; this sentence is longer than one line of the dialog. */}
        <p className="flex items-start gap-[6px] text-foreground da-body-small">
          <span className="mt-[6px] size-[7px] shrink-0 rounded-full bg-info" aria-hidden />
          Phiên bản mới ở trạng thái nháp; người phụ trách khác bạn duyệt rồi mới được phục vụ.
        </p>
      </div>
    </ConfirmDialog>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-da-xs">
      <span className="text-muted-foreground da-caption">{label}</span>
      <p className={box}>{value}</p>
    </div>
  );
}
