/**
 * Answer text is markdown written by a model: it is drawn with GFM and its citations, and nothing
 * in it runs.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Citation } from '../types';
import { MarkdownContent } from './MarkdownContent';

const doc: Citation = {
  index: 1,
  source: 'Quy định QD-GV-2026/02',
  type: 'document',
  snippet: 'Biên lợi nhuận gộp bằng tổng lãi gộp chia tổng doanh thu thuần',
  doc_id: 'a0000000-0000-0000-0000-000000000c12',
  score: 0.91,
};

describe('MarkdownContent', () => {
  it('draws GFM: a table, bold, lists and code', () => {
    const { container } = render(
      <MarkdownContent
        text={[
          '## Biên lợi nhuận',
          'Biên **giảm** ở Hà Nội.',
          '',
          '| Chi nhánh | Biên |',
          '| --- | --- |',
          '| Hà Nội | 18,2% |',
          '',
          '- tháng 8',
          '- tháng 9',
          '',
          '```sql',
          'SELECT 1',
          '```',
        ].join('\n')}
      />,
    );
    expect(screen.getByText('Biên lợi nhuận')).toHaveClass('da-heading-2');
    expect(screen.getByText('giảm').tagName).toBe('STRONG');
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByRole('cell', { name: '18,2%' })).toBeInTheDocument();
    expect(screen.getAllByRole('listitem')).toHaveLength(2);
    expect(container.querySelector('pre code')?.textContent).toBe('SELECT 1\n');
  });

  it('turns [n] into the citation it names, and opens a document one', () => {
    const open = vi.fn();
    render(
      <MarkdownContent
        text={'Biên là lãi gộp chia doanh thu [1]. Mục [7] không có nguồn; `[1]` là code.'}
        citations={[doc]}
        onOpenCitation={open}
      />,
    );
    const marker = screen.getByRole('button', { name: 'Mở nguồn 1: Quy định QD-GV-2026/02' });
    expect(marker).toHaveAttribute('title', expect.stringContaining('điểm 0.91'));
    fireEvent.click(marker);
    expect(open).toHaveBeenCalledWith(doc);
    // A number that names no citation, and one inside code, stay as written.
    expect(screen.getByText(/Mục \[7\] không có nguồn/)).toBeInTheDocument();
    expect(screen.getByText('[1]').tagName).toBe('CODE');
  });

  it('runs nothing the model wrote: no raw HTML, no javascript: link', () => {
    const { container } = render(
      <MarkdownContent
        text={
          'Xem <img src=x onerror="alert(1)"> và <script>alert(2)</script> [bấm](javascript:alert(3))'
        }
      />,
    );
    expect(container.querySelector('img, script')).toBeNull();
    const link = screen.getByText('bấm').closest('a');
    expect(link?.getAttribute('href') ?? '').not.toMatch(/^javascript:/i);
  });

  it('opens an http link in a new tab without an opener', () => {
    render(<MarkdownContent text="[NHNN](https://www.sbv.gov.vn)" />);
    const link = screen.getByRole('link', { name: 'NHNN' });
    expect(link).toHaveAttribute('href', 'https://www.sbv.gov.vn');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });
});
