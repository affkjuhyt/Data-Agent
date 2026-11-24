/**
 * RG-09 Sức khoẻ kho tài liệu (DI-21) against GET /v1/document-health: the counts, the issues by
 * kind and severity, the documents worst off opening in the viewer, and the collection filter.
 */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import type { DocumentHealth } from '../../lib/v1/documents';
import CorpusHealthPage from './CorpusHealthPage';

const navigate = vi.fn();
vi.mock('../../AppStateContext', () => ({ useAppState: () => ({ navigate }) }));

const HEALTH: DocumentHealth = {
  documents: 4,
  by_status: [
    { status: 'ready', count: 2 },
    { status: 'waiting_review', count: 1 },
    { status: 'failed', count: 1 },
  ],
  pages_read: 40,
  pages_unusable: 10,
  open_issues: 4,
  blocking_issues: 2,
  gated_documents: 1,
  chunks: 120,
  chunks_unindexed: 3,
  chunks_injection_suspected: 1,
  expired_documents: 1,
  superseded_documents: 2,
  issues: [
    { kind: 'low_ocr', severity: 'blocking', count: 2, documents: 1 },
    { kind: 'garbled_text', severity: 'warning', count: 1, documents: 1 },
    { kind: 'pii_unmasked', severity: 'warning', count: 1, documents: 1 },
  ],
  most_affected: [
    {
      id: 'doc-scan',
      collection_id: 'col-laws',
      file_name: 'scan.pdf',
      title: 'Thông tư 31 bản quét',
      status: 'waiting_review',
      page_count: 12,
      pages_unusable: 8,
      open_issues: 2,
      blocking_issues: 2,
      kinds: ['low_ocr'],
    },
    {
      id: 'doc-contract',
      collection_id: 'col-contracts',
      file_name: 'hop_dong.pdf',
      title: null,
      status: 'ready',
      page_count: null,
      pages_unusable: 0,
      open_issues: 2,
      blocking_issues: 0,
      kinds: ['garbled_text', 'pii_unmasked'],
    },
  ],
};

const COLLECTIONS = [
  { id: 'col-laws', name: 'Văn bản pháp quy' },
  { id: 'col-contracts', name: 'Hợp đồng' },
].map((c) => ({ id: c.id, name: c.name, display: { ...c, icon: 'file' } }));

let asked: (string | null)[] = [];
let answer: DocumentHealth = HEALTH;
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
beforeEach(() => {
  asked = [];
  answer = HEALTH;
  server.resetHandlers(
    http.get('*/v1/collections', () =>
      HttpResponse.json({ items: COLLECTIONS, next_page_token: '' }),
    ),
    http.get('*/v1/document-health', ({ request }) => {
      const collection = new URL(request.url).searchParams.get('collection_id');
      asked.push(collection);
      return HttpResponse.json(
        collection === 'col-contracts'
          ? { ...answer, documents: 1, most_affected: answer.most_affected.slice(1) }
          : answer,
      );
    }),
  );
});
afterEach(() => navigate.mockReset());
afterAll(() => server.close());

const wrap = () =>
  render(
    <ToastProvider>
      <CorpusHealthPage />
    </ToastProvider>,
  );

