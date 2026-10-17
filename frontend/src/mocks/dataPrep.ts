import { http, HttpResponse } from 'msw';
import type {
  DerivedTable,
  LineageGraph,
  OutputDraft,
  Pipeline,
  PipelineDetail,
  PipelineSchedule,
  PipelineStep,
  ScheduleKind,
  SqlResult,
  Writeback,
} from '../lib/v1/dataPrep';

/*
 * P06 Chuẩn bị dữ liệu, first half: the SQL editor, pipelines, their output and schedule,
 * write-back and lineage. Data from "v3.1 · Dữ liệu · SQL" (2082:6880), "P06 · Pipeline · Danh
 * sách" (2288:17612), "Làm sạch bằng hội thoại" (2288:17795), "Lưu thành bảng & lịch chạy"
 * (2288:17996), "Hộp thoại · Lịch chạy" (2239:15581), "Ghi ngược có duyệt" (2290:17788) and
 * "Dòng dữ liệu" (2290:17979).
 */
const page = <T>(items: T[]) => HttpResponse.json({ items, next_page_token: '' });

const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    { type: `https://errors.dataagent/${code}`, title: detail, status, code, detail },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

/** The mock Data Space "Rủi ro tín dụng" (same id as fixtures.ts). */
const DS = 'ds-rui-ro-tin-dung';

/** Today at hh:mm local time, so "lần chạy gần nhất" reads as in the design. */
const today = (hh: number, mm: number) => {
  const d = new Date();
  d.setHours(hh, mm, 0, 0);
  return d.toISOString();
};

function pipelinesFixture(): Pipeline[] {
  return [
    {
      id: 'pl-loans-retail',
      name: 'Chuẩn hoá khoản vay bán lẻ',
      owner_name: 'Đỗ Hải Yến',
      step_count: 7,
      inputs: ['raw.ld_loans'],
      output_table: 'prep.loans_retail',
      schedule_text: 'Sau job ld_loans',
      last_run: { started_at: today(6, 12), duration_s: 240 },
      status: 'succeeded',
    },
    {
      id: 'pl-customers',
      name: 'Gộp khách hàng trùng',
      owner_name: 'Agent Chuẩn bị dữ liệu',
      step_count: 5,
      inputs: ['raw.customers', 'crm'],
      output_table: 'prep.customers',
      schedule_text: 'Hằng ngày 04:00',
      last_run: { started_at: today(4, 9), duration_s: 540 },
      status: 'succeeded',
    },
    {
      id: 'pl-credit-risk',
      name: 'Mart rủi ro tín dụng',
      owner_name: 'Trần Quốc An',
      step_count: 11,
      inputs: ['prep.*'],
      output_table: 'mart.credit_risk',
      schedule_text: 'Sau 2 pipeline trên',
      last_run: { started_at: today(6, 20), duration_s: 360 },
      status: 'succeeded',
    },
    {
      id: 'pl-targets',
      name: 'Chỉ tiêu chi nhánh',
      owner_name: 'Lê Minh Châu',
      step_count: 3,
      inputs: ['upload.chi_tieu'],
      output_table: 'prep.targets',
      schedule_text: 'Khi tải tệp mới',
      last_run: { started_at: '2026-10-01T09:30:00+07:00', duration_s: 60 },
      status: 'succeeded',
    },
    {
      id: 'pl-card-daily',
      name: 'Giao dịch thẻ theo ngày',
      owner_name: 'Agent Chuẩn bị dữ liệu',
      step_count: 6,
      inputs: ['raw.card_transactions'],
      output_table: 'prep.card_daily',
      schedule_text: 'Sau job card_transactions',
      last_run: null,
      status: 'waiting_input',
    },
  ];
}

const step = (
  seq: number,
  title: string,
  sql: string,
  rows_in: number,
  rows_out: number,
  extra: Partial<PipelineStep> = {},
): PipelineStep => ({
  seq,
  title,
  sql,
  rows_in,
  rows_out,
  is_new: false,
  kind_label: null,
  ...extra,
});

