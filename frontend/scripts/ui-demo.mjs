// UI demo/screenshot harness — runs the React frontend WITHOUT Keycloak or any backend.
//
//   npm run dev                       # in another terminal (VITE_API_URL=http://localhost:3000 recommended)
//   node frontend/scripts/ui-demo.mjs          # headless: writes frontend/scripts/ui-demo-shots/*.png
//   node frontend/scripts/ui-demo.mjs --open   # headed: opens Chromium with the mocks, leaves it for you to click around
//   node frontend/scripts/ui-demo.mjs --open --role executive-viewer --hash '#ops/REVIEWS'
//
// How it works — no product code is touched:
//  - src/lib/keycloak.ts reuses window.__kc when present, so an already-initialised fake instance is
//    injected before the app boots (AuthProvider takes its fast path; the approval gate fails open).
//  - every /api/** call is answered by page.route with payloads shaped exactly like the backend frames
//    (orchestrator routers/stream.py, workflow_runtime conversation_runner.py, routers/reviews.py).
// Use it to check layout and interaction; it says nothing about the backend. For that, run the stack
// (deploy/docker/docker-compose.kb-e2e.yml) and point VITE_API_URL at Kong.
import { mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const HERE = dirname(fileURLToPath(import.meta.url));
const require = createRequire(resolve(HERE, '..', 'package.json'));
const { chromium } = require('playwright');

const args = process.argv.slice(2);
const flag = (name) => { const i = args.indexOf(name); return i === -1 ? null : (args[i + 1] ?? true); };
const OPEN = args.includes('--open');
const ROLE = flag('--role') ?? 'executive-viewer';
const HASH = flag('--hash') ?? '';
const ASK_VARIANT = flag('--answer') ?? 'ok'; // ok | ask (clarification)

const OUT = resolve(HERE, 'ui-demo-shots');
mkdirSync(OUT, { recursive: true });
const BASE = process.env.UI_DEMO_BASE ?? 'http://localhost:3000';

const sse = (frames) => frames.map(f => `data: ${typeof f === 'string' && f.startsWith('[') ? f : JSON.stringify(f)}\n\n`).join('');

const ROWS = [
  { vung: 'Miền Bắc', doanh_thu_thuan: '612400000.00', so_don: 1842 },
  { vung: 'Miền Nam', doanh_thu_thuan: '480120000.00', so_don: 1503 },
  { vung: 'Miền Trung', doanh_thu_thuan: '192040000.00', so_don: 611 },
  { vung: 'Tây Nguyên', doanh_thu_thuan: '58900000.00', so_don: 190 },
];
const EVIDENCE_OK = {
  runs: [{
    id: 'sql_7f3a', kind: 'sql', db_name: 'dw_postgres', db_type: 'postgres',
    sql: "SELECT r.name AS vung,\n       SUM(f.amount - f.returns) AS doanh_thu_thuan,\n       COUNT(DISTINCT f.order_id) AS so_don\nFROM fact_sales f\nJOIN dim_region r ON r.id = f.region_id\nWHERE f.status = 'done'\n  AND f.order_date >= DATE '2026-06-01' AND f.order_date < DATE '2026-07-01'\nGROUP BY r.name\nORDER BY doanh_thu_thuan DESC",
    row_count: 4, generated_by: 'semantic_layer',
    metric: { metric: 'doanh_thu_thuan@3', matched_term: 'doanh thu thuần', definition: 'SUM(amount − returns) trên đơn đã hoàn tất', aggregation: 'sum', unit: 'VND', grain: 'month', dimensions: ['vùng'], filters: ["status = 'done'"], business_date_column: 'order_date', owner: 'Phòng Tài chính' },
    provenance: { status: 'fresh', as_of: '2026-06-30T23:05:00+07:00', age_seconds: 4200, classification: 'internal', owners: ['DW team'], stale_tables: [], unknown_tables: [], tables: [{ table: 'public.fact_sales', owner: 'DW team', data_as_of: '2026-06-30T23:05:00+07:00' }, { table: 'public.dim_region', owner: 'DW team', data_as_of: '2026-06-30T22:40:00+07:00' }] },
    aggregation: { findings: [], structurally_safe: true, aggregates: ['SUM', 'COUNT'], joins: 1 },
    truncation: null, columns: ['vung', 'doanh_thu_thuan', 'so_don'], rows: ROWS, rows_capped: false, citation_index: 1,
  }],
  clarifications: [], warnings: [],
};
const ANSWER_OK = [
  { __session_id: 'sess-demo-1' },
  ...('Doanh thu thuần tháng 6/2026 đạt **1.343.460.000 ₫** trên 4 vùng [1]. Miền Bắc dẫn đầu với 612,4 triệu ₫ (45,6%), Miền Nam 480,1 triệu ₫; Tây Nguyên thấp nhất với 58,9 triệu ₫.\n\nSố liệu tính theo chỉ số *doanh thu thuần* đã duyệt (v3), chỉ gồm đơn ở trạng thái hoàn tất, dữ liệu đến 23:05 ngày 30/06/2026.'
    .split(' ').map((w, i) => (i ? ' ' : '') + w)),
  { __evidence: EVIDENCE_OK },
  { __suggestions: ['So với tháng 5/2026', 'Tách theo sản phẩm', 'Chỉ xem Miền Bắc'] },
  { __chart: [{ chart_type: 'bar', title: 'Doanh thu thuần tháng 6/2026 theo vùng', x_key: 'vung', y_keys: ['doanh_thu_thuan'], data: ROWS.map(r => ({ ...r, doanh_thu_thuan: Number(r.doanh_thu_thuan) })) }] },
  { __citations: [{ index: 1, source: 'tool/sql · dw_postgres', type: 'db', score: null, snippet: '[{"vung":"Miền Bắc","doanh_thu_thuan":"612400000.00","so_don":1842},…]', provenance: EVIDENCE_OK.runs[0].provenance }] },
  '[DONE]',
];
const ANSWER_ASK = [
  { __session_id: 'sess-demo-2' },
  ...('Câu hỏi chưa nói khoảng thời gian, và “doanh thu” khớp hai chỉ số đã đăng ký cho ra con số khác nhau. Tôi chưa chạy truy vấn nào — bạn chọn giúp:'.split(' ').map((w, i) => (i ? ' ' : '') + w)),
  { __evidence: { runs: [], warnings: [], clarifications: [
    { reason: 'missing_time_range', text: 'Bạn muốn xem trong khoảng thời gian nào? Nếu không chọn, hệ thống sẽ tính trên TOÀN BỘ dữ liệu lịch sử — thường không phải điều bạn muốn.', options: ['this_month', 'last_month', 'this_quarter', 'this_year'] },
    { reason: 'ambiguous_metric', text: 'Câu hỏi có thể hiểu theo nhiều chỉ số khác nhau và các chỉ số này cho ra con số KHÁC nhau. Bạn muốn dùng chỉ số nào?', options: ['Doanh thu gộp', 'Doanh thu thuần'] },
  ] } },
  '[DONE]',
];
const AGENTS = [
  { id: 'agent-sales', name: 'Kinh doanh — Miền Bắc', description: 'DW bán hàng, 7 bảng, 6 chỉ số đã duyệt', routing_keywords: ['doanh thu', 'đơn hàng'] },
  { id: 'agent-finance', name: 'Tài chính', description: 'Sổ cái, công nợ, dòng tiền' },
  { id: 'agent-ops', name: 'Vận hành kho', description: 'Tồn kho, giao vận' },
];
const REVIEWS = [
  { conv_id: 'conv-9c1e', agent_id: 'agent-finance', query: 'Xuất danh sách công nợ quá hạn trên 90 ngày kèm số điện thoại khách hàng', waiting_secs: 754,
    payload: { title: 'Truy vấn chạm dữ liệu cá nhân (PII)', description: 'Kết quả chứa cột phone, email của 312 khách hàng. Chính sách tenant yêu cầu người có thẩm quyền duyệt trước khi trả về.', detail: { tables: ['crm.customers', 'ar.invoices'], pii_columns: ['phone', 'email'], rows: 312 } } },
  { conv_id: 'conv-2b77', agent_id: 'agent-sales', query: 'Gửi báo cáo doanh thu tuần cho toàn bộ danh sách email phòng kinh doanh', waiting_secs: 128,
    payload: { title: 'Hành động gửi email ra ngoài', description: 'Agent yêu cầu gọi tool send_email tới 42 địa chỉ. Cần duyệt vì đây là hành động không hoàn tác được.', detail: { tool: 'send_email', recipients: 42 } } },
];

const traceObs = [
  { id: 'o1', name: 'single-agent', type: 'SPAN', startTime: '2026-06-30T07:02:00.000Z', endTime: '2026-06-30T07:02:03.200Z', metadata: {} },
  { id: 'o2', name: 'planner', type: 'SPAN', parentObservationId: 'o1', startTime: '2026-06-30T07:02:00.100Z', endTime: '2026-06-30T07:02:00.900Z', metadata: {} },
];

async function setup(page, { stream, role }) {
  const pickStream = () => (typeof stream === 'function' ? stream() : stream);
  await page.addInitScript(({ role }) => {
    window.__kc = {
      didInitialize: true, authenticated: true, token: 'demo-token',
      tokenParsed: { sub: 'u-demo', name: 'Nguyễn Thị Lan', preferred_username: 'lan', email: 'lan@bank.vn', realm_access: { roles: [role] } },
      init: () => Promise.resolve(true), logout: () => {}, updateToken: () => Promise.resolve(false), onTokenExpired: null,
    };
  }, { role });
  // catch-all FIRST: Playwright gives precedence to the most recently registered route
  await page.route('**/api/**', r => r.fulfill({ status: 503, json: { detail: 'backend not running (screenshot harness)' } }));
  await page.route('**/api/orchestrate/stream', r => r.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body: sse(pickStream()) }));
  await page.route('**/api/catalog/agents', r => r.fulfill({ json: AGENTS }));
  await page.route('**/api/reviews/pending', r => r.fulfill({ json: REVIEWS }));
  await page.route('**/api/orchestrate/traces/**', r => r.fulfill({ json: { observations: traceObs, traceId: 'sess-demo-1' } }));
}

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1.5, locale: 'vi-VN' });