describe('CorpusHealthPage', () => {
  it('counts the corpus and names its open issues by kind and severity', async () => {
    wrap();
    expect(
      await screen.findByRole('heading', { name: 'Sức khoẻ kho tài liệu' }),
    ).toBeInTheDocument();
    expect(await screen.findByText('50% sẵn sàng để trả lời')).toBeInTheDocument();
    expect(screen.getByText('2 chặn tài liệu')).toBeInTheDocument();
    expect(screen.getByText('25% của 40 trang đã đọc')).toBeInTheDocument();
    expect(screen.getByText('Bị giữ chờ duyệt')).toBeInTheDocument();

    const status = screen.getByRole('region', { name: 'Tài liệu theo trạng thái' });
    expect(within(status).getByText('Sẵn sàng')).toBeInTheDocument();
    expect(within(status).getByText('Chờ người xem')).toBeInTheDocument();
    expect(within(status).getByText('Lỗi')).toBeInTheDocument();

    const facts = within(screen.getByLabelText('Ảnh hưởng tới truy hồi'));
    expect(facts.getByText('Đoạn chưa có vector').nextSibling).toHaveTextContent('3 / 120');
    expect(facts.getByText('Đoạn nghi chèn lệnh').nextSibling).toHaveTextContent('1');
    expect(facts.getByText('Tài liệu hết hiệu lực').nextSibling).toHaveTextContent('1');
    expect(facts.getByText('Tài liệu đã bị thay thế').nextSibling).toHaveTextContent('2');

    const issues = screen.getByRole('region', { name: 'Vấn đề đang mở theo loại' });
    const rows = within(issues).getAllByRole('row').slice(1);
    expect(rows.map((r) => r.textContent)).toEqual([
      'Nhận dạng chữ độ tin thấpChặn21',
      'Chữ bị lỗi mãCảnh báo11',
      'Có dữ liệu cá nhân chưa cheCảnh báo11',
    ]);
  });

  it('lists the documents worst off, each opening in the viewer', async () => {
    wrap();
    const affected = await screen.findByRole('region', {
      name: 'Tài liệu bị ảnh hưởng nhiều nhất',
    });
    const [scan, contract] = within(affected).getAllByRole('row').slice(1);
    expect(scan).toHaveTextContent('Thông tư 31 bản quét');
    expect(scan).toHaveTextContent('2 · 2 chặn');
    expect(scan).toHaveTextContent('8 / 12');
    // Without a title the file name stands in; without a page count the bare number.
    expect(contract).toHaveTextContent('hop_dong.pdf');
    expect(contract).toHaveTextContent('Chữ bị lỗi mã, Có dữ liệu cá nhân chưa che');

    const link = within(scan).getByRole('link', {
      name: 'Mở Thông tư 31 bản quét trong trình xem tài liệu',
    });
    expect(link).toHaveAttribute('href', '/data/document/doc-scan');
    fireEvent.click(link);
    expect(navigate).toHaveBeenCalledWith('data', 'DOCUMENT', 'doc-scan');

    fireEvent.click(screen.getByRole('link', { name: 'Mở hàng chờ chất lượng' }));
    expect(navigate).toHaveBeenCalledWith('data', 'DOC_QUALITY');
  });

  it('reads one collection when the filter names it', async () => {
    wrap();
    await screen.findByText('Thông tư 31 bản quét');
    fireEvent.click(screen.getByRole('button', { name: /Bộ sưu tập/ }));
    fireEvent.click(await screen.findByRole('option', { name: 'Hợp đồng' }));
    await waitFor(() => expect(screen.queryByText('Thông tư 31 bản quét')).toBeNull());
    expect(asked).toEqual([null, 'col-contracts']);
    expect(screen.getByText('hop_dong.pdf')).toBeInTheDocument();
  });

  it('says so when the corpus is empty', async () => {
    answer = { ...HEALTH, documents: 0, by_status: [], issues: [], most_affected: [] };
    wrap();
    expect(await screen.findByText('Chưa có tài liệu')).toBeInTheDocument();
  });

  it('says a healthy corpus has nothing open', async () => {
    answer = {
      ...HEALTH,
      by_status: [{ status: 'ready', count: 4 }],
      pages_unusable: 0,
      open_issues: 0,
      blocking_issues: 0,
      gated_documents: 0,
      issues: [],
      most_affected: [],
    };
    wrap();
    expect(await screen.findByText('Không có vấn đề chất lượng nào đang mở.')).toBeInTheDocument();
    expect(
      screen.getByText('Không tài liệu nào có vấn đề mở hay trang không dùng được.'),
    ).toBeInTheDocument();
    expect(screen.getByText('100% sẵn sàng để trả lời')).toBeInTheDocument();
  });
});
