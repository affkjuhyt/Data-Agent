import { http, HttpResponse } from 'msw';
import type {
  SessionAgentDraft,
  SessionSkillDraft,
  SkillDefinition,
  SkillRun,
  SkillRunForm,
  SkillTestRun,
  SkillTrial,
} from '../lib/v1/skillAuthoring';

/*
 * P03 skill authoring and running. Data from "v3.1 · Kỹ năng · Trình soạn kỹ năng" (2089:7437),
 * "P03 · Tạo kỹ năng từ phiên" (2294:18171), "P03 · Kiểm định kỹ năng" (2294:18369),
 * "P03 · Chạy kỹ năng · Phân rã biến động" (2293:18186), "P03 · Tìm yếu tố bên ngoài"
 * (2293:18368) and "Biến phiên thành agent" (2070:3837). Skill ids match the library mock
 * (skills.ts): sk-variance, sk-external.
 */
const SESSION_ID = 'ws-npl-sept';
const FROM_SESSION_ID = 'sk-region-variance';
const minutesAgo = (m: number) => new Date(Date.now() - m * 60_000).toISOString();

function definitionFixture(): SkillDefinition {
  return {
    skill_id: 'sk-variance',
    name: 'Phân rã biến động',
    version: 4,
    status: 'draft',
    inputs: [
      { name: 'metric', description: 'Chỉ số trong semantic layer · bắt buộc' },
      { name: 'period_a, period_b', description: 'Hai kỳ cần so sánh' },
      { name: 'dimensions', description: 'Tối đa 3 chiều; mặc định theo chỉ số' },
    ],
    output_text: 'Bảng đóng góp · biểu đồ · kết luận có trích dẫn',
    steps: [
      { seq: 1, text: 'Lấy giá trị hai kỳ qua semantic layer', tool: 'metric.query' },
      { seq: 2, text: 'Phân rã theo từng chiều được chọn', tool: 'metric.query' },
      { seq: 3, text: 'Kiểm tra tổng đóng góp = chênh lệch (sai số < 0,01)', tool: 'check.sum' },
      { seq: 4, text: 'Chọn chiều giải thích nhiều nhất', tool: null },
      { seq: 5, text: 'Viết kết luận, gắn trích dẫn tới artifact', tool: 'artifact.save' },
    ],
    instructions:
      'Chỉ dùng chỉ số đã chứng nhận trừ khi người dùng chọn khác. Trình bày đóng góp theo thứ tự tuyệt đối giảm dần. Không suy luận nhân quả; dùng “đóng góp” thay vì “gây ra”. Nếu một chiều chiếm > 70% mức thay đổi, đề xuất phân rã sâu hơn chiều đó.',
    guardrails: [
      { text: 'Chỉ đọc dữ liệu · không có công cụ ghi', kind: 'check' },
      { text: 'Dừng và báo nếu tổng đóng góp lệch > 0,01', kind: 'check' },
      { text: 'Hỏi người khi chỉ số có nhiều định nghĩa', kind: 'check' },
      { text: 'Không chạy khi kỳ chưa có dữ liệu đầy đủ', kind: 'block' },
    ],
    owner: { id: 'u-yen', name: 'Đỗ Hải Yến' },
    scope_label: 'Toàn tổ chức',
    domain_text: 'Mọi domain · cần chỉ số trong semantic layer',
    used_by_text: '4 agent · 312 lần/30 ngày',
    quality_text: 'v3: 96% · v4: chưa chạy thử',
    test_count: 18,
    version_count: 4,
    quick_test: {
      params: { metric: 'NPL_RATIO@v4', period: '2026-08 → 2026-09', dims: 'region, product' },
      lines: ['metric=NPL_RATIO@v4', 'period: 2026-08 → 2026-09', 'dims: region, product'],
    },
  };
}

