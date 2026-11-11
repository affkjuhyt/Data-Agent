import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Citation } from '../types';
import { CitationPassagePanel } from './CitationPassagePanel';

const citation: Citation = {
  index: 1,
  source: 'TT 31/2024 · Điều 10',
  type: 'document',
  snippet: 'Khoản cơ cấu lại lần 2 xếp tối thiểu vào nhóm 3.',
  doc_id: 'doc-1',
  page: 4,
};

describe('CitationPassagePanel', () => {
  it('shows the passage and opens the viewer', () => {
    const onOpen = vi.fn();
    const onClose = vi.fn();
    render(<CitationPassagePanel citation={citation} onOpen={onOpen} onClose={onClose} />);
    expect(screen.getByRole('heading', { name: /TT 31\/2024/ })).toBeInTheDocument();
    expect(screen.getByText(/nhóm 3/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Mở đoạn gốc' }));
    expect(onOpen).toHaveBeenCalledWith(citation);
    fireEvent.click(screen.getByRole('button', { name: 'Đóng' }));
    expect(onClose).toHaveBeenCalled();
  });

  it('hides when there is no document citation', () => {
    const { container } = render(
      <CitationPassagePanel
        citation={{ ...citation, type: 'db' }}
        onOpen={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
