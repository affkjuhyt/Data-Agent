import { http, HttpResponse } from 'msw';
import type {
  CallRecording,
  DocumentAnswer,
  DocumentChunk,
  DocumentPage,
  DocumentRow,
  DocumentValidity,
  DocumentView,
  EmailThread,
  ExtractedField,
  ExtractionSchema,
  NewCollection,
  QualityIssue,
  ResolveIssue,
} from '../lib/v1/documents';
import { DOCUMENT_ANSWER_PURPOSE } from '../lib/v1/documents';
import type { StepState } from '../lib/v1/sources';

/*
 * P05 Tài liệu phi cấu trúc beyond the collection list: the library, the viewers, the quality
 * queue, validity, answers from documents and extraction schemas. Data from the Figma frames
 * ("Trình xem tài liệu" 2069:3041, "Trình xem email" 2075:5236, "Trình xem ghi âm" 2075:5061,
 * "Hàng chờ chất lượng" 2300:19017, "Trả lời từ tài liệu" 2300:19144, "Hiệu lực văn bản"
 * 2300:19248, "Lược đồ trích xuất" 2069:3224, "Tệp & tài liệu" 2065:2161).
 *
 * GET /v1/documents/:id is also served by mocks/sources.ts (the collection panel). This mock
 * stands before it, answers the ids it holds with the richer viewer shape (a superset of the
 * sources one) and lets every other id fall through.
 */

const page = <T>(items: T[]) => HttpResponse.json({ items, next_page_token: '' });

const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    { title: code, status, code, detail },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

const today = (hhmm: string) => {
  const [h, m] = hhmm.split(':').map(Number);
  const d = new Date();
  d.setHours(h, m, 0, 0);
  return d.toISOString();
};

/* ─── Library rows ─────────────────────────────────────────────────────── */

function libraryFixture(): DocumentRow[] {
  const row = (r: Partial<DocumentRow> & Pick<DocumentRow, 'id' | 'title'>): DocumentRow => ({
    category: 'regulation',
    category_label: 'Quy định',
    validity: null,
    clause_count: null,
    links_text: null,
    processing: null,
    thread: null,
    ...r,
  });
  const active = { tone: 'success' as const, text: 'Đang hiệu lực', ended: false };
  return [
    row({
      id: 'doc-tt-2026',
      title: 'Thông tư phân loại nợ (sửa đổi 2026)',
      validity: { tone: 'warning', text: 'Từ 01/01/2027', ended: false },
      clause_count: 14,
      links_text: 'NPL_RATIO · đề xuất v5',
    }),
    row({
      id: 'doc-tt31',
      title: 'Thông tư phân loại nợ (2024)',
      validity: active,
      clause_count: 38,
      links_text: 'NPL_RATIO v4',
    }),
    row({
      id: 'doc-provision',
      title: 'Quy chế trích lập dự phòng nội bộ',
      category: 'internal',
      category_label: 'Nội bộ',
      validity: active,
      clause_count: 22,
      links_text: 'COST_OF_RISK',
    }),
    row({
      id: 'doc-stat-t9',
      title: 'Hướng dẫn báo cáo thống kê T9',
      validity: active,
      clause_count: 9,
      links_text: 'Báo cáo đã nộp',
    }),
    row({
      id: 'doc-rate-q3',
      title: 'Chính sách lãi suất ưu đãi Q3',
      category: 'internal',
      category_label: 'Nội bộ',
      validity: { tone: 'idle', text: 'Hết hiệu lực 30/09', ended: true },
      clause_count: 6,
      links_text: null,
    }),
    row({
      id: 'doc-qd1120',
      title: 'QĐ 1120/2023 · quy trình tín dụng bán lẻ',
      category: 'internal',
      category_label: 'Nội bộ',
      validity: { tone: 'warning', text: 'Bị thay thế 01/09', ended: false },
      clause_count: 31,
      links_text: 'Quy trình cho vay',
    }),
    row({
      id: 'doc-cv-2026',
      title: 'Công văn hướng dẫn trích lập 2026',
      processing: { step_text: 'Trích điều khoản', progress: 0.62, started_at: today('10:31') },
    }),
    row({
      id: 'doc-qt-v7',
      title: 'Quy trình thẩm định cho vay v7',
      category: 'internal',
      category_label: 'Nội bộ',
      processing: { step_text: 'Nhận dạng chữ', progress: 0.3, started_at: today('10:40') },
    }),
    row({
      id: 'doc-fee-q4',
      title: 'Biểu phí dịch vụ thẻ Q4',
      category: 'internal',
      category_label: 'Nội bộ',
      processing: { step_text: 'Liên kết chỉ số', progress: 0.9, started_at: today('10:12') },
    }),
  ];
}

function mailRows(): DocumentRow[] {
  const mail = (
    id: string,
    title: string,
    from_text: string,
    level: 'high' | 'medium' | 'low',
    text: string,
  ): DocumentRow => ({
    id,
    title,
    category: 'correspondence',
    category_label: 'Email',
    validity: null,
    clause_count: null,
    links_text: null,
    processing: null,
    thread: { from_text, severity: { level, text } },
  });
  return [
    mail(
      'doc-mail-3390',
      'Phí trả nợ trước hạn không đúng hợp đồng',
      'Khách hàng ••••3390 · 2 giờ',
      'high',
      'Mức cao',
    ),
    mail(
      'doc-mail-7712',
      'Chưa nhận được sao kê tháng 9',
      'Khách hàng ••••7712 · 5 giờ',
      'low',
      'Thấp',
    ),
    mail(
      'doc-mail-1180',
      'Đề nghị giảm lãi suất',
      'Khách hàng ••••1180 · hôm qua',
      'medium',
      'Trung bình',
    ),
    mail(
      'doc-mail-5504',
      'Sai thông tin địa chỉ trên hợp đồng',
      'Khách hàng ••••5504 · hôm qua',
      'low',
      'Thấp',
    ),
  ];
}

/* ─── Viewer documents ─────────────────────────────────────────────────── */

const STEP_TITLES = [
  'Định danh',
  'Trích xuất',
  'Chuẩn hoá',
  'Lọc',
  'Dữ liệu cá nhân',
  'Chia đoạn',
  'Metadata',
  'Kiểm tra',
];

function steps(done: number, tail: StepState[] = []): StepState[] {
  const list: StepState[] = [...Array.from({ length: done }, () => 'done' as const), ...tail];
  while (list.length < 8) list.push('todo');
  return list;
}

function processingOf(s: StepState[]): DocumentView['processing'] {
  const state = { done: 'done', warn: 'pendingApproval', error: 'error', todo: 'waiting' } as const;
  const detail = { done: 'xong', warn: 'chờ người xem lại', error: 'lỗi', todo: 'chưa chạy' };
  return s.map((x, i) => ({
    state: state[x],
    title: STEP_TITLES[i],
    detail: detail[x],
    duration: null,
  }));
}

