/**
 * "Báo câu trả lời sai" (UC-01-05, 2239:15535): what is wrong, the correct number or reading,
 * and whether to send it to the data steward. The report goes into review (Cần bạn); it never
 * changes the number by itself.
 */
import { useEffect, useState } from 'react';
import { Button } from '../../../design-system/Button';
import { Checkbox } from '../../../design-system/Checkbox';
import { Input } from '../../../design-system/Input';
import { Radio } from '../../../design-system/Radio';
import { reportAnswer, type AnswerReport, type AnswerReportKind } from '../../../lib/v1/answerReports';
import { AskDialog } from './AskDialog';

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
  target: { messageId: string; sessionId?: string; question: string; answer: string } | null;
  onClose: () => void;
  onReported: (report: AnswerReport) => void;
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
      const report = await submitReport({
        session_id: target.sessionId,
        message_id: target.messageId,
        question: target.question,
        answer: target.answer,
        kind,
        correction: correction.trim() || undefined,
        send_to_steward: toSteward,
      });
      onReported(report);
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
    <AskDialog
      open={open}
      title="Báo câu trả lời sai"
      onClose={onClose}
      footer={
        <>
          <span className="flex-1" />
          <Button kind="ghost" onClick={onClose}>Huỷ</Button>
          <Button kind="primary" onClick={submit} loading={busy}>Gửi báo sai</Button>
        </>
      }
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
      {error && <p role="alert" className="m-0 text-error da-caption">{error}</p>}
    </AskDialog>
  );
}