if (OPEN) {
  // Headed browser, alternating answers: 1st question → full answer, 2nd → clarification, 3rd → full, ...
  const browser = await chromium.launch({ headless: false });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: 'vi-VN' });
  const page = await ctx.newPage();
  let n = 0;
  await setup(page, { role: ROLE, stream: () => (ASK_VARIANT === 'ask' || (ASK_VARIANT === 'ok' && n++ % 2 === 1) ? ANSWER_ASK : ANSWER_OK) });
  await page.goto(`${BASE}/${HASH}`);
  console.log(`Opened ${BASE}/${HASH} as ${ROLE}. Close the browser window to exit.`);
  await new Promise(res => browser.on('disconnected', res));
  process.exit(0);
}

// 1. Ask — landing (executive role lands on #ask)
{
  const page = await ctx.newPage();
  await setup(page, { stream: ANSWER_OK, role: 'executive-viewer' });
  await page.goto(`${BASE}/`);
  await page.waitForSelector('text=Hỏi bằng tiếng Việt', { timeout: 30000 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/01-ask-landing.png` });

  // 2. Ask — a full answer with table, chart, evidence panel
  await page.fill('textarea[aria-label="Câu hỏi"]', 'Doanh thu thuần tháng 6 theo vùng');
  await page.keyboard.press('Enter');
  await page.waitForSelector('[data-testid="data-grid"]', { timeout: 15000 });
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${OUT}/02-ask-answer.png` });
  await page.screenshot({ path: `${OUT}/02b-ask-answer-full.png`, fullPage: true });
  await page.close();
}

