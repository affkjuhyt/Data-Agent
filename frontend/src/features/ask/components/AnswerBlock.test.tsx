import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { AnswerBlock } from './AnswerBlock';
import { AnswerTheme } from './AnswerTheme';
import { ClarificationCard } from './ClarificationCard';
import { DataGrid } from './DataGrid';
import { runChips } from './evidenceSummary';
import type { AskMessage, Evidence, EvidenceRun, ResultSet } from '../types';

const run: EvidenceRun = {
  id: 'sql_1',
  kind: 'sql',
  db_name: 'dw',
  db_type: 'postgres',
  sql: 'SELECT region, SUM(amount) AS revenue FROM fact_sales GROUP BY region',
  row_count: 1204,
  generated_by: 'semantic_layer',
  metric: { metric: 'net_revenue@3', owner: 'Finance department', aggregation: 'sum', unit: 'USD' },
  provenance: { status: 'fresh', as_of: '2026-06-30T23:00:00', tables: [{ table: 'fact_sales' }, { table: 'dim_region' }] },
  aggregation: { findings: [], structurally_safe: true, aggregates: ['SUM'], joins: 0 },
  truncation: null,
  columns: ['region', 'revenue'],
  rows: [
    { region: 'North', revenue: '612000000.00' },
    { region: 'South', revenue: 480000000 },
    { region: 'Central', revenue: 192560000 },
  ],
  rows_capped: false,
};

const evidence: Evidence = { runs: [run], clarifications: [], warnings: [] };

const msg: AskMessage = {
  id: 'a1',
  role: 'assistant',
  content: 'Net revenue for June 2026 is **$1,284,560,000** [1].',
  timestamp: '2026-06-30T14:02:00',
  status: 'done',
  evidence,
  citations: [{ index: 1, source: 'tool/sql', type: 'db', snippet: '[...]' }],
  suggestions: ['Compare to May', 'Split by product'],
};

const wrap = (ui: React.ReactElement) => render(<AnswerTheme mode="light">{ui}</AnswerTheme>);

