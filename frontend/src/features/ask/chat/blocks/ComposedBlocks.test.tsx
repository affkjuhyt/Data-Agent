import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { AnswerBlockView } from '../../types';
import { ComposedBlocks, isComposed } from './ComposedBlocks';

const blocks = [
  { type: 'markdown', id: 'b1', composed: true, content: 'Europe dẫn đầu doanh thu.' },
  {
    type: 'kpi',
    id: 'b2',
    composed: true,
    items: [{ label: 'Doanh thu', value: 1500, unit: 'USD', change_pct: -12.5, compare_label: 'kỳ trước', trust: 'certified', trust_label: 'Theo chỉ số chuẩn' }],
  },
  { type: 'insights', id: 'b3', composed: true, items: [{ text: 'Pacific giảm mạnh nhất.', tone: 'negative' }] },
  { type: 'timeline_of_the_future', id: 'b4', composed: true, payload: 1 },
] as unknown as AnswerBlockView[];

describe('ComposedBlocks', () => {
  it('draws the blocks in the order the agent chose, an unknown one as its data', () => {
    expect(isComposed(blocks)).toBe(true);
    render(<ComposedBlocks blocks={blocks} />);
    const drawn = screen.getAllByTestId('answer-block').map((el) => el.getAttribute('data-type'));
    expect(drawn).toEqual(['markdown', 'kpi', 'insights', 'timeline_of_the_future']);
    expect(screen.getByText('Europe dẫn đầu doanh thu.')).toBeInTheDocument();
    expect(screen.getByRole('group', { name: 'Doanh thu' })).toHaveTextContent('so với kỳ trước');
    expect(screen.getByText(/timeline_of_the_future/)).toBeInTheDocument();
  });

  it('leaves an answer of the older shape to the fixed layout', () => {
    expect(isComposed([{ type: 'markdown', id: 'a-markdown', content: 'x' }] as unknown as AnswerBlockView[])).toBe(false);
  });

  it('offers the chart types the rows fit and redraws on a click', () => {
    const chart = [
      {
        type: 'chart',
        id: 'c1',
        composed: true,
        alternatives: ['line', 'bar'],
        spec: { chart_type: 'line', title: 'Doanh thu', x_key: 'Tháng', y_keys: ['v'], data: [{ Tháng: '2013-01', v: 1 }, { Tháng: '2013-02', v: 2 }] },
      },
    ] as unknown as AnswerBlockView[];
    render(<ComposedBlocks blocks={chart} />);
    const group = screen.getByRole('group', { name: 'Kiểu biểu đồ' });
    expect(group).toHaveTextContent('Đường');
    fireEvent.click(screen.getByRole('button', { name: 'Cột' }));
    expect(screen.getByRole('button', { name: 'Cột' })).toHaveAttribute('aria-pressed', 'true');
  });
});

describe('ComposedBlocks: a block that cannot be drawn', () => {
  it('leaves the rest of the answer readable', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    render(
      <ComposedBlocks
        blocks={[
          // A composed chart without y_keys: drawing its series throws.
          { type: 'chart', composed: true, spec: { chart_type: 'composed', data: [{ x: 1 }] } },
          { type: 'markdown', composed: true, content: 'Doanh thu quý 3 tăng 12%.' },
        ] as never}
      />,
    );
    expect(screen.getByText('Không hiển thị được phần này của câu trả lời.')).toBeInTheDocument();
    expect(screen.getByText('Doanh thu quý 3 tăng 12%.')).toBeInTheDocument();
    vi.restoreAllMocks();
  });
});