function trialFixture(): SkillTrial {
  return {
    version: 4,
    duration_text: '11 giây',
    steps: [
      { title: 'Lấy giá trị hai kỳ', detail: '2 truy vấn · 0,9 s', state: 'done' },
      { title: 'Phân rã theo vùng, sản phẩm', detail: '14 nhóm · 3,1 s', state: 'done' },
      { title: 'Kiểm tra tổng', detail: 'lệch 0,000 · đạt', state: 'done' },
      { title: 'Chọn chiều chính', detail: 'vùng · 74% mức thay đổi', state: 'done' },
      { title: 'Viết kết luận', detail: '3 trích dẫn', state: 'done' },
    ],
    bars: [
      { label: 'DH Bắc Bộ', value: 0.61, value_text: '+0,61', main: true },
      { label: 'ĐB sông Hồng', value: 0.21, value_text: '+0,21', main: false },
      { label: 'Bắc Trung Bộ', value: 0.14, value_text: '+0,14', main: false },
      { label: 'Đông Nam Bộ', value: 0.12, value_text: '+0,12', main: false },
    ],
    conclusion:
      'Mức tăng NPL đóng góp chủ yếu từ Duyên hải Bắc Bộ (74%). Đề xuất phân rã sâu hơn theo sản phẩm trong vùng này.',
  };
}

function skillDraftFixture(): SessionSkillDraft {
  return {
    session_id: SESSION_ID,
    session_title: 'Điều tra nợ xấu bán lẻ T9',
    cells: [
      {
        seq: 1,
        title: 'Lấy NPL_RATIO theo vùng, hai kỳ',
        param_text: 'NPL_RATIO → {chỉ số} · T8, T9 → {kỳ trước}, {kỳ này}',
        note: null,
        kept: true,
      },
      { seq: 2, title: 'Tìm vùng tăng mạnh nhất', param_text: null, note: null, kept: true },
      {
        seq: 3,
        title: 'Phân rã vùng đó theo sản phẩm',
        param_text: '“Duyên hải Bắc Bộ” → {vùng tăng mạnh nhất}',
        note: null,
        kept: true,
      },
      {
        seq: 4,
        title: 'Biểu đồ trang trí cho slide',
        param_text: null,
        note: 'bỏ: chỉ để trình bày',
        kept: false,
      },
      { seq: 5, title: 'Kiểm tra khoản cơ cấu trong kỳ', param_text: null, note: null, kept: true },
      {
        seq: 6,
        title: 'Ghi chú của Đỗ Hải Yến',
        param_text: null,
        note: 'bỏ: không phải bước tính',
        kept: false,
      },
      {
        seq: 7,
        title: 'Kết luận bằng một đoạn',
        param_text: null,
        note: 'giữ khuôn kết luận',
        kept: true,
      },
    ],
    name: 'Điều tra biến động theo vùng',
    description: 'Tìm vùng làm chỉ số thay đổi nhiều nhất và tách theo sản phẩm',
    params: [
      { name: 'chỉ số', type_label: 'Chỉ số đã chứng nhận', default_text: 'NPL_RATIO' },
      { name: 'kỳ trước', type_label: 'Kỳ', default_text: 'tháng trước' },
      { name: 'kỳ này', type_label: 'Kỳ', default_text: 'tháng này' },
    ],
    test_case_text:
      'Kết quả của phiên này được giữ làm ca kiểm thử đầu tiên: với T8→T9, vùng tăng mạnh nhất phải là DH Bắc Bộ.',
  };
}

