import { fireEvent, render, screen, within } from '@testing-library/react';
import { HttpResponse, http } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import type { TurnStep } from '../steps';
import { StepTimeline } from './StepTimeline';

const server = setupServer(
  http.get('*/v1/query-runs/run-1/sql', () =>
    HttpResponse.json({ sql_executed: 'SELECT vung, sum(x) FROM t GROUP BY vung', sql_generated: '', policies_applied: [] }),
  ),
  http.get('*/v1/query-runs/run-hidden/sql', () =>
    HttpResponse.json(
      { type: 'about:blank', title: 'Forbidden', status: 403, code: 'forbidden', detail: 'view_detail' },
      { status: 403, headers: { 'Content-Type': 'application/problem+json' } },
    ),
  ),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const step = (over: Partial<TurnStep>): TurnStep => ({
  stepId: 's1', seq: 1, action: 'understand', title: 'Hiểu câu hỏi', target: '', chips: [], status: 'done',
  startedAt: '2026-11-02T10:00:00Z', durationMs: 80, detail: null, summary: null, parent: null, children: [], ...over,
});

const steps: TurnStep[] = [
  step({ detail: { reading: [{ kind: 'metric', label: 'Doanh thu Internet', value: 'DOANH_THU_INTERNET', source: 'default' }] } }),
  step({
    stepId: 's2', seq: 2, action: 'query', title: 'Truy vấn', target: 'AdventureWorksDW cho: Doanh thu Internet',
    chips: [{ kind: 'source', label: 'AdventureWorksDW' }], detail: { row_count: 1, query_run_id: 'run-1', truncated: false },
  }),
  step({
    stepId: 's3', seq: 3, action: 'write_answer', title: 'Viết câu trả lời', detail: { by: 'model' },
    children: [step({ stepId: 's4', seq: 4, action: 'model_call', title: 'Gọi mô hình responder', parent: 's3', durationMs: 760, detail: { model: 'responder', input_tokens: 251, output_tokens: 45, latency_ms: 760 } })],
  }),
];

describe('StepTimeline', () => {
  it('says how long the turn took and opens or closes on click', () => {
    render(<StepTimeline steps={steps} running={false} durationMs={2400} recorded />);
    const header = screen.getByRole('button', { name: /Đã làm trong 2,4 giây/ });
    expect(header).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(header);
    expect(header).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('Truy vấn')).toBeInTheDocument();
    expect(screen.getByText('AdventureWorksDW cho: Doanh thu Internet')).toBeInTheDocument();
  });

  it('is open while the turn runs and counts its steps', () => {
    render(<StepTimeline steps={[step({ status: 'running', durationMs: null })]} running recorded />);
    expect(screen.getByRole('button', { name: /Đang làm… 1 bước/ })).toHaveAttribute('aria-expanded', 'true');
  });

  it('opens a query step to its SQL, read through the query run', async () => {
    render(<StepTimeline steps={steps} running={false} durationMs={2400} recorded />);
    fireEvent.click(screen.getByRole('button', { name: /Đã làm trong/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Xem chi tiết: Truy vấn' }));
    expect(await screen.findByText(/GROUP BY vung/)).toBeInTheDocument();
    expect(screen.getByText(/1 dòng/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sao chép SQL' })).toBeInTheDocument();
  });

  it('says when the SQL may not be read', async () => {
    const hidden = [step({ stepId: 's2', action: 'query', title: 'Truy vấn', detail: { row_count: 1, query_run_id: 'run-hidden' } })];
    render(<StepTimeline steps={hidden} running={false} durationMs={100} recorded />);
    fireEvent.click(screen.getByRole('button', { name: /Đã làm trong/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Xem chi tiết: Truy vấn' }));
    expect(await screen.findByText(/không có quyền xem SQL/)).toBeInTheDocument();
  });

  it('shows the reading of the question and nests the model call', () => {
    render(<StepTimeline steps={steps} running={false} durationMs={2400} recorded />);
    fireEvent.click(screen.getByRole('button', { name: /Đã làm trong/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Xem chi tiết: Hiểu câu hỏi' }));
    expect(screen.getByText('Doanh thu Internet')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Xem chi tiết: Viết câu trả lời' }));
    expect(screen.getByText('Mô hình responder · 251 + 45 token · 760 ms')).toBeInTheDocument();
  });

  it('a failed step says why', () => {
    const failed = [step({ stepId: 's2', action: 'query', title: 'Truy vấn', status: 'failed', summary: 'Chưa truy vấn được nguồn Kho.' })];
    render(<StepTimeline steps={failed} running={false} durationMs={100} recorded />);
    fireEvent.click(screen.getByRole('button', { name: /Đã làm trong/ }));
    const row = screen.getByRole('listitem');
    expect(within(row).getByText('Chưa truy vấn được nguồn Kho.')).toBeInTheDocument();
    expect(within(row).getByLabelText('Lỗi')).toBeInTheDocument();
  });

  it('the search for a period with data says what was asked, what exists and what is shown', () => {
    const explored = [step({
      stepId: 's5', action: 'explore', title: 'Tìm kỳ có dữ liệu', target: 'năm 2026',
      detail: { asked: 'năm 2026', grain: 'year', available: ['năm 2013', 'năm 2014'], chosen: 'năm 2014', query_run_id: 'run-1' },
    })];
    render(<StepTimeline steps={explored} running={false} durationMs={100} recorded />);
    fireEvent.click(screen.getByRole('button', { name: /Đã làm trong/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Xem chi tiết: Tìm kỳ có dữ liệu' }));
    expect(screen.getByText('năm 2013 · năm 2014')).toBeInTheDocument();
    expect(screen.getByText('năm 2014', { selector: 'dd' })).toBeInTheDocument();
  });

  it('widening a filter says what was asked and which members have figures', async () => {
    const widened = [step({
      stepId: 's6', action: 'widen_filter', title: 'Nới bộ lọc', target: 'vùng “Miền Trung”',
      detail: { dimension: 'vùng', asked: ['Miền Trung'], available: ['Miền Bắc', 'Miền Nam'], query_run_id: 'run-1', row_count: 2 },
    })];
    render(<StepTimeline steps={widened} running={false} durationMs={100} recorded />);
    fireEvent.click(screen.getByRole('button', { name: /Đã làm trong/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Xem chi tiết: Nới bộ lọc' }));
    expect(screen.getByText('Miền Bắc · Miền Nam')).toBeInTheDocument();
    expect(screen.getByText('Miền Trung', { selector: 'dd' })).toBeInTheDocument();
    expect(await screen.findByText(/GROUP BY vung/)).toBeInTheDocument();
  });

  it('a name looked up in the data says where it was found', () => {
    const looked = [step({
      stepId: 's2', action: 'find_value', title: 'Tìm giá trị', target: '“mien nam”',
      detail: { phrase: 'mien nam', dimension: 'vùng', value: 'Miền Nam' },
    })];
    render(<StepTimeline steps={looked} running={false} durationMs={100} recorded />);
    fireEvent.click(screen.getByRole('button', { name: /Đã làm trong/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Xem chi tiết: Tìm giá trị' }));
    expect(screen.getByText('Miền Nam', { selector: 'dd' })).toBeInTheDocument();
    expect(screen.getByText('vùng', { selector: 'dd' })).toBeInTheDocument();
  });

  it('a turn recorded before steps were kept says so', () => {
    render(<StepTimeline steps={[]} running={false} recorded={false} />);
    expect(screen.getByText('Lượt này chưa ghi chi tiết các bước.')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});