function loansRetailFixture(base: Pipeline): PipelineDetail {
  return {
    ...base,
    step_count: 6,
    input_table: 'raw.ld_loans',
    input_rows: 1_284_562,
    preview_sample: 1000,
    steps: [
      step(
        1,
        'Đọc raw.ld_loans · snapshot mới nhất',
        'SELECT * FROM raw.ld_loans WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM raw.ld_loans)',
        1_284_562,
        1_284_562,
      ),
      step(
        2,
        'Bỏ dòng trùng loan_id',
        'SELECT DISTINCT ON (loan_id) * FROM s1 ORDER BY loan_id, etl_ts DESC',
        1_284_562,
        1_284_554,
      ),
      step(
        3,
        'Chỉ giữ phân khúc bán lẻ',
        "SELECT * FROM s2 WHERE segment = 'retail'",
        1_284_554,
        1_102_311,
        { kind_label: 'lọc' },
      ),
      step(
        4,
        'Bỏ khoản tất toán trước 01/01/2024',
        "SELECT * FROM s3 WHERE NOT (status = 'closed' AND closed_at < DATE '2024-01-01')",
        1_102_311,
        884_020,
        { is_new: true },
      ),
      step(
        5,
        'Chuẩn hoá branch_code về 6 ký tự',
        "SELECT *, LPAD(REPLACE(branch_code, '0', ''), 6, '0') AS branch_code FROM s4",
        884_020,
        884_020,
        { is_new: true },
      ),
      step(
        6,
        'Tính dpd theo ngày trả nợ thực tế; loại 8 khoản thiếu ngày',
        'SELECT *, (CURRENT_DATE - actual_due_date) AS dpd FROM s5 WHERE actual_due_date IS NOT NULL',
        884_020,
        884_012,
        { is_new: true },
      ),
    ],
    messages: [
      {
        id: 'm1',
        author: 'user',
        text: 'Bỏ khoản đã tất toán trước 2024, chuẩn hoá mã chi nhánh về 6 ký tự, và tính số ngày quá hạn theo ngày trả nợ thực tế.',
      },
      {
        id: 'm2',
        author: 'agent',
        text: 'Đã thêm 3 bước (4–6). Lưu ý: 312 khoản có mã chi nhánh 5 ký tự — tôi thêm số 0 ở đầu. 8 khoản không có ngày trả nợ thực tế, tôi giữ ngày đến hạn và đánh dấu để bạn xem.',
      },
      { id: 'm3', author: 'user', text: 'Với 8 khoản đó thì loại ra luôn.' },
      {
        id: 'm4',
        author: 'agent',
        text: 'Đã sửa bước 6: loại 8 khoản, ghi vào bảng ngoại lệ prep.loans_excluded.',
      },
    ],
  };
}

/** The rows of a step preview's query run (qr-step-<seq>), as the worker stores them. */
function previewRows(seq: number) {
  if (seq === 5)
    return {
      columns: ['loan_id', 'branch_code'],
      items: [
        ['LD2409-0187', 'HPG001'],
        ['LD2409-0201', '012045'],
        ['LD2410-0031', 'QNH002'],
      ],
    };
  return {
    columns: ['loan_id', 'status'],
    items: [
      ['LD2409-0187', 'active'],
      ['LD2311-0442', 'closed'],
    ],
  };
}

function outputDraftFixture(): OutputDraft {
  return {
    table_name: 'prep.loans_retail',
    data_space_id: DS,
    write_mode: 'replace',
    columns: [
      { name: 'loan_id', type: 'VARCHAR(20)', from_step: 1, is_new: false },
      { name: 'branch_code', type: 'CHAR(6)', from_step: 5, is_new: false },
      { name: 'dpd', type: 'INT', from_step: 6, is_new: true },
      { name: 'outstanding_amt', type: 'NUMERIC(18,2)', from_step: 1, is_new: false },
      { name: 'debt_group', type: 'SMALLINT', from_step: 1, is_new: false },
    ],
    consumers_text:
      'Pipeline “Mart rủi ro tín dụng” sẽ đọc bảng này. Đổi cấu trúc bảng sẽ báo cho người phụ trách mart.',
    input_jobs: [
      { value: 'job-ld-loans', label: 'Job nạp ld_loans thành công' },
      { value: 'job-core-banking', label: 'Job nạp Core banking thành công' },
    ],
    notify_options: [
      { value: 'hy-teams', label: 'Đỗ Hải Yến · Teams' },
      { value: 'hy-email', label: 'Đỗ Hải Yến · Email' },
      { value: 'data-eng', label: 'Kỹ sư dữ liệu · Teams' },
    ],
  };
}

