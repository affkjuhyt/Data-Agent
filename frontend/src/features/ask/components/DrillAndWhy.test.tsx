import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from 'vitest';
import { setDataSpace } from '../../../lib/v1/client';
import { DecompositionCard } from './DecompositionCard';
import { DrillPicker } from './DrillPicker';

const bar = (label: string, value: number, value_text: string) => ({ label, value, value_text });
let skills: { id: string; current_version: number | null }[] = [];
let runBody: unknown;
const server = setupServer(
  http.get('*/v1/skills', () => HttpResponse.json({ items: skills, next_page_token: '' })),
  http.post('*/v1/skills/:id/runs', async ({ request }) => {
    runBody = await request.json();
    return HttpResponse.json({ id: 'run-1', status: 'queued', poll_after_ms: 1 }, { status: 202 });
  }),
  http.get('*/v1/skill-runs/:id', () =>
    HttpResponse.json({
      id: 'run-1',
      skill_id: 'sk-1',
      version: 1,
      params: {},
      status: 'succeeded',
      error: null,
      started_at: '2026-10-17T02:00:00Z',
      created_at: '2026-10-17T02:00:00Z',
      result: {
        summary: 'Tỷ lệ nợ xấu tăng từ 0,00% (08/2026) lên 10,00% (09/2026), +10,00 điểm %.',
        method: 'Phân rã kế toán: các phần đóng góp cộng lại đúng bằng mức thay đổi.',
        confidence: 'Chính xác theo phép cộng (đối soát tổng: khớp)',
        decomposition: {
          summary: '',
          start: bar('08/2026', 0, '0,00%'),
          steps: [bar('Miền Bắc', 6.67, '+6,67 điểm %'), bar('Miền Nam', 3.33, '+3,33 điểm %')],
          end: bar('09/2026', 10, '10,00%'),
          contributions: [],
        },
      },
    }),
  ),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  setDataSpace(null);
});
afterAll(() => server.close());

const PROPOSAL = {
  explain: 'Phân rã biến động Tỷ lệ nợ xấu so với tháng trước',
  params: { metric: 'NPL_RATIO', dimension: 'vung', period: '2026-09', base_period: '2026-08' },
};

describe('Đi sâu (UC-01-04)', () => {
  it('AC-01-04-02 asks the follow-up for the chosen member and cut, from the keyboard too', () => {
    const onAsk = vi.fn();
    const onPick = vi.fn();
    const drill = { axis: 'Vùng', cuts: [{ code: 'chi_nhanh', name: 'chi nhánh' }] };
    const { rerender } = render(
      <DrillPicker drill={drill} members={['Miền Bắc', 'Miền Nam']} picked={null} onPick={onPick} onAsk={onAsk} />,
    );
    fireEvent.change(screen.getByLabelText('Vùng'), { target: { value: 'Miền Bắc' } });
    expect(onPick).toHaveBeenCalledWith('Miền Bắc');
    rerender(
      <DrillPicker drill={drill} members={['Miền Bắc', 'Miền Nam']} picked="Miền Bắc" onPick={onPick} onAsk={onAsk} />,
    );
    fireEvent.click(screen.getByText('chi nhánh'));
    expect(onAsk).toHaveBeenCalledWith('Đi sâu Miền Bắc theo chi nhánh');
  });
});

describe('Vì sao (UC-03-01)', () => {
  it('AC-03-01-01 runs the decomposition only once confirmed and draws it with its confidence', async () => {
    setDataSpace('ds-td');
    skills = [{ id: 'sk-1', current_version: 1 }];
    runBody = undefined;
    render(<DecompositionCard proposal={PROPOSAL} />);
    expect(screen.getByText('08/2026 → 09/2026')).toBeTruthy();
    expect(runBody).toBeUndefined();
    fireEvent.click(screen.getByRole('button', { name: 'Chạy phân rã' }));
    await waitFor(() => expect(screen.getByText(/lên 10,00% \(09\/2026\)/)).toBeTruthy());
    expect(runBody).toEqual({ params: PROPOSAL.params });
    expect(screen.getByText(/Mức tin cậy: Chính xác theo phép cộng/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Chạy phân rã' })).toBeNull();
  });

  it('says so when the Data Space has no decomposition skill to run', async () => {
    setDataSpace('ds-td');
    skills = [];
    render(<DecompositionCard proposal={PROPOSAL} />);
    fireEvent.click(screen.getByRole('button', { name: 'Chạy phân rã' }));
    await waitFor(() =>
      expect(screen.getByText('Data Space chưa có kỹ năng Phân rã biến động được phát hành.')).toBeTruthy(),
    );
  });
});
