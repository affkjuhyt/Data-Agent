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
  source: 'Định nghĩa chỉ số AW-KPI-01',
  type: 'document',
  snippet: 'Biên lợi nhuận gộp Internet bằng tổng lãi gộp chia tổng doanh thu Internet',
  doc_id: 'a0000000-0000-0000-0000-000000aa0e01',
  score: 0.91,
};

describe('MarkdownContent', () => {
  it('draws GFM: a table, bold, lists and code', () => {
    const { container } = render(
      <MarkdownContent
        text={[
          '## Biên lợi nhuận',
          'Biên **giảm** ở Bikes.',
          '',
          '| Nhóm sản phẩm | Biên |',
          '| --- | --- |',
          '| Bikes | 40,5% |',
          '',
          '- tháng 11/2013',
          '- tháng 12/2013',
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
    expect(screen.getByRole('cell', { name: '40,5%' })).toBeInTheDocument();
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
    const marker = screen.getByRole('button', { name: 'Mở nguồn 1: Định nghĩa chỉ số AW-KPI-01' });
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

  it('loads no image the text names: an injected one would carry the data away', () => {
    const { container } = render(
      <MarkdownContent text={'Doanh thu 16,35 triệu USD ![biểu đồ](https://evil.example/c.png?q=1635)'} />,
    );
    expect(container.querySelector('img')).toBeNull();
    expect(container.innerHTML).not.toContain('evil.example');
  });

  it('opens an http link in a new tab without an opener', () => {
    render(<MarkdownContent text="[AdventureWorks](https://github.com/microsoft/sql-server-samples)" />);
    const link = screen.getByRole('link', { name: 'AdventureWorks' });
    expect(link).toHaveAttribute('href', 'https://github.com/microsoft/sql-server-samples');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });
});