describe('AnswerBlock', () => {
  it('renders every first-class result set, including an empty result', () => {
    const base: ResultSet = {
      result_id: '3f2a9c1e5b7d4e0f8a6b2c4d1e3f5a7b',
      kind: 'table',
      columns: [{ name: 'region', type: 'text' }, { name: 'revenue', type: 'number', unit: 'VND' }],
      row_count: 1200,
      rows_preview: Array.from({ length: 50 }, (_, i) => ({ region: `Region ${i + 1}`, revenue: i + 1 })),
      stats: { revenue: { nulls: 0, min: '10', max: '10', sum: '10' } },
      provenance: { connection_id: 'dw', engine: 'postgres', sql: 'SELECT 1', tables: ['fact_sales'], executed_at: '2026-10-06T09:15:00Z', generated_by: 'llm' },
      trust: 'EXPLORATORY', warnings: [], full_rows_available: false,
    };
    const empty = { ...base, result_id: '4f2a9c1e5b7d4e0f8a6b2c4d1e3f5a7c', columns: [], row_count: 0, rows_preview: [] };
    const truncated = {
      ...base,
      result_id: '5f2a9c1e5b7d4e0f8a6b2c4d1e3f5a7d',
      row_count: 10000,
      provenance: { ...base.provenance, truncation: { reason: 'row_limit', rows_returned: 10000 } },
      warnings: [{ code: 'truncated', detail: 'row_limit' }],
    };
    wrap(<AnswerBlock msg={{ ...msg, evidence: undefined, resultSets: [base, empty, truncated] }} />);

    expect(screen.getAllByTestId('data-grid')).toHaveLength(3);
    expect(screen.getAllByRole('columnheader', { name: /revenue \(VND\)/ })).toHaveLength(2);
    expect(screen.getByText(/Query result 1/)).toBeInTheDocument();
    expect(screen.getAllByText(/1\.200 rows/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Preview 10 \/ 50 rows/)).toHaveLength(2);
    expect(screen.getByText('Không có dòng nào khớp điều kiện')).toBeInTheDocument();
    expect(screen.getByText('Kết quả bị cắt ở 10.000 dòng')).toBeInTheDocument();
    expect(screen.getAllByText('Model-generated SQL').length).toBeGreaterThan(0);
    expect(screen.getAllByText('EXPLORATORY').length).toBeGreaterThan(0);
  });

  it('shows answer, table, evidence strip with metric + SQL toggle, and suggestions', () => {
    wrap(<AnswerBlock msg={msg} onSuggestion={() => {}} />);

    expect(screen.getByText(/Net revenue for June 2026/)).toBeInTheDocument();

    const grid = screen.getByTestId('data-grid');
    expect(within(grid).getByText(/1\.204 rows/)).toBeInTheDocument();
    // US number formatting of a numeric string from Postgres
    expect(within(grid).getByText('612.000.000')).toBeInTheDocument();

    const strip = screen.getByTestId('evidence-strip');
    expect(within(strip).getByText(/Approved metric · net_revenue v3/)).toBeInTheDocument();
    expect(within(strip).getByText('2 tables')).toBeInTheDocument();
    expect(within(strip).getByText(/Fresh data/)).toBeInTheDocument();

    expect(screen.queryByTestId('sql-block')).not.toBeInTheDocument();
    fireEvent.click(within(strip).getByRole('button', { name: /View SQL/ }));
    expect(screen.getByTestId('sql-block')).toHaveTextContent('SELECT region, SUM(amount)');

    expect(screen.getByRole('button', { name: /Compare to May/ })).toBeInTheDocument();
  });

  it('labels model-written SQL honestly', () => {
    const llmRun: EvidenceRun = { ...run, generated_by: 'llm', metric: null, aggregation: { findings: [{ kind: 'fan_out', detail: 'join may duplicate rows' }], structurally_safe: false, aggregates: ['SUM'], joins: 1 } };
    const chips = runChips(llmRun).map(c => c.label);
    expect(chips).toContain('Model-generated SQL');
    expect(chips).toContain('Aggregation risk');
  });

  it('renders the clarification card and forwards the chosen option', () => {
    const onAnswer = vi.fn();
    const asking: AskMessage = {
      ...msg,
      content: 'What time range do you want to see?',
      evidence: undefined,
      citations: undefined,
      suggestions: undefined,
      clarification: {
        kind: 'followup',
        questions: [{ reason: 'missing_time_range', text: 'What time range do you want to see?', options: ['this_month', 'last_quarter'] }],
      },
    };
    wrap(<AnswerBlock msg={asking} onAnswerClarification={onAnswer} />);

    const card = screen.getByTestId('clarification-card');
    expect(within(card).getByText('Missing time range')).toBeInTheDocument();
    fireEvent.click(within(card).getByRole('button', { name: 'Last quarter' }));
    expect(onAnswer).toHaveBeenCalledWith({
      text: 'Last quarter',
      answers: [{ reason: 'missing_time_range', value: 'last_quarter' }],
    });
  });

  it('shows an assumption apart from warnings', () => {
    const assumed: AskMessage = {
      ...msg,
      evidence: {
        ...evidence,
        warnings: [{ kind: 'assumption', reason: 'missing_time_range', detail: 'No time range chosen — computed over all history.' }],
      },
    };
    wrap(<AnswerBlock msg={assumed} />);
    expect(within(screen.getByTestId('evidence-strip')).getByTestId('assumption')).toHaveTextContent('Assumed: No time range chosen');
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
    wrap(<ClarificationCard clarification={{ kind: 'resume', sessionId: 's1', questions: [{ text: 'Which period?' }] }} onAnswer={onAnswer} />);
    expect(screen.getByText('Waiting for your answer')).toBeInTheDocument();
    const input = screen.getByLabelText('Custom answer');
    fireEvent.change(input, { target: { value: 'from 06/01 to 06/15' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onAnswer).toHaveBeenCalledWith({ text: 'from 06/01 to 06/15', answers: [] });
  });

  it('collects one pick per question and answers once all are picked', () => {
    const onAnswer = vi.fn();
    wrap(
      <ClarificationCard
        clarification={{
          kind: 'followup',
          questions: [
            { reason: 'ambiguous_metric', text: 'Which metric?', options: ['Doanh thu gộp', 'Doanh thu thuần'] },
            { reason: 'missing_time_range', text: 'Which period?', options: ['this_month', 'last_month'] },
          ],
        }}
        onAnswer={onAnswer}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Doanh thu thuần' }));
    expect(onAnswer).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Doanh thu thuần' })).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(screen.getByRole('button', { name: 'Last month' }));
    expect(onAnswer).toHaveBeenCalledTimes(1);
    expect(onAnswer).toHaveBeenCalledWith({
      text: 'Doanh thu thuần, Last month',
      answers: [
        { reason: 'ambiguous_metric', value: 'Doanh thu thuần' },
        { reason: 'missing_time_range', value: 'last_month' },
      ],
    });
  });

  it('offers Other next to the options, answered as text only', () => {
    const onAnswer = vi.fn();
    wrap(
      <ClarificationCard
        clarification={{ kind: 'followup', questions: [{ reason: 'missing_time_range', text: 'Which period?', options: ['this_month', 'last_month'] }] }}
        onAnswer={onAnswer}
      />,
    );

    expect(screen.queryByLabelText('Custom answer')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /Other/ }));
    const input = screen.getByLabelText('Custom answer');
    fireEvent.change(input, { target: { value: 'the last 45 days' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    // Not a slot value, so no structured answer — the text carries it.
    expect(onAnswer).toHaveBeenCalledWith({ text: 'the last 45 days', answers: [] });
  });

  it('pairs each answer with its question when the model asked several', () => {
    const onAnswer = vi.fn();
    wrap(
      <ClarificationCard
        clarification={{
          kind: 'resume',
          sessionId: 's1',
          questions: [
            { text: 'Which branch?', options: ['Hà Nội', 'TP.HCM'] },
            { text: 'Which period?', options: ['This quarter'] },
          ],
        }}
        onAnswer={onAnswer}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'TP.HCM' }));
    fireEvent.click(screen.getAllByRole('button', { name: /Other/ })[1]);
    const input = screen.getByLabelText('Custom answer 2');
    fireEvent.change(input, { target: { value: 'June 2026' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send answer 2' }));

    expect(onAnswer).toHaveBeenCalledWith({ text: 'Which branch? → TP.HCM; Which period? → June 2026', answers: [] });
  });

  it('sends options without a reason as text only', () => {
    const onAnswer = vi.fn();
    wrap(<ClarificationCard clarification={{ kind: 'followup', questions: [{ text: '', options: ['Yes', 'No'] }] }} onAnswer={onAnswer} />);
    fireEvent.click(screen.getByRole('button', { name: 'Yes' }));
    expect(onAnswer).toHaveBeenCalledWith({ text: 'Yes', answers: [] });
  });
});

describe('DataGrid', () => {
  const rows = Array.from({ length: 25 }, (_, i) => ({ id: `SO-${String(i + 1).padStart(3, '0')}`, amount: (25 - i) * 1000 }));

  it('pages in place and sorts on header click', () => {
    wrap(<DataGrid columns={['id', 'amount']} rows={rows} pageSize={10} />);
    expect(screen.getAllByRole('row')).toHaveLength(11); // header + 10
    fireEvent.click(screen.getByRole('button', { name: /more rows/ }));
    expect(screen.getAllByRole('row')).toHaveLength(26); // all 25 rows + header

    const amountHeader = screen.getByRole('columnheader', { name: /amount/ });
    fireEvent.click(amountHeader); // asc
    const firstDataRow = screen.getAllByRole('row')[1];
    expect(within(firstDataRow).getByText('1.000')).toBeInTheDocument();
    expect(amountHeader).toHaveAttribute('aria-sort', 'ascending');
  });

  it('flags a truncated result', () => {
    wrap(<DataGrid columns={['id']} rows={[{ id: 1 }]} truncation={{ reason: 'row_limit', rows_returned: 1 }} />);
    expect(screen.getByText('Kết quả bị cắt ở 1 dòng')).toBeInTheDocument();
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
    expect(screen.getByText(/Full result 1-1 \/ 1\.200 rows/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Excel \+ Source/ })).toBeInTheDocument();
  });
});
