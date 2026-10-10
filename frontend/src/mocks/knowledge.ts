import { http, HttpResponse } from 'msw';
import type {
  KnowledgePage,
  KnowledgePageSummary,
  KnowledgePageVersion,
} from '../lib/v1/knowledge';

/* The knowledge pages of "P07 · Trang tri thức" (2405:14761). */
function pagesFixture(): KnowledgePageSummary[] {
  return [
    { id: 'kp-bank-b', title: 'Ngân hàng B', kind: 'entity', status: 'certified' },
    {
      id: 'kp-deposit-12m',
      title: 'Lãi suất huy động 12 tháng bình quân',
      kind: 'metric',
      status: 'approved',
    },
    { id: 'kp-bank-k', title: 'Ngân hàng K', kind: 'entity', status: 'decertified' },
    { id: 'kp-sbv-rate', title: 'Lãi suất điều hành NHNN', kind: 'source', status: 'certified' },
    { id: 'kp-fees', title: 'Biểu phí dịch vụ ngân hàng', kind: 'topic', status: 'needs_review' },
    { id: 'kp-casa', title: 'Tỷ lệ CASA', kind: 'metric', status: 'draft' },
  ];
}

function bankB(): KnowledgePage {
  return {
    ...pagesFixture()[0],
    version: 5,
    certified_at: '2026-10-08T10:00:00+07:00',
    certified_by_name: 'Lê Thu Hà',
    external_source: true,
    summary: [
      {
        text: 'Ngân hàng B là ngân hàng TMCP quy mô vừa, công bố biểu lãi suất huy động trên website và tệp PDF hằng tuần. Lãi suất 12 tháng tại quầy tuần 41 là ',
      },
      { figure: '4,90%/năm', query_run_id: 'qr-8841', source_title: 'Dataset hợp nhất v42' },
      { text: ', cao hơn bình quân 29 ngân hàng ' },
      { figure: '0,05 điểm', query_run_id: 'qr-8842', source_title: 'Dataset hợp nhất v42' },
      { text: '; kỳ hạn 6 tháng là ' },
      { figure: '4,70%/năm', query_run_id: 'qr-8843', source_title: 'Dataset hợp nhất v42' },
      { text: '. Chuyển khoản liên ngân hàng trên kênh online miễn phí từ 01/09/2026.' },
    ],
    claims: [
      {
        text: 'Lãi suất 12 tháng tại quầy {lai_suat_12t}',
        source_title: 'Dataset hợp nhất v42',
        source_detail: 'Lãi suất huy động · tuần 41',
        valid_text: '07/10 – 13/10/2026',
        status: 'valid',
      },
      {
        text: 'Chuyển khoản liên ngân hàng online miễn phí',
        source_title: 'Biểu phí 07/10/2026',
        source_detail: 'PDF · trang 3',
        valid_text: 'Từ 01/09/2026',
        status: 'valid',
      },
      {
        text: 'Mã ngân hàng và tên miền chính thức',
        source_title: 'Danh mục thực thể chuẩn',
        source_detail: 'reference_entity',
        valid_text: 'Không thời hạn',
        status: 'valid',
      },
      {
        text: 'Sáp nhập công ty tài chính con',
        source_title: 'Tin công bố 12/03/2025',
        source_detail: 'nganhangb.vn',
        valid_text: '12/03 – 31/12/2025',
        status: 'expired',
      },
    ],
    policy: {
      data_spaces_text: 'Thị trường (nguồn ngoài), Sản phẩm',
      classification: 'Nội bộ',
      viewer_rule: 'Cần quyền trên mọi nguồn của trang',
    },
    links: [
      {
        direction: 'out',
        title: 'Lãi suất huy động 12 tháng bình quân',
        page_id: 'kp-deposit-12m',
      },
      { direction: 'out', title: 'Biểu phí dịch vụ ngân hàng', page_id: 'kp-fees' },
      { direction: 'in', title: 'Ngân hàng K (so sánh)', page_id: 'kp-bank-k' },
      { direction: 'in', title: 'Câu trả lời đã lưu: “So sánh lãi suất B với K”', page_id: null },
    ],
    version_count: 5,
  };
}

/** The other pages read as short pages with no claims yet. */
function plain(summary: KnowledgePageSummary): KnowledgePage {
  return {
    ...summary,
    version: 1,
    certified_at: null,
    certified_by_name: null,
    external_source: false,
    summary: [{ text: `Trang ${summary.title} đang được biên soạn.` }],
    claims: [],
    policy: { data_spaces_text: 'Rủi ro tín dụng', classification: 'Nội bộ', viewer_rule: '—' },
    links: [],
    version_count: 1,
  };
}

const VERSIONS: KnowledgePageVersion[] = [5, 4, 3, 2, 1].map((v) => ({
  id: `kpv-bank-b-${v}`,
  version: v,
  status: v === 5 ? 'certified' : 'superseded',
  change_note: v === 5 ? 'Biểu phí tháng 10' : v === 4 ? 'Lãi suất tuần 40' : null,
  created_at: new Date(2026, 9, 8 - (5 - v) * 7).toISOString(),
}));

const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    { title: code, status, code, detail },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

export function createKnowledgeHandlers() {
  const pages = pagesFixture();
  return [
    http.get('*/v1/knowledge-pages', () =>
      HttpResponse.json({ items: pages, next_page_token: '' }),
    ),
    http.get('*/v1/knowledge-pages/:id/versions', ({ params }) =>
      params.id === 'kp-bank-b'
        ? HttpResponse.json({ items: VERSIONS, next_page_token: '' })
        : HttpResponse.json({ items: [], next_page_token: '' }),
    ),
    http.get('*/v1/knowledge-pages/:id', ({ params }) => {
      const summary = pages.find((p) => p.id === params.id);
      if (!summary) return problem(404, 'not_found', 'Không có trang hoặc bạn thiếu quyền.');
      return HttpResponse.json(summary.id === 'kp-bank-b' ? bankB() : plain(summary));
    }),
    http.post('*/v1/knowledge-runs', () =>
      HttpResponse.json({ id: 'kr-' + Date.now() }, { status: 202 }),
    ),
  ];
}