function agentDraftFixture(): SessionAgentDraft {
  return {
    session_id: SESSION_ID,
    session_title: 'Điều tra nợ xấu bán lẻ T9',
    cells: [
      { seq: 1, label: 'Ô 1 · Mục tiêu (văn bản)', kept: false },
      { seq: 2, label: 'Ô 2 · Yêu cầu phân rã NPL theo vùng × sản phẩm', kept: true },
      { seq: 3, label: 'Ô 3 · SQL phân rã', kept: true },
      { seq: 4, label: 'Ô 4 · Biểu đồ đóng góp', kept: true },
      { seq: 5, label: 'Ô 5 · Kiểm tra số với báo cáo đã nộp', kept: true },
      { seq: 6, label: 'Ô 6 · Kết luận nháp', kept: false },
    ],
    params: [
      {
        key: 'period',
        label: 'Kỳ',
        value: 'Tuần trước (thay cho T8 → T9)',
        options: ['Tuần trước (thay cho T8 → T9)', 'Tháng trước', 'Giữ T8 → T9'],
      },
      {
        key: 'region',
        label: 'Vùng',
        value: 'Tất cả · lọc vùng tăng > ngưỡng',
        options: ['Tất cả · lọc vùng tăng > ngưỡng', 'Tất cả', 'Duyên hải Bắc Bộ'],
      },
      {
        key: 'threshold',
        label: 'Ngưỡng',
        value: '0,3 điểm',
        options: ['0,1 điểm', '0,3 điểm', '0,5 điểm'],
      },
      {
        key: 'definition',
        label: 'Định nghĩa',
        value: 'NPL_RATIO v4 (giữ cố định)',
        options: ['NPL_RATIO v4 (giữ cố định)', 'Bản đã chứng nhận mới nhất'],
      },
      {
        key: 'trigger',
        label: 'Trigger',
        value: 'Thứ Hai 07:00',
        options: ['Thứ Hai 07:00', 'Hằng ngày 07:00', 'Khi dữ liệu kỳ mới về'],
      },
      {
        key: 'autonomy',
        label: 'Mức tự chủ',
        value: 'Hỏi khi mơ hồ',
        options: ['Hỏi khi mơ hồ', 'Chỉ đề xuất', 'Tự làm, báo sau'],
      },
      {
        key: 'deliver',
        label: 'Gửi kết quả',
        value: 'Bạn · Teams',
        options: ['Bạn · Teams', 'Bạn · Email'],
      },
    ],
  };
}

/* Eight cases; the frame shows the first five. */
function testRunFixture(): SkillTestRun {
  const c = (
    id: string,
    name: string,
    period_text: string,
    expected: string,
    actual = expected,
  ) => ({
    id,
    name,
    period_text,
    expected,
    actual,
    passed: expected === actual,
    note: null,
  });
  return {
    skill_id: FROM_SESSION_ID,
    skill_name: 'Điều tra biến động theo vùng',
    version: 1,
    status: 'testing',
    ran_at: new Date(new Date().setHours(10, 31, 0, 0)).toISOString(),
    cases: [
      c('tc-1', 'Nợ xấu, tăng mạnh', 'T8→T9/2026', 'DH Bắc Bộ'),
      c('tc-2', 'Nợ xấu, ít biến động', 'T5→T6/2026', 'không vùng nào > 0,1'),
      c('tc-3', 'Dư nợ thẻ', 'T8→T9/2026', 'ĐB sông Hồng'),
      c('tc-4', 'Chỉ số giảm', 'T3→T4/2026', 'Tây Nguyên giảm nhiều nhất', 'Đông Nam Bộ'),
      c('tc-5', 'Kỳ chưa đủ dữ liệu', 'T10/2026', 'báo thiếu dữ liệu'),
      c('tc-6', 'Huy động, tăng nhẹ', 'T7→T8/2026', 'Đông Nam Bộ'),
      c('tc-7', 'Nợ xấu doanh nghiệp', 'Q2→Q3/2026', 'ĐB sông Hồng'),
      c('tc-8', 'Chỉ số có hai định nghĩa', 'T8→T9/2026', 'hỏi người chọn định nghĩa'),
    ],
    failures: [
      {
        case_id: 'tc-4',
        explanation:
          'Bước 2 chọn vùng có thay đổi lớn nhất theo giá trị tuyệt đối dương, nên bỏ qua vùng giảm. Đông Nam Bộ tăng nhẹ 0,02 nên được chọn.',
        fix_diff: ['- ORDER BY delta DESC', '+ ORDER BY ABS(delta) DESC'],
      },
    ],
  };
}

