import { http, HttpResponse } from 'msw';
import type { ExampleQuery, ExampleWire, Synonym } from '../lib/v1/examples';

/* The examples and synonyms of "P07 · Câu hỏi mẫu & đồng nghĩa" (2301:19194). */
function examplesFixture(): ExampleQuery[] {
  const verified = (id: string, question: string, usage: string, by: string): ExampleQuery => ({
    id,
    question,
    sql: `-- ${usage}`,
    usage_text: usage,
    source_label: by,
    status: 'verified',
    reason: null,
    result_text: null,
  });
  return [
    verified(
      'ex-npl-region',
      'Tỷ lệ nợ xấu bán lẻ theo vùng tháng trước',
      'NPL_RATIO · vùng · tháng trước',
      'Đỗ Hải Yến',
    ),
    verified(
      'ex-card-yoy',
      'Dư nợ thẻ tăng bao nhiêu so với cùng kỳ',
      'LOAN_BALANCE · thẻ · YoY',
      'Phạm Đức Long',
    ),
    {
      id: 'ex-dpd90-haiphong',
      question: 'Có bao nhiêu khoản quá hạn trên 90 ngày ở Hải Phòng',
      sql: [
        'SELECT COUNT(*) FROM metric_base(LOAN_COUNT)',
        "WHERE dpd > 90 AND branch_region = 'Hải Phòng'",
        '  AND snapshot = :ky_moi_nhat',
      ].join('\n'),
      usage_text: 'LOAN_COUNT · dpd > 90 · chi nhánh',
      source_label: 'Agent gợi ý · hỏi 7 lần',
      status: 'pending',
      reason: 'Agent gợi ý vì câu này được hỏi 7 lần trong 30 ngày và 2 lần bị báo sai.',
      result_text:
        'Kết quả chạy: 1.204 khoản. Lần báo sai trước do agent hiểu “Hải Phòng” là chi nhánh HPG01 thay vì cả 4 chi nhánh của thành phố.',
    },
    {
      id: 'ex-npl-internal',
      question: 'Nợ xấu nhóm 2–5',
      sql: 'SELECT value FROM metric(NPL_INTERNAL) WHERE snapshot = :ky_moi_nhat',
      usage_text: 'NPL_INTERNAL',
      source_label: 'Agent gợi ý',
      status: 'pending',
      reason: 'Agent gợi ý vì người dùng hay hỏi nợ xấu theo định nghĩa nội bộ.',
      result_text: null,
    },
  ];
}

function synonymsFixture(): Synonym[] {
  return [
    {
      id: 'syn-npl',
      phrases: ['nợ xấu', 'NPL', 'tỷ lệ nợ xấu'],
      target_text: 'NPL_RATIO',
      scope_label: 'Toàn tổ chức',
    },
    {
      id: 'syn-north',
      phrases: ['miền Bắc'],
      target_text: 'vùng IN (DH Bắc Bộ, ĐB sông Hồng, Trung du)',
      scope_label: 'Rủi ro tín dụng',
    },
    {
      id: 'syn-consumer',
      phrases: ['vay tiêu dùng'],
      target_text: 'sản phẩm = tiêu dùng không TSBĐ',
      scope_label: 'Kinh doanh bán lẻ',
    },
  ];
}

const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    { title: code, status, code, detail },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

export function createExampleHandlers() {
  let examples = examplesFixture();
  const synonyms = synonymsFixture();
  const page = <T>(items: T[]) => HttpResponse.json({ items, next_page_token: '' });

  return [
    http.get('*/v1/examples', () => page(examples)),
    http.get('*/v1/synonyms', () => page(synonyms)),
    http.post('*/v1/examples', async ({ request }) => {
      const body = (await request.json()) as { question: string; sql: string };
      if (!body.question?.trim() || !body.sql?.trim())
        return problem(422, 'validation_failed', 'Cần câu hỏi và SQL.');
      if (!/^\s*select\b/i.test(body.sql))
        return problem(422, 'validation_failed', 'SQL không chạy được: chỉ nhận câu SELECT.');
      const created: ExampleQuery = {
        id: `ex-${examples.length + 1}`,
        question: body.question.trim(),
        sql: body.sql.trim(),
        usage_text: '—',
        source_label: 'Bạn',
        status: 'verified',
        reason: null,
        result_text: null,
      };
      examples = [...examples, created];
      // ExampleView as the api sends it, with the PROPOSAL display fields.
      const wire: ExampleWire = {
        ...created,
        status: 'active',
        origin: 'manual',
        metric_ids: [],
        data_space_id: 'ds-rui-ro-tin-dung',
        last_verified_at: new Date().toISOString(),
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      return HttpResponse.json(wire, { status: 201 });
    }),
    http.patch('*/v1/examples/:id', async ({ params, request }) => {
      const item = examples.find((e) => e.id === params.id);
      if (!item) return problem(404, 'not_found', 'Không tìm thấy câu hỏi mẫu.');
      const { sql } = (await request.json()) as { sql: string };
      if (!/^\s*select\b/i.test(sql ?? ''))
        return problem(422, 'validation_failed', 'SQL không chạy được: chỉ nhận câu SELECT.');
      const saved = { ...item, sql };
      examples = examples.map((e) => (e.id === item.id ? saved : e));
      return HttpResponse.json(saved);
    }),
    http.post('*/v1/examples\\:suggest', () =>
      HttpResponse.json({ status: 'queued' }, { status: 202 }),
    ),
    http.post('*/v1/examples/:action', ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      const item = examples.find((e) => e.id === id);
      if (!item || verb !== 'verify')
        return problem(404, 'not_found', 'Không tìm thấy câu hỏi mẫu.');
      const verified: ExampleQuery = { ...item, status: 'verified' };
      examples = examples.map((e) => (e.id === id ? verified : e));
      return HttpResponse.json(verified);
    }),
  ];
}