function scheduleFixture(): PipelineSchedule {
  return {
    kind: 'after_source',
    cron: null,
    skip_holidays: true,
    no_overlap: true,
    retry_and_notify: true,
    after_source_label: 'sau job nạp Core banking',
    notify_label: 'Kỹ sư dữ liệu',
  };
}

const SCHEDULE_PREVIEW: Record<ScheduleKind, string> = {
  after_source:
    'Chạy ngay khi job nạp Core banking xong, thường 06:05–06:15. Nếu đến 08:00 chưa có dữ liệu mới thì báo Kỹ sư dữ liệu.',
  daily: 'Chạy lúc 06:30.',
  business_hours: 'Chạy mỗi giờ từ 08:00 đến 17:00.',
  cron: 'Chạy theo biểu thức cron đã nhập.',
};

const SCHEDULE_TEXT: Record<ScheduleKind, string> = {
  after_source: 'chạy sau job nạp Core banking',
  daily: 'chạy hằng ngày lúc 06:30',
  business_hours: 'chạy mỗi giờ trong giờ làm việc',
  cron: 'chạy theo cron',
};

function writebackFixture(): Writeback {
  return {
    id: 'wb-0042',
    code: 'WB-0042',
    title: 'Ghi ngược hạng tín dụng vào CRM',
    status: 'pending_approval',
    summary_text:
      'Agent Phân tích đề xuất cập nhật hạng tín dụng cho 1.240 khách hàng · ghi vào bảng chờ crm_staging.credit_tier, không ghi thẳng vào CRM',
    target_table: 'crm_staging.credit_tier',
    row_count: 1240,
    changes: [
      { subject: 'KH ••••4821', before: 'B', after: 'C', reason: 'Nợ quá hạn 45 ngày tháng 9' },
      { subject: 'KH ••••1093', before: 'C', after: 'B', reason: 'Trả hết nợ quá hạn 02/10' },
      {
        subject: 'KH ••••7720',
        before: 'A',
        after: 'B',
        reason: 'Dư nợ thẻ vượt 90% hạn mức 3 tháng',
      },
      { subject: 'KH ••••3318', before: 'B', after: 'C', reason: 'Khoản vay cơ cấu lần 2' },
      { subject: 'KH ••••5402', before: null, after: 'C', reason: 'Khách hàng mới, chưa có hạng' },
    ],
    checks: [
      { ok: true, text: '1.240 dòng hợp lệ với cấu trúc crm_staging.credit_tier' },
      { ok: true, text: 'Không dòng nào ghi vào khách hàng ngoài phạm vi Data Space' },
      {
        ok: false,
        text: '31 khách hàng đang có yêu cầu điều chỉnh thủ công trong CRM — sẽ bị bỏ qua',
      },
    ],
    proposal_id: 'proposal-wb-0042',
    approvers: [
      { name: 'Trần Quốc An', role_label: 'Chủ sở hữu dữ liệu tín dụng', state: 'pending' },
      { name: 'Ngô Thanh Bình', role_label: 'Chủ sở hữu hệ thống CRM', state: 'waiting_previous' },
    ],
    can_decide: true,
    after_approval_text:
      'Dữ liệu vào bảng chờ; đội CRM chạy tác vụ nhập theo quy trình của họ. Data Agent không ghi trực tiếp vào bảng nghiệp vụ.',
    rollback_text: 'Bảng chờ giữ bản trước 30 ngày; có thể tạo lệnh ghi ngược đảo chiều.',
  };
}

