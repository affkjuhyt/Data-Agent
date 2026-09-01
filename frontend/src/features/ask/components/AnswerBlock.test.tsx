import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { AnswerBlock } from './AnswerBlock';
import { AnswerTheme } from './AnswerTheme';
import { ClarificationCard } from './ClarificationCard';
import { DataGrid } from './DataGrid';
import { runChips } from './evidenceSummary';
import type { AskMessage, Evidence, EvidenceRun } from '../types';

const run: EvidenceRun = {
  id: 'sql_1',
  kind: 'sql',
  db_name: 'dw',
  db_type: 'postgres',
  sql: 'SELECT region, SUM(amount) AS revenue FROM fact_sales GROUP BY region',
  row_count: 1204,
  generated_by: 'semantic_layer',
  metric: { metric: 'doanh_thu_thuan@3', owner: 'Phòng Tài chính', aggregation: 'sum', unit: 'VND' },
  provenance: { status: 'fresh', as_of: '2026-06-30T23:00:00', tables: [{ table: 'fact_sales' }, { table: 'dim_region' }] },
  aggregation: { findings: [], structurally_safe: true, aggregates: ['SUM'], joins: 0 },
  truncation: null,
  columns: ['region', 'revenue'],
  rows: [
    { region: 'Miền Bắc', revenue: '612000000.00' },
    { region: 'Miền Nam', revenue: 480000000 },
    { region: 'Miền Trung', revenue: 192560000 },
  ],
  rows_capped: false,
};

const evidence: Evidence = { runs: [run], clarifications: [], warnings: [] };

const msg: AskMessage = {
  id: 'a1',
  role: 'assistant',
  content: 'Doanh thu thuần tháng 6/2026 là **1.284.560.000 ₫** [1].',
  timestamp: '2026-06-30T14:02:00',
  status: 'done',
  evidence,
  citations: [{ index: 1, source: 'tool/sql', type: 'db', snippet: '[...]' }],
  suggestions: ['So với tháng 5', 'Tách theo sản phẩm'],
};

const wrap = (ui: React.ReactElement) => render(<AnswerTheme mode="light">{ui}</AnswerTheme>);

describe('AnswerBlock', () => {
  it('shows answer, table, evidence strip with metric + SQL toggle, and suggestions', () => {
    wrap(<AnswerBlock msg={msg} onSuggestion={() => {}} />);

    expect(screen.getByText(/Doanh thu thuần tháng 6\/2026/)).toBeInTheDocument();

    const grid = screen.getByTestId('data-grid');
    expect(within(grid).getByText(/1\.204 dòng/)).toBeInTheDocument();
    // Vietnamese number formatting of a numeric string from Postgres
    expect(within(grid).getByText('612.000.000')).toBeInTheDocument();

    const strip = screen.getByTestId('evidence-strip');
    expect(within(strip).getByText(/Chỉ số đã duyệt · doanh_thu_thuan v3/)).toBeInTheDocument();
    expect(within(strip).getByText('2 bảng')).toBeInTheDocument();
    expect(within(strip).getByText(/Dữ liệu tươi/)).toBeInTheDocument();

    expect(screen.queryByTestId('sql-block')).not.toBeInTheDocument();
    fireEvent.click(within(strip).getByRole('button', { name: /Xem SQL/ }));
    expect(screen.getByTestId('sql-block')).toHaveTextContent('SELECT region, SUM(amount)');

    expect(screen.getByRole('button', { name: /So với tháng 5/ })).toBeInTheDocument();
  });

  it('labels model-written SQL honestly', () => {
    const llmRun: EvidenceRun = { ...run, generated_by: 'llm', metric: null, aggregation: { findings: [{ kind: 'fan_out', detail: 'join có thể nhân đôi dòng' }], structurally_safe: false, aggregates: ['SUM'], joins: 1 } };
    const chips = runChips(llmRun).map(c => c.label);
    expect(chips).toContain('SQL do mô hình sinh');
    expect(chips).toContain('Rủi ro cộng gộp');
  });

  it('renders the clarification card and forwards the chosen option in Vietnamese', () => {
    const onAnswer = vi.fn();
    const asking: AskMessage = {
      ...msg,
      content: 'Bạn muốn xem trong khoảng thời gian nào?',
      evidence: undefined,
      citations: undefined,
      suggestions: undefined,
      clarification: {
        kind: 'followup',
        questions: [{ reason: 'missing_time_range', text: 'Bạn muốn xem trong khoảng thời gian nào?', options: ['this_month', 'last_quarter'] }],
      },
    };
    wrap(<AnswerBlock msg={asking} onAnswerClarification={onAnswer} />);

    const card = screen.getByTestId('clarification-card');
    expect(within(card).getByText('Thiếu khoảng thời gian')).toBeInTheDocument();
    fireEvent.click(within(card).getByRole('button', { name: 'Quý trước' }));
    expect(onAnswer).toHaveBeenCalledWith('Quý trước');
  });

  it('shows typing dots while streaming with no text', () => {
    wrap(<AnswerBlock msg={{ ...msg, content: '', status: 'streaming', evidence: undefined, citations: undefined, suggestions: undefined }} />);
    expect(screen.getByTestId('typing')).toBeInTheDocument();
    expect(screen.queryByTestId('evidence-strip')).not.toBeInTheDocument();
  });
});

