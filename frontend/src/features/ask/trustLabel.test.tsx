/**
 * R1-14: every answer the Ask screen draws carries the trust label its numbers earn (invariant 14),
 * read from what the worker stored with it (figures[].trust, citations), and an answer whose query
 * the model wrote shows how far its ways of writing it agreed.
 */
import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { Answer } from '../../lib/v1/questions';
import { fromAnswer, hasReceipt } from './answerMessage';
import { AnswerBlock } from './components/AnswerBlock';
import { AnswerTheme } from './components/AnswerTheme';
import type { AskMessage } from './types';

const at = '2026-10-30T08:00:00Z';
const figure = (trust: string) => ({
  key: 'f1',
  label: 'Tỷ lệ nợ xấu',
  value: '2.10',
  unit: '%',
  decimals: 2,
  dimensions: {},
  display_state: 'shown',
  query_run_id: 'qr-1',
  metric_version_id: 'mv-1',
  trust,
  source: { query_run_id: 'qr-1', row: 0, col: 'value' },
});
const verification = (extra: Record<string, unknown> = {}) => ({
  verification: {
    verdict: 'PASS',
    iteration: 1,
    checks: [{ name: 'Số trong câu có nguồn', passed: true, detail: '1/1' }],
    ...extra,
  },
});
const answer = (over: Partial<Record<keyof Answer, unknown>>): Answer =>
  ({
    id: 'a-1',
    question_id: 'q-1',
    plan_id: 'p-1',
    kind: 'data',
    forecast_job_id: null,
    number_check: verification(),
    text: 'Tỷ lệ nợ xấu tháng 9/2026 là 2,10%.',
    figures: [],
    chart_spec: null,
    citations: [],
    filed_vs_current: null,
    reconcile_badge: null,
    agent_name: 'Agent Phân tích',
    latency_ms: 900,
    lineage_hash: null,
    data_product_id: null,
    product_scope: 'not_applicable',
    created_at: at,
    ...over,
  }) as Answer;

function drawn(a: Answer) {
  const msg: AskMessage = {
    id: 'm-1',
    role: 'assistant',
    content: a.text ?? '',
    timestamp: at,
    status: 'done',
    answerId: a.id,
    ...fromAnswer(a),
  } as AskMessage;
  render(
    <AnswerTheme mode="light">
      <AnswerBlock msg={msg} />
    </AnswerTheme>,
  );
  return within(screen.getByTestId('trust-label'));
}

describe('the trust label of an answer', () => {
  it.each([
    ['a certified metric in a data product', 'Theo chỉ số chuẩn', 'Theo chỉ số chuẩn'],
    ['a metric with no approved definition', 'chưa có định nghĩa chuẩn', 'Chưa có định nghĩa chuẩn'],
    ['a query the model wrote', 'Khám phá (truy vấn do mô hình viết)', 'Khám phá (truy vấn do mô hình viết)'],
  ])('%s is labelled from its figures', (_, trust, shown) => {
    expect(drawn(answer({ figures: [figure(trust)] })).getByText(shown)).toBeInTheDocument();
  });

  it('AC-01-07-03 an answer outside any data product is labelled so and offers no receipt', () => {
    const outside = drawn(
      answer({
        product_scope: 'out_of_scope',
        figures: [figure('Ngoài phạm vi data product, không có biên nhận')],
      }),
    );
    expect(outside.getByText('Ngoài phạm vi data product, không có biên nhận')).toBeInTheDocument();
    const shown = (a: Answer) => {
      const m = fromAnswer(a);
      return hasReceipt({ answerId: a.id, content: m.content ?? '', productScope: m.productScope });
    };
    expect(shown(answer({ product_scope: 'out_of_scope' }))).toBe(false);
    expect(shown(answer({ product_scope: 'in_scope' }))).toBe(true);
  });

  it('a document answer is a hypothesis from documents, and one with no source has no evidence', () => {
    const cited = answer({
      kind: 'document',
      text: 'Theo quy định, kỳ chốt là ngày 5 [1].',
      number_check: verification(),
      citations: [
        {
          n: 1,
          document_id: 'd-1',
          document_title: 'Quy chế chốt kỳ',
          doc_no: 'QĐ-01',
          section_path: 'Điều 3',
          page_from: 2,
          superseded_by: null,
          quote: 'Kỳ chốt là ngày 5 hằng tháng.',
        },
      ],
    });
    expect(drawn(cited).getByText('Giả thuyết từ tài liệu')).toBeInTheDocument();
  });

  it('a document answer that found nothing says there is no evidence', () => {
    const empty = answer({
      kind: 'document',
      text: 'Không tìm thấy trong tài liệu được phép nội dung trả lời câu hỏi này.',
    });
    expect(drawn(empty).getByText('Chưa đủ bằng chứng')).toBeInTheDocument();
  });

  it('a query the model wrote shows how far its ways of writing agreed', () => {
    drawn(
      answer({
        figures: [figure('Khám phá (truy vấn do mô hình viết)')],
        number_check: verification({ confidence: 0.6667, consensus: { agreeing: 4, written: 6 } }),
      }),
    );
    const meter = screen.getByRole('meter', { name: 'Độ tin cậy' });
    expect(meter).toHaveAttribute('aria-valuenow', '67');
    expect(meter).toHaveAttribute('aria-valuetext', '4/6 cách viết truy vấn cho cùng kết quả');
  });

  it('a governed answer has no vote, so no meter', () => {
    drawn(answer({ figures: [figure('Theo chỉ số chuẩn')] }));
    expect(screen.queryByTestId('confidence-meter')).toBeNull();
  });
});
