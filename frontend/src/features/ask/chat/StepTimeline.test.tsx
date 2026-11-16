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
  it('folds the finished turn to one line: its steps and how long, opened on click', () => {
    render(<StepTimeline steps={steps} running={false} durationMs={2400} recorded />);
    const header = screen.getByRole('button', { name: 'Đã thực hiện 3 bước · 2,4 giây' });
    expect(header).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(header);
    expect(header).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('Truy vấn')).toBeInTheDocument();
    expect(screen.getByText('AdventureWorksDW cho: Doanh thu Internet')).toBeInTheDocument();
  });

  it('while the turn runs shows its reasoning open: the plan, what each step found, what comes next', () => {
    const now = [
      step({
        stepId: 'p', seq: 1, action: 'plan', title: 'Lập kế hoạch',
        detail: {
          thought: 'Cần số theo tháng để thấy tháng 12 nổi bật.',
          steps: [
            { id: 's1', title: 'Lấy doanh thu Internet theo tháng', status: 'done' },
            { id: 's2', title: 'Tìm nguyên nhân tháng 12', status: 'pending' },
          ],
        },
      }),
      step({
        stepId: 'q', seq: 2, action: 'query', title: 'Truy vấn chỉ số', target: 'Lấy doanh thu Internet theo tháng',
        detail: { said: 'Doanh thu Internet theo tháng: 12 dòng; cao nhất 2013-12 (1.874.360,29).' },
      }),
    ];
    const { rerender } = render(<StepTimeline steps={now} running recorded />);
    const header = screen.getByRole('button', { name: /^Đang suy luận/ });
    expect(header).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('Cần số theo tháng để thấy tháng 12 nổi bật.')).toBeInTheDocument();
    expect(screen.getByText(/12 dòng; cao nhất 2013-12/)).toBeInTheDocument();
    // The planned step no step has run for yet.
    expect(screen.getByText('Tìm nguyên nhân tháng 12')).toBeInTheDocument();
    // The answer arrives: the account folds to one line.
    rerender(<StepTimeline steps={now} running={false} durationMs={21000} recorded />);
    expect(screen.getByRole('button', { name: /^Đã thực hiện 2 bước · 21 giây/ })).toHaveAttribute('aria-expanded', 'false');
  });

  it('says why a plan was changed', () => {
    const changed = step({
      stepId: 'r', seq: 3, action: 'replan', title: 'Điều chỉnh kế hoạch', target: 'Không chỉ số chuẩn nào đo khách hàng',
      detail: { thought: 'Đổi sang đếm số khách hàng mua qua Internet.', steps: [] },
    });
    render(<StepTimeline steps={[changed]} running recorded />);
    expect(screen.getByText(/Điều chỉnh vì: Không chỉ số chuẩn nào đo khách hàng/)).toBeInTheDocument();
    expect(screen.getByText('Đổi sang đếm số khách hàng mua qua Internet.')).toBeInTheDocument();
  });

  it('counts the failed steps on the folded line', () => {
    render(<StepTimeline steps={[...steps, step({ stepId: 's5', seq: 5, status: 'failed' })]} running={false} durationMs={2400} recorded />);
    expect(screen.getByRole('button', { name: 'Đã thực hiện 4 bước · 2,4 giây · 1 bước lỗi' })).toBeInTheDocument();
  });

  it('OP-8 shows operator.why when the turn has ended', () => {
    render(
      <StepTimeline
        steps={steps}
        running={false}
        durationMs={2400}
        recorded
        operatorWhy="Có bước lỗi (s2); đã replan 1 lần."
      />,
    );
    expect(screen.getByTestId('operator-why')).toHaveTextContent('Có bước lỗi (s2)');
  });

  it('opens a query step to its SQL, read through the query run', async () => {
    render(<StepTimeline steps={steps} running={false} durationMs={2400} recorded />);
    fireEvent.click(screen.getByRole('button', { name: /Đã thực hiện/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Xem chi tiết: Truy vấn' }));
    expect(await screen.findByText(/GROUP BY vung/)).toBeInTheDocument();
    expect(screen.getByText(/1 dòng/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sao chép SQL' })).toBeInTheDocument();
  });

  it('says when the SQL may not be read', async () => {
    const hidden = [step({ stepId: 's2', action: 'query', title: 'Truy vấn', detail: { row_count: 1, query_run_id: 'run-hidden' } })];
    render(<StepTimeline steps={hidden} running={false} durationMs={100} recorded />);
    fireEvent.click(screen.getByRole('button', { name: /Đã thực hiện/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Xem chi tiết: Truy vấn' }));
    expect(await screen.findByText(/không có quyền xem SQL/)).toBeInTheDocument();
  });

  it('shows the reading of the question and nests the model call', () => {
    render(<StepTimeline steps={steps} running={false} durationMs={2400} recorded />);
    fireEvent.click(screen.getByRole('button', { name: /Đã thực hiện/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Xem chi tiết: Hiểu câu hỏi' }));
    expect(screen.getByText('Doanh thu Internet')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Xem chi tiết: Viết câu trả lời' }));
    expect(screen.getByText('Mô hình responder · 251 + 45 token · 760 ms')).toBeInTheDocument();
  });

  it('a failed step says why', () => {
    const failed = [step({ stepId: 's2', action: 'query', title: 'Truy vấn', status: 'failed', summary: 'Chưa truy vấn được nguồn Kho.' })];
    render(<StepTimeline steps={failed} running={false} durationMs={100} recorded />);
    fireEvent.click(screen.getByRole('button', { name: /Đã thực hiện/ }));
    const row = screen.getByRole('listitem');
    expect(within(row).getByText(/Chưa truy vấn được nguồn Kho\./)).toBeInTheDocument();
    expect(within(row).getByLabelText('Lỗi')).toBeInTheDocument();
  });

  it('the search for a period with data says what was asked, what exists and what is shown', () => {
    const explored = [step({
      stepId: 's5', action: 'explore', title: 'Tìm kỳ có dữ liệu', target: 'năm 2026',
      detail: { asked: 'năm 2026', grain: 'year', available: ['năm 2013', 'năm 2014'], chosen: 'năm 2014', query_run_id: 'run-1' },
    })];
    render(<StepTimeline steps={explored} running={false} durationMs={100} recorded />);
    fireEvent.click(screen.getByRole('button', { name: /Đã thực hiện/ }));
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
    fireEvent.click(screen.getByRole('button', { name: /Đã thực hiện/ }));
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
    fireEvent.click(screen.getByRole('button', { name: /Đã thực hiện/ }));
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
