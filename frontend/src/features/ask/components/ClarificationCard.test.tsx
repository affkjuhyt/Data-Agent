import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ClarificationCard } from './ClarificationCard';

const card = {
  kind: 'followup' as const,
  questions: [
    {
      text: 'Chưa có chỉ số chuẩn nào đo “Khách hàng rời bỏ”. Bạn muốn xem chỉ số nào dưới đây?',
      options: ['Số khách hàng mua qua Internet là bao nhiêu?', 'Số đơn Internet là bao nhiêu?'],
    },
  ],
};

describe('ClarificationCard (E4)', () => {
  it('marks a choice, and asks it only on Xác nhận', () => {
    const onAnswer = vi.fn();
    render(<ClarificationCard clarification={card} onAnswer={onAnswer} />);
    const confirm = screen.getByRole('button', { name: 'Xác nhận' });
    expect(confirm).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Số đơn Internet là bao nhiêu?' }));
    expect(onAnswer).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Số khách hàng mua qua Internet là bao nhiêu?' }));
    fireEvent.click(confirm);
    expect(onAnswer).toHaveBeenCalledWith({ text: 'Số khách hàng mua qua Internet là bao nhiêu?', answers: [] });
  });

  it('takes an answer of the reader\'s own under Khác…', () => {
    const onAnswer = vi.fn();
    render(<ClarificationCard clarification={card} onAnswer={onAnswer} />);
    fireEvent.click(screen.getByRole('button', { name: 'Khác…' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Câu trả lời khác' }), { target: { value: 'Tỷ lệ khách quay lại' } });
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Câu trả lời khác' }), { key: 'Enter' });
    fireEvent.click(screen.getByRole('button', { name: 'Xác nhận' }));
    expect(onAnswer).toHaveBeenCalledWith({ text: 'Tỷ lệ khách quay lại', answers: [] });
  });

  it('folds on Bỏ qua without asking anything, and opens again', () => {
    const onAnswer = vi.fn();
    render(<ClarificationCard clarification={card} onAnswer={onAnswer} />);
    fireEvent.click(screen.getByRole('button', { name: 'Bỏ qua' }));
    expect(onAnswer).not.toHaveBeenCalled();
    expect(screen.queryByTestId('clarification-card')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Mở lại' }));
    expect(screen.getByTestId('clarification-card')).toBeInTheDocument();
  });

  it('is one read-only line once answered: nothing can be picked again', () => {
    const onAnswer = vi.fn();
    render(<ClarificationCard clarification={card} answered="Số đơn Internet là bao nhiêu?" onAnswer={onAnswer} />);
    expect(screen.getByTestId('clarification-answered')).toHaveTextContent('Đã chọn: Số đơn Internet là bao nhiêu?');
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});