/** A viewer document with defaults for what a fixture does not say. */
function view(d: Partial<DocumentView> & Pick<DocumentView, 'id' | 'title'>): DocumentView {
  const s = d.steps ?? steps(8);
  return {
    type_text: 'PDF',
    steps: s,
    validity: null,
    status: { tone: 'success', text: 'Sẵn sàng' },
    origin_text: 'Đồng bộ từ SharePoint “Pháp chế/Tín dụng”',
    processing: processingOf(s),
    excerpt: null,
    effective_text: null,
    impact_text: null,
    kind: 'file',
    file_name: `${d.id}.pdf`,
    collection: { id: 'col-regulation', name: 'Văn bản pháp quy' },
    page_count: 1,
    focus_page: 1,
    summary_text: null,
    type_label: 'Văn bản',
    signals: [],
    fields: [],
    issue: null,
    index_text: null,
    original_url: null,
    email: null,
    call: null,
    ...d,
  };
}

const f = (name: string, value: string, pageNo: number, confidence: number): ExtractedField => ({
  name,
  value,
  page: pageNo,
  confidence,
  review: confidence < 0.8,
});

const TT_EXCERPT = {
  label: 'Điều 10, khoản 2 · sửa đổi',
  text: '“Khoản nợ được cơ cấu lại thời hạn trả nợ lần thứ hai được phân loại tối thiểu vào nhóm 3…”',
};

const TT_PROCESSING: DocumentView['processing'] = [
  { state: 'done', title: 'Đọc và nhận dạng', detail: '32 trang · độ tin 0,98', duration: '0:41' },
  {
    state: 'done',
    title: 'Trích điều khoản',
    detail: '14 điều khoản mới hoặc sửa',
    duration: '1:02',
  },
  { state: 'done', title: 'Liên kết chỉ số', detail: 'NPL_RATIO, COST_OF_RISK', duration: '0:12' },
  {
    state: 'pendingApproval',
    title: 'Đề xuất NPL_RATIO v5',
    detail: 'Chờ chủ sở hữu chỉ số',
    duration: null,
  },
];

function regulationField(no: string, issuer: string, from: string): ExtractedField[] {
  return [
    f('doc_number', no, 1, 0.99),
    f('issued_by', issuer, 1, 0.98),
    f('effective_from', from, 1, 0.97),
  ];
}

function viewsFixture(): DocumentView[] {
  const credit = view({
    id: 'doc-hs-0187',
    title: 'Hợp đồng tín dụng LD2409-0187',
    type_text: 'PDF scan · 12 trang',
    status: { tone: 'warning', text: 'Chờ xem lại · 2 trường' },
    steps: steps(7, ['warn']),
    origin_text: 'Nạp từ thư mục quét chi nhánh Hà Nội · 02/10',
    file_name: 'HS_LD2409_0187.pdf',
    collection: { id: 'col-credit-files', name: 'Hồ sơ tín dụng' },
    page_count: 12,
    focus_page: 3,
    summary_text:
      'Agent đã đọc hợp đồng này và trích 14 trường. 2 trường độ tin thấp cần bạn xem; số tiền vay đang lệch với Core banking.',
    type_label: 'Hợp đồng tín dụng',
    signals: [
      { tone: 'success', text: 'Phân loại 0,99' },
      { tone: 'warning', text: '2 trường cần xem' },
      { tone: 'success', text: 'Đã che CCCD' },
    ],
    fields: [
      f('contract_no', 'HĐTD-2409/0187', 1, 0.99),
      f('borrower', 'Nguyễn V•• A••', 1, 0.98),
      f('loan_amount', '1.200.000.000 đ', 3, 0.71),
      f('interest_rate', '9,2%/năm', 3, 0.96),
      f('term_months', '60', 3, 0.97),
      f('collateral', 'Quyền sử dụng đất · Hà Nội', 5, 0.93),
      f('signing_date', '24/09/2024', 1, 0.99),
      f('branch', 'Chi nhánh Hà Nội', 1, 0.98),
      f('purpose', 'Tiêu dùng cá nhân', 2, 0.95),
      f('repayment_method', 'Gốc đều hằng tháng', 4, 0.94),
      f('guarantor', '—', 6, 0.78),
      f('disbursement_date', '01/10/2024', 4, 0.96),
      f('maturity_date', '01/10/2029', 4, 0.97),
      f('prepayment_fee', '1% sau năm thứ hai', 8, 0.92),
    ],
    issue: {
      quality_issue_id: 'qi-0187-amount',
      field: 'loan_amount',
      title: 'Số tiền vay: độ tin thấp do chữ viết tay đè lên số in',
      detail: 'Core banking ghi 1.250.000.000 đ cho LD2409-0187 · lệch 50 triệu so với bản trích.',
      suggested_value: '1.250.000.000',
      current_value: '1.200.000.000',
    },
    index_text: '412 đoạn trong bộ sưu tập dùng tài liệu này · chỉ mục 06:00',
  });

  const tt2026 = view({
    id: 'doc-tt-2026',
    title: 'Thông tư phân loại nợ (sửa đổi 2026)',
    type_text: 'PDF · 32 trang',
    validity: { text: 'Từ 01/01/2027', warn: true },
    origin_text: 'Nạp tự động từ cổng văn bản · 08:12 hôm nay',
    processing: TT_PROCESSING,
    excerpt: TT_EXCERPT,
    effective_text: '01/01/2027 · thay thế bản 2024 một phần',
    impact_text: '1 chỉ số · 4 báo cáo · 3 agent',
    file_name: 'TT_phan_loai_no_sua_doi_2026.pdf',
    page_count: 32,
    type_label: 'Thông tư',
    signals: [{ tone: 'success', text: 'Phân loại 0,99' }],
    fields: regulationField('TT xx/2026/TT-NHNN', 'Ngân hàng Nhà nước', '01/01/2027'),
  });

  const tt31 = view({
    id: 'doc-tt31',
    title: 'TT 31/2024/TT-NHNN · phân loại nợ',
    type_text: 'PDF · 48 trang',
    validity: { text: 'Đang hiệu lực', warn: false },
    origin_text: 'Nạp tự động từ cổng văn bản · 08:12 hôm nay',
    processing: TT_PROCESSING,
    excerpt: TT_EXCERPT,
    effective_text: '01/01/2027 · thay thế bản 2024 một phần',
    impact_text: '1 chỉ số · 4 báo cáo · 3 agent',
    file_name: 'TT_31_2024_TT-NHNN.pdf',
    page_count: 48,
    focus_page: 14,
    type_label: 'Thông tư',
    signals: [{ tone: 'success', text: 'Phân loại 0,99' }],
    fields: regulationField('31/2024/TT-NHNN', 'Ngân hàng Nhà nước', '01/07/2024'),
  });

  const qd1120 = view({
    id: 'doc-qd1120',
    title: 'QĐ 1120/2023 · quy trình tín dụng bán lẻ',
    type_text: 'Word · 22 trang',
    validity: { text: 'Bị thay thế 01/09', warn: true },
    status: { tone: 'idle', text: 'Sẵn sàng · không dùng trả lời hiện tại' },
    file_name: 'QD_1120_2023.docx',
    page_count: 22,
    focus_page: 9,
    type_label: 'Quyết định nội bộ',
    signals: [{ tone: 'warning', text: 'Bị thay thế 01/09/2026' }],
    fields: regulationField('1120/2023/QĐ', 'Tổng giám đốc', '01/04/2023'),
  });

  const hdtd = view({
    id: 'doc-hdtd',
    title: 'Biên bản họp HĐTD 05/10',
    type_text: 'PDF scan · 6 trang',
    steps: steps(3, ['warn']),
    status: { tone: 'warning', text: 'Chờ xem lại · OCR kém trang 4' },
    file_name: 'BB_HDTD_0510.pdf',
    collection: { id: 'col-committee', name: 'Biên bản hội đồng tín dụng' },
    page_count: 6,
    focus_page: 4,
    summary_text:
      'Agent đã đọc biên bản này; trang 4 có chữ viết tay mờ, chưa xác nhận nên chưa dùng để trả lời.',
    type_label: 'Biên bản hội đồng',
    signals: [
      { tone: 'warning', text: 'OCR 0,61 · trang 4' },
      { tone: 'success', text: 'Đã che tên người' },
    ],
  });

  const contract = view({
    id: 'doc-contract',
    title: 'Hợp đồng mẫu vay tiêu dùng',
    type_text: 'PDF · 12 trang',
    steps: steps(5),
    validity: { text: 'Đang hiệu lực', warn: false },
    status: { tone: 'info', text: 'Đang xử lý · bước 5' },
    file_name: 'HD_mau_vay_tieu_dung.pdf',
    page_count: 12,
    type_label: 'Hợp đồng mẫu',
  });

  const legalMail = view({
    id: 'doc-email',
    title: 'Email trao đổi pháp chế 02/10',
    type_text: 'Email · 3 thư',
    steps: steps(4, ['error', 'error']),
    status: { tone: 'error', text: 'Lỗi · không đọc được tệp đính kèm' },
    file_name: 'phap_che_0210.eml',
    page_count: 1,
    type_label: 'Email',
    signals: [{ tone: 'error', text: 'Tệp đính kèm lỗi' }],
  });

  const library = libraryFixture()
    .filter((r) => !['doc-tt-2026', 'doc-tt31', 'doc-qd1120'].includes(r.id))
    .map((r) =>
      view({
        id: r.id,
        title: r.title,
        validity: r.validity
          ? { text: r.validity.text, warn: r.validity.tone === 'warning' }
          : null,
        steps: r.processing ? steps(Math.round(r.processing.progress * 8)) : steps(8),
        status: r.processing
          ? { tone: 'info', text: `Đang xử lý · ${r.processing.step_text.toLowerCase()}` }
          : { tone: 'success', text: 'Sẵn sàng' },
        page_count: 8,
        type_label: r.category_label,
      }),
    );

  return [
    credit,
    tt2026,
    tt31,
    qd1120,
    hdtd,
    contract,
    legalMail,
    ...library,
    ...mailViews(),
    callView(),
  ];
}

