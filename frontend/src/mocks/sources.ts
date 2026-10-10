import { http, HttpResponse } from 'msw';
import type {
  ActivateSource,
  Collection,
  CollectionDetail,
  CollectionDocument,
  CollectionsSummary,
  ColumnProfile,
  ConnectionTest,
  Connector,
  DocumentDetail,
  IngestionJob,
  NewSource,
  SecretRef,
  SourceRow,
  SourceTable,
  StepState,
  TableProfile,
  UploadAnalysis,
  UploadColumn,
} from '../lib/v1/sources';

/*
 * MSW handlers for lib/v1/sources.ts (Dữ liệu · Nguồn, Job nạp, Tải tệp, Tài liệu), with the
 * data of the P04/P05 frames. State lives in memory for the page's lifetime: a source created
 * in the connect flow appears in the list; its first connection test fails the read-only check
 * (the "lỗi quyền ghi" frame) and the next one passes, as if the DBA had revoked the grant.
 * This file serves GET /v1/sources too, with the richer rows; it must come before the base
 * handlers (mocks/handlers.ts) for that path.
 */
const page = <T>(items: T[]) => HttpResponse.json({ items, next_page_token: '' });

const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    {
      type: `https://docs.dataagent/errors/${code}`,
      title: code,
      status,
      code,
      detail,
      trace_id: 'mock',
    },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

const minutesAgo = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();
/** Today at hh:mm, or yesterday when that is still ahead, so a fixture time reads as in Figma. */
const at = (hh: number, mm: number) => {
  const d = new Date();
  d.setHours(hh, mm, 0, 0);
  if (d.getTime() > Date.now()) d.setDate(d.getDate() - 1);
  return d.toISOString();
};

const RISK = { id: 'ds-rui-ro-tin-dung', name: 'Rủi ro tín dụng' };
const RETAIL = { id: 'ds-kinh-doanh-ban-le', name: 'Kinh doanh bán lẻ' };
const FINANCE = { id: 'ds-tai-chinh', name: 'Tài chính' };
const OPERATIONS = { id: 'ds-van-hanh', name: 'Vận hành' };

function row(
  partial: Partial<SourceRow> &
    Pick<SourceRow, 'id' | 'name' | 'connector' | 'connector_label' | 'endpoint'>,
): SourceRow {
  return {
    kind: partial.connector,
    mode: 'ingest',
    status: 'active',
    status_reason: null,
    last_checked_at: minutesAgo(3),
    last_refreshed_at: null,
    late_by_minutes: null,
    refresh_text: null,
    status_label: 'Đúng lịch',
    schema_drift: false,
    lag_seconds: null,
    held_records: null,
    data_spaces: [],
    ...partial,
  };
}

