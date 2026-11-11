import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ExploreResult } from '../../lib/v1/explore';
import { chartRows, ResultChart } from './ResultChart';

const result: ExploreResult = {
  id: 'qr-1',
  title: 'Lợi nhuận theo vùng',
  subtitle_text: 'PROFIT v2 · 0,4 giây',
  category_label: 'Vùng',
  category_dimension_id: 'dim-region',
  series: [
    { key: 'q2', label: 'Quý 2' },
    { key: 'q3', label: 'Quý 3' },
  ],
  rows: [
    { key: 'north', label: 'Miền Bắc', short_label: 'Bắc', values: [120, 150], delta: 30 },
    // A loss: drawn below zero, not as a 2px stub on the axis.
    { key: 'central', label: 'Miền Trung', short_label: 'Trung', values: [-40, -15], delta: 25 },
  ],
  unit: 'amount',
  increase_is_bad: false,
  row_count: 2,
  drill_dimension_id: 'dim-province',
  next_steps: [],
  reconciliation_text: null,
  partial_text: null,
  data_space_id: 'ds-1',
};

describe('ResultChart', () => {
  it('feeds recharts one record per category with each series under its key', () => {
    expect(chartRows(result)).toEqual([
      { key: 'north', label: 'Bắc', q2: 120, q3: 150 },
      { key: 'central', label: 'Trung', q2: -40, q3: -15 },
    ]);
  });

  it('offers each column group to drill into from the keyboard, losses included', () => {
    const onDrill = vi.fn();
    render(
      <ResultChart
        result={result}
        kind="bar"
        onKind={vi.fn()}
        loading={false}
        onDrill={onDrill}
        links={null}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /^Đi sâu: Miền Trung/ }));
    expect(onDrill).toHaveBeenCalledWith(result.rows[1]);
    expect(screen.getByRole('img', { name: /Lợi nhuận theo vùng/ })).toBeInTheDocument();
  });

  it('offers no drill while a new query runs', () => {
    render(
      <ResultChart
        result={result}
        kind="bar"
        onKind={vi.fn()}
        loading
        onDrill={vi.fn()}
        links={null}
      />,
    );
    expect(screen.queryByRole('button', { name: /^Đi sâu/ })).toBeNull();
  });
});