function mailViews(): DocumentView[] {
  const thread3390: EmailThread = {
    subject: 'Phí trả nợ trước hạn không đúng hợp đồng',
    messages: [
      {
        id: 'm1',
        from: 'Khách hàng ••••3390',
        sent_at_text: '08/10 08:12',
        body: 'Tôi tất toán khoản vay ngày 01/10 nhưng bị thu phí 3% trong khi hợp đồng ghi 1% sau năm thứ hai. Đề nghị ngân hàng hoàn lại phần chênh lệch.',
        attachments: ['HĐTD_scan.pdf', 'Biên lai.jpg'],
      },
      {
        id: 'm2',
        from: 'Chi nhánh Hà Nội',
        sent_at_text: '08/10 09:30',
        body: 'Chúng tôi đã nhận phản ánh và đang kiểm tra hồ sơ. Sẽ phản hồi trong 2 ngày làm việc.',
        attachments: [],
      },
    ],
    facts: [
      { label: 'Phân loại', value: 'Khiếu nại phí · trả nợ trước hạn' },
      { label: 'Mức độ', value: 'Cao · có yêu cầu hoàn tiền' },
      { label: 'Cảm xúc', value: 'Không hài lòng' },
      { label: 'Hạn phản hồi', value: '10/10 · còn 1 ngày 6 giờ' },
      { label: 'Liên kết', value: 'Khách hàng ••••3390 · khoản vay LD2311-0456' },
      { label: 'Điều khoản', value: 'HĐTD điều 8.3 · khớp tệp đính kèm' },
    ],
    similar: {
      title: '17 khiếu nại tương tự trong 30 ngày',
      detail:
        'Cùng sản phẩm vay tiêu dùng 2023 · Agent Phân tích có thể mở lượt điều tra nguyên nhân hệ thống.',
    },
    review: {
      hitl_id: 'hitl-mail-3390',
      kind_label: 'Đánh giá kết quả',
      from_text: 'từ Agent Chăm sóc khách hàng · 10:05',
      title: 'Bản nháp trả lời: hoàn 2% phí, xin lỗi khách hàng',
      detail:
        'Đối chiếu hợp đồng (điều 8.3: 1% sau năm thứ hai) và lịch sử tất toán. Cần chủ quản chi nhánh duyệt trước khi gửi.',
    },
  };
  const simple = (id: string, subject: string, from: string, body: string, kind: string) =>
    view({
      id,
      title: subject,
      type_text: 'Email · 1 thư',
      kind: 'email',
      file_name: `${id}.eml`,
      collection: { id: 'col-complaints', name: 'Email khiếu nại' },
      type_label: 'Email khiếu nại',
      email: {
        subject,
        messages: [{ id: `${id}-1`, from, sent_at_text: '07/10 16:20', body, attachments: [] }],
        facts: [{ label: 'Phân loại', value: kind }],
        similar: null,
        review: null,
      },
    });
  return [
    view({
      id: 'doc-mail-3390',
      title: thread3390.subject,
      type_text: 'Email · 2 thư',
      kind: 'email',
      file_name: 'khieunai_3390.eml',
      collection: { id: 'col-complaints', name: 'Email khiếu nại' },
      type_label: 'Email khiếu nại',
      email: thread3390,
    }),
    simple(
      'doc-mail-7712',
      'Chưa nhận được sao kê tháng 9',
      'Khách hàng ••••7712',
      'Đến nay tôi vẫn chưa nhận được sao kê thẻ tháng 9 qua email.',
      'Yêu cầu chứng từ · sao kê',
    ),
    simple(
      'doc-mail-1180',
      'Đề nghị giảm lãi suất',
      'Khách hàng ••••1180',
      'Tôi đã trả đúng hạn 24 kỳ, đề nghị ngân hàng xem xét giảm lãi suất khoản vay.',
      'Đề nghị · lãi suất',
    ),
    simple(
      'doc-mail-5504',
      'Sai thông tin địa chỉ trên hợp đồng',
      'Khách hàng ••••5504',
      'Địa chỉ trên hợp đồng không đúng với địa chỉ thường trú của tôi.',
      'Khiếu nại thông tin · hợp đồng',
    ),
  ];
}

