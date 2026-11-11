import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { BlockBoundary, PageBoundary } from './ErrorBoundaries';

let broken = true;
function Flaky({ label }: { label: string }) {
  if (broken) throw new Error('render failed');
  return <p>{label}</p>;
}

// React logs a caught render error to the console; the test only wants what the screen shows.
beforeEach(() => {
  broken = true;
  vi.spyOn(console, 'error').mockImplementation(() => undefined);
});
afterEach(() => vi.restoreAllMocks());

describe('PageBoundary', () => {
  it('keeps the shell when a page fails to render, and retries it', () => {
    render(
      <div>
        <nav>Thanh điều hướng</nav>
        <PageBoundary resetKeys={['data', 'SOURCES']}>
          <Flaky label="Nguồn dữ liệu" />
        </PageBoundary>
      </div>,
    );
    expect(screen.getByText('Thanh điều hướng')).toBeInTheDocument();
    expect(screen.getByText('Màn hình này gặp lỗi khi hiển thị')).toBeInTheDocument();
    broken = false;
    fireEvent.click(screen.getByRole('button', { name: 'Thử lại' }));
    expect(screen.getByText('Nguồn dữ liệu')).toBeInTheDocument();
  });

  it('starts over when the page changes', () => {
    function Shell() {
      const [page, setPage] = useState('A');
      return (
        <>
          <button type="button" onClick={() => setPage('B')}>
            Sang trang B
          </button>
          <PageBoundary resetKeys={[page]}>
            <Flaky label={`Trang ${page}`} />
          </PageBoundary>
        </>
      );
    }
    render(<Shell />);
    expect(screen.getByText('Màn hình này gặp lỗi khi hiển thị')).toBeInTheDocument();
    broken = false;
    fireEvent.click(screen.getByRole('button', { name: 'Sang trang B' }));
    expect(screen.getByText('Trang B')).toBeInTheDocument();
  });
});

describe('BlockBoundary', () => {
  it('leaves the other blocks of an answer in place', () => {
    render(
      <>
        <BlockBoundary>
          <Flaky label="Biểu đồ" />
        </BlockBoundary>
        <p>Kết luận vẫn hiện</p>
      </>,
    );
    expect(screen.getByText('Không hiển thị được phần này của câu trả lời.')).toBeInTheDocument();
    expect(screen.getByText('Kết luận vẫn hiện')).toBeInTheDocument();
  });
});
