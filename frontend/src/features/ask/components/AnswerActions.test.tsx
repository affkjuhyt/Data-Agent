import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { AnswerBlock } from './AnswerBlock';
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
  it('shows how the question was read and a trust label on the answer', () => {
    wrap(<AnswerBlock msg={msg} />);
    const reading = screen.getByRole('list', { name: 'Hệ thống hiểu là' });
    expect(within(reading).getByText('npl_ratio v4')).toBeInTheDocument();
    expect(within(reading).getByText('ban_le')).toBeInTheDocument();
    expect(within(reading).getByText('vung')).toBeInTheDocument();
    expect(screen.getByTestId('trust-label')).toHaveTextContent('Có quản trị');
  });

  it('labels an answer with no data behind it, instead of leaving it unlabelled', () => {
    wrap(<AnswerBlock msg={{ ...msg, evidence: undefined }} />);
    expect(screen.getByTestId('trust-label')).toHaveTextContent('Chưa đủ bằng chứng');
  });

  it('offers Cách tính and Báo sai only where the screen handles them', () => {
    const onOpen = vi.fn();
    const onReport = vi.fn();
    const { unmount } = wrap(<AnswerBlock msg={msg} />);
    expect(screen.queryByRole('button', { name: 'Cách tính' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Báo sai' })).toBeNull();
    unmount();

    wrap(<AnswerBlock msg={msg} onOpenEvidence={onOpen} onReport={onReport} />);
    fireEvent.click(screen.getByRole('button', { name: 'Cách tính' }));
    fireEvent.click(screen.getByRole('button', { name: 'Báo sai' }));
    expect(onOpen).toHaveBeenCalledOnce();
    expect(onReport).toHaveBeenCalledOnce();
  });

  it('offers Lưu thành tri thức only where the screen handles it', () => {
    const onSaveKnowledge = vi.fn();
    const { unmount } = wrap(<AnswerBlock msg={msg} />);
    expect(screen.queryByRole('button', { name: 'Lưu thành tri thức' })).toBeNull();
    unmount();

    wrap(<AnswerBlock msg={msg} onSaveKnowledge={onSaveKnowledge} />);
    fireEvent.click(screen.getByRole('button', { name: 'Lưu thành tri thức' }));
    expect(onSaveKnowledge).toHaveBeenCalledOnce();
  });

  it('marks a reported answer as under review', () => {
    wrap(<AnswerBlock msg={msg} onReport={vi.fn()} reported />);
    expect(screen.getByText('Đang kiểm tra')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Báo sai' })).toBeDisabled();
  });

  it('opens the export dialog from Xuất', () => {
    wrap(<AnswerBlock msg={msg} />);
    fireEvent.click(screen.getByRole('button', { name: 'Xuất' }));
    expect(screen.getByRole('dialog', { name: 'Xuất kết quả' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /CSV · bảng xem trước/ })).toBeChecked();
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
  const target = { messageId: 'a1', sessionId: 's1', question: 'Nợ xấu bán lẻ tăng ở đâu?', answer: msg.content };

  it('sends what is wrong and the correction, then reports back', async () => {
    reportAnswer.mockResolvedValue({ id: 'rep1', message_id: 'a1', status: 'under_review' } as Awaited<ReturnType<typeof reportAnswerApi>>);
    const onReported = vi.fn();
    const onClose = vi.fn();
    render(<ReportDialog open target={target} onClose={onClose} onReported={onReported} submitReport={reportAnswer} />);

    expect(screen.getByRole('radio', { name: 'Con số sai' })).toBeChecked();
    fireEvent.click(screen.getByRole('radio', { name: /Hiểu sai câu hỏi/ }));
    fireEvent.change(screen.getByPlaceholderText(/Ví dụ: 1,86%/), { target: { value: '1,86%' } });
    fireEvent.click(screen.getByRole('button', { name: 'Gửi báo sai' }));

    await waitFor(() => expect(onReported).toHaveBeenCalledWith(expect.objectContaining({ id: 'rep1' })));
    expect(reportAnswer).toHaveBeenCalledWith({
      session_id: 's1', message_id: 'a1', question: target.question, answer: target.answer,
      kind: 'misread_question', correction: '1,86%', send_to_steward: true,
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
