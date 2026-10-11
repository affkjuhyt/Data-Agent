import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import AskDataPage from './AskDataPage';
import type { AskMessage } from './types';

const send = vi.fn();
const navigate = vi.fn();
let messages: AskMessage[] = [];

vi.mock('../../AppStateContext', () => ({
  useAppState: () => ({ user: { id: 'u1', name: 'Đỗ Hải Yến' }, navigate }),
}));
vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./api')>()),
  fetchAgents: () => Promise.resolve([{ id: 'ag1', label: 'Agent Phân tích', description: 'Phân tích nợ xấu' }]),
}));
vi.mock('./hooks/useAgentStream', () => ({
  useAgentStream: () => ({ messages, sending: false, send, answerClarification: vi.fn(), stop: vi.fn() }),
}));

const conversation: AskMessage[] = [
  { id: 'q1', role: 'user', content: 'Nợ xấu bán lẻ tháng này tăng ở đâu?', timestamp: '2026-10-08T10:50:00', status: 'done' },
  {
    id: 'a1', role: 'assistant', content: 'Nợ xấu bán lẻ T9 là 1,84%.', timestamp: '2026-10-08T10:50:02', status: 'done',
    suggestions: ['So với cùng kỳ năm trước'],
    evidence: {
      runs: [{
        kind: 'sql', sql: 'SELECT 1', generated_by: 'semantic_layer', trust: 'GOVERNED',
        metric: { metric: 'npl_ratio@4', owner: 'Phạm Đức Long' },
        columns: ['vung'], rows: [{ vung: 'DH Bắc Bộ' }], row_count: 1,
      }],
      clarifications: [], warnings: [],
    },
  },
];

beforeEach(() => {
  send.mockReset();
  navigate.mockReset();
  messages = [];
  localStorage.clear();
});

describe('AskDataPage', () => {
  it('starts empty with examples and asks what is typed', async () => {
    render(<AskDataPage />);
    // The desktop title; under 768px the page also carries the mobile title "Hỏi" (CSS picks one).
    expect(screen.getByRole('heading', { level: 1, name: 'Hỏi đáp' })).toBeInTheDocument();
    expect(screen.getByText('Bắt đầu bằng một câu hỏi rõ ràng')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/hệ thống tự chọn agent trả lời/)).toBeInTheDocument());

    const box = screen.getByLabelText('Câu hỏi');
    fireEvent.change(box, { target: { value: 'Doanh thu tháng này' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(send).toHaveBeenCalledWith('Doanh thu tháng này');
    // remembered for this viewer
    expect(JSON.parse(localStorage.getItem('dataagent.ask.history') ?? '[]')[0].q).toBe('Doanh thu tháng này');
  });

  it('titles the page with the first question and labels the answer', () => {
    messages = conversation;
    render(<AskDataPage />);
    expect(
      screen.getByRole('heading', { level: 1, name: 'Nợ xấu bán lẻ tháng này tăng ở đâu?' }),
    ).toBeInTheDocument();
    expect(screen.getByText(/Phiên của Đỗ Hải Yến · 1 câu hỏi/)).toBeInTheDocument();
    expect(screen.getByTestId('trust-label')).toHaveTextContent('Có quản trị');
    expect(screen.getByLabelText('Câu hỏi')).toHaveAttribute('placeholder', 'Hỏi tiếp…');
  });

  it('opens the receipt of an answer over the conversation, which stays', () => {
    messages = conversation;
    render(<AskDataPage />);
    fireEvent.click(screen.getByRole('button', { name: 'Biên nhận' }));
    expect(screen.getByRole('dialog', { name: 'Biên nhận câu trả lời' })).toBeInTheDocument();
    expect(screen.getByTestId('trust-label')).toBeInTheDocument();
    expect(navigate).not.toHaveBeenCalled();
  });

  it('opens Cách tính beside the conversation and closes it', () => {
    messages = conversation;
    render(<AskDataPage />);
    fireEvent.click(screen.getByRole('button', { name: 'Cách tính' }));
    const panel = screen.getByRole('complementary', { name: 'Cách tính' });
    expect(within(panel).getByText(/npl_ratio v4 · đã duyệt, người phụ trách Phạm Đức Long/)).toBeInTheDocument();
    fireEvent.click(within(panel).getByRole('button', { name: 'Đóng' }));
    expect(screen.queryByRole('complementary', { name: 'Cách tính' })).toBeNull();
  });

  it('asks a follow-up chip and saves a question', () => {
    messages = conversation;
    render(<AskDataPage />);
    fireEvent.click(screen.getByRole('button', { name: 'So với cùng kỳ năm trước' }));
    expect(send).toHaveBeenCalledWith('So với cùng kỳ năm trước');
    fireEvent.click(screen.getByRole('button', { name: 'Lưu câu hỏi' }));
    expect(JSON.parse(localStorage.getItem('dataagent.ask.saved') ?? '[]')[0].q).toBe('Nợ xấu bán lẻ tháng này tăng ở đâu?');
  });

  it('opens Báo sai with the answer quoted', () => {
    messages = conversation;
    render(<AskDataPage />);
    fireEvent.click(screen.getByRole('button', { name: 'Báo sai' }));
    const dialog = screen.getByRole('dialog', { name: 'Báo câu trả lời sai' });
    expect(within(dialog).getByText('“Nợ xấu bán lẻ T9 là 1,84%.”')).toBeInTheDocument();
  });
});