// 3. Ask — clarification card
{
  const page = await ctx.newPage();
  await setup(page, { stream: ANSWER_ASK, role: 'executive-viewer' });
  await page.goto(`${BASE}/#ask`);
  await page.waitForSelector('textarea[aria-label="Câu hỏi"]', { timeout: 30000 });
  await page.fill('textarea[aria-label="Câu hỏi"]', 'Doanh thu theo vùng');
  await page.keyboard.press('Enter');
  await page.waitForSelector('[data-testid="clarification-card"]', { timeout: 15000 });
  await page.waitForTimeout(600);
  await page.screenshot({ path: `${OUT}/03-ask-clarification.png` });
  await page.close();
}

// 4. Review Center under Vận hành › Chờ duyệt
{
  const page = await ctx.newPage();
  await setup(page, { stream: ANSWER_OK, role: 'ai-engineer' });
  await page.goto(`${BASE}/#ops/REVIEWS`);
  await page.waitForSelector('text=Hàng đợi phê duyệt', { timeout: 30000 });
  await page.waitForSelector('text=ĐANG CHỜ', { timeout: 15000 });
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${OUT}/04-review-center.png` });
  await page.close();
}

// 5. Playground (dark) with the shared answer block + evidence strip
{
  const page = await ctx.newPage();
  await setup(page, { stream: ANSWER_OK, role: 'ai-engineer' });
  await page.goto(`${BASE}/#build/PLAYGROUND`);
  await page.waitForSelector('text=Playground', { timeout: 30000 });
  await page.click('text=Select an agent…');
  await page.click('text=Kinh doanh — Miền Bắc');
  await page.fill('textarea', 'Doanh thu thuần tháng 6 theo vùng');
  await page.keyboard.press('Enter');
  await page.waitForSelector('[data-testid="evidence-strip"]', { timeout: 15000 });
  await page.click('[data-testid="evidence-strip"] button:has-text("Xem SQL")');
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${OUT}/05-playground-dark.png` });
  await page.close();
}

// 6. Engineer landing → Xây dựng (nav overview)
{
  const page = await ctx.newPage();
  await setup(page, { stream: ANSWER_OK, role: 'ai-engineer' });
  await page.goto(`${BASE}/`);
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${OUT}/06-build-landing.png` });
  await page.close();
}

await browser.close();
console.log(`done → ${OUT}`);
