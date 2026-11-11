import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { HttpResponse, http } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import type { AskMessage } from '../../types';
import { AnswerBlocks } from './AnswerBlocks';

const server = setupServer(
  http.get('*/v1/query-runs/run-1/sql', () =>
    HttpResponse.json({ sql_executed: 'SELECT sum(sales) FROM fact', sql_generated: '', policies_applied: [] }),
  ),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const run = {
  id: 'run-1', kind: 'sql', sql: '', generated_by: 'semantic_layer',
  columns: ['Năm', 'Doanh thu'], rows: [{ Năm: '2013', 'Doanh thu': 16351550.34 }], row_count: 1,
};

const message = (over: Partial<AskMessage> = {}): AskMessage => ({
  id: 'a1', role: 'assistant', content: 'Doanh thu Internet năm 2013 là 16.351.550,34 USD.', timestamp: '2026-11-02T10:00:00Z',
  status: 'done', answerId: 'ans-1',
  chartSpecs: [{ chart_type: 'bar', title: 'Doanh thu', x_key: 'Năm', y_keys: ['Doanh thu'], data: [{ Năm: '2013', 'Doanh thu': 16351550.34 }] } as never],
  evidence: { runs: [run as never], clarifications: [], warnings: [] },
  reading: [
    { key: 'metric', label: 'Chỉ số', value: 'Doanh thu Internet' },
    { key: 'period', label: 'Kỳ', value: 'năm 2013' },
  ],
  blocks: [
    { type: 'markdown', id: 'm', content: 'Doanh thu Internet năm 2013 là 16.351.550,34 USD.' },
    { type: 'metric', id: 'F1', label: 'Doanh thu Internet', value: '16351550.34', unit: 'USD', dimensions: {} },
    { type: 'chart', id: 'c', spec: {} },
    { type: 'table', id: 't', title: 'Bảng dữ liệu', columns: ['value'], rows: [{ value: '16351550.34' }], row_count: 1 },
    { type: 'sql', id: 's', query_run_id: 'run-1' },
    { type: 'caveats', id: 'w', items: ['Số liệu Khám phá.'] },
  ],
  ...over,
});

describe('AnswerBlocks', () => {
  it('draws the text, a metric card, one result card, the SQL and the caveats', () => {
    render(<AnswerBlocks message={message()} />);
    expect(screen.getByText(/16.351.550,34 USD\./)).toBeInTheDocument();
    const card = screen.getByRole('group', { name: 'Doanh thu Internet' });
    expect(within(card).getByText(/16\.351\.550,34/)).toBeInTheDocument();
    expect(screen.getAllByRole('region', { name: /Kết quả/ })).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'SQL đã chạy' })).toBeInTheDocument();
    const caveats = screen.getByRole('note', { name: 'Lưu ý' });
    expect(within(caveats).getByText('Số liệu Khám phá.')).toBeInTheDocument();
  });

  it('switches the result card between chart and table', () => {
    render(<AnswerBlocks message={message()} />);
    fireEvent.click(screen.getByRole('tab', { name: 'Bảng' }));
    expect(screen.getByRole('tab', { name: 'Bảng' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByText('2013')).toBeInTheDocument();
  });

  it('shows the filters the answer applied, read-only', () => {
    render(<AnswerBlocks message={message()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Bộ lọc 1' }));
    const filters = screen.getByRole('dialog', { name: 'Bộ lọc của câu trả lời' });
    expect(within(filters).getByText('năm 2013')).toBeInTheDocument();
    expect(within(filters).queryByRole('textbox')).not.toBeInTheDocument();
  });

  it('closes the filters on Esc and gives focus back to their button', async () => {
    const user = userEvent.setup();
    render(<AnswerBlocks message={message()} />);
    const button = screen.getByRole('button', { name: 'Bộ lọc 1' });
    await user.click(button);
    expect(screen.getByRole('dialog', { name: 'Bộ lọc của câu trả lời' })).toBeInTheDocument();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog', { name: 'Bộ lọc của câu trả lời' })).toBeNull();
    await waitFor(() => expect(button).toHaveFocus());
  });

  it('puts the SQL first when /sql asked for it', () => {
    render(<AnswerBlocks message={message({ layout: 'sql_first' })} />);
    const order = screen.getAllByTestId('answer-block').map((el) => el.getAttribute('data-type'));
    expect(order[0]).toBe('sql');
  });

  it('opens the SQL that ran from Khám phá, and hides Khám phá without a query run', async () => {
    const { unmount } = render(<AnswerBlocks message={message()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Khám phá' }));
    expect(await screen.findByText('SELECT sum(sales) FROM fact')).toBeInTheDocument();
    unmount();
    render(<AnswerBlocks message={message({ blocks: message().blocks!.filter((b) => b.type !== 'sql') })} />);
    expect(screen.queryByRole('button', { name: 'Khám phá' })).not.toBeInTheDocument();
  });

  it('exports the table as CSV from the card menu', () => {
    const onExport = vi.fn();
    render(<AnswerBlocks message={message()} onExportCsv={onExport} />);
    fireEvent.click(screen.getByRole('button', { name: 'Thêm thao tác với kết quả' }));
    fireEvent.click(screen.getByRole('menuitem', { name: /Tải CSV/ }));
    expect(onExport).toHaveBeenCalled();
  });
});