export function sourceRowsFixture(): SourceRow[] {
  return [
    row({
      id: 'src-core',
      name: 'Core banking',
      connector: 'oracle',
      connector_label: 'Oracle',
      endpoint: 'core-db.noibo',
      refresh_text: 'CDC',
      status_label: 'Đang đồng bộ',
      last_refreshed_at: at(10, 41),
      lag_seconds: 12,
      data_spaces: [RISK, RETAIL, FINANCE],
    }),
    row({
      id: 'src-dwh-retail',
      name: 'DWH bán lẻ',
      connector: 'sqlserver',
      connector_label: 'SQL Server',
      endpoint: 'dwh-retail',
      mode: 'federated',
      status: 'needs_fix',
      status_reason:
        'Không đăng nhập được từ 02:10. Thường do mật khẩu tài khoản kỹ thuật đã hết hạn.',
      status_label: 'Cần sửa · đăng nhập lỗi',
      last_refreshed_at: at(2, 10),
      late_by_minutes: 8 * 60,
      data_spaces: [RETAIL],
    }),
    row({
      id: 'src-gl',
      name: 'Sổ cái GL',
      connector: 'oracle',
      connector_label: 'Oracle',
      endpoint: 'gl-db',
      refresh_text: 'hằng ngày',
      last_refreshed_at: at(5, 30),
      data_spaces: [FINANCE],
    }),
    row({
      id: 'src-card',
      name: 'Thẻ tín dụng',
      connector: 'rest',
      connector_label: 'API',
      endpoint: 'card-api',
      refresh_text: '2 giờ',
      status_label: 'Đổi cấu trúc · chờ duyệt',
      schema_drift: true,
      status_reason: 'Nguồn thêm trường mới; 1.402 bản ghi đang chờ bạn xem trước khi nạp.',
      last_refreshed_at: at(8, 0),
      held_records: 1402,
      data_spaces: [RISK],
    }),
    row({
      id: 'src-edw',
      name: 'Kho dữ liệu doanh nghiệp',
      connector: 'postgresql',
      connector_label: 'PostgreSQL',
      endpoint: 'edw',
      mode: 'federated',
      data_spaces: [RISK, RETAIL, FINANCE, OPERATIONS],
    }),
    row({
      id: 'src-txn-events',
      name: 'Sự kiện giao dịch',
      connector: 'kafka',
      connector_label: 'Kafka',
      endpoint: 'txn-events',
      refresh_text: 'liên tục',
      status_label: 'Đang đồng bộ',
      last_refreshed_at: at(10, 42),
      lag_seconds: 3,
      data_spaces: [OPERATIONS],
    }),
    row({
      id: 'src-credit-files',
      name: 'Hồ sơ tín dụng',
      connector: 'folder',
      connector_label: 'Tệp',
      endpoint: 'thư mục quét',
      refresh_text: 'tệp mới',
      status: 'testing',
      status_reason: '312 tệp đang đọc',
      status_label: 'Đang đọc 312 tệp',
      last_refreshed_at: at(10, 40),
      data_spaces: [RISK],
    }),
    row({
      id: 'src-callcenter',
      name: 'Tổng đài',
      connector: 'recording',
      connector_label: 'Ghi âm',
      endpoint: 'pbx-archive',
      refresh_text: 'hằng ngày',
      last_refreshed_at: at(6, 0),
      data_spaces: [OPERATIONS],
    }),
    row({
      id: 'src-mailbox',
      name: 'Hộp thư nghiệp vụ',
      connector: 'email',
      connector_label: 'Email',
      endpoint: 'nghiepvu@noibo',
      refresh_text: 'liên tục',
      status_label: 'Đang đồng bộ',
      last_refreshed_at: minutesAgo(2),
      lag_seconds: 40,
      data_spaces: [RETAIL],
    }),
    row({
      id: 'src-fx',
      name: 'Tỷ giá NHNN',
      connector: 'rest',
      connector_label: 'API',
      endpoint: 'sbv-fx',
      refresh_text: 'hằng ngày',
      last_refreshed_at: at(8, 30),
      data_spaces: [FINANCE],
    }),
    row({
      id: 'src-branches',
      name: 'Danh mục chi nhánh',
      connector: 'postgresql',
      connector_label: 'PostgreSQL',
      endpoint: 'branch-db',
      mode: 'federated',
      data_spaces: [RETAIL, OPERATIONS],
    }),
    row({
      id: 'src-crm',
      name: 'CRM',
      connector: 'sqlserver',
      connector_label: 'SQL Server',
      endpoint: 'crm-db',
      refresh_text: 'hằng ngày',
      last_refreshed_at: at(4, 15),
      data_spaces: [RETAIL],
    }),
  ];
}

function tablesFixture(): Record<string, SourceTable[]> {
  const t = (
    id: string,
    name: string,
    description: string,
    rows: number,
    cols: number,
  ): SourceTable => ({
    id,
    name,
    description,
    row_count: rows,
    column_count: cols,
    refreshed_at: at(10, 41),
  });
  return {
    'src-core': [
      t('tbl-ld-loans', 'LD_LOANS', 'Khoản vay', 1_284_562, 24),
      t('tbl-cif', 'CIF_CUSTOMERS', 'Khách hàng', 612_408, 41),
      t('tbl-collateral', 'COLLATERAL', 'Tài sản bảo đảm', 402_115, 18),
    ],
    'src-gl': [t('tbl-gl-balances', 'GL_BALANCES', 'Số dư sổ cái theo ngày', 412_000, 12)],
  };
}