function callView(): DocumentView {
  const call: CallRecording = {
    title: 'Cuộc gọi #CL-88213 · 08/10 10:42',
    policy: { tone: 'warning', text: 'Chỉ dùng cho thu hồi nợ' },
    duration_s: 278,
    audio_url: null,
    waveform: Array.from(
      { length: 70 },
      (_, i) => 0.3 + 0.7 * Math.abs(Math.sin(i * 1.7) * Math.cos(i * 0.6)),
    ),
    highlight: { from_s: 60, to_s: 90 },
    transcript: [
      {
        at_s: 4,
        speaker: 'Tổng đài viên',
        role: 'agent',
        text: 'Chào anh, em gọi từ ngân hàng về khoản vay tiêu dùng đến hạn ngày 05/10 ạ.',
      },
      {
        at_s: 15,
        speaker: 'Khách hàng',
        role: 'customer',
        text: 'Vâng, tháng này công ty chậm lương nên tôi chưa thanh toán được.',
      },
      {
        at_s: 48,
        speaker: 'Tổng đài viên',
        role: 'agent',
        text: 'Anh có thể thanh toán một phần trước được không ạ?',
      },
      {
        at_s: 72,
        speaker: 'Khách hàng',
        role: 'customer',
        text: 'Tôi cam kết trả 15 triệu vào ngày 20/10, phần còn lại cuối tháng.',
      },
      {
        at_s: 100,
        speaker: 'Tổng đài viên',
        role: 'agent',
        text: 'Em ghi nhận cam kết của anh. Số điện thoại ••••••4821 vẫn liên lạc được chứ ạ?',
      },
    ],
    facts: [
      { label: 'Ý định', value: 'Xin gia hạn một phần', tone: 'success' },
      { label: 'Cam kết trả nợ', value: '15.000.000 đ · 20/10', tone: 'warning' },
      { label: 'Cảm xúc', value: 'Lo lắng, hợp tác', badge: true },
      { label: 'Lý do', value: 'Chậm lương', badge: true },
      { label: 'Liên kết', value: 'Khoản vay LD2407-1122 · khách hàng ••••4821' },
      { label: 'Chuyển văn bản', value: 'Độ tin 0,93 · tiếng Việt, giọng Bắc' },
      { label: 'Đã che', value: 'Số điện thoại, CCCD' },
      { label: 'Chính sách', value: 'Chỉ Agent Thu hồi nợ được đọc; mọi lần đọc ghi kiểm toán' },
    ],
    agent_note:
      'Agent Thu hồi nợ đã tạo lịch nhắc ngày 19/10 và cập nhật cam kết vào hồ sơ khoản vay.',
  };
  return view({
    id: 'doc-call-88213',
    title: 'Cuộc gọi #CL-88213',
    type_text: 'Âm thanh · 04:38',
    kind: 'call',
    file_name: 'CL-88213.wav',
    collection: { id: 'col-calls', name: 'Ghi âm tổng đài' },
    type_label: 'Ghi âm tổng đài',
    call,
  });
}

/* ─── Pages, chunks, links, activity ───────────────────────────────────── */

/** The layout of "Trang" in 2069:3102: a title, section headings and text lines. */
function layout(title: string): DocumentPage['blocks'] {
  const blocks: DocumentPage['blocks'] = [
    { kind: 'title', text: title, box: { x: 180, y: 40, w: 200, h: 20 } },
    { kind: 'line', text: '', box: { x: 200, y: 70, w: 160, h: 6 } },
  ];
  const sections = [
    { y: 110, lines: [464, 464, 464, 464], heading: false },
    { y: 188, lines: [464, 464, 464, 464], heading: true },
    { y: 286, lines: [464, 464, 464, 464, 300], heading: true },
    { y: 400, lines: [464, 464, 464, 300, 464, 464], heading: true },
    { y: 530, lines: [464, 300, 464, 464, 464, 464, 300], heading: true },
  ];
  for (const s of sections) {
    let y = s.y;
    if (s.heading) {
      blocks.push({ kind: 'heading', text: '', box: { x: 48, y, w: 140, h: 9 } });
      y += 20;
    }
    for (const w of s.lines) {
      blocks.push({ kind: 'line', text: '', box: { x: 48, y, w, h: 7 } });
      y += 16;
    }
  }
  return blocks;
}

function pageOf(doc: DocumentView, n: number): DocumentPage {
  const credit = doc.id === 'doc-hs-0187' && n === 3;
  return {
    number: n,
    width: 560,
    height: 700,
    image_url: null,
    blocks: layout(doc.kind === 'file' ? doc.type_label.toUpperCase() : doc.title),
    highlights: credit
      ? [
          {
            field: 'contract_no',
            label: 'Số hợp đồng',
            review: false,
            box: { x: 42, y: 120, w: 290, h: 19 },
          },
          {
            field: 'borrower',
            label: 'Bên vay',
            review: false,
            box: { x: 42, y: 202, w: 476, h: 19 },
          },
          {
            field: 'loan_amount',
            label: 'Số tiền vay',
            review: doc.fields.find((x) => x.name === 'loan_amount')?.review ?? false,
            box: { x: 42, y: 332, w: 290, h: 19 },
          },
          {
            field: 'interest_rate',
            label: 'Lãi suất',
            review: false,
            box: { x: 274, y: 348, w: 221, h: 19 },
          },
          {
            field: 'collateral',
            label: 'Tài sản bảo đảm',
            review: false,
            box: { x: 42, y: 478, w: 290, h: 19 },
          },
        ]
      : [],
    text: credit
      ? 'HỢP ĐỒNG TÍN DỤNG\nSố: HĐTD-2409/0187\nĐiều 2. Số tiền vay\nBên B đồng ý cho Bên A vay số tiền 1.200.000.000 đồng (một tỷ hai trăm triệu đồng)…\nĐiều 3. Lãi suất\nLãi suất cho vay 9,2%/năm, điều chỉnh 6 tháng một lần theo lãi suất cơ sở…'
      : `${doc.title}\nTrang ${n}`,
  };
}

