import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { AnswerBlock } from './AnswerBlock';
import { AnswerTheme } from './AnswerTheme';
import { RefusalCard } from './RefusalCard';
import type { AskMessage, Refusal } from '../types';

const configError: Refusal = {
  code: 'AGENT_CONFIG_ERROR',
  message: 'Không đọc được cấu hình của agent này nên hệ thống chưa trả lời.',
  next_action: 'retry',
  detail_id: 'conv-7',
};

const plannerError: Refusal = {
  code: 'PLANNER_ERROR',
  message: 'Hệ thống chưa lập được cách trả lời cho câu hỏi này.',
  next_action: 'rephrase',
  detail_id: 'conv-8',
};

const renderCard = (props: Parameters<typeof RefusalCard>[0]) =>
  render(<AnswerTheme mode="light"><RefusalCard {...props} /></AnswerTheme>);

describe('RefusalCard', () => {
  it('offers "Thử lại" for AGENT_CONFIG_ERROR and sends the question again', () => {
    const onRetry = vi.fn();
    renderCard({ refusal: configError, onRetry, onRephrase: vi.fn() });

    expect(screen.getByText(configError.message)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Thử lại/ }));
    expect(onRetry).toHaveBeenCalledOnce();
    expect(screen.queryByRole('button', { name: /Diễn đạt lại/ })).toBeNull();
  });

  it('has no retry for PLANNER_ERROR — asking the same way again fails the same way', () => {
    const onRephrase = vi.fn();
    renderCard({ refusal: plannerError, onRetry: vi.fn(), onRephrase });

    expect(screen.queryByRole('button', { name: /Thử lại/ })).toBeNull();
    expect(screen.getByText(/diễn đạt lại câu hỏi/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: /Diễn đạt lại/ }));
    expect(onRephrase).toHaveBeenCalledOnce();
  });

  it('branches on next_action, not on the message', () => {
    renderCard({ refusal: { ...configError, next_action: 'contact_steward' }, onRetry: vi.fn() });

    expect(screen.queryByRole('button', { name: /Thử lại/ })).toBeNull();
    expect(screen.getByText(/Liên hệ người phụ trách/)).toBeTruthy();
  });

  it('shows the code and lookup id only where asked (Playground)', () => {
    const { unmount } = renderCard({ refusal: configError });
    expect(screen.queryByTestId('refusal-detail')).toBeNull();
    unmount();

    renderCard({ refusal: configError, showDetail: true });
    const detail = screen.getByTestId('refusal-detail');
    expect(detail.textContent).toContain('AGENT_CONFIG_ERROR');
    expect(detail.textContent).toContain('conv-7');
  });

  it('replaces the whole answer block, with nothing streamed beside it', () => {
    const msg: AskMessage = {
      id: 'a1', role: 'assistant', content: '', timestamp: '2026-10-06T10:00:00', status: 'done',
      refusal: configError, suggestions: ['Doanh thu tháng trước'],
    };
    render(<AnswerTheme mode="light"><AnswerBlock msg={msg} onSuggestion={vi.fn()} onRetry={vi.fn()} /></AnswerTheme>);

    expect(screen.getByTestId('refusal-card')).toBeTruthy();
    expect(screen.queryByText('Doanh thu tháng trước')).toBeNull();
    expect(screen.queryByRole('button', { name: /Copy answer/ })).toBeNull();
  });
});
