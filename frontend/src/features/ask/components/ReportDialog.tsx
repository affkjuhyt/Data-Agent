/**
 * "Báo câu trả lời sai" (UC-01-05, 2239:15535): what is wrong, the correct number or reading,
 * and whether to send it to the data steward. The report goes into review (Cần bạn); it never
 * changes the number by itself.
 */
import { useEffect, useState } from 'react';
import { Checkbox } from '../../../design-system/Checkbox';
import { Dialog } from '../../../design-system/Dialog';
import { Input } from '../../../design-system/Input';
import { Radio } from '../../../design-system/Radio';
import { reportAnswer, type AnswerReportKind, type Feedback } from '../../../lib/v1/answerReports';

const KINDS: Array<{ value: AnswerReportKind; label: string }> = [
  { value: 'misread_question', label: 'Hiểu sai câu hỏi (chỉ số, phạm vi, kỳ)' },
  { value: 'wrong_number', label: 'Con số sai' },
  { value: 'wrong_source', label: 'Dùng sai nguồn hoặc sai thời điểm dữ liệu' },
  { value: 'incomplete', label: 'Thiếu thông tin hoặc giải thích chưa đúng' },
];

const EXCERPT_MAX = 220;

export function ReportDialog({
  open,
  target,
  onClose,
  onReported,
  submitReport = reportAnswer,
}: {
  open: boolean;
  /** the server's answer id: a report is feedback on that answer (A2 · Hỏi đáp) */
  target: { answerId: string; question: string; answer: string } | null;
  onClose: () => void;
  onReported: (feedback: Feedback) => void;
  /** the endpoint call; tests pass their own */
  submitReport?: typeof reportAnswer;
}) {
  const [kind, setKind] = useState<AnswerReportKind>('wrong_number');
  const [correction, setCorrection] = useState('');
  const [toSteward, setToSteward] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setKind('wrong_number');
    setCorrection('');
    setToSteward(true);
    setError(null);
  }, [open]);

  const submit = async () => {
    if (!target || busy) return;
    setBusy(true);
    setError(null);
    try {
      const feedback = await submitReport(target.answerId, {
        kind,
        correction: correction.trim() || undefined,
        sendToSteward: toSteward,
      });
      onReported(feedback);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? `Chưa gửi được: ${e.message}` : 'Chưa gửi được báo sai.');
    } finally {
      setBusy(false);
    }
  };

  const excerpt = target
    ? target.answer.length > EXCERPT_MAX ? `${target.answer.slice(0, EXCERPT_MAX)}…` : target.answer
    : '';

  return (
    <Dialog
      open={open}
      title="Báo câu trả lời sai"
      onClose={onClose}
      onSubmit={() => void submit()}
      submitLabel="Gửi báo sai"
      submitting={busy}
      error={error ?? undefined}
    >
      {excerpt && (
        <blockquote className="m-0 rounded-sm bg-subtle p-[10px] text-muted-foreground da-caption">“{excerpt}”</blockquote>
      )}
      <fieldset className="m-0 flex flex-col gap-da-lg border-0 p-0">
        <legend className="mb-da-lg text-foreground da-body-strong">Sai ở đâu?</legend>
        {KINDS.map(option => (
          <Radio key={option.value} name="ask-report-kind" value={option.value} checked={kind === option.value} onChange={v => setKind(v as AnswerReportKind)}>
            {option.label}
          </Radio>
        ))}
      </fieldset>
      <label className="flex flex-col gap-da-lg">
        <span className="text-foreground da-body-strong">Con số hoặc cách hiểu đúng</span>
        <Input
          value={correction}
          onChange={e => setCorrection(e.target.value)}
          placeholder="Ví dụ: 1,86% theo số đã nộp ngày 05/10"
        />
      </label>
      <Checkbox checked={toSteward} onChange={setToSteward}>
        Gửi người phụ trách dữ liệu xem; nếu đúng, thêm vào bộ câu hỏi chuẩn
      </Checkbox>
      <p className="m-0 text-muted-foreground da-caption">
        Câu trả lời được gắn nhãn “đang kiểm tra” cho mọi người đã xem. Bạn nhận kết quả trong Trung tâm thông báo.
      </p>
    </Dialog>
  );
}