function runFormFixtures(): Record<string, SkillRunForm> {
  const metric = {
    key: 'metric',
    label: 'Chỉ số',
    value: 'NPL_RATIO@4',
    options: [
      { value: 'NPL_RATIO@4', label: 'NPL_RATIO v4' },
      { value: 'CARD_BALANCE@2', label: 'Dư nợ thẻ v2' },
      { value: 'DEPOSIT_GROWTH@3', label: 'Tăng trưởng huy động v3' },
    ],
  };
  return {
    'sk-variance': {
      skill_id: 'sk-variance',
      name: 'Phân rã biến động',
      version: 4,
      status: 'certified',
      kind: 'decomposition',
      params: [
        metric,
        {
          key: 'periods',
          label: 'So kỳ',
          value: '2026-09/2026-08',
          options: [
            { value: '2026-09/2026-08', label: 'T9/2026 với T8/2026' },
            { value: '2026-08/2026-07', label: 'T8/2026 với T7/2026' },
            { value: '2026-09/2025-09', label: 'T9/2026 với T9/2025' },
          ],
        },
        {
          key: 'dimensions',
          label: 'Tách theo',
          value: 'region,product',
          options: [
            { value: 'region,product', label: 'Vùng, sản phẩm' },
            { value: 'region', label: 'Vùng' },
            { value: 'product', label: 'Sản phẩm' },
          ],
        },
        {
          key: 'scope',
          label: 'Phạm vi',
          value: 'segment=retail',
          options: [
            { value: 'segment=retail', label: 'Phân khúc = Bán lẻ' },
            { value: 'all', label: 'Toàn ngân hàng' },
          ],
        },
      ],
      params_note: 'Chiều chỉ chọn được trong danh sách chiều hợp lệ của chỉ số.',
    },
    'sk-external': {
      skill_id: 'sk-external',
      name: 'Tìm yếu tố bên ngoài',
      version: 2,
      status: 'certified',
      kind: 'external_factors',
      params: [metric],
      params_note: 'Chỉ dùng chuỗi bên ngoài đã được duyệt đưa vào hệ thống.',
    },
  };
}

function decompositionRun(params: Record<string, string>): SkillRun {
  return {
    id: `srun-${Math.random().toString(36).slice(2, 8)}`,
    skill_id: 'sk-variance',
    version: 4,
    kind: 'decomposition',
    params,
    ran_at: new Date().toISOString(),
    external: null,
    decomposition: {
      summary:
        'Nợ xấu bán lẻ tăng 0,22 điểm. 74% mức tăng đến từ Duyên hải Bắc Bộ, trong đó vay tiêu dùng không TSBĐ chiếm phần lớn.',
      start: { label: 'T8', value: 1.62, value_text: '1,62%' },
      steps: [
        { label: 'DH Bắc Bộ · tiêu dùng', value: 0.12, value_text: '+0,12' },
        { label: 'DH Bắc Bộ · thẻ', value: 0.04, value_text: '+0,04' },
        { label: 'ĐB sông Hồng', value: 0.03, value_text: '+0,03' },
        { label: 'Bắc Trung Bộ', value: 0.02, value_text: '+0,02' },
        { label: 'Đông Nam Bộ', value: 0.02, value_text: '+0,02' },
        { label: 'Tây Nguyên', value: -0.01, value_text: '−0,01' },
        { label: 'Khác', value: 0, value_text: '+0,00' },
      ],
      end: { label: 'T9', value: 1.84, value_text: '1,84%' },
      contributions: [
        { label: 'DH Bắc Bộ · tiêu dùng không TSBĐ', points_text: '+0,12', share_text: '55%' },
        { label: 'DH Bắc Bộ · thẻ tín dụng', points_text: '+0,04', share_text: '18%' },
        { label: 'ĐB sông Hồng · tất cả', points_text: '+0,03', share_text: '14%' },
      ],
    },
  };
}