describe('ClarificationCard', () => {
  it('accepts a free-text answer on Enter', () => {
    const onAnswer = vi.fn();
    wrap(<ClarificationCard clarification={{ kind: 'resume', sessionId: 's1', questions: [{ text: 'Kỳ nào?' }] }} onAnswer={onAnswer} />);
    expect(screen.getByText('Đang chờ bạn trả lời')).toBeInTheDocument();
    const input = screen.getByLabelText('Trả lời khác');
    fireEvent.change(input, { target: { value: 'từ 01/06 đến 15/06' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onAnswer).toHaveBeenCalledWith('từ 01/06 đến 15/06');
  });
});

describe('DataGrid', () => {
  const rows = Array.from({ length: 25 }, (_, i) => ({ id: `SO-${String(i + 1).padStart(3, '0')}`, amount: (25 - i) * 1000 }));

  it('pages in place and sorts on header click', () => {
    wrap(<DataGrid columns={['id', 'amount']} rows={rows} pageSize={10} />);
    expect(screen.getAllByRole('row')).toHaveLength(11); // header + 10
    fireEvent.click(screen.getByRole('button', { name: /Xem thêm/ }));
    expect(screen.getAllByRole('row')).toHaveLength(26); // all 25 rows + header

    const amountHeader = screen.getByRole('columnheader', { name: /amount/ });
    fireEvent.click(amountHeader); // asc
    const firstDataRow = screen.getAllByRole('row')[1];
    expect(within(firstDataRow).getByText('1.000')).toBeInTheDocument();
    expect(amountHeader).toHaveAttribute('aria-sort', 'ascending');
  });

  it('flags a truncated result', () => {
    wrap(<DataGrid columns={['id']} rows={[{ id: 1 }]} truncation={{ reason: 'row_limit', rows_returned: 1 }} />);
    expect(screen.getByText(/trần số dòng/)).toBeInTheDocument();
  });

  it('loads full result pages from the backend artifact', async () => {
    const loadRows = vi.fn().mockResolvedValue({
      columns: ['id', 'amount'],
      rows: [{ id: 'SO-501', amount: 501000 }],
      total: 1200,
      offset: 0,
      limit: 500,
      has_more: true,
    });

    wrap(
      <DataGrid
        columns={['id', 'amount']}
        rows={[{ id: 'SO-001', amount: 1000 }]}
        fullResult={{ totalRows: 1200, loadRows, onExportXlsx: vi.fn() }}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Full result' }));

    await waitFor(() => expect(loadRows).toHaveBeenCalledWith({ offset: 0, limit: 500, sort: undefined, dir: undefined }));
    expect(screen.getByText('SO-501')).toBeInTheDocument();
    expect(screen.getByText(/Full result 1-1 \/ 1\.200 dòng/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Excel \+ Nguồn/ })).toBeInTheDocument();
  });
});
