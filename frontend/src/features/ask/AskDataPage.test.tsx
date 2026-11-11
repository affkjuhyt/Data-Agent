import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ToastProvider } from '../../design-system/Toast';
import AskDataPage from './AskDataPage';
import type { AskMessage } from './types';

const { send, retry, navigate, saveAnswerToSession } = vi.hoisted(() => ({
  send: vi.fn(),
  retry: vi.fn(),
  navigate: vi.fn(),
  saveAnswerToSession: vi.fn(
    async (_workspaceId: string, _answer: { answerId: string; title: string }) => ({ id: 'tile-1' }),
  ),
}));
let messages: AskMessage[] = [];

vi.mock('../../AppStateContext', () => ({
  useAppState: () => ({ user: { id: 'u1', name: 'Đỗ Hải Yến' }, navigate, routeId: undefined }),
}));
vi.mock('../../lib/v1/questions', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/v1/questions')>();
  return {
    ...actual,
    // As the api: `q` searches every conversation, here an older one the first page does not hold.
    listConversations: vi.fn(async (_size: number, q?: string) => {
      const all = [
        { id: 'c-1', title: 'Doanh thu Internet 2013', last_question_at: null, updated_at: '2026-11-02T10:00:00Z' },
        { id: 'c-2', title: 'Tồn kho cuối kỳ', last_question_at: null, updated_at: '2026-11-01T10:00:00Z' },
        { id: 'c-9', title: 'Tồn kho năm 2011', last_question_at: null, updated_at: '2026-01-01T10:00:00Z' },
      ];
      const items = q ? all.filter((c) => c.title.toLowerCase().includes(q.toLowerCase())) : all.slice(0, 2);
      return { items, next_page_token: '' };
    }),
    saveAnswerToSession,
  };
});
vi.mock('../../lib/v1/api', () => ({
  listWorkspaces: vi.fn(async () => ({
    items: [{ id: 'ws-npl-sept', title: 'Điều tra nợ xấu bán lẻ T9' }],
    next_page_token: '',
  })),
}));
vi.mock('../../lib/v1/useV1', async (importOriginal) => ({
  // ReportDialog writes through the real useV1Mutation.
  ...(await importOriginal<typeof import('../../lib/v1/useV1')>()),
  useV1: () => ({ data: { metrics: [{ id: 'm1', label: 'Doanh thu Internet', code: 'DTI', certified: true, version: 1, allowed_dimension_ids: [] }], dimensions: [] } }),
}));
vi.mock('./hooks/useQuestionStream', () => ({
  useQuestionStream: () => ({
    messages,
    sending: false,
    hydrating: false,
    conversationId: null,
    send,
    answerClarification: vi.fn(),
    retry,
    addNotice: vi.fn(),
    stop: vi.fn(),
    hydrate: vi.fn(),
    resetConversation: vi.fn(),
  }),
}));

const wrap = (ui: React.ReactNode) => render(<ToastProvider>{ui}</ToastProvider>);

const conversation: AskMessage[] = [
  { id: 'q1', role: 'user', content: 'Doanh thu Internet năm 2013 là bao nhiêu?', timestamp: '2026-11-02T10:00:00Z', status: 'done' },
  {
    id: 'a1', role: 'assistant', content: 'Doanh thu Internet năm 2013 là 16.351.550,34 USD.', timestamp: '2026-11-02T10:00:03Z',
    status: 'done', questionId: 'question-1', answerId: 'answer-1', productScope: 'in_scope',
    questionText: 'Doanh thu Internet năm 2013 là bao nhiêu?', durationMs: 2400, stepsRecorded: true,
    steps: [{ stepId: 's1', seq: 1, action: 'understand', title: 'Hiểu câu hỏi', target: '', chips: [], status: 'done', startedAt: '', durationMs: 80, detail: null, summary: null, parent: null, children: [] }],
    blocks: [{ type: 'markdown', id: 'm', content: 'Doanh thu Internet năm 2013 là 16.351.550,34 USD.' }],
    suggestions: ['Doanh thu Internet năm 2013 theo quý'],
  },
];

describe('AskDataPage', () => {
  beforeEach(() => {
    send.mockReset();
    retry.mockReset();
    navigate.mockReset();
    messages = [];
  });

  it('starts from a greeting, the starters and the recent chats, and asks what is typed', async () => {
    wrap(<AskDataPage />);
    expect(screen.getByRole('heading', { name: /Xin chào Đỗ Hải Yến/ })).toBeInTheDocument();
    expect(await screen.findAllByText('Doanh thu Internet 2013')).not.toHaveLength(0);
    const box = screen.getByRole('textbox', { name: 'Câu hỏi' });
    fireEvent.change(box, { target: { value: 'Tồn kho cuối năm 2013' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(send).toHaveBeenCalledWith('Tồn kho cuối năm 2013');
  });

  it('the conversation panel opens a recent chat and filters by title', async () => {
    wrap(<AskDataPage />);
    const panel = screen.getByRole('navigation', { name: 'Cuộc trò chuyện' });
    await within(panel).findByText('Tồn kho cuối kỳ');
    expect(within(panel).queryByText('Tồn kho năm 2011')).not.toBeInTheDocument();
    fireEvent.change(within(panel).getByRole('searchbox', { name: 'Tìm cuộc trò chuyện' }), { target: { value: 'tồn' } });
    // The search reaches a conversation older than the loaded ones.
    expect(await within(panel).findByText('Tồn kho năm 2011')).toBeInTheDocument();
    expect(within(panel).queryByText('Doanh thu Internet 2013')).not.toBeInTheDocument();
    fireEvent.click(within(panel).getByText('Tồn kho cuối kỳ'));
    expect(navigate).toHaveBeenCalledWith('analysis', 'ASK', 'c-2');
  });

  it('draws the thread: the question, the steps, the answer and its next question', () => {
    messages = conversation;
    wrap(<AskDataPage />);
    expect(screen.getByText('Doanh thu Internet năm 2013 là bao nhiêu?', { selector: 'p' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Đã làm trong 2,4 giây/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Doanh thu Internet năm 2013 theo quý' }));
    expect(send).toHaveBeenCalledWith('Doanh thu Internet năm 2013 theo quý');
  });

  it('asks again, and opens Báo sai with the answer quoted', () => {
    messages = conversation;
    wrap(<AskDataPage />);
    fireEvent.click(screen.getByRole('button', { name: 'Hỏi lại' }));
    expect(retry).toHaveBeenCalledWith('a1');
    fireEvent.click(screen.getByRole('button', { name: 'Báo sai' }));
    expect(screen.getByRole('dialog')).toHaveTextContent('16.351.550,34 USD');
  });

  it('starts a new conversation from the panel', () => {
    messages = conversation;
    wrap(<AskDataPage />);
    const panel = screen.getByRole('navigation', { name: 'Cuộc trò chuyện' });
    fireEvent.click(within(panel).getByRole('button', { name: 'Cuộc trò chuyện mới' }));
    expect(navigate).toHaveBeenCalledWith('analysis', 'ASK');
  });
});