const CHUNKS: Record<string, DocumentChunk[]> = {
  'doc-hs-0187': [
    {
      id: 'ck-12',
      page: 3,
      ordinal: 12,
      text: 'Bên B đồng ý cho Bên A vay số tiền 1.200.000.000 đồng (một tỷ hai trăm triệu đồng)…',
    },
    {
      id: 'ck-13',
      page: 3,
      ordinal: 13,
      text: 'Lãi suất cho vay 9,2%/năm, điều chỉnh 6 tháng một lần theo lãi suất cơ sở…',
    },
    { id: 'ck-21', page: 5, ordinal: 21, text: 'Tài sản bảo đảm là quyền sử dụng đất tại…' },
  ],
};

/** Who read or changed a document (audit_log rows, GET /v1/audit?object_kind=document). */
const AUDIT: Record<
  string,
  { id: string; action: string; actor_kind: string; occurred_at: string }[]
> = {
  'doc-hs-0187': [
    {
      id: 'au-1',
      action: 'document.read',
      actor_kind: 'user',
      occurred_at: '2026-10-08T10:42:00+07:00',
    },
    {
      id: 'au-2',
      action: 'document.update',
      actor_kind: 'agent',
      occurred_at: '2026-10-08T06:05:00+07:00',
    },
  ],
};

/* ─── Quality queue ────────────────────────────────────────────────────── */

function issuesFixture(): QualityIssue[] {
  return [
    {
      id: 'qi-hdtd-p4',
      document: { id: 'doc-hdtd', title: 'Biên bản họp HĐTD 05/10' },
      page: 4,
      reason_text:
        'Agent Tài liệu không chắc kết quả OCR (độ tin 0,61) · có 1 đoạn nghi là dữ liệu cá nhân',
      image_url: null,
      original_lines: [
        '4. Ý kiến các thành viên',
        'Ông Nguyễn V. A. đề nghị nâng hạn mức cho khách hàng',
        'CIF 0012•••• lên 15 tỷ với điều kiện bổ sung tài sản',
        'bảo đảm là quyền sử dụng đất tại Hải Phòng…',
        '(chữ viết tay bị mờ ở lề phải)',
      ],
      read: {
        heading: '4. Ý kiến các thành viên',
        paragraphs: [
          'Ông [TÊN NGƯỜI] đề nghị nâng hạn mức cho khách hàng [MÃ KHÁCH HÀNG] lên 15 tỷ với điều kiện bổ sung tài sản bảo đảm là quyền sử dụng đất tại Hải Phòng.',
        ],
      },
      unreadable: ['Không đọc được: “… hạn mức 15 tỷ hoặc 1,5 tỷ?” — chữ viết tay ở lề phải'],
      agent_actions: [
        'Che 1 tên người và 1 mã khách hàng',
        'Độ tin OCR trang này 0,61, các trang khác 0,95',
        'Đánh dấu con số “15 tỷ” là không chắc',
      ],
    },
    {
      id: 'qi-0187-amount',
      document: { id: 'doc-hs-0187', title: 'Hợp đồng tín dụng LD2409-0187' },
      page: 3,
      reason_text: 'Số tiền vay: độ tin thấp do chữ viết tay đè lên số in (0,71)',
      image_url: null,
      original_lines: [
        'Điều 2. Số tiền vay',
        'Bên B đồng ý cho Bên A vay số tiền 1.2•0.000.000 đồng',
        '(một tỷ hai trăm … triệu đồng)',
      ],
      read: {
        heading: 'Điều 2. Số tiền vay',
        paragraphs: [
          'Bên B đồng ý cho Bên A vay số tiền 1.200.000.000 đồng (một tỷ hai trăm triệu đồng).',
        ],
      },
      unreadable: ['Chữ số thứ ba bị chữ viết tay đè: “2” hoặc “5”'],
      agent_actions: [
        'Đối chiếu Core banking: 1.250.000.000 đ',
        'Đánh dấu số tiền vay là không chắc',
      ],
    },
    {
      id: 'qi-provision-p2',
      document: { id: 'doc-provision', title: 'Quy chế trích lập dự phòng nội bộ' },
      page: 2,
      reason_text: 'Bảng tỷ lệ trích lập bị cắt mép khi quét (độ tin 0,74)',
      image_url: null,
      original_lines: ['Điều 5. Tỷ lệ trích lập cụ thể', 'Nhóm 2: 5% · Nhóm 3: 20% · Nhóm 4: …'],
      read: {
        heading: 'Điều 5. Tỷ lệ trích lập cụ thể',
        paragraphs: ['Nhóm 2: 5%; Nhóm 3: 20%; Nhóm 4: 50%; Nhóm 5: 100%.'],
      },
      unreadable: ['Cột “Nhóm 4” bị cắt: agent suy ra 50% từ văn bản gốc của NHNN'],
      agent_actions: ['Đánh dấu tỷ lệ nhóm 4 là suy luận'],
    },
  ];
}

/* ─── Validity ─────────────────────────────────────────────────────────── */

function validityFixture(): Record<string, DocumentValidity> {
  return {
    'doc-qd1120': {
      document_id: 'doc-qd1120',
      title: 'QĐ 1120/2023 · quy trình tín dụng bán lẻ',
      status: { tone: 'warning', text: 'Bị thay thế' },
      owner_text:
        'Người phụ trách: Ngô Thanh Bình (Pháp chế) · agent phát hiện văn bản thay thế ngày 02/09',
      timeline: [
        {
          date_text: '15/03/2023',
          text: 'QĐ 1120/2023 ban hành, hiệu lực từ 01/04/2023',
          change: false,
        },
        { date_text: '20/11/2024', text: 'Sửa đổi bởi QĐ 2210/2024 (mục 5.2, 6.1)', change: false },
        { date_text: '15/08/2026', text: 'QĐ 845/2026 ban hành, thay thế toàn bộ', change: true },
        { date_text: '01/09/2026', text: 'QĐ 1120/2023 hết hiệu lực', change: true },
      ],
      impact: [
        {
          subject: '37 đoạn của QĐ 1120/2023',
          effect: 'Không dùng làm căn cứ cho câu hỏi về hiện tại',
        },
        {
          subject: 'Câu hỏi theo thời điểm trước 01/09/2026',
          effect: 'Vẫn dùng QĐ 1120/2023 (đã sửa đổi 2024)',
        },
        {
          subject: '4 câu trả lời đã lưu trong phiên',
          effect: 'Được đánh dấu “căn cứ đã hết hiệu lực”, người tạo nhận thông báo',
        },
      ],
      finding: {
        text: 'Điều 25 của QĐ 845/2026 ghi “thay thế Quyết định 1120/2023”. Agent đề xuất đánh dấu bị thay thế từ 01/09/2026; chờ người phụ trách xác nhận.',
        footnote: 'UC-05-05 · chỉ người phụ trách văn bản xác nhận được quan hệ hiệu lực',
      },
      pending: {
        superseded_by: 'doc-qd845',
        superseded_by_title: 'QĐ 845/2026 · quy trình tín dụng bán lẻ',
        valid_to: '2026-09-01',
      },
      can_confirm: true,
    },
  };
}