const LD_LOANS_COLUMNS: ColumnProfile[] = [
  {
    name: 'loan_id',
    data_type: 'VARCHAR2(20)',
    null_ratio: 0,
    distinct: 'unique',
    histogram: null,
    histogram_note: 'duy nhất',
    note: 'Khoá chính',
    warn: false,
  },
  {
    name: 'customer_id',
    data_type: 'VARCHAR2(16)',
    null_ratio: 0,
    distinct: 612_408,
    histogram: null,
    histogram_note: 'duy nhất',
    note: 'Nối Khách hàng',
    warn: false,
  },
  {
    name: 'outstanding_amt',
    data_type: 'NUMBER(18,2)',
    null_ratio: 0,
    distinct: null,
    histogram: [0.17, 0.44, 0.78, 1, 0.67, 0.39, 0.22, 0.11, 0.06, 0.06],
    histogram_note: null,
    note: '0 – 48,2 tỷ · trung vị 412 triệu',
    warn: false,
  },
  {
    name: 'debt_group',
    data_type: 'NUMBER(1)',
    null_ratio: 0.002,
    distinct: 5,
    histogram: [0.89, 0.17, 0.11, 0.06, 0.06],
    histogram_note: null,
    note: 'Nhóm 1: 93% · nhóm 3–5: 2,1%',
    warn: false,
  },
  {
    name: 'due_date',
    data_type: 'DATE',
    null_ratio: 0,
    distinct: 3612,
    histogram: [0.17, 0.44, 0.78, 1, 0.67, 0.39, 0.22, 0.11, 0.06, 0.06],
    histogram_note: null,
    note: '2019 – 2041',
    warn: false,
  },
  {
    name: 'branch_code',
    data_type: 'VARCHAR2(6)',
    null_ratio: 0,
    distinct: 184,
    histogram: [0.89, 0.17, 0.11, 0.06, 0.06],
    histogram_note: null,
    note: '184 chi nhánh',
    warn: false,
  },
  {
    name: 'id_number',
    data_type: 'VARCHAR2(12)',
    null_ratio: 0.014,
    distinct: null,
    histogram: null,
    histogram_note: 'che',
    note: 'CCCD · đã che, không lập hồ sơ',
    warn: false,
  },
  {
    name: 'restructure_flag',
    data_type: 'CHAR(1)',
    null_ratio: 0.62,
    distinct: 2,
    histogram: null,
    histogram_note: null,
    note: '62% trống: nguồn chỉ ghi khi có cơ cấu',
    warn: true,
  },
];

function profileOf(table: SourceTable, source: SourceRow): TableProfile {
  const isLoans = table.id === 'tbl-ld-loans';
  return {
    id: table.id,
    name: table.name,
    source: { id: source.id, name: source.name },
    row_count: table.row_count ?? 0,
    column_count: table.column_count ?? 0,
    refreshed_at: table.refreshed_at,
    profiled_text: '03:00 Chủ nhật trên mẫu 1%',
    // Only LD_LOANS has a profile in the design; the other tables read as not profiled yet.
    columns: isLoans ? LD_LOANS_COLUMNS : [],
    review: isLoans
      ? {
          agent_name: 'Agent Chuẩn bị dữ liệu',
          remarks: [
            'Bảng khớp thực thể Khoản vay của gói Ngân hàng; 22/24 cột đã ánh xạ.',
            'restructure_flag trống 62% là bình thường; nên coi trống là “không cơ cấu”.',
            'id_number là dữ liệu cá nhân, đã che; không dùng làm khoá nối.',
          ],
          footnote: 'UC-04-02 · mẫu dữ liệu luôn đã che; hồ sơ không tính trên cột dữ liệu cá nhân',
        }
      : null,
  };
}

const DB_PREP = [
  'Tài khoản chỉ có quyền SELECT trên các schema cần dùng',
  'Mở cổng từ máy chủ Data Agent tới {host}:{port}',
  'Với chế độ nạp về liên tục: bật supplemental logging cho CDC',
];

function connectorsFixture(): Connector[] {
  const c = (
    code: string,
    name: string,
    category: Connector['category'],
    extra: Partial<Connector> = {},
  ): Connector => ({
    code,
    name,
    category,
    subtitle: null,
    connected_count: 0,
    badge: null,
    form: null,
    prep_notes: [],
    sample_sql: null,
    ...extra,
  });
  const db = (label: string, port: number): Partial<Connector> => ({
    form: { database_label: label, default_port: port },
    prep_notes: DB_PREP,
    sample_sql:
      'CREATE USER {user} IDENTIFIED BY ***;\nGRANT CREATE SESSION TO {user};\nGRANT SELECT ANY TABLE ON SCHEMA card TO {user};',
  });
  return [
    c('oracle', 'Oracle', 'database', db('Service name', 1521)),
    c('postgresql', 'PostgreSQL', 'database', db('Tên CSDL', 5432)),
    c('sqlserver', 'SQL Server', 'database', db('Tên CSDL', 1433)),
    c('mysql', 'MySQL', 'database', db('Tên CSDL', 3306)),
    c('snowflake', 'Snowflake', 'warehouse'),
    c('bigquery', 'BigQuery', 'warehouse'),
    c('databricks', 'Databricks', 'warehouse'),
    c('clickhouse', 'ClickHouse', 'warehouse', { badge: 'new' }),
    c('rest', 'REST API', 'app'),
    c('kafka', 'Kafka (CDC)', 'app'),
    c('csv', 'CSV / Excel', 'file', { badge: 'upload' }),
    c('sharepoint', 'SharePoint', 'document'),
    c('folder', 'Thư mục quét', 'document'),
    c('email', 'Hộp thư email', 'document'),
    c('recording', 'Ghi âm tổng đài', 'document'),
    c('custom', 'Connector tuỳ chỉnh', 'app', {
      subtitle: 'Bằng SDK hoặc MCP',
      badge: 'engineering',
    }),
  ];
}