function externalRun(params: Record<string, string>): SkillRun {
  const label = params.metric === 'NPL_RATIO@4' || !params.metric ? 'nợ xấu bán lẻ' : params.metric;
  return {
    id: `srun-${Math.random().toString(36).slice(2, 8)}`,
    skill_id: 'sk-external',
    version: 2,
    kind: 'external_factors',
    params,
    ran_at: new Date().toISOString(),
    decomposition: null,
    // Only NPL has approved external series in the fixture: other metrics find nothing.
    external:
      params.metric && params.metric !== 'NPL_RATIO@4'
        ? {
            title: `Yếu tố bên ngoài · ${label}`,
            scope_text: 'Agent so 24 chuỗi vĩ mô và thị trường đã được phép dùng trong 36 tháng',
            factors: [],
            method_note:
              'Kiểm định: Granger, mức ý nghĩa 5%, đã tính theo độ trễ tốt nhất trong 6 tháng.',
          }
        : {
            title: 'Yếu tố bên ngoài · nợ xấu bán lẻ',
            scope_text:
              'Agent so 24 chuỗi vĩ mô và thị trường đã được phép dùng với NPL_RATIO trong 36 tháng',
            factors: [
              {
                id: 'ef-consumer-rate',
                name: 'Lãi suất cho vay tiêu dùng bình quân',
                source_text: 'NHNN · tháng',
                lag_text: '3 tháng',
                correlation_text: '0,71',
                test_text: 'p = 0,004',
                significant: true,
                verdict: 'worth',
                overlay: {
                  title: 'Lãi suất tiêu dùng và nợ xấu',
                  factor_label: 'Lãi suất (trước 3 tháng)',
                  metric_label: 'Nợ xấu',
                  factor: [0.04, 0.07, 0.12, 0.26, 0.38, 0.48, 0.55, 0.64, 0.74, 0.82, 0.87, 0.94],
                  metric: [0.1, 0.12, 0.14, 0.14, 0.2, 0.28, 0.34, 0.38, 0.43, 0.45, 0.5, 0.96],
                  note: 'Lãi suất tăng đi trước nợ xấu khoảng 3 tháng. Bước tiếp: hỏi Agent Phân tích tách nhóm khoản vay lãi suất thả nổi để kiểm tra.',
                },
              },
              {
                id: 'ef-unemployment',
                name: 'Tỷ lệ thất nghiệp khu vực DH Bắc Bộ',
                source_text: 'Tổng cục Thống kê · quý',
                lag_text: '2 quý',
                correlation_text: '0,58',
                test_text: 'p = 0,03',
                significant: true,
                verdict: 'worth',
                overlay: {
                  title: 'Thất nghiệp DH Bắc Bộ và nợ xấu',
                  factor_label: 'Thất nghiệp (trước 2 quý)',
                  metric_label: 'Nợ xấu',
                  factor: [0.2, 0.22, 0.3, 0.35, 0.33, 0.45, 0.52, 0.6, 0.66, 0.7, 0.78, 0.85],
                  metric: [0.1, 0.12, 0.14, 0.14, 0.2, 0.28, 0.34, 0.38, 0.43, 0.45, 0.5, 0.96],
                  note: 'Thất nghiệp vùng tăng đi trước nợ xấu khoảng hai quý; tương quan yếu hơn lãi suất.',
                },
              },
              {
                id: 'ef-cpi',
                name: 'Chỉ số giá tiêu dùng',
                source_text: 'Tổng cục Thống kê · tháng',
                lag_text: '1 tháng',
                correlation_text: '0,32',
                test_text: 'p = 0,21',
                significant: false,
                verdict: 'weak',
                overlay: null,
              },
              {
                id: 'ef-steel',
                name: 'Giá thép xây dựng',
                source_text: 'Thị trường · tuần',
                lag_text: '—',
                correlation_text: '0,44',
                test_text: 'p = 0,38',
                significant: false,
                verdict: 'random',
                overlay: null,
              },
            ],
            method_note:
              'Kiểm định: Granger, mức ý nghĩa 5%, đã tính theo độ trễ tốt nhất trong 6 tháng. Chuỗi có xu hướng được lấy sai phân trước khi kiểm định.',
          },
  };
}

const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    { title: code, status, code, detail },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

const notFound = () => problem(404, 'not_found', 'Không tìm thấy kỹ năng.');

