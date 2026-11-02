import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { TurnForTest as AnswerBlock } from '../chat/turnForTest';
import { AnswerTheme } from './AnswerTheme';
import { ExportDialog } from './ExportDialog';
import { ReportDialog } from './ReportDialog';
import type { AskMessage, EvidenceRun } from '../types';
import type { reportAnswer as reportAnswerApi } from '../../../lib/v1/answerReports';

const reportAnswer = vi.fn<typeof reportAnswerApi>();

const run: EvidenceRun = {
  id: 'r1', kind: 'sql', sql: 'SELECT 1', generated_by: 'semantic_layer', trust: 'GOVERNED',
  metric: { metric: 'npl_ratio@4', dimensions: ['vung'], filters: ['ban_le'] },
  provenance: { status: 'fresh', as_of: '2026-09-30T23:59:00' },
  columns: ['vung', 'npl'], rows: [{ vung: 'DH Bắc Bộ', npl: 2.32 }], row_count: 1,
};

const msg: AskMessage = {
  id: 'a1', role: 'assistant', content: 'Nợ xấu bán lẻ T9 là 1,84%.', timestamp: '2026-10-08T10:52:00',
  status: 'done', evidence: { runs: [run], clarifications: [], warnings: [] }, latencyMs: 2100,
};

const wrap = (ui: React.ReactElement) => render(<AnswerTheme mode="light">{ui}</AnswerTheme>);

beforeEach(() => reportAnswer.mockReset());

describe('answer meta row', () => {
  it('labels the answer with the trust its runs carry', () => {
    // How the question was read is the understand step's detail now (StepTimeline).
    wrap(<AnswerBlock msg={msg} />);
    expect(screen.getByTestId('trust-label')).toHaveTextContent('Có quản trị');
  });

  it('labels an answer with no data behind it, instead of leaving it unlabelled', () => {
    wrap(<AnswerBlock msg={{ ...msg, evidence: undefined }} />);
    expect(screen.getByTestId('trust-label')).toHaveTextContent('Chưa đủ bằng chứng');
  });

  it('offers Báo sai only on an answer the server keeps', () => {
    // "Cách tính" is the step timeline now: each step opens to what it did.
    const onReport = vi.fn();
    const { unmount } = wrap(<AnswerBlock msg={msg} />);
    expect(screen.queryByRole('button', { name: 'Báo sai' })).toBeNull();
    unmount();

    wrap(<AnswerBlock msg={msg} onReport={onReport} />);
    fireEvent.click(screen.getByRole('button', { name: 'Báo sai' }));
    expect(onReport).toHaveBeenCalledOnce();
  });

  it('offers Lưu thành tri thức only where the screen handles it', () => {
    const onSaveKnowledge = vi.fn();
    const { unmount } = wrap(<AnswerBlock msg={msg} />);
    expect(screen.queryByRole('button', { name: 'Thêm thao tác với câu trả lời' })).toBeNull();
    unmount();

    wrap(<AnswerBlock msg={msg} onSaveKnowledge={onSaveKnowledge} />);
    fireEvent.click(screen.getByRole('button', { name: 'Thêm thao tác với câu trả lời' }));
    fireEvent.click(screen.getByRole('menuitem', { name: /Lưu vào tri thức/ }));
    expect(onSaveKnowledge).toHaveBeenCalledOnce();
  });

  it('offers Biên nhận only where the screen handles it', () => {
    const onOpenReceipt = vi.fn();
    const { unmount } = wrap(<AnswerBlock msg={msg} />);
    expect(screen.queryByRole('button', { name: 'Biên nhận' })).toBeNull();
    unmount();

    wrap(<AnswerBlock msg={msg} onOpenReceipt={onOpenReceipt} />);
    fireEvent.click(screen.getByRole('button', { name: 'Biên nhận' }));
    expect(onOpenReceipt).toHaveBeenCalledOnce();
  });

  it('marks a reported answer as under review', () => {
    wrap(<AnswerBlock msg={msg} onReport={vi.fn()} reported />);
    expect(screen.getByText('Đang kiểm tra')).toBeInTheDocument();
    // Reported once is enough: the action is gone until the report is reviewed.
    expect(screen.queryByRole('button', { name: 'Báo sai' })).toBeNull();
  });

  it('opens the export dialog from Xuất', () => {
    wrap(<AnswerBlock msg={{ ...msg, answerId: 'answer-1' }} />);
    fireEvent.click(screen.getByRole('button', { name: 'Thêm thao tác với câu trả lời' }));
    fireEvent.click(screen.getByRole('menuitem', { name: /Xuất/ }));
    expect(screen.getByRole('dialog', { name: 'Xuất kết quả' })).toBeInTheDocument();
    // A kept answer can also be exported by the server (xlsx), offered first; CSV stays.
    expect(screen.getByRole('radio', { name: /CSV · bảng xem trước/ })).toBeInTheDocument();
  });
});