/* ─── Answers ──────────────────────────────────────────────────────────── */

function answerTo(question: string, scope: string): DocumentAnswer {
  const id = `ans-${Math.random().toString(36).slice(2, 8)}`;
  if (!/cơ cấu/i.test(question))
    return {
      id,
      question,
      scope_text: scope,
      answer:
        'Không tìm thấy đoạn nào trả lời câu hỏi này trong các văn bản bạn được đọc. Thử hỏi cụ thể hơn hoặc chọn bộ sưu tập khác.',
      not_found: true,
      citations: [],
      meta_text: 'Agent Tài liệu · 0 nguồn · 2,1 giây',
    };
  return {
    id,
    question,
    scope_text: scope,
    answer:
      'Khoản nợ được cơ cấu lại thời hạn trả nợ lần thứ hai phải phân loại tối thiểu vào nhóm 3 (nợ dưới tiêu chuẩn) [1]. Quy trình nội bộ trước đây cho phép giữ nhóm 2 nếu khách hàng trả đúng hạn 3 kỳ, nhưng quy trình này đã bị thay thế từ 01/09/2026 [2].',
    not_found: false,
    citations: [
      {
        n: 1,
        document_id: 'doc-tt31',
        title: 'TT 31/2024/TT-NHNN',
        locator: 'Điều 10, khoản 3 · trang 14',
        page: 14,
        validity: { tone: 'success', text: 'Đang hiệu lực' },
        superseded: false,
        quote:
          '“Khoản nợ được cơ cấu lại thời hạn trả nợ lần thứ hai … phân loại vào nhóm có mức độ rủi ro cao hơn, tối thiểu là nhóm 3 …”',
      },
      {
        n: 2,
        document_id: 'doc-qd1120',
        title: 'QĐ 1120/2023 · quy trình tín dụng bán lẻ',
        locator: 'Mục 5.2 · trang 9',
        page: 9,
        validity: { tone: 'warning', text: 'Bị thay thế 01/09/2026 bởi QĐ 845/2026' },
        superseded: true,
        quote:
          '“Khoản vay cơ cấu lại được giữ nhóm 2 nếu khách hàng trả nợ đúng hạn ba kỳ liên tiếp sau cơ cấu …”',
      },
    ],
    meta_text: 'Agent Tài liệu · 2 nguồn · 3,4 giây',
  };
}

/* ─── Extraction schemas ───────────────────────────────────────────────── */

function schemasFixture(): ExtractionSchema[] {
  const field = (
    name: string,
    type_label: string,
    description: string,
    required: boolean,
    accuracy: number,
    accuracy_state: 'ok' | 'low' | 'new',
    target_column: string,
  ) => ({ name, type_label, description, required, accuracy, accuracy_state, target_column });
  return [
    {
      id: 'xs-credit-v2',
      collection: { id: 'col-credit-files', name: 'Hồ sơ tín dụng' },
      version: 2,
      status: 'draft',
      proposal_text:
        'Agent Tài liệu đề xuất v2 từ 20 tệp mẫu: thêm 3 trường (người bảo lãnh, lịch trả nợ, điều kiện đặc biệt) và đổi kiểu loan_amount sang số.',
      fields: [
        field('contract_no', 'text', 'Số hợp đồng tín dụng', true, 0.99, 'ok', 'contract_no'),
        field('loan_id', 'text', 'Mã khoản vay, khớp Core banking', true, 0.98, 'ok', 'loan_id'),
        field(
          'borrower',
          'text · PII',
          'Tên bên vay, che khi hiển thị',
          true,
          0.98,
          'ok',
          'borrower_masked',
        ),
        field('loan_amount', 'số', 'Số tiền vay theo hợp đồng', true, 0.91, 'low', 'loan_amount'),
        field('interest_rate', 'số', 'Lãi suất năm tại ngày ký', true, 0.96, 'ok', 'interest_rate'),
        field(
          'collateral',
          'danh mục',
          'Loại và vị trí tài sản bảo đảm',
          false,
          0.93,
          'ok',
          'collateral_type',
        ),
        field(
          'guarantor',
          'text · PII',
          'Người bảo lãnh (mới)',
          false,
          0.88,
          'new',
          'guarantor_masked',
        ),
        field('repayment', 'bảng', 'Lịch trả nợ (mới)', false, 0.82, 'low', 'repayment_schedule'),
        field(
          'special_terms',
          'văn bản',
          'Điều kiện đặc biệt (mới)',
          false,
          0.86,
          'new',
          'special_terms',
        ),
      ],
      output: {
        table: 'credit_file_extracted',
        rows_text: '18.400 dòng · cập nhật mỗi giờ · nguồn có cấu trúc cấp 2',
        used_by: 'Agent Đối soát (so với Core banking), Agent Phân tích',
        sample_text: '50 tệp · độ chính xác TB 93,6%',
        core_diff_text: '37 khoản lệch số tiền > 1% → tạo yêu cầu xung đột',
      },
      test_size: 50,
    },
    {
      id: 'xs-contracts-v3',
      collection: { id: 'col-contracts', name: 'Hợp đồng & phụ lục' },
      version: 3,
      status: 'active',
      proposal_text: null,
      fields: [
        field('contract_no', 'text', 'Số hợp đồng', true, 0.99, 'ok', 'contract_no'),
        field('customer_id', 'text', 'Mã khách hàng', true, 0.97, 'ok', 'customer_id'),
        field('signed_on', 'ngày', 'Ngày ký', true, 0.98, 'ok', 'signed_on'),
      ],
      output: {
        table: 'contract_extracted',
        rows_text: '6.210 dòng · cập nhật mỗi giờ',
        used_by: 'Agent Phân tích',
        sample_text: '50 tệp · độ chính xác TB 97,9%',
        core_diff_text: null,
      },
      test_size: 50,
    },
  ];
}

/* ─── Handlers ─────────────────────────────────────────────────────────── */

const AT = '2026-10-08T08:00:00+07:00';

/**
 * GET /v1/documents/{id} as the api sends it (DocumentDetail), with the screen's row under
 * `display`; the collection page's mock uses it too.
 */
export const documentApi = (id: string, title: string, display: unknown) => ({
  id,
  collection_id: 'col-mock',
  file_name: title,
  title,
  media: 'pdf',
  status: 'ready',
  step: 'done',
  progress: { chunks: 0, chunks_indexed: 0, pages_read: 0, pages_total: null, pages_usable: 0 },
  blocking_quality_issues: 0,
  open_quality_issues: 0,
  sha256: '',
  size_bytes: 0,
  page_count: null,
  doc_no: null,
  document_source_id: null,
  effective_from: null,
  effective_to: null,
  error: null,
  issued_on: null,
  parent_document_id: null,
  supersedes_id: null,
  created_at: AT,
  updated_at: AT,
  display,
});

