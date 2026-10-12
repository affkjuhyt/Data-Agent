import { http, HttpResponse } from 'msw';
import type {
  CatalogProduct,
  DataMart,
  DataProduct,
  DataProductDetail,
  QualityRule,
  RuleSeverity,
} from '../lib/v1/dataProducts';

/* "P06 · Data mart & sản phẩm dữ liệu" (2290:18136) and "Hộp thoại · suggestMart" (2095:9307). */
function martFixture(): DataMart {
  return {
    id: 'mart-credit-risk',
    name: 'mart.credit_risk',
    summary_text: '1 bảng sự kiện · 5 chiều · 14 độ đo · cập nhật sau pipeline lúc 06:20',
    fact: {
      name: 'fact_loan_daily',
      lines: [
        'loan_key · date_key · branch_key',
        'product_key · customer_key',
        'outstanding_amt',
        'overdue_days · debt_group',
        'provision_amt',
      ],
    },
    dimensions: [
      { name: 'dim_date', lines: ['date_key', 'ngày, tháng, quý, năm'], suggested: false },
      { name: 'dim_branch', lines: ['branch_key', 'chi nhánh, vùng, miền'], suggested: false },
      { name: 'dim_product', lines: ['product_key', 'sản phẩm, nhóm, TSBĐ'], suggested: false },
      { name: 'dim_customer', lines: ['customer_key', 'phân khúc, ngành nghề'], suggested: false },
      {
        name: 'dim_officer',
        lines: ['officer_key', 'cán bộ tín dụng · agent gợi ý'],
        suggested: true,
      },
    ],
    product_draft: {
      name: 'credit_risk_retail',
      version: '2.0',
      version_text: '2.0 · thay đổi phá vỡ: bỏ cột branch_name',
      contract_text: '18 cột có kiểu, ý nghĩa, đơn vị',
      freshness_text: 'Trước 07:00 hằng ngày',
      quality_text: '6 quy tắc phải đạt khi làm mới',
      consumers_text: '3 Data Space · 4 báo cáo · 2 agent',
      breaking_note:
        'Thay đổi phá vỡ: bản 1.x chạy song song 30 ngày, bên dùng nhận thông báo kèm hướng dẫn chuyển. Đường nét đứt là chiều agent gợi ý thêm.',
    },
    suggestion: {
      dimensions: [
        { key: 'dim_officer', label: 'dim_officer (cán bộ tín dụng) · 41 câu hỏi/tháng' },
      ],
      measures: [{ key: 'provision_rate', label: 'provision_rate · dùng trong COST_OF_RISK' }],
    },
  };
}

/* The catalog of "v3.1 · Dữ liệu · Sản phẩm dữ liệu" (2062:2601) and its drawer tabs. */
function productsFixture(): DataProductDetail[] {
  const at = '2026-10-10T06:00:00+07:00';
  const plain = (
    id: string,
    name: string,
    code: string,
    version: string,
    description: string,
    status: DataProduct['status'] = 'published',
  ): DataProductDetail => ({
    id,
    name,
    code,
    version,
    next_version: null,
    description,
    status,
    breaking_change: false,
    used_by_spaces: status === 'published' ? 12 : null,
    quality_pct: status === 'published' ? 98 : null,
    refreshed_at: at,
    contract: [],
    checks: [],
    checked_at: at,
    consumers: [],
    consumer_spaces_total: status === 'published' ? 12 : 0,
    affected_by_next: 0,
    versions: [{ version: `v${version}`, change: 'Bản hiện hành', state: 'current' }],
    agent_note: null,
  });
  return [
    {
      ...plain(
        'dp-sales-margin',
        'Biên LN theo ngành hàng',
        'sales_margin',
        '2.3',
        'Biên lợi nhuận gộp theo ngành hàng, vùng và ngày; đã đối soát với sổ cái GL.',
      ),
      next_version: '3',
      breaking_change: true,
      contract: [
        { name: 'sale_date', type: 'date', description: 'Ngày bán', changed: false },
        { name: 'category_id', type: 'string', description: 'Mã ngành hàng', changed: false },
        { name: 'region', type: 'string', description: 'Vùng (Bắc/Trung/Nam)', changed: false },
        {
          name: 'cost_price',
          type: 'decimal(18,2)',
          description: 'Giá vốn bình quân → giá nhập chính thức',
          changed: true,
        },
        {
          name: 'gross_margin',
          type: 'decimal(5,2)',
          description: '(Doanh thu − Giá vốn) / Doanh thu',
          changed: false,
        },
      ],
      checks: [
        { text: 'Không trùng khoá (sale_date, category_id, region)', state: 'pass' },
        { text: 'Đối soát tổng với sổ cái lệch < 0,1%', state: 'pass' },
        { text: 'Độ mới: cập nhật trước 07:00 · 30/30 ngày', state: 'pass' },
        { text: 'cost_price đổi định nghĩa: 3 báo cáo phụ thuộc', state: 'warn' },
      ],
      consumers: [
        { data_space_name: 'Kinh doanh bán lẻ', used_for: '4 báo cáo, 2 agent', version: 'v2.3' },
        { data_space_name: 'Ban điều hành', used_for: 'Bảng điều hành', version: 'v2.3' },
        { data_space_name: 'Kế hoạch tài chính', used_for: 'Dự báo', version: 'v2.2' },
      ],
      affected_by_next: 3,
      versions: [
        { version: 'v3', change: 'cost_price → giá nhập chính thức', state: 'draft' },
        { version: 'v2.3', change: 'Thêm cột region', state: 'current' },
        { version: 'v2.2', change: 'Đối soát với GL', state: 'supported' },
        { version: 'v1.0', change: 'Bản đầu', state: 'retired' },
      ],
      agent_note: {
        text: 'Đổi nghĩa cost_price ảnh hưởng 3 báo cáo của 2 Data Space (Kinh doanh bán lẻ, Ban điều hành). Đề xuất phát hành v3 song song v2.3 trong 30 ngày và gửi thông báo cho người dùng.',
        action_label: 'Tạo bản v3 nháp',
      },
    },
    plain(
      'dp-net-revenue',
      'Doanh thu thuần theo ngày',
      'net_revenue',
      '4.1',
      'Doanh thu sau chiết khấu và trả hàng, theo cửa hàng và kênh.',
    ),
    plain(
      'dp-cogs-detail',
      'Giá vốn chi tiết',
      'cogs_detail',
      '1.8',
      'Giá vốn theo mã hàng, bình quân gia quyền tháng; có quyền theo dòng.',
    ),
    plain(
      'dp-ar-aging',
      'Công nợ phải thu',
      'ar_aging',
      '2.0',
      'Tuổi nợ theo khách hàng và hợp đồng; cập nhật 06:00 hằng ngày.',
    ),
    plain(
      'dp-cashflow-fc',
      'Dự báo dòng tiền 13 tuần',
      'cashflow_fc',
      '0.3',
      'Bản nháp do agent khởi tạo từ mô hình dự báo; chưa có contract.',
      'draft',
    ),
    plain(
      'dp-budget-actual',
      'Ngân sách và thực hiện',
      'budget_actual',
      '3.2',
      'So sánh ngân sách với thực hiện theo trung tâm chi phí.',
    ),
  ];
}