function lineageFixture(): LineageGraph {
  const node = (
    id: string,
    label: string,
    kind: LineageGraph['nodes'][number]['kind'],
    detail_text: string,
    late = false,
    facts: { label: string; text: string }[] = [],
  ) => ({ id, label, kind, detail_text, late, facts });
  return {
    focus_id: 'metric-npl',
    title: 'NPL_RATIO',
    nodes: [
      node('src-core', 'Core banking', 'source', 'nguồn · Oracle', false, [
        { label: 'Lần nạp gần nhất', text: '10:41 · đúng hạn' },
      ]),
      node('src-dwh', 'DWH bán lẻ', 'source', 'nguồn · trễ 8 giờ', true, [
        { label: 'Lần nạp gần nhất', text: '02:10 · trễ 8 giờ so với cam kết' },
      ]),
      node('raw-loans', 'raw.ld_loans', 'raw_table', 'bảng thô · 10:41'),
      node('raw-sales', 'raw.retail_sales', 'raw_table', 'bảng thô · 02:10', true),
      node('prep-loans', 'prep.loans_retail', 'pipeline_table', 'pipeline · 7 bước', false, [
        { label: 'Pipeline', text: 'Chuẩn hoá khoản vay bán lẻ · 7 bước' },
      ]),
      node('mart-credit', 'mart.credit_risk', 'mart', 'data mart'),
      node('metric-npl', 'NPL_RATIO v4', 'metric', 'chỉ số · chứng nhận', false, [
        {
          label: 'Công thức',
          text: 'dư nợ nhóm 3–5 ÷ tổng dư nợ, phân khúc bán lẻ, loại vay nội bộ',
        },
        { label: 'Dữ liệu mới nhất', text: '30/09 23:59 · Core banking' },
        {
          label: 'Phụ thuộc trễ',
          text: 'DWH bán lẻ trễ 8 giờ — chỉ ảnh hưởng chiều “kênh bán”, không ảnh hưởng giá trị NPL',
        },
        { label: 'Dùng ở', text: '23 artifact · 5 agent · 2 báo cáo định kỳ' },
      ]),
      node('report-t9', 'Báo cáo rủi ro T9', 'report', 'báo cáo'),
      node('agent-risk', 'Agent Giám sát rủi ro', 'agent', 'agent'),
    ],
    edges: [
      { from: 'src-core', to: 'raw-loans' },
      { from: 'src-dwh', to: 'raw-sales' },
      { from: 'raw-loans', to: 'prep-loans' },
      { from: 'prep-loans', to: 'mart-credit' },
      { from: 'raw-sales', to: 'mart-credit' },
      { from: 'mart-credit', to: 'metric-npl' },
      { from: 'metric-npl', to: 'report-t9' },
      { from: 'metric-npl', to: 'agent-risk' },
    ],
  };
}

/** The fixture graph as GET /v1/lineage sends it (keys stand for kind:id). */
const API_KIND: Record<string, string> = {
  source: 'source',
  raw_table: 'dataset',
  pipeline_table: 'dataset',
  mart: 'data_product',
  metric: 'metric',
  report: 'report',
  agent: 'agent',
};
function lineageWire(g: LineageGraph) {
  const nodes = g.nodes.map((n) => ({
    key: n.id,
    kind: API_KIND[n.kind],
    id: n.id,
    name: n.label,
    hidden: false,
  }));
  return {
    root: nodes.find((n) => n.key === g.focus_id),
    direction: 'both',
    depth: 3,
    nodes,
    edges: g.edges.map((e) => ({
      source: e.from,
      target: e.to,
      via: e.to === 'prep-loans' ? 'pipeline' : null,
      via_ref: null,
    })),
    grouped: false,
    groups: [],
  };
}

function prepTableLineage(): LineageGraph {
  const base = lineageFixture();
  const keep = new Set(['src-core', 'raw-loans', 'prep-loans', 'mart-credit']);
  return {
    focus_id: 'prep-loans',
    title: 'prep.loans_retail',
    nodes: base.nodes.filter((n) => keep.has(n.id)),
    edges: base.edges.filter((e) => keep.has(e.from) && keep.has(e.to)),
  };
}

const SQL_RESULT: SqlResult = {
  columns: ['region', 'product', 'npl'],
  rows: [
    ['DH Bắc Bộ', 'Tiêu dùng', '0,0284'],
    ['ĐB sông Hồng', 'Thẻ', '0,0228'],
    ['DH Bắc Bộ', 'Thẻ', '0,0219'],
    ['Tây Nguyên', 'Tiêu dùng', '0,0201'],
  ],
  row_count: 20,
  duration_ms: 800,
  permission_scope: 'Rủi ro tín dụng',
  masked_columns: ['customer_id'],
};