export function createDocumentHandlers() {
  let library = libraryFixture();
  const mails = mailRows();
  let views = viewsFixture();
  let issues = issuesFixture();
  const validity = validityFixture();
  let schemas = schemasFixture();
  const created: { id: string; name: string }[] = [];
  // Questions recorded with the documents purpose, and the answer each one gets.
  const asked = new Map<string, DocumentAnswer>();

  const findView = (id: string) => views.find((v) => v.id === id);
  const saveView = (next: DocumentView) => {
    views = views.map((v) => (v.id === next.id ? next : v));
  };

  return [
    // DocumentListItem with the library row under display; the category and the validity review
    // are filtered by the client. Other collections' documents are the collection mock's.
    http.get('*/v1/documents', ({ request }) => {
      const q = new URL(request.url).searchParams;
      const collection = q.get('collection_id');
      if (collection && collection !== 'col-complaints') return undefined;
      let rows = collection ? mails : library;
      if (q.get('status') === 'processing') rows = rows.filter((r) => r.processing);
      return page(
        rows.map((r) => ({
          ...documentApi(r.id, r.title, {
            ...r,
            ...(validity[r.id]?.pending ? { validity_pending: true } : {}),
          }),
          status: r.processing ? 'processing' : 'ready',
        })),
      );
    }),
    // Multipart, one file per request (A2). The body is not read: under MSW in the jsdom unit
    // tests a multipart body never settles, so the mock accepts each file; the real api answers a
    // duplicate 200 with the document it holds.
    http.post('*/v1/documents', () => {
      const file = `tệp ${library.length + 1}`;
      const fresh = [{ file_name: file }];
      library = [
        ...fresh.map(
          (x, i): DocumentRow => ({
            id: `doc-up-${Date.now().toString(36)}-${i}`,
            title: x.file_name,
            category: 'internal',
            category_label: 'Nội bộ',
            validity: null,
            clause_count: null,
            links_text: null,
            processing: {
              step_text: 'Định danh',
              progress: 0.05,
              started_at: new Date().toISOString(),
            },
            thread: null,
          }),
        ),
        ...library,
      ];
      return HttpResponse.json({ id: library[0].id, status: 'received' }, { status: 202 });
    }),
    http.get('*/v1/documents/:id/file', ({ params }) => {
      const doc = findView(String(params.id));
      if (!doc) return problem(404, 'not_found', 'Không tìm thấy tài liệu.');
      return new HttpResponse(new Blob(['%PDF-1.7 mock preview']), {
        headers: { 'Content-Type': 'application/pdf' },
      });
    }),
    http.get('*/v1/documents/:id/chunks', ({ params }) => {
      const doc = findView(String(params.id));
      if (!doc) return problem(404, 'not_found', 'Không tìm thấy tài liệu.');
      return page(
        (CHUNKS[doc.id] ?? []).map((c) => ({
          id: c.id,
          document_id: doc.id,
          collection_id: doc.collection?.id ?? 'col-mock',
          page_from: c.page,
          page_to: c.page,
          text: c.text,
          section_path: null,
          context_header: null,
          effective_from: null,
          effective_to: null,
          human_corrected: false,
          injection_suspected: false,
          pii_masked: true,
          tokens: 120,
          created_at: AT,
        })),
      );
    }),
    http.get('*/v1/documents/:id/pages/:n', ({ params }) => {
      const doc = findView(String(params.id));
      const n = Number(params.n);
      if (!doc) return problem(404, 'not_found', 'Không tìm thấy tài liệu.');
      if (!Number.isInteger(n) || n < 1 || n > doc.page_count)
        return problem(404, 'not_found', 'Không có trang này.');
      return HttpResponse.json({
        id: `page-${doc.id}-${n}`,
        document_id: doc.id,
        page_no: n,
        text: null,
        text_layer: true,
        usable: true,
        human_corrected: false,
        ocr_conf: null,
        page_hash: '',
        created_at: AT,
        updated_at: AT,
        chunks: (CHUNKS[doc.id] ?? [])
          .filter((c) => c.page === n)
          .map((c) => ({
            id: c.id,
            document_id: doc.id,
            collection_id: doc.collection?.id ?? 'col-mock',
            page_from: c.page,
            page_to: c.page,
            text: c.text,
            section_path: null,
            context_header: null,
            effective_from: null,
            effective_to: null,
            human_corrected: false,
            injection_suspected: false,
            pii_masked: false,
            tokens: 120,
            created_at: AT,
          })),
        display: pageOf(doc, n),
      });
    }),
    // DocumentView rows of the document's versions, with the validity screen's view under display.
    http.get('*/v1/documents/:id/versions', ({ params }) => {
      const id = String(params.id);
      const doc = findView(id);
      const v = validity[id];
      if (!doc && !v) return problem(404, 'not_found', 'Không tìm thấy tài liệu.');
      const title = doc?.title ?? v?.title ?? id;
      return HttpResponse.json({
        items: [
          {
            ...documentApi(id, title, undefined),
            display: undefined,
            created_at: '2026-10-02T09:00:00+07:00',
            doc_no: null,
            effective_from: null,
          },
        ],
        next_page_token: '',
        ...(v ? { display: v } : {}),
      });
    }),
    http.get('*/v1/audit', ({ request }) => {
      const q = new URL(request.url).searchParams;
      if (q.get('object_kind') !== 'document') return undefined;
      return page(AUDIT[q.get('object_id') ?? ''] ?? []);
    }),
    // DocumentPatch: the replacing document records what it supersedes (the confirmation of the
    // relation the agent proposed), the replaced one its effective_to.
    http.patch('*/v1/documents/:id', async ({ params, request }) => {
      const id = String(params.id);
      const body = (await request.json()) as { supersedes_id?: string; effective_to?: string };
      const replaced = body.supersedes_id ? validity[body.supersedes_id] : undefined;
      if (replaced) {
        if (!replaced.can_confirm)
          return problem(
            403,
            'forbidden',
            'Chỉ người phụ trách văn bản xác nhận được quan hệ hiệu lực.',
          );
        validity[replaced.document_id] = { ...replaced, pending: null };
        return HttpResponse.json({ ...documentApi(id, id, undefined), display: undefined });
      }
      if (!validity[id] && !findView(id)) return undefined;
      return HttpResponse.json({
        ...documentApi(id, id, undefined),
        display: undefined,
        effective_to: body.effective_to ?? null,
      });
    }),
    // A background task for an agent (UC-08-05): the email viewer's "Mở lượt điều tra".
    http.post('*/v1/tasks', async ({ request }) => {
      const body = (await request.json()) as { description?: string };
      if (!body.description?.trim())
        return problem(422, 'validation_failed', 'Mô tả việc cần làm.');
      return HttpResponse.json(
        { id: `task-${Date.now().toString(36)}`, status: 'queued', poll_after_ms: 1000 },
        { status: 202 },
      );
    }),
    // Ids this mock does not hold fall through to mocks/sources.ts.
    http.get('*/v1/documents/:id', ({ params }) => {
      const doc = findView(String(params.id));
      return doc ? HttpResponse.json(documentApi(doc.id, doc.title, doc)) : undefined;
    }),

    // QualityIssueView with the side-by-side review under display.
    http.get('*/v1/quality-issues', () =>
      page(
        issues.map((i) => ({
          id: i.id,
          document_id: i.document.id,
          page_no: i.page,
          kind: 'low_ocr',
          severity: 'blocking',
          status: 'open',
          detail: i.reason_text,
          old_value: null,
          chunk_id: null,
          extracted_record_id: null,
          requires_role: null,
          resolution: null,
          resolved_at: null,
          resolved_by: null,
          created_at: AT,
          updated_at: AT,
          display: i,
        })),
      ),
    ),
    http.post('*/v1/quality-issues/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      if (verb !== 'resolve') return problem(404, 'not_found', 'Không có thao tác này.');
      const issue = issues.find((i) => i.id === id);
      if (!issue) return problem(404, 'not_found', 'Vấn đề đã được xử lý.');
      // A2's resolution, or the screen's PROPOSAL keep_and_conflict.
      const raw = (await request.json()) as
        | { resolution: string; corrected_text?: string }
        | { action: 'keep_and_conflict' };
      const body: ResolveIssue =
        'action' in raw
          ? raw
          : raw.resolution === 'corrected'
            ? { action: 'edit', text: raw.corrected_text ?? '' }
            : raw.resolution === 'value_fixed'
              ? { action: 'correct', value: raw.corrected_text ?? '' }
              : raw.resolution === 'page_dropped'
                ? { action: 'drop', reason: '' }
                : { action: 'confirm' };
      if (body.action === 'edit' && !body.text?.trim())
        return problem(422, 'validation_failed', 'Văn bản sửa không được trống.');
      issues = issues.filter((i) => i.id !== id);
      // The viewer's field issue is the same quality issue: clear it on the document too.
      const doc = views.find((v) => v.issue?.quality_issue_id === id);
      if (doc) {
        const field = doc.issue!.field;
        saveView({
          ...doc,
          issue: null,
          fields: doc.fields.map((x) =>
            x.name === field
              ? {
                  ...x,
                  review: false,
                  value: body.action === 'correct' ? `${body.value} đ` : x.value,
                }
              : x,
          ),
          signals: doc.signals.filter((s) => s.tone !== 'warning'),
        });
      }
      return HttpResponse.json({ status: 'resolved' });
    }),

    // Answers from documents: the question is Hỏi đáp's, recorded with the documents purpose (the
    // others fall through to its mock), and the passages are the hybrid search's.
    http.post('*/v1/questions', async ({ request }) => {
      const body = (await request.clone().json()) as { text?: string; purpose?: string };
      if (body.purpose !== DOCUMENT_ANSWER_PURPOSE) return undefined;
      const id = `q-doc-${asked.size + 1}`;
      asked.set(id, answerTo(body.text ?? '', ''));
      return HttpResponse.json(
        {
          id,
          question_id: id,
          conversation_id: `conv-${id}`,
          status: 'received',
          poll_after_ms: 0,
        },
        { status: 202 },
      );
    }),
    http.get('*/v1/questions/:id', ({ params }) => {
      const answer = asked.get(String(params.id));
      return answer
        ? HttpResponse.json({ id: params.id, status: 'answered', answer_id: answer.id })
        : undefined;
    }),
    http.get('*/v1/answers/:id', ({ params }) => {
      const answer = [...asked.values()].find((a) => a.id === params.id);
      return answer
        ? HttpResponse.json({
            id: answer.id,
            kind: answer.not_found ? 'refusal' : 'document',
            text: answer.answer,
            agent_name: 'Agent Tài liệu',
            latency_ms: 3400,
            citations: [],
          })
        : undefined;
    }),
    http.post('*/v1/search/documents', async ({ request }) => {
      const body = (await request.json()) as { query?: string };
      return page(
        answerTo(body.query ?? '', '').citations.map((c) => ({
          id: `hit-${c.document_id}`,
          document_id: c.document_id,
          collection_id: 'col-regulation',
          document_title: c.title,
          doc_no: null,
          section_path: c.locator.split(' · ')[0],
          page_from: c.page,
          page_to: c.page,
          text: c.quote,
          effective_from: null,
          effective_to: c.superseded ? '2026-09-01' : null,
          score: 0.9,
          context_header: null,
          human_corrected: false,
          injection_suspected: false,
          pii_masked: false,
          tokens: 80,
          created_at: AT,
        })),
      );
    }),

    http.get('*/v1/extraction-schemas', () => page(schemas)),
    http.get('*/v1/extraction-schemas/:id', ({ params }) => {
      const schema = schemas.find((s) => s.id === params.id);
      return schema
        ? HttpResponse.json(schema)
        : problem(404, 'not_found', 'Không tìm thấy lược đồ.');
    }),
    http.post('*/v1/extraction-schemas/:action', ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      const schema = schemas.find((s) => s.id === id);
      if (!schema) return problem(404, 'not_found', 'Không tìm thấy lược đồ.');
      let next: ExtractionSchema;
      if (verb === 'test') {
        if (schema.status === 'active')
          return problem(409, 'invalid_state', 'Lược đồ đã xuất bản.');
        next = { ...schema, status: 'testing' };
      } else if (verb === 'activate') {
        if (schema.status === 'active')
          return problem(409, 'invalid_state', 'Lược đồ đã xuất bản.');
        next = { ...schema, status: 'active', proposal_text: null };
      } else return problem(404, 'not_found', 'Không có thao tác này.');
      schemas = schemas.map((s) => (s.id === id ? next : s));
      return HttpResponse.json(next, { status: verb === 'test' ? 202 : 200 });
    }),

    // CollectionCreate: code, name, purpose and the media kinds.
    http.post('*/v1/collections', async ({ request }) => {
      const body = (await request.json()) as { code: string; name: string; purpose: string };
      if (!body.name?.trim()) return problem(422, 'validation_failed', 'Đặt tên bộ sưu tập.');
      if (!body.code || !body.purpose)
        return problem(422, 'validation_failed', 'Thiếu mã hoặc mục đích.');
      const item = { id: `col-new-${created.length + 1}`, name: body.name.trim() };
      created.push(item);
      return HttpResponse.json(
        { ...item, code: body.code, purpose: body.purpose },
        { status: 201 },
      );
    }),

    // The thread's review is answered with the ordinary HITL :resolve; other ids fall through.
    http.post('*/v1/hitl-requests/:action', async ({ params }) => {
      const [id] = String(params.action).split(':');
      const doc = views.find((v) => v.email?.review?.hitl_id === id);
      if (!doc?.email) return undefined;
      saveView({ ...doc, email: { ...doc.email, review: null } });
      return HttpResponse.json({ id, status: 'resolved' });
    }),
  ];
}