const SECRET_REFS: SecretRef[] = [
  { ref: 'openbao://db/card-ro', label: 'Lưu trong kho bí mật · openbao://db/card-ro' },
  { ref: 'openbao://db/core-ro', label: 'Lưu trong kho bí mật · openbao://db/core-ro' },
  { ref: 'openbao://db/gl-ro', label: 'Lưu trong kho bí mật · openbao://db/gl-ro' },
];

function testResult(source: NewSource, passReadOnly: boolean): ConnectionTest {
  const { host, port, username } = source.config;
  const checks: ConnectionTest['checks'] = [
    { key: 'network', title: `Kết nối mạng tới ${host}:${port}`, ok: true, detail: '38 ms' },
    { key: 'login', title: `Đăng nhập bằng ${username}`, ok: true, detail: 'thành công' },
    {
      key: 'catalog',
      title: 'Đọc được danh mục bảng',
      ok: true,
      detail: '46 bảng trong schema CARD',
    },
    passReadOnly
      ? { key: 'read_only', title: 'Tài khoản không có quyền ghi', ok: true, detail: 'chỉ đọc' }
      : {
          key: 'read_only',
          title: 'Tài khoản không có quyền ghi',
          ok: false,
          detail: `${username} có quyền INSERT, UPDATE trên CARD.CARD_LIMITS`,
        },
    { key: 'version', title: 'Phiên bản và mã ký tự', ok: true, detail: 'Oracle 19c · AL32UTF8' },
  ];
  return {
    checked_at: new Date().toISOString(),
    checks,
    fix: passReadOnly
      ? null
      : {
          reason:
            'Data Agent chỉ kết nối bằng tài khoản không ghi được vào nguồn, để agent không bao giờ sửa dữ liệu gốc. Nhờ đội quản trị CSDL thu hồi quyền ghi rồi bấm Kiểm tra lại.',
          sql: `REVOKE INSERT, UPDATE ON CARD.CARD_LIMITS FROM ${username};`,
        },
    schema: 'CARD',
    table_count: 46,
    tables: [
      {
        name: 'card_statements',
        description: 'Sao kê thẻ theo kỳ',
        personal_data: null,
        estimated_rows: 41_000_000,
      },
      {
        name: 'card_accounts',
        description: 'Tài khoản thẻ',
        personal_data: 'Số thẻ · che',
        estimated_rows: 2_000_000,
      },
      {
        name: 'card_transactions',
        description: 'Giao dịch thẻ',
        personal_data: null,
        estimated_rows: 150_000_000,
      },
      {
        name: 'card_limits',
        description: 'Hạn mức thẻ',
        personal_data: null,
        estimated_rows: 2_000_000,
      },
      {
        name: 'card_products',
        description: 'Sản phẩm thẻ',
        personal_data: null,
        estimated_rows: 120,
      },
    ],
    scan_minutes: 4,
    initial_load_hours: 3,
  };
}

function jobsFixture(): IngestionJob[] {
  const repeat = <T>(value: T, n: number): T[] => Array.from({ length: n }, () => value);
  return [
    {
      id: 'job-ld-loans',
      table_name: 'ld_loans',
      source_name: 'Core banking',
      method: 'cdc',
      hours: repeat('ok', 24),
      lag_seconds: 12,
      late: false,
      rows_today: 1_280_000,
      status: 'running',
      status_reason: null,
    },
    {
      id: 'job-gl-balances',
      table_name: 'gl_balances',
      source_name: 'Sổ cái GL',
      method: 'schedule',
      hours: [...repeat<'ok'>('ok', 23), 'none'],
      lag_seconds: null,
      late: false,
      rows_today: 412_000,
      status: 'on_time',
      status_reason: null,
    },
    {
      id: 'job-card-transactions',
      table_name: 'card_transactions',
      source_name: 'Thẻ tín dụng',
      method: 'incremental',
      hours: [...repeat<'ok'>('ok', 16), ...repeat<'none'>('none', 8)],
      lag_seconds: 6 * 3600,
      late: true,
      rows_today: 0,
      status: 'paused',
      status_reason: 'cấu trúc đổi',
    },
    {
      id: 'job-retail-sales',
      table_name: 'retail_sales',
      source_name: 'DWH bán lẻ',
      method: 'schedule',
      hours: [...repeat<'ok'>('ok', 8), ...repeat<'error'>('error', 16)],
      lag_seconds: 8 * 3600,
      late: true,
      rows_today: 0,
      status: 'error',
      status_reason: 'đăng nhập',
    },
    {
      id: 'job-txn-events',
      table_name: 'txn_events',
      source_name: 'Sự kiện giao dịch',
      method: 'continuous',
      hours: repeat('ok', 24),
      lag_seconds: 3,
      late: false,
      rows_today: 9_400_000,
      status: 'running',
      status_reason: null,
    },
    {
      id: 'job-branch-targets',
      table_name: 'branch_targets',
      source_name: 'Tải tệp',
      method: 'new_files',
      hours: [...repeat<'none'>('none', 10), 'ok', ...repeat<'none'>('none', 13)],
      lag_seconds: null,
      late: false,
      rows_today: 184,
      status: 'on_time',
      status_reason: null,
    },
  ];
}