describe('ExportDialog', () => {
  it('offers only the formats that exist and exports the chosen one', async () => {
    const onExportCsv = vi.fn();
    const onExportXlsx = vi.fn().mockResolvedValue(true);
    const onClose = vi.fn();
    render(<ExportDialog open run={run} onClose={onClose} onExportCsv={onExportCsv} onExportXlsx={onExportXlsx} />);

    expect(screen.getAllByRole('radio')).toHaveLength(2);
    expect(screen.getByRole('radio', { name: /Excel · toàn bộ kết quả/ })).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: 'Xuất' }));
    await waitFor(() => expect(onExportXlsx).toHaveBeenCalledOnce());
    expect(onExportCsv).not.toHaveBeenCalled();
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it('keeps the dialog open with the reason when the export fails', async () => {
    const onClose = vi.fn();
    render(<ExportDialog open run={run} onClose={onClose} onExportXlsx={vi.fn().mockRejectedValue(new Error('500'))} />);
    fireEvent.click(screen.getByRole('button', { name: 'Xuất' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Không xuất được: 500');
    expect(onClose).not.toHaveBeenCalled();
  });

  it('closes on Esc', () => {
    const onClose = vi.fn();
    render(<ExportDialog open run={run} onClose={onClose} onExportCsv={vi.fn()} />);
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(onClose).toHaveBeenCalledOnce();
  });
});

describe('ReportDialog', () => {
  const target = { answerId: 'a1', question: 'Nợ xấu bán lẻ tăng ở đâu?', answer: msg.content };

  it('sends what is wrong and the correction, then reports back', async () => {
    reportAnswer.mockResolvedValue({ id: 'rep1', answer_id: 'a1', status: 'open' } as unknown as Awaited<ReturnType<typeof reportAnswerApi>>);
    const onReported = vi.fn();
    const onClose = vi.fn();
    render(<ReportDialog open target={target} onClose={onClose} onReported={onReported} submitReport={reportAnswer} />);

    expect(screen.getByRole('radio', { name: 'Con số sai' })).toBeChecked();
    fireEvent.click(screen.getByRole('radio', { name: /Hiểu sai câu hỏi/ }));
    fireEvent.change(screen.getByPlaceholderText(/Ví dụ: 1,86%/), { target: { value: '1,86%' } });
    fireEvent.click(screen.getByRole('button', { name: 'Gửi báo sai' }));

    await waitFor(() => expect(onReported).toHaveBeenCalledWith(expect.objectContaining({ id: 'rep1' })));
    expect(reportAnswer).toHaveBeenCalledWith('a1', {
      kind: 'misread_question', correction: '1,86%', sendToSteward: true,
    });
    expect(onClose).toHaveBeenCalled();
  });

  it('shows why it could not send and stays open', async () => {
    const submitReport = vi.fn().mockRejectedValue(new Error('mất kết nối'));
    const onClose = vi.fn();
    render(<ReportDialog open target={target} onClose={onClose} onReported={vi.fn()} submitReport={submitReport} />);
    fireEvent.click(screen.getByRole('button', { name: 'Gửi báo sai' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Chưa gửi được: mất kết nối');
    expect(onClose).not.toHaveBeenCalled();
  });
});
