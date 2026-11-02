import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ChatComposer } from './ChatComposer';

const model = {
  metrics: [{ id: 'm1', label: 'Doanh thu Internet', code: 'DOANH_THU_INTERNET', certified: true, version: 1, allowed_dimension_ids: [] }],
  dimensions: [{ id: 'd1', code: 'nam', label: 'Năm', group_label: 'Lịch', values: [], incompatible_metric_ids: [], incompatible_reason: null, parent_id: null, is_time: true }],
} as never;

describe('ChatComposer', () => {
  it('sends on Enter and keeps Shift+Enter for a new line', () => {
    const onSend = vi.fn();
    render(<ChatComposer sending={false} onSend={onSend} onStop={vi.fn()} model={model} />);
    const box = screen.getByRole('textbox', { name: 'Câu hỏi' });
    fireEvent.change(box, { target: { value: 'Doanh thu 2013' } });
    fireEvent.keyDown(box, { key: 'Enter', shiftKey: true });
    expect(onSend).not.toHaveBeenCalled();
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(onSend).toHaveBeenCalledWith('Doanh thu 2013');
  });

  it('@ pins a metric as a chip, and the question carries it', () => {
    const onSend = vi.fn();
    render(<ChatComposer sending={false} onSend={onSend} onStop={vi.fn()} model={model} />);
    const box = screen.getByRole('textbox', { name: 'Câu hỏi' });
    fireEvent.change(box, { target: { value: '@doanh' } });
    fireEvent.click(screen.getByRole('option', { name: /Doanh thu Internet/ }));
    expect(screen.getByRole('button', { name: 'Bỏ Doanh thu Internet' })).toBeInTheDocument();
    fireEvent.change(box, { target: { value: 'năm 2013' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(onSend).toHaveBeenCalledWith('Doanh thu Internet năm 2013');
  });

  it('/ lists the chat commands', () => {
    render(<ChatComposer sending={false} onSend={vi.fn()} onStop={vi.fn()} model={model} />);
    fireEvent.change(screen.getByRole('textbox', { name: 'Câu hỏi' }), { target: { value: '/' } });
    const commands = screen.getByRole('listbox', { name: 'Lệnh chat' });
    expect(commands).toHaveTextContent('/chart');
    expect(commands).toHaveTextContent('/sql');
  });

  it('offers Dừng while an answer is coming', () => {
    const onStop = vi.fn();
    render(<ChatComposer sending onSend={vi.fn()} onStop={onStop} model={model} />);
    fireEvent.click(screen.getByRole('button', { name: 'Dừng' }));
    expect(onStop).toHaveBeenCalled();
  });
});