function uploadAnalysis(fileName: string): UploadAnalysis {
  const stem = fileName
    .replace(/\.[^.]+$/, '')
    .replace(/_Q\d$/i, '')
    .toLowerCase();
  const columns: UploadColumn[] = [
    {
      header: 'Mã CN',
      name: 'branch_code',
      type: 'string',
      unit: null,
      example: 'HPG01, QNH02',
      personal: false,
      issue: null,
    },
    {
      header: 'Tên chi nhánh',
      name: 'branch_name',
      type: 'string',
      unit: null,
      example: 'Hải Phòng',
      personal: false,
      issue: null,
    },
    {
      header: 'Chỉ tiêu dư nợ (tỷ)',
      name: 'loan_target',
      type: 'number',
      unit: 'tỷ đồng',
      example: '1.250,5',
      personal: false,
      issue: null,
    },
    {
      header: 'Chỉ tiêu nợ xấu (%)',
      name: 'npl_target',
      type: 'percent',
      unit: '%',
      example: '1,8%',
      personal: false,
      issue: null,
    },
    {
      header: 'Ngày hiệu lực',
      name: 'effective_date',
      type: 'date',
      unit: null,
      example: '01/10/2026',
      personal: false,
      issue: '3 dòng sai',
    },
    {
      header: 'GĐ chi nhánh',
      name: 'manager_name',
      type: 'string',
      unit: null,
      example: 'Nguyễn V… (đã che)',
      personal: true,
      issue: null,
    },
  ];
  return {
    id: `upload-${Math.random().toString(36).slice(2, 8)}`,
    file_name: fileName,
    sheet: 'Chỉ tiêu',
    row_count: 184,
    header_row: 3,
    columns,
    issue_note:
      '3 dòng có ngày dạng “1/10/26”: agent đọc là 01/10/2026. Bấm vào dòng vàng để xem và sửa từng ô.',
    target: {
      table: `upload.${stem}`,
      is_new: true,
      data_space_name: 'Kinh doanh bán lẻ',
      on_reupload: 'Thay toàn bộ, giữ bản cũ 30 ngày',
      expires: null,
    },
  };
}

function collectionsFixture(): Collection[] {
  const c = (
    id: string,
    name: string,
    icon: Collection['icon'],
    content: string,
    extraction: string,
    status: Collection['status'],
  ): Collection => ({
    id,
    name,
    icon,
    content_text: content,
    extraction_text: extraction,
    status,
    archived: status === null,
  });
  return [
    c(
      'col-credit-files',
      'Hồ sơ tín dụng',
      'file',
      'PDF scan, ảnh · 18.400 tệp',
      '14 trường · 96% đã trích · liên kết khoản vay',
      { tone: 'success', text: 'Mới 06:00' },
    ),
    c(
      'col-contracts',
      'Hợp đồng & phụ lục',
      'file',
      'PDF, Word · 6.210 tệp',
      '22 trường · 91% · liên kết khách hàng',
      { tone: 'success', text: 'Mới 05:40' },
    ),
    c(
      'col-regulation',
      'Văn bản pháp quy',
      'file',
      'PDF, HTML · 412 văn bản',
      'điều khoản · hiệu lực · liên kết chỉ số',
      { tone: 'success', text: 'Mới 08:12' },
    ),
    c(
      'col-complaints',
      'Email khiếu nại',
      'mail',
      'Email, tệp đính kèm · 12.900 thư',
      'phân loại · cảm xúc · liên kết khách hàng',
      { tone: 'warning', text: '2 lô chờ duyệt PII' },
    ),
    c(
      'col-calls',
      'Ghi âm tổng đài',
      'phone',
      'Âm thanh → văn bản · 3.100 giờ',
      'ý định · cam kết trả nợ · liên kết khoản vay',
      { tone: 'info', text: 'Đang nạp 312' },
    ),
    c(
      'col-collateral',
      'Ảnh tài sản bảo đảm',
      'file',
      'Ảnh, biên bản · 7.300 ảnh',
      'loại TS · địa chỉ · liên kết hợp đồng',
      { tone: 'error', text: '4 lỗi nhận dạng' },
    ),
    c(
      'col-committee',
      'Biên bản hội đồng tín dụng',
      'file',
      'Word, PDF · 840 tệp',
      'quyết định · điều kiện · liên kết khoản vay',
      { tone: 'success', text: 'Mới hôm qua' },
    ),
    c(
      'col-old-reports',
      'Báo cáo nội bộ cũ',
      'file',
      'PDF, Excel · 3.900 tệp',
      'bảng · số liệu · —',
      null,
    ),
    c(
      'col-product-docs',
      'Tài liệu sản phẩm',
      'file',
      'Wiki, PDF · 650 trang',
      'điều kiện sản phẩm · liên kết sản phẩm',
      { tone: 'success', text: 'Mới 02/10' },
    ),
  ];
}