/* "Hộp thoại · importCatalog" (2095:9055): products the other Data Spaces publish. */
function catalogFixture(): CatalogProduct[] {
  return [
    {
      id: 'cat-net-revenue',
      code: 'net_revenue',
      name: 'Doanh thu thuần theo ngày',
      version: 'v4.1',
      owner_space: { id: 'ds-retail-sales', name: 'Kinh doanh bán lẻ' },
      has_pii: false,
    },
    {
      id: 'cat-customer-360',
      code: 'customer_360',
      name: 'Hồ sơ khách hàng 360',
      version: 'v2.0',
      owner_space: { id: 'ds-crm', name: 'CRM' },
      has_pii: true,
    },
  ];
}

/* "P06 · Quy tắc chất lượng" (2290:18318). */
function rulesFixture(): QualityRule[] {
  const an = { id: 'u-tran-quoc-an', name: 'Trần Quốc An' };
  const pass7 = Array<'pass'>(7).fill('pass');
  return [
    {
      id: 'qr-outstanding-non-negative',
      name: 'Dư nợ không âm',
      target: 'prep.loans_retail.outstanding_amt',
      severity: 'block',
      owner: an,
      last_7d: pass7,
      current: { state: 'pass', value_text: null },
    },
    {
      id: 'qr-loan-id-unique',
      name: 'Không trùng loan_id',
      target: 'prep.loans_retail.loan_id',
      severity: 'block',
      owner: an,
      last_7d: pass7,
      current: { state: 'pass', value_text: null },
    },
    {
      id: 'qr-gl-reconcile',
      name: 'Tổng dư nợ lệch sổ cái < 0,1%',
      target: 'mart.credit_risk',
      severity: 'block',
      owner: an,
      last_7d: ['pass', 'pass', 'pass', 'pass', 'pass', 'fail', 'fail'],
      current: { state: 'fail', value_text: '0,12%' },
    },
    {
      id: 'qr-customer-id-null',
      name: 'Tỷ lệ trống customer_id < 0,5%',
      target: 'raw.card_transactions',
      severity: 'warn',
      owner: { id: 'u-le-minh-chau', name: 'Lê Minh Châu' },
      last_7d: ['pass', 'pass', 'pass', 'pass', 'pass', 'pass', 'warn'],
      current: { state: 'warn', value_text: '0,7%' },
    },
    {
      id: 'qr-disbursement-before-maturity',
      name: 'Ngày giải ngân ≤ ngày đáo hạn',
      target: 'raw.ld_loans',
      severity: 'warn',
      owner: an,
      last_7d: pass7,
      current: { state: 'pass', value_text: null },
    },
  ];
}

const page = <T>(items: T[]) => HttpResponse.json({ items, next_page_token: '' });

const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    { title: code, status, code, detail },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

