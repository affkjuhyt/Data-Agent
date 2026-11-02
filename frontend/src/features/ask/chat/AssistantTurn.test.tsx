import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { AskMessage } from '../types';
import { AssistantTurn } from './AssistantTurn';

const base: AskMessage = {
  id: 'a1', role: 'assistant', content: 'Doanh thu Internet năm 2013 là 16.351.550,34 USD.', timestamp: '2026-11-02T10:05:00Z',
  status: 'done', answerId: 'ans-1', productScope: 'in_scope', questionText: 'Doanh thu Internet năm 2013?',
  durationMs: 2400, stepsRecorded: true,
  steps: [{ stepId: 's1', seq: 1, action: 'understand', title: 'Hiểu câu hỏi', target: '', chips: [], status: 'done', startedAt: '', durationMs: 80, detail: null, summary: null, parent: null, children: [] }],
  blocks: [{ type: 'markdown', id: 'm', content: 'Doanh thu Internet năm 2013 là 16.351.550,34 USD.' }],
  trust: { level: 'GOVERNED', label: 'Theo chỉ số chuẩn', tone: 'success', meaning: '' } as never,
  suggestions: ['Doanh thu Internet năm 2013 theo quý'],
};

const handlers = () => ({
  onRetry: vi.fn(), onReport: vi.fn(), onReceipt: vi.fn(), onSaveKnowledge: vi.fn(), onSaveSession: vi.fn(),
  onAsk: vi.fn(), onAnswerClarification: vi.fn(), onRetryRefusal: vi.fn(), onRephrase: vi.fn(),
});

describe('AssistantTurn', () => {
  it('shows the steps, the answer, the trust label and the next questions', () => {
    const h = handlers();
    render(<AssistantTurn message={base} busy={false} {...h} />);
    expect(screen.getByRole('button', { name: /Đã làm trong 2,4 giây/ })).toBeInTheDocument();
    expect(screen.getByText(/16.351.550,34 USD/)).toBeInTheDocument();
    expect(screen.getByText('Theo chỉ số chuẩn')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Doanh thu Internet năm 2013 theo quý' }));
    expect(h.onAsk).toHaveBeenCalledWith('Doanh thu Internet năm 2013 theo quý');
  });

  it('offers retry, Báo sai, copy and the receipt, and no thumbs-up', async () => {
    const h = handlers();
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<AssistantTurn message={base} busy={false} {...h} />);
    fireEvent.click(screen.getByRole('button', { name: 'Hỏi lại' }));
    expect(h.onRetry).toHaveBeenCalledWith('a1');
    fireEvent.click(screen.getByRole('button', { name: 'Báo sai' }));
    expect(h.onReport).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Sao chép' }));
    expect(writeText).toHaveBeenCalledWith(base.content);
    fireEvent.click(screen.getByRole('button', { name: 'Biên nhận' }));
    expect(h.onReceipt).toHaveBeenCalledWith('ans-1');
    expect(screen.queryByRole('button', { name: /Thích|Hữu ích/ })).not.toBeInTheDocument();
  });

  it('has no receipt outside every data product', () => {
    render(<AssistantTurn message={{ ...base, productScope: 'out_of_scope' }} busy={false} {...handlers()} />);
    expect(screen.queryByRole('button', { name: 'Biên nhận' })).not.toBeInTheDocument();
  });

  it('a refusal is a card with its next action', () => {
    const h = handlers();
    render(
      <AssistantTurn
        message={{ ...base, blocks: [], content: '', trust: undefined, refusal: { code: 'TOOL_UNAVAILABLE', message: 'Chưa truy vấn được nguồn Kho.', next_action: 'retry' } }}
        busy={false}
        {...h}
      />,
    );
    expect(screen.getByRole('alert')).toHaveTextContent('Chưa truy vấn được nguồn Kho.');
  });

  it('a question back is answered from its card', () => {
    const h = handlers();
    render(
      <AssistantTurn
        message={{ ...base, blocks: [], content: 'Bạn muốn hỏi chỉ số nào?', trust: undefined, clarification: { kind: 'followup', questions: [{ text: '' }] } }}
        busy={false}
        {...h}
      />,
    );
    expect(screen.getByText('Bạn muốn hỏi chỉ số nào?')).toBeInTheDocument();
    expect(screen.getByTestId('clarification-card')).toBeInTheDocument();
  });

  it('while running shows the open timeline and no footer', () => {
    render(<AssistantTurn message={{ ...base, status: 'streaming', blocks: [], content: '' }} busy {...handlers()} />);
    expect(screen.getByRole('button', { name: /Đang làm… 1 bước/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Hỏi lại' })).not.toBeInTheDocument();
  });
});