const SUMMARY: CollectionsSummary = {
  ingesting_files: 312,
  recognition_errors: 4,
  pii_batches_pending: 2,
  index_coverage: 0.992,
};

const STEP_NAMES = [
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

function documentsFixture(): Record<string, DocumentDetail[]> {
  const doc = (d: CollectionDocument, detail: Partial<DocumentDetail> = {}): DocumentDetail => ({
    origin_text: 'Đồng bộ từ SharePoint “Pháp chế/Tín dụng”',
    processing: d.steps.map((state, i) => ({
      state:
        state === 'done'
          ? 'done'
          : state === 'warn'
            ? 'pendingApproval'
            : state === 'error'
              ? 'error'
              : 'waiting',
      title: STEP_NAMES[i],
      detail:
        state === 'done'
          ? 'xong'
          : state === 'warn'
            ? 'chờ người xem lại'
            : state === 'error'
              ? 'lỗi'
              : 'chưa chạy',
      duration: null,
    })),
    excerpt: null,
    effective_text: null,
    impact_text: null,
    ...d,
    ...detail,
  });
  return {
    'col-regulation': [
      doc(
        {
          id: 'doc-tt31',
          title: 'TT 31/2024/TT-NHNN · phân loại nợ',
          type_text: 'PDF · 48 trang',
          steps: steps(8),
          validity: { text: 'Đang hiệu lực', warn: false },
          status: { tone: 'success', text: 'Sẵn sàng' },
        },
        {
          origin_text: 'Nạp tự động từ cổng văn bản · 08:12 hôm nay',
          processing: [
            {
              state: 'done',
              title: 'Đọc và nhận dạng',
              detail: '32 trang · độ tin 0,98',
              duration: '0:41',
            },
            {
              state: 'done',
              title: 'Trích điều khoản',
              detail: '14 điều khoản mới hoặc sửa',
              duration: '1:02',
            },
            {
              state: 'done',
              title: 'Liên kết chỉ số',
              detail: 'NPL_RATIO, COST_OF_RISK',
              duration: '0:12',
            },
            {
              state: 'pendingApproval',
              title: 'Đề xuất NPL_RATIO v5',
              detail: 'Chờ chủ sở hữu chỉ số',
              duration: null,
            },
          ],
          excerpt: {
            label: 'Điều 10, khoản 2 · sửa đổi',
            text: '“Khoản nợ được cơ cấu lại thời hạn trả nợ lần thứ hai được phân loại tối thiểu vào nhóm 3…”',
          },
          effective_text: '01/01/2027 · thay thế bản 2024 một phần',
          impact_text: '1 chỉ số · 4 báo cáo · 3 agent',
        },
      ),
      doc({
        id: 'doc-qd1120',
        title: 'QĐ 1120/2023 · quy trình tín dụng bán lẻ',
        type_text: 'Word · 22 trang',
        steps: steps(8),
        validity: { text: 'Bị thay thế 01/09', warn: true },
        status: { tone: 'idle', text: 'Sẵn sàng · không dùng trả lời hiện tại' },
      }),
      doc({
        id: 'doc-hdtd',
        title: 'Biên bản họp HĐTD 05/10',
        type_text: 'PDF scan · 6 trang',
        steps: steps(3, ['warn']),
        validity: null,
        status: { tone: 'warning', text: 'Chờ xem lại · OCR kém trang 4' },
      }),
      doc({
        id: 'doc-contract',
        title: 'Hợp đồng mẫu vay tiêu dùng',
        type_text: 'PDF · 12 trang',
        steps: steps(5),
        validity: { text: 'Đang hiệu lực', warn: false },
        status: { tone: 'info', text: 'Đang xử lý · bước 5' },
      }),
      doc({
        id: 'doc-email',
        title: 'Email trao đổi pháp chế 02/10',
        type_text: 'Email · 3 thư',
        steps: steps(4, ['error', 'error']),
        validity: null,
        status: { tone: 'error', text: 'Lỗi · không đọc được tệp đính kèm' },
      }),
    ],
  };
}

const COLLECTION_DETAIL: Record<string, Omit<CollectionDetail, keyof Collection>> = {
  'col-regulation': {
    document_count: 412,
    sync_text: 'đồng bộ từ SharePoint “Pháp chế/Tín dụng” mỗi giờ',
    data_space_name: 'Rủi ro tín dụng',
    pages_pending_review: 23,
  },
};

export function createSourcesHandlers() {
  let sources = sourceRowsFixture();
  const tables = tablesFixture();
  const configs: Record<string, NewSource> = {};
  const tests: Record<string, number> = {};
  const passed: Record<string, boolean> = {};
  let jobs = jobsFixture();
  const uploads: Record<string, UploadAnalysis> = {};
  const collections = collectionsFixture();
  const documents = documentsFixture();
  const connectors = connectorsFixture();

  const findTable = (id: string) => {
    for (const [sourceId, list] of Object.entries(tables)) {
      const table = list.find((t) => t.id === id);
      if (table) return { table, source: sources.find((s) => s.id === sourceId)! };
    }
    return null;
  };

  return [
    http.get('*/v1/sources', () => page(sources)),
    http.get('*/v1/sources/:id', ({ params }) => {
      const source = sources.find((s) => s.id === params.id);
      return source
        ? HttpResponse.json({ ...source, tables: tables[source.id] ?? [] })
        : problem(404, 'not_found', 'Không tìm thấy nguồn.');
    }),
    http.post('*/v1/sources', async ({ request }) => {
      const body = (await request.json()) as NewSource;
      if (!body.name?.trim() || !body.config?.host?.trim() || !body.config?.username?.trim())
        return problem(422, 'validation_failed', 'Cần tên, máy chủ và tài khoản.');
      const connector = connectors.find((c) => c.code === body.connector);
      const id = `src-new-${Object.keys(configs).length + 1}`;
      const created = row({
        id,
        name: body.name,
        connector: body.connector,
        connector_label: connector?.name ?? body.connector,
        endpoint: body.config.host,
        mode: body.mode,
        status: 'draft',
        status_label: 'Bản nháp',
        last_checked_at: null,
        refresh_text: body.mode === 'ingest' ? 'CDC' : null,
      });
      configs[id] = body;
      sources = [created, ...sources];
      return HttpResponse.json(created, { status: 201 });
    }),
    http.patch('*/v1/sources/:id', async ({ params }) => {
      // The mock keeps the first config; the flow only patches when the user goes back.
      const source = sources.find((s) => s.id === params.id);
      return source
        ? HttpResponse.json(source)
        : problem(404, 'not_found', 'Không tìm thấy nguồn.');
    }),
    http.post('*/v1/sources/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      const source = sources.find((s) => s.id === id);
      if (!source) return problem(404, 'not_found', 'Không tìm thấy nguồn.');
      if (verb === 'test') {
        const config = configs[id] ?? {
          name: source.name,
          connector: source.connector,
          mode: source.mode,
          config: {
            host: source.endpoint,
            port: 1521,
            database: '',
            username: 'agent_ro',
            secret_ref: '',
          },
        };
        tests[id] = (tests[id] ?? 0) + 1;
        const result = testResult(config, tests[id] > 1 || !configs[id]);
        passed[id] = result.checks.every((c) => c.ok);
        return HttpResponse.json(result);
      }
      if (verb === 'activate') {
        if (!passed[id])
          return problem(409, 'invalid_state', 'Tài khoản còn quyền ghi: không lưu được kết nối.');
        const body = (await request.json()) as ActivateSource;
        if (!body.tables?.length)
          return problem(422, 'validation_failed', 'Chọn ít nhất một bảng.');
        tables[id] = body.tables.map((t, i) => ({
          id: `${id}-t${i}`,
          name: t.name.toUpperCase(),
          description: null,
          row_count: null,
          column_count: null,
          refreshed_at: null,
        }));
        const activated: SourceRow = {
          ...source,
          status: 'testing',
          status_label: 'Đang quét cấu trúc',
          last_checked_at: new Date().toISOString(),
        };
        sources = sources.map((s) => (s.id === id ? activated : s));
        return HttpResponse.json(activated);
      }
      return problem(404, 'not_found', 'Không có thao tác này.');
    }),

    http.get('*/v1/datasets/:id/profile', ({ params }) => {
      const found = findTable(String(params.id));
      return found
        ? HttpResponse.json(profileOf(found.table, found.source))
        : problem(404, 'not_found', 'Không tìm thấy bảng.');
    }),
    http.post('*/v1/datasets/:action', ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      if (verb !== 'profile' || !findTable(id))
        return problem(404, 'not_found', 'Không tìm thấy bảng.');
      return HttpResponse.json({ status: 'queued' }, { status: 202 });
    }),

    http.get('*/v1/connectors', () =>
      page(
        connectors.map((c) => ({
          ...c,
          connected_count: sources.filter((s) => s.connector === c.code && s.status !== 'draft')
            .length,
        })),
      ),
    ),
    http.get('*/v1/secret-refs', () => page(SECRET_REFS)),

    http.get('*/v1/ingestion-jobs', () => page(jobs)),
    http.post('*/v1/ingestion-jobs/:action', ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      const job = jobs.find((j) => j.id === id);
      if (!job) return problem(404, 'not_found', 'Không tìm thấy job.');
      const next: Partial<Record<string, IngestionJob['status']>> = {
        pause: 'paused',
        resume: 'running',
        run: 'running',
      };
      const status = next[verb];
      if (!status) return problem(404, 'not_found', 'Không có thao tác này.');
      if (verb === 'pause' && job.status === 'paused')
        return problem(409, 'invalid_state', 'Job đã tạm dừng.');
      if (verb === 'resume' && job.status !== 'paused')
        return problem(409, 'invalid_state', 'Job không ở trạng thái tạm dừng.');
      const updated: IngestionJob = {
        ...job,
        status,
        status_reason: status === 'paused' ? 'bạn tạm dừng' : null,
      };
      jobs = jobs.map((j) => (j.id === id ? updated : j));
      return HttpResponse.json(updated);
    }),

    http.post('*/v1/uploads', async ({ request }) => {
      const body = (await request.json()) as { file_name: string; size_bytes: number };
      if (!/\.(xlsx|xls|csv)$/i.test(body.file_name ?? ''))
        return problem(415, 'unsupported_media_type', 'Chỉ nhận tệp CSV hoặc Excel.');
      const analysis = uploadAnalysis(body.file_name);
      uploads[analysis.id] = analysis;
      return HttpResponse.json(analysis, { status: 201 });
    }),
    http.post('*/v1/uploads/:action', ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      const upload = uploads[id];
      if (verb !== 'commit' || !upload)
        return problem(404, 'not_found', 'Không tìm thấy tệp đã tải.');
      return HttpResponse.json({ table: upload.target.table });
    }),

    http.get('*/v1/collections', () => page(collections)),
    http.get('*/v1/document-pipeline', () => HttpResponse.json(SUMMARY)),
    http.get('*/v1/collections/:id', ({ params }) => {
      const collection = collections.find((c) => c.id === params.id);
      if (!collection) return problem(404, 'not_found', 'Không tìm thấy bộ sưu tập.');
      const detail = COLLECTION_DETAIL[collection.id] ?? {
        document_count: (documents[collection.id] ?? []).length,
        sync_text: 'tải lên thủ công',
        data_space_name: 'Rủi ro tín dụng',
        pages_pending_review: 0,
      };
      return HttpResponse.json({ ...collection, ...detail });
    }),
    http.get('*/v1/collections/:id/documents', ({ params }) => {
      const list = documents[String(params.id)] ?? [];
      return page(
        list.map(({ id, title, type_text, steps: s, validity, status }) => ({
          id,
          title,
          type_text,
          steps: s,
          validity,
          status,
        })),
      );
    }),
    http.post('*/v1/collections/:id/documents', async ({ params, request }) => {
      const id = String(params.id);
      if (!collections.some((c) => c.id === id))
        return problem(404, 'not_found', 'Không tìm thấy bộ sưu tập.');
      const body = (await request.json()) as { file_name: string };
      const created: DocumentDetail = {
        id: `doc-${Math.random().toString(36).slice(2, 8)}`,
        title: body.file_name,
        type_text: body.file_name.split('.').pop()?.toUpperCase() ?? 'Tệp',
        steps: steps(1),
        validity: null,
        status: { tone: 'info', text: 'Đang xử lý · bước 2' },
        origin_text: 'Tải lên thủ công · vừa xong',
        processing: [
          { state: 'running', title: 'Trích xuất', detail: 'đang đọc tệp', duration: null },
        ],
        excerpt: null,
        effective_text: null,
        impact_text: null,
      };
      documents[id] = [created, ...(documents[id] ?? [])];
      return HttpResponse.json(created, { status: 201 });
    }),
    http.get('*/v1/documents/:id', ({ params }) => {
      const doc = Object.values(documents)
        .flat()
        .find((d) => d.id === params.id);
      return doc ? HttpResponse.json(doc) : problem(404, 'not_found', 'Không tìm thấy tài liệu.');
    }),
  ];
}