export function createSkillAuthoringHandlers() {
  const definitions = new Map([['sk-variance', definitionFixture()]]);
  const trials = new Map<string, SkillTrial>([['sk-variance', trialFixture()]]);
  // "Kỹ năng mới" in the library (skills mock) creates sk-new-<n>; it opens here as an empty draft.
  const definitionOf = (id: string) => {
    if (!definitions.has(id) && id.startsWith('sk-new-')) definitions.set(id, emptyDraft(id));
    return definitions.get(id);
  };
  // The editor's "Kiểm thử" opens the library skill's tests too, not only the one made from a session.
  const testRuns = new Map<string, SkillTestRun>([
    [FROM_SESSION_ID, testRunFixture()],
    ['sk-variance', { ...testRunFixture(), skill_id: 'sk-variance' }],
  ]);
  const forms = runFormFixtures();
  const runs = new Map<string, SkillRun>([
    [
      'sk-variance',
      { ...decompositionRun(defaultParams(forms['sk-variance'])), ran_at: minutesAgo(40) },
    ],
    [
      'sk-external',
      { ...externalRun(defaultParams(forms['sk-external'])), ran_at: minutesAgo(90) },
    ],
  ]);

  const rerun = (run: SkillTestRun, fixed = false): SkillTestRun => {
    const cases = run.cases.map((c) =>
      fixed && !c.passed ? { ...c, actual: c.expected, passed: true, note: null } : c,
    );
    return {
      ...run,
      ran_at: new Date().toISOString(),
      cases,
      failures: run.failures.filter((f) => cases.some((c) => c.id === f.case_id && !c.passed)),
    };
  };

  return [
    http.get('*/v1/skills/:id/definition', ({ params }) => {
      const d = definitionOf(String(params.id));
      return d ? HttpResponse.json(d) : notFound();
    }),
    http.put('*/v1/skills/:id/definition', async ({ params, request }) => {
      const d = definitionOf(String(params.id));
      if (!d) return notFound();
      const body = (await request.json()) as Pick<SkillDefinition, 'instructions' | 'steps'>;
      if (!body.instructions?.trim())
        return problem(422, 'validation_failed', 'Hướng dẫn cho agent không được để trống.');
      const next = { ...d, instructions: body.instructions, steps: body.steps };
      definitions.set(d.skill_id, next);
      return HttpResponse.json(next);
    }),
    http.get('*/v1/skills/:id/trials/latest', ({ params }) =>
      definitionOf(String(params.id))
        ? HttpResponse.json(trials.get(String(params.id)) ?? null)
        : notFound(),
    ),
    http.get('*/v1/skills/:id/test-runs/latest', ({ params }) => {
      const run = testRuns.get(String(params.id));
      return run ? HttpResponse.json(run) : notFound();
    }),
    http.post('*/v1/skills/:id/test-cases', async ({ params, request }) => {
      const run = testRuns.get(String(params.id));
      if (!run) return notFound();
      const body = (await request.json()) as {
        name: string;
        period_text: string;
        expected: string;
      };
      if (!body.name?.trim() || !body.expected?.trim())
        return problem(422, 'validation_failed', 'Ca kiểm thử cần tên và kết quả kỳ vọng.');
      const next: SkillTestRun = {
        ...run,
        cases: [
          ...run.cases,
          {
            id: `tc-${run.cases.length + 1}`,
            name: body.name,
            period_text: body.period_text,
            expected: body.expected,
            actual: body.expected,
            passed: true,
            note: null,
          },
        ],
      };
      testRuns.set(run.skill_id, next);
      return HttpResponse.json(next, { status: 201 });
    }),
    http.get('*/v1/skills/:id/run-form', ({ params }) => {
      const form = forms[String(params.id)];
      return form ? HttpResponse.json(form) : notFound();
    }),
    http.get('*/v1/skills/:id/runs', ({ params }) => {
      const run = runs.get(String(params.id));
      if (!forms[String(params.id)]) return notFound();
      return HttpResponse.json({ items: run ? [run] : [], next_page_token: '' });
    }),
    http.post('*/v1/skills/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      // :share and the library's other verbs belong to the skills mock.
      if (!['try', 'run', 'test', 'apply-fix', 'submit'].includes(verb)) return undefined;
      const body = (await request.json().catch(() => ({}))) as {
        params?: Record<string, string>;
        case_id?: string;
        notes?: Record<string, string>;
      };
      if (verb === 'try') {
        const d = definitionOf(id);
        if (!d) return notFound();
        if (d.steps.length === 0)
          return problem(409, 'conflict', 'Kỹ năng chưa có bước nào để chạy thử.');
        const trial = { ...trialFixture(), version: d.version };
        trials.set(id, trial);
        definitions.set(id, { ...d, quality_text: `v3: 96% · v${d.version}: đã chạy thử` });
        return HttpResponse.json(trial);
      }
      if (verb === 'run') {
        const form = forms[id];
        if (!form) return notFound();
        const run =
          form.kind === 'decomposition'
            ? decompositionRun(body.params ?? {})
            : externalRun(body.params ?? {});
        runs.set(id, run);
        return HttpResponse.json(run);
      }
      const run = testRuns.get(id);
      if (!run) return notFound();
      if (verb === 'test') {
        const next = rerun(run);
        testRuns.set(id, next);
        return HttpResponse.json(next);
      }
      if (verb === 'apply-fix') {
        if (!run.failures.some((f) => f.case_id === body.case_id))
          return problem(409, 'conflict', 'Ca này không còn lỗi.');
        const next = rerun(run, true);
        testRuns.set(id, next);
        return HttpResponse.json(next);
      }
      if (verb === 'submit') {
        const unexplained = run.cases.filter((c) => !c.passed && !body.notes?.[c.id]?.trim());
        if (unexplained.length)
          return problem(
            409,
            'conflict',
            `Ca “${unexplained[0].name}” lỗi chưa có ghi chú; sửa hoặc ghi chú trước khi gửi duyệt.`,
          );
        testRuns.set(id, { ...run, status: 'pending_review' });
        return HttpResponse.json({ status: 'pending_review' }, { status: 202 });
      }
      return undefined;
    }),
    http.post('*/v1/skills\\:from-session', async ({ request }) => {
      const body = (await request.json()) as {
        session_id: string;
        kept_cells: number[];
        name: string;
      };
      if (!body.name?.trim()) return problem(422, 'validation_failed', 'Kỹ năng cần một tên.');
      if (!body.kept_cells?.length)
        return problem(422, 'validation_failed', 'Giữ lại ít nhất một ô của phiên.');
      const run = testRunFixture();
      testRuns.set(FROM_SESSION_ID, { ...run, skill_name: body.name.trim() });
      return HttpResponse.json({ skill_id: FROM_SESSION_ID, version: 1 }, { status: 201 });
    }),
    http.post('*/v1/skill-runs/:action', ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      if (verb !== 'add-to-session') return undefined;
      const known = [...runs.values()].some((r) => r.id === id);
      return known
        ? HttpResponse.json({ session_id: SESSION_ID })
        : problem(404, 'not_found', 'Không tìm thấy lượt chạy.');
    }),
    http.get('*/v1/workspaces/:id/skill-draft', ({ params }) =>
      params.id === SESSION_ID
        ? HttpResponse.json(skillDraftFixture())
        : problem(404, 'not_found', 'Phiên chưa có ô tính nào để tạo kỹ năng.'),
    ),
    http.get('*/v1/workspaces/:id/agent-draft', ({ params }) =>
      params.id === SESSION_ID
        ? HttpResponse.json(agentDraftFixture())
        : problem(404, 'not_found', 'Phiên chưa có ô tính nào để biến thành agent.'),
    ),
  ];
}

function emptyDraft(id: string): SkillDefinition {
  return {
    skill_id: id,
    name: 'Kỹ năng chưa đặt tên',
    version: 1,
    status: 'draft',
    inputs: [],
    output_text: '—',
    steps: [],
    instructions: '',
    guardrails: [],
    owner: { id: 'u-yen', name: 'Đỗ Hải Yến' },
    scope_label: 'Data Space hiện tại',
    domain_text: '—',
    used_by_text: 'Chưa agent nào dùng',
    quality_text: 'v1: chưa chạy thử',
    test_count: 0,
    version_count: 1,
    quick_test: { params: {}, lines: [] },
  };
}

function defaultParams(form: SkillRunForm): Record<string, string> {
  return Object.fromEntries(form.params.map((p) => [p.key, p.value]));
}
