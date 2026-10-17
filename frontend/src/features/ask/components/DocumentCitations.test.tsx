import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { AnswerBlock } from './AnswerBlock';
import { citationsOf } from '../hooks/useQuestionStream';
import type { AskMessage } from '../types';

const citations = citationsOf({
  citations: [
    {
      n: 1,
      chunk_id: 'ch-1',
      document_id: 'doc-qd123',
      document_title: 'Quyết định lãi suất huy động',
      doc_no: '123/QĐ-NH',
      section_path: 'Điều 5. Lãi suất',
      page_from: 14,
      superseded_by: null,
      quote: 'Kỳ hạn 12 tháng: 5,8%/năm',
      score: 0.82,
    },
  ],
});

const msg: AskMessage = {
  id: 'a1',
  role: 'assistant',
  content: 'Kỳ hạn 12 tháng có lãi suất 5,8%/năm [1].',
  timestamp: '2026-10-17T09:00:00',
  status: 'done',
  citations,
};

describe('document citations on the Ask screen', () => {
  it('reads qa.answer.citations as numbered document sources', () => {
    expect(citations).toEqual([
      {
        index: 1,
        source: 'Quyết định lãi suất huy động · Điều 5. Lãi suất · trang 14',
        type: 'document',
        snippet: 'Kỳ hạn 12 tháng: 5,8%/năm',
        doc_id: 'doc-qd123',
        page: 14,
        score: 0.82,
      },
    ]);
    expect(citationsOf({ citations: [] })).toEqual([]);
  });

  it('opens the cited document from the [n] marker and from the source list', () => {
    const open = vi.fn();
    render(<AnswerBlock msg={msg} question="Lãi suất 12 tháng?" onOpenCitation={open} />);
    fireEvent.click(screen.getByRole('button', { name: /Mở nguồn 1/ }));
    expect(open).toHaveBeenLastCalledWith(citations[0]);
    fireEvent.click(screen.getByRole('button', { name: citations[0].source }));
    expect(open).toHaveBeenCalledTimes(2);
  });

  it('labels an answer with document citations as from documents', () => {
    render(<AnswerBlock msg={msg} question="Lãi suất 12 tháng?" />);
    expect(screen.getByTestId('trust-label')).toHaveTextContent('Giả thuyết từ tài liệu');
  });
});