// The list carries the card fields only, as GET /v1/data-products would.
const summary = (p: DataProductDetail): DataProduct => ({
  id: p.id,
  name: p.name,
  code: p.code,
  version: p.version,
  next_version: p.next_version,
  description: p.description,
  status: p.status,
  breaking_change: p.breaking_change,
  used_by_spaces: p.used_by_spaces,
  quality_pct: p.quality_pct,
  refreshed_at: p.refreshed_at,
});

export function createDataProductsHandlers() {
  let mart = martFixture();
  let products = productsFixture();
  let rules = rulesFixture();
  // Marts a product was made from, so :publish knows whether it breaks the previous version.
  const fromMart = new Map<string, string>();

  return [
    http.get('*/v1/marts', () => page([{ id: mart.id, name: mart.name }])),
    http.get('*/v1/marts/:id', ({ params }) =>
      params.id === mart.id
        ? HttpResponse.json(mart)
        : problem(404, 'not_found', 'Không tìm thấy data mart.'),
    ),
    http.post('*/v1/marts/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      if (id !== mart.id || verb !== 'apply-suggestion')
        return problem(404, 'not_found', 'Không tìm thấy data mart.');
      const body = (await request.json()) as { dimensions: string[]; measures: string[] };
      mart = {
        ...mart,
        dimensions: mart.dimensions
          .filter((d) => !d.suggested || body.dimensions.includes(d.name))
          .map((d) =>
            d.suggested ? { ...d, suggested: false, lines: [d.lines[0], 'cán bộ tín dụng'] } : d,
          ),
        suggestion: null,
      };
      return HttpResponse.json(mart);
    }),

    http.get('*/v1/data-products', () => page(products.map(summary))),
    // Before /data-products/:id, which would read "catalog" as an id.
    http.get('*/v1/data-products/catalog', () => page(catalogFixture())),
    http.post('*/v1/data-products\\:request-use', async ({ request }) => {
      const body = (await request.json()) as { product_ids: string[]; data_space_id: string };
      const known = catalogFixture().filter((p) => body.product_ids?.includes(p.id));
      if (known.length === 0 || !body.data_space_id)
        return problem(422, 'validation_failed', 'Chọn sản phẩm và Data Space dùng nó.');
      return HttpResponse.json(
        {
          requests: known.map((p, i) => ({
            id: `req-use-${i + 1}`,
            product_id: p.id,
            status: 'pending',
          })),
        },
        { status: 202 },
      );
    }),
    http.get('*/v1/data-products/:id', ({ params }) => {
      const found = products.find((p) => p.id === params.id);
      return found
        ? HttpResponse.json(found)
        : problem(404, 'not_found', 'Không tìm thấy sản phẩm dữ liệu.');
    }),
    http.post('*/v1/data-products', async ({ request }) => {
      // DataProductCreate (layer=published); the version is the mart's draft one.
      const body = (await request.json()) as { data_mart_id: string; name: string; code: string };
      if (body.data_mart_id !== mart.id)
        return problem(422, 'validation_failed', 'Data mart không tồn tại.');
      const id = `dp-${body.name.replace(/_/g, '-')}`;
      const created: DataProductDetail = {
        ...productsFixture()[4],
        id,
        name: body.name,
        code: body.code,
        version: mart.product_draft.version,
        description: mart.name,
        used_by_spaces: null,
        quality_pct: null,
      };
      products = [...products.filter((p) => p.id !== id), created];
      fromMart.set(id, mart.id);
      return HttpResponse.json(summary(created), { status: 201 });
    }),
    http.post('*/v1/data-products/:action', ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      const found = products.find((p) => p.id === id);
      if (!found) return problem(404, 'not_found', 'Không tìm thấy sản phẩm dữ liệu.');
      if (verb === 'draft-version') {
        const done: DataProductDetail = { ...found, agent_note: null };
        products = products.map((p) => (p.id === id ? done : p));
        return HttpResponse.json(done);
      }
      if (verb !== 'publish') return problem(404, 'not_found', 'Không có thao tác này.');
      // A breaking change goes to an approver instead of out (BR: bên dùng phải được báo trước).
      const breaking = fromMart.get(id) === mart.id && mart.product_draft.breaking_note !== null;
      const done: DataProductDetail = {
        ...found,
        status: breaking ? 'pending_approval' : 'published',
      };
      products = products.map((p) => (p.id === id ? done : p));
      return HttpResponse.json(summary(done), { status: breaking ? 202 : 200 });
    }),

    http.get('*/v1/quality-rules', () => page(rules)),
    http.post('*/v1/quality-rules', async ({ request }) => {
      const body = (await request.json()) as {
        name: string;
        target: string;
        severity: RuleSeverity;
      };
      if (!body.name?.trim() || !body.target?.trim())
        return problem(422, 'validation_failed', 'Cần tên quy tắc và nơi áp vào.');
      const created: QualityRule = {
        id: `qr-${rules.length + 1}`,
        name: body.name.trim(),
        target: body.target.trim(),
        severity: body.severity,
        owner: { id: 'u-me', name: 'Bạn' },
        last_7d: Array(7).fill(null),
        current: null,
      };
      rules = [...rules, created];
      return HttpResponse.json(created, { status: 201 });
    }),
  ];
}
