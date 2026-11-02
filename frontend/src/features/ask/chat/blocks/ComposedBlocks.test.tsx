import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
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
});