/** PipelineView as the api sends it, with the screen's row under display. */
const pipelineWire = (p: Pipeline) => ({
  id: p.id,
  name: p.name,
  steps: [],
  input_dataset_ids: [],
  target_dataset_id: null,
  status: p.status === 'draft' ? 'draft' : 'active',
  scope: 'data_space',
  cron: null,
  depends_on_pipeline_ids: [],
  trigger_on_job_id: null,
  no_overlap: true,
  on_failure: 'notify',
  owner_id: 'me',
  work_calendar_id: null,
  data_space_id: 'ds-rui-ro-tin-dung',
  created_at: '2026-10-01T09:00:00+07:00',
  updated_at: '2026-10-01T09:00:00+07:00',
  display: p,
});

export function createDataPrepHandlers() {
  const pipelines = pipelinesFixture();
  const details = new Map<string, PipelineDetail>([
    ['pl-loans-retail', loansRetailFixture(pipelines[0])],
  ]);
  const schedules = new Map<string, PipelineSchedule>();
  let writeback = writebackFixture();
  let seq = 0;

  const detailOf = (id: string): PipelineDetail | undefined => {
    if (details.has(id)) return details.get(id);
    const base = pipelines.find((p) => p.id === id);
    if (!base) return undefined;
    const detail: PipelineDetail = {
      ...base,
      input_table: base.inputs[0] ?? '',
      input_rows: 0,
      preview_sample: 1000,
      steps: [],
      messages: [],
    };
    details.set(id, detail);
    return detail;
  };
  const notFound = () => problem(404, 'not_found', 'Không tìm thấy pipeline.');
  const materialized = new Map<string, { runId: string; tableId: string }>();

  return [
    /* The SQL screen uses the canonical query-run worker. */
    http.post('*/v1/query-runs', async ({ request }) => {
      const body = (await request.json()) as { sql?: string };
      const sql = body.sql ?? '';
      if (!sql.trim()) return problem(400, 'invalid_request', 'Chưa có câu truy vấn.');
      if (/\b(insert|update|delete|drop|alter|truncate)\b/i.test(sql))
        return problem(403, 'read_only', 'Trình SQL chỉ chạy câu đọc dữ liệu.');
      return HttpResponse.json(
        {
          id: 'qr-sql-001',
          status: 'succeeded',
          row_count: SQL_RESULT.row_count,
          duration_ms: SQL_RESULT.duration_ms,
          permission_scope: SQL_RESULT.permission_scope,
          masked_columns: SQL_RESULT.masked_columns,
          error: null,
        },
        { status: 202 },
      );
    }),
    http.get('*/v1/query-runs/:id', () =>
      HttpResponse.json({
        id: 'qr-sql-001',
        status: 'succeeded',
        row_count: SQL_RESULT.row_count,
        duration_ms: SQL_RESULT.duration_ms,
        permission_scope: SQL_RESULT.permission_scope,
        masked_columns: SQL_RESULT.masked_columns,
        error: null,
      }),
    ),
    http.get('*/v1/query-runs/:id/rows', ({ params }) => {
      const id = String(params.id);
      if (id.startsWith('qr-step-')) {
        const step = previewRows(Number(id.slice('qr-step-'.length)));
        return HttpResponse.json({
          columns: step.columns.map((name) => ({ name, type: 'string' })),
          items: step.items,
          truncated: false,
          next_page_token: '',
          expires_at: '2026-10-15T00:00:00+07:00',
        });
      }
      return HttpResponse.json({
        columns: SQL_RESULT.columns.map((name) => ({ name, type: 'string' })),
        items: SQL_RESULT.rows,
        truncated: false,
        next_page_token: '',
        expires_at: '2026-10-15T00:00:00+07:00',
      });
    }),
    http.post('*/v1/derived-tables', async ({ request }) => {
      const body = (await request.json()) as Omit<DerivedTable, 'id' | 'version' | 'write_mode'>;
      if (!/^[a-z][a-z0-9_.]{2,62}$/.test(body.name))
        return problem(
          422,
          'validation_failed',
          'Tên chỉ gồm chữ thường, số, dấu chấm và gạch dưới.',
        );
      return HttpResponse.json(
        { ...body, id: `dt-${++seq}`, version: 1, write_mode: 'replace' } satisfies DerivedTable,
        { status: 201 },
      );
    }),

    /* Pipelines */
    // PipelineView with the screen's row under display.
    http.get('*/v1/pipelines', () => page(pipelines.map(pipelineWire))),
    // The table the create dialog names, found by GET /v1/datasets?q= (the catalog's search);
    // any other dataset list falls through to the catalog mock.
    http.get('*/v1/datasets/:id', ({ params }) => {
      const id = String(params.id);
      // Only this dialog's table. A source scan's datasets are answered by the catalog mock.
      if (id !== 'dataset-loans-dedup') return undefined;
      return HttpResponse.json({
        id,
        schema_name: 'mart',
        table_name: 'loans_dedup_0930',
        source_id: null,
        columns: ['region', 'product', 'balance', 'debt_group', 'segment'].map((name, ordinal) => ({
          id: `column-${name}`,
          name,
          data_type: name === 'balance' ? 'numeric' : 'text',
          dataset_version: 1,
          description: null,
          description_status: 'none',
          display_name: null,
          is_key: false,
          is_nullable: true,
          normalized_type: name === 'balance' ? 'number' : 'string',
          ordinal,
          pii_confirmed: true,
          pii_label: null,
          unit: null,
        })),
      });
    }),
    http.get('*/v1/datasets', ({ request }) => {
      const url = new URL(request.url);
      // A connect scan lists datasets by source_id; that list belongs to the catalog mock.
      if (url.searchParams.get('source_id') || url.searchParams.get('enabled')) return undefined;
      const q = url.searchParams.get('q');
      if (!q)
        return page([
          {
            id: 'dataset-loans-dedup',
            schema_name: 'mart',
            table_name: 'loans_dedup_0930',
            source_id: null,
          },
        ]);
      return page([{ id: `dataset-${q}`, schema_name: 'raw', table_name: q }]);
    }),
    // PipelineCreate: the name and the input dataset ids.
    http.post('*/v1/pipelines', async ({ request }) => {
      const body = (await request.json()) as { name: string; input_dataset_ids: string[] };
      const created: Pipeline = {
        id: `pl-new-${++seq}`,
        name: body.name,
        owner_name: 'Bạn',
        step_count: 0,
        inputs: body.input_dataset_ids.map((id) => `raw.${id.replace(/^dataset-/, '')}`),
        output_table: null,
        schedule_text: 'Thủ công',
        last_run: null,
        status: 'draft',
      };
      pipelines.push(created);
      return HttpResponse.json(pipelineWire(created), { status: 201 });
    }),
    http.post('*/v1/pipelines/:id/runs', ({ params }) => {
      const p = pipelines.find((x) => x.id === params.id);
      if (!p) return notFound();
      if (p.status === 'waiting_input')
        return problem(
          409,
          'input_not_ready',
          'Job nạp đầu vào chưa xong; pipeline không chạy trên dữ liệu cũ.',
        );
      if (p.status === 'running') return problem(409, 'already_running', 'Pipeline đang chạy.');
      return HttpResponse.json({ id: `run-${++seq}` }, { status: 202 });
    }),
    http.get('*/v1/pipelines/:id/steps/:seq/preview', ({ params }) =>
      detailOf(String(params.id))
        ? HttpResponse.json({
            seq: Number(params.seq),
            kind: 'compute',
            shows: 'output',
            query_run_id: `qr-step-${params.seq}`,
            status: 'succeeded',
          })
        : notFound(),
    ),
    http.post('*/v1/pipelines/:id/messages', async ({ params, request }) => {
      const detail = detailOf(String(params.id));
      if (!detail) return notFound();
      const { text } = (await request.json()) as { text: string };
      const nextSeq = detail.steps.length + 1;
      const rows = detail.steps.at(-1)?.rows_out ?? detail.input_rows;
      const updated: PipelineDetail = {
        ...detail,
        step_count: nextSeq,
        steps: [
          ...detail.steps.map((s) => ({ ...s, is_new: false })),
          step(nextSeq, text, `-- ${text}\nSELECT * FROM s${nextSeq - 1}`, rows, rows, {
            is_new: true,
          }),
        ],
        messages: [
          ...detail.messages,
          { id: `m-${++seq}`, author: 'user', text },
          {
            id: `m-${++seq}`,
            author: 'agent',
            text: `Đã thêm bước ${nextSeq}. Bạn xem số dòng trước khi chạy thử toàn bộ.`,
          },
        ],
      };
      details.set(detail.id, updated);
      return HttpResponse.json(updated);
    }),
    http.get('*/v1/pipelines/:id/output-draft', ({ params }) =>
      detailOf(String(params.id)) ? HttpResponse.json(outputDraftFixture()) : notFound(),
    ),
    http.get('*/v1/pipelines/:id/schedule', ({ params }) => {
      const id = String(params.id);
      if (!pipelines.some((p) => p.id === id)) return notFound();
      return HttpResponse.json(schedules.get(id) ?? scheduleFixture());
    }),
    http.put('*/v1/pipelines/:id/schedule', async ({ params, request }) => {
      const id = String(params.id);
      const p = pipelines.find((x) => x.id === id);
      if (!p) return notFound();
      const body = (await request.json()) as PipelineSchedule & {
        schedule_kind?: ScheduleKind;
      };
      const kind: ScheduleKind =
        body.schedule_kind ?? body.kind ?? (body.cron ? 'cron' : 'after_source');
      if (kind === 'cron' && !body.cron?.trim())
        return problem(422, 'validation_failed', 'Nhập biểu thức cron.');
      schedules.set(id, {
        ...body,
        kind,
      });
      return HttpResponse.json({
        schedule_text:
          kind === 'daily'
            ? 'chạy hằng ngày lúc 06:30'
            : kind === 'business_hours'
              ? 'chạy mỗi giờ trong giờ làm việc'
              : kind === 'after_source'
                ? 'chạy sau job nạp Core banking'
                : `Theo lịch cron ${body.cron}`,
      });
    }),
    http.post('*/v1/pipelines/:id/:action', async ({ params, request }) => {
      const action = String(params.action);
      if (action === 'schedule:preview') {
        const body = (await request.json()) as PipelineSchedule & { schedule_kind?: ScheduleKind };
        const holidays = body.skip_holidays
          ? ' Ngày làm việc tới: Thứ Sáu 09/10, Thứ Hai 12/10 (bỏ 10–11/10).'
          : '';
        const kind = body.schedule_kind ?? body.kind ?? (body.cron ? 'cron' : 'after_source');
        return HttpResponse.json({ text: SCHEDULE_PREVIEW[kind] + holidays });
      }
      return undefined;
    }),
    http.post('*/v1/pipelines/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      const detail = verb ? detailOf(id) : undefined;
      if (!verb) return undefined;
      if (!detail) return notFound();
      if (verb === 'test-run')
        return HttpResponse.json({
          rows_out: detail.steps.at(-1)?.rows_out ?? 0,
          excluded_rows: 8,
          duration_s: 41,
        });
      if (verb === 'materialize') {
        const body = (await request.json()) as { table_name: string };
        if (!/^[a-z_][a-z0-9_]{0,62}$/.test(body.table_name))
          return problem(422, 'validation_failed', 'Tên bảng chỉ gồm chữ thường, số và _.');
        const runId = `run-${++seq}`;
        const tableId = `dt-${seq}`;
        materialized.set(id, { runId, tableId });
        const p = pipelines.find((x) => x.id === id);
        if (p) p.output_table = body.table_name;
        return HttpResponse.json({ id: runId, status: 'running' }, { status: 202 });
      }
      return problem(404, 'not_found', 'Không có thao tác này.');
    }),
    http.get('*/v1/pipelines/:id', ({ params }) => {
      const detail = detailOf(String(params.id));
      if (!detail) return notFound();
      // After :materialize the worker has run: the last run and the table it wrote.
      const saved = materialized.get(String(params.id));
      return HttpResponse.json(
        saved
          ? {
              ...detail,
              data_space_id: 'ds-tin-dung',
              target_dataset_id: saved.tableId,
              last_run: { id: saved.runId, status: 'succeeded', error: null },
            }
          : detail,
      );
    }),

    /* Ghi ngược */
    http.get('*/v1/writebacks', () =>
      page(writeback.status === 'pending_approval' ? [writeback] : []),
    ),
    http.get('*/v1/writebacks/:id', ({ params }) =>
      params.id === writeback.id
        ? HttpResponse.json(writeback)
        : problem(404, 'not_found', 'Không tìm thấy lệnh ghi ngược.'),
    ),
    http.post(/\/v1\/proposals\/[^/]+:decide$/, async ({ request }) => {
      const proposalId = new URL(request.url).pathname.split('/').at(-1)?.split(':')[0];
      // Other proposals (Cần bạn, skills) are decided by their own handlers.
      if (proposalId !== writeback.proposal_id) return;
      const body = (await request.json()) as { decision?: string; note?: string };
      const current = writeback.approvers.findIndex((a) => a.state === 'pending');
      if (body.decision === 'accept') {
        const approvers = writeback.approvers.map((a, i) =>
          i === current
            ? { ...a, state: 'approved' as const }
            : i === current + 1
              ? { ...a, state: 'pending' as const }
              : a,
        );
        const done = approvers.every((a) => a.state === 'approved');
        writeback = {
          ...writeback,
          approvers,
          status: done ? 'approved' : 'pending_approval',
          can_decide: false,
        };
      } else if (body.decision === 'reject' || body.decision === 'request_changes') {
        if (!body.note?.trim())
          return problem(422, 'validation_failed', 'Ghi lý do để người đề xuất biết cần sửa gì.');
        writeback = {
          ...writeback,
          status: body.decision === 'reject' ? 'rejected' : 'changes_requested',
          approvers:
            body.decision === 'reject'
              ? writeback.approvers.map((a, i) =>
                  i === current ? { ...a, state: 'rejected' as const } : a,
                )
              : writeback.approvers,
          can_decide: false,
        };
      }
      return HttpResponse.json({ id: writeback.proposal_id, status: 'pending_review' });
    }),
    http.post('*/v1/writebacks/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      if (id !== writeback.id) return problem(404, 'not_found', 'Không tìm thấy lệnh ghi ngược.');
      if (!writeback.can_decide)
        return problem(403, 'forbidden', 'Chưa đến lượt bạn duyệt lệnh này.');
      const body = (await request.json().catch(() => ({}))) as { reason?: string; note?: string };
      const current = writeback.approvers.findIndex((a) => a.state === 'pending');
      if (verb === 'approve') {
        const approvers = writeback.approvers.map((a, i) =>
          i === current
            ? { ...a, state: 'approved' as const }
            : i === current + 1
              ? { ...a, state: 'pending' as const }
              : a,
        );
        const done = approvers.every((a) => a.state === 'approved');
        writeback = {
          ...writeback,
          approvers,
          status: done ? 'approved' : 'pending_approval',
          // The next approver is someone else.
          can_decide: false,
        };
        return HttpResponse.json(writeback);
      }
      if (verb === 'reject' || verb === 'request-changes') {
        if (!(body.reason ?? body.note)?.trim())
          return problem(422, 'validation_failed', 'Ghi lý do để người đề xuất biết cần sửa gì.');
        writeback = {
          ...writeback,
          status: verb === 'reject' ? 'rejected' : 'changes_requested',
          approvers: writeback.approvers.map((a, i) =>
            i === current && verb === 'reject' ? { ...a, state: 'rejected' as const } : a,
          ),
          can_decide: false,
        };
        return HttpResponse.json(writeback);
      }
      return problem(404, 'not_found', 'Không có thao tác này.');
    }),

    /* Dòng dữ liệu */
    http.get('*/v1/lineage/targets', () =>
      HttpResponse.json([
        { id: 'metric-npl', label: 'NPL_RATIO v4 · chỉ số' },
        { id: 'prep-loans', label: 'prep.loans_retail · bảng' },
      ]),
    ),
    http.get('*/v1/lineage', ({ request }) => {
      const target = new URL(request.url).searchParams.get('object_id');
      if (target === 'metric-npl') return HttpResponse.json(lineageWire(lineageFixture()));
      if (target === 'prep-loans') return HttpResponse.json(lineageWire(prepTableLineage()));
      return problem(404, 'not_found', 'Không tìm thấy đối tượng hoặc bạn không có quyền xem.');
    }),
  ];
}
