import { http, HttpResponse } from 'msw';
import type {
  Calibration,
  EntityMappings,
  IndustryPack,
  MetricOverride,
  PackDraft,
  PackInstall,
  PackUpgrade,
  PackVersion,
} from '../lib/v1/packs';

/*
 * Gói ngành (P09) for the dev mock: the installed and available packs of "Gói ngành · Danh sách"
 * (2284:17173); installing Ngân hàng · bán lẻ 2026.3 (2284:17334); onboarding Rủi ro tín dụng —
 * mappings (2284:17560) and the calibration against reported figures (2286:17389); the NPL_RATIO
 * override (2286:17580); the 2026.3 → 2026.4 upgrade (2286:17750); and the 2026.4 draft
 * (2287:17550).
 */
const problem = (status: number, code: string, detail: string) =>
  HttpResponse.json(
    { title: code, status, code, detail },
    { status, headers: { 'Content-Type': 'application/problem+json' } },
  );

function packsFixture(): IndustryPack[] {
  return [
    {
      id: 'pack-bank-retail',
      name: 'Ngân hàng · bán lẻ',
      publisher: 'Data Agent',
      installed: {
        install_id: 'inst-retail',
        version: '2026.3',
        scope_text: 'Ngân hàng Việt Nam · 3 Data Space',
        mapped: 46,
        mapped_total: 50,
        overrides: 14,
        first_override_id: 'ovr-npl-ratio',
        status: 'active',
        upgrade: { to_version: '2026.4', upgrade_id: 'upg-retail-2026-4' },
      },
      latest: { version: '2026.4', version_id: 'pv-retail-2026-4', content_text: '' },
    },
    {
      id: 'pack-bank-credit-risk',
      name: 'Ngân hàng · rủi ro tín dụng',
      publisher: 'Data Agent',
      installed: {
        install_id: 'inst-credit-risk',
        version: '2026.3',
        scope_text: 'Mở rộng của gói Ngân hàng',
        mapped: 18,
        mapped_total: 23,
        overrides: 6,
        first_override_id: 'ovr-npl-ratio',
        status: 'onboarding',
        upgrade: null,
      },
      latest: { version: '2026.3', version_id: 'pv-credit-2026-3', content_text: '' },
    },
    {
      id: 'pack-life-insurance',
      name: 'Bảo hiểm nhân thọ',
      publisher: 'Data Agent',
      installed: null,
      latest: {
        version: '2026.2',
        version_id: 'pv-life-2026-2',
        content_text: '38 thực thể · 112 chỉ số · 9 kỹ năng · 4 agent mẫu',
      },
    },
    {
      id: 'pack-retail-chain',
      name: 'Bán lẻ chuỗi',
      publisher: 'Data Agent',
      installed: null,
      latest: {
        version: '2026.1',
        version_id: 'pv-chain-2026-1',
        content_text: '29 thực thể · 86 chỉ số · 7 kỹ năng · 3 agent mẫu',
      },
    },
    {
      id: 'pack-securities',
      name: 'Chứng khoán',
      publisher: 'Đối tác · FinPack',
      installed: null,
      latest: {
        version: '2025.4',
        version_id: 'pv-sec-2025-4',
        content_text: '31 thực thể · 74 chỉ số · 5 kỹ năng',
      },
    },
  ];
}

function versionFixture(id: string): PackVersion | null {
  const pack = packsFixture().find((p) => p.latest.version_id === id) ?? null;
  const name = id === 'pv-retail-2026-3' ? 'Ngân hàng · bán lẻ' : pack?.name;
  if (!name) return null;
  return {
    id,
    pack_name: name,
    version: id === 'pv-retail-2026-3' ? '2026.3' : (pack?.latest.version ?? ''),
    status: 'published',
    counts: [
      { label: 'Thực thể', value: 50 },
      { label: 'Chỉ số', value: 148 },
      { label: 'Thuật ngữ', value: 612 },
      { label: 'Kỹ năng', value: 12 },
      { label: 'Agent mẫu', value: 6 },
      { label: 'Luật kiểm tra', value: 84 },
    ],
    checks: [
      { item: 'Phiên bản Data Agent tương thích', result: '3.1 ≥ 3.0 yêu cầu', ok: true },
      {
        item: 'Không ghi đè định nghĩa đang được chứng nhận',
        result: '0 định nghĩa bị ghi đè',
        ok: true,
      },
      { item: 'Ngôn ngữ thuật ngữ', result: 'Tiếng Việt, kèm tên tiếng Anh', ok: true },
      {
        item: '2 chỉ số trùng mã với chỉ số đã có',
        result: 'NPL_RATIO, CASA_RATIO — chọn cách xử lý bên dưới',
        ok: false,
      },
      { item: 'Chữ ký của nhà phát hành', result: 'Hợp lệ · Data Agent', ok: true },
    ],
    code_conflicts: [
      {
        code: 'NPL_RATIO',
        org_text: 'v4 · nhóm 3–5 · loại trừ vay nội bộ · đã chứng nhận',
        pack_text: 'nhóm 3–5 · gồm vay nội bộ',
        suggested: 'keep_org',
      },
      {
        code: 'CASA_RATIO',
        org_text: 'v3 · trung bình ngày',
        pack_text: 'cuối kỳ',
        suggested: 'keep_org',
      },
    ],
    targets: [
      { id: 'ds-credit-risk', name: 'Rủi ro tín dụng', default: true },
      { id: 'ds-retail', name: 'Kinh doanh bán lẻ', default: true },
      { id: 'ds-finance', name: 'Tài chính', default: true },
      { id: 'ds-ops', name: 'Vận hành', default: false },
    ],
  };
}

function installFixture(): PackInstall {
  return {
    id: 'inst-credit-risk',
    title: 'Rủi ro tín dụng',
    summary_text:
      '18/23 thực thể đã ánh xạ · Agent Chuẩn bị dữ liệu đề xuất 41 ánh xạ, bạn đã duyệt 36',
    entities: [
      { key: 'loan', name: 'Khoản vay', mapped: 12, total: 12 },
      { key: 'customer', name: 'Khách hàng', mapped: 9, total: 10 },
      { key: 'collateral', name: 'Tài sản bảo đảm', mapped: 6, total: 8 },
      { key: 'repayment', name: 'Giao dịch trả nợ', mapped: 7, total: 7 },
      { key: 'branch', name: 'Chi nhánh', mapped: 4, total: 4 },
      { key: 'card', name: 'Thẻ tín dụng', mapped: 0, total: 9 },
    ],
  };
}

function customerMappings(): EntityMappings {
  return {
    entity: 'customer',
    name: 'Khách hàng',
    main_table: 'core.CUSTOMERS',
    rows: [
      {
        attr: 'customer_id',
        required: true,
        column: 'CUSTOMERS.CIF_NO',
        options: ['CUSTOMERS.CIF_NO', 'CUSTOMERS.CUST_ID'],
        confidence: 0.99,
        note: 'Khoá, khớp 100% với LD_LOANS.customer_id',
        unsure: false,
        approved: true,
      },
      {
        attr: 'segment',
        required: false,
        column: 'CUSTOMERS.CUST_TYPE',
        options: ['CUSTOMERS.CUST_TYPE', 'CUSTOMERS.SEGMENT_CD'],
        confidence: 0.93,
        note: 'Giá trị I/C → cá nhân/doanh nghiệp',
        unsure: false,
        approved: true,
      },
      {
        attr: 'birth_date',
        required: false,
        column: 'CUSTOMERS.DOB',
        options: ['CUSTOMERS.DOB'],
        confidence: 0.97,
        note: 'Dữ liệu cá nhân · chỉ dùng để tính tuổi',
        unsure: false,
        approved: true,
      },
      {
        attr: 'income_band',
        required: false,
        column: 'CUSTOMERS.INCOME_GRP',
        options: ['CUSTOMERS.INCOME_GRP', 'CUSTOMERS.INCOME_AMT'],
        confidence: 0.61,
        note: 'Agent không chắc: 7 nhóm thu nhập, gói dùng 5 nhóm',
        unsure: true,
        approved: false,
      },
      {
        attr: 'kyc_level',
        required: true,
        column: null,
        options: ['CUSTOMERS.KYC_FLAG', 'EKYC.LEVEL'],
        confidence: null,
        note: 'Chưa tìm thấy cột phù hợp',
        unsure: true,
        approved: false,
      },
    ],
    decisions: [
      {
        text: 'income_band: gộp 7 nhóm thu nhập của tổ chức thành 5 nhóm của gói? Agent đề xuất gộp nhóm 6–7 vào nhóm 5.',
        code_mapping: {
          attr: 'income_band',
          rows: [
            { from: '1 · dưới 5 triệu', to: '1' },
            { from: '2 · 5–10 triệu', to: '2' },
            { from: '3 · 10–20 triệu', to: '3' },
            { from: '4 · 20–35 triệu', to: '4' },
            { from: '5 · 35–50 triệu', to: '5' },
            { from: '6 · 50–100 triệu', to: '5' },
            { from: '7 · trên 100 triệu', to: '5' },
          ],
        },
      },
      {
        text: 'kyc_level là thuộc tính bắt buộc. Không có cột phù hợp: có thể lấy từ hệ thống eKYC hoặc đánh dấu “không áp dụng”, khi đó 3 chỉ số tuân thủ sẽ không tính được.',
        code_mapping: null,
      },
    ],
  };
}

function calibrationFixture(): Calibration {
  return {
    install_title: 'Rủi ro tín dụng',
    matched: 5,
    total: 6,
    tolerance_text: 'ngưỡng chấp nhận lệch 0,1%',
    rows: [
      {
        id: 'c1',
        metric: 'Dư nợ cho vay',
        period: '09/2026',
        computed: '182.431 tỷ',
        reported: '182.430 tỷ',
        diff: '0,00%',
        ok: true,
        accepted_note: null,
      },
      {
        id: 'c2',
        metric: 'Dư nợ cho vay',
        period: '08/2026',
        computed: '180.416 tỷ',
        reported: '180.416 tỷ',
        diff: '0,00%',
        ok: true,
        accepted_note: null,
      },
      {
        id: 'c3',
        metric: 'Nợ xấu (NPL_RATIO)',
        period: '09/2026',
        computed: '1,85%',
        reported: '1,84%',
        diff: '+0,01 điểm',
        ok: true,
        accepted_note: null,
      },
      {
        id: 'c4',
        metric: 'Nợ xấu (NPL_RATIO)',
        period: '08/2026',
        computed: '1,63%',
        reported: '1,62%',
        diff: '+0,01 điểm',
        ok: true,
        accepted_note: null,
      },
      {
        id: 'c5',
        metric: 'Số khoản vay',
        period: '09/2026',
        computed: '1.284.562',
        reported: '1.284.562',
        diff: '0,00%',
        ok: true,
        accepted_note: null,
      },
      {
        id: 'c6',
        metric: 'Dư nợ thẻ tín dụng',
        period: '09/2026',
        computed: '9.721 tỷ',
        reported: '9.643 tỷ',
        diff: '+0,81%',
        ok: false,
        accepted_note: null,
      },
    ],
    findings: [
      {
        row_id: 'c6',
        title: 'Dư nợ thẻ tín dụng lệch 0,81% (78 tỷ)',
        text: 'Agent Đối soát tìm thấy: số đã báo cáo không tính 2.140 thẻ phụ có dư nợ, còn ánh xạ hiện tại gộp thẻ phụ vào thẻ chính. Thực thể Thẻ tín dụng mới ánh xạ 0/9 thuộc tính, nên dư nợ thẻ đang lấy tạm từ bảng tổng hợp.',
        fix: { id: 'fix-exclude-supplementary', label: 'Loại thẻ phụ khỏi ánh xạ' },
      },
    ],
    after_text:
      '148 chỉ số của gói chuyển sang “đã ánh xạ” và dùng được trong Khám phá; agent mẫu của gói có thể bật.',
  };
}

function overrideFixture(): MetricOverride {
  return {
    id: 'ovr-npl-ratio',
    metric_code: 'NPL_RATIO',
    status: 'draft',
    summary_text:
      'Bản gốc từ gói Ngân hàng 2026.3 · bản của tổ chức v4 đang dùng ở 23 artifact và 5 agent',
    base_expression: 'dư nợ nhóm 3–5\n÷ tổng dư nợ\nlọc: phân khúc = bán lẻ',
    org_expression:
      'dư nợ nhóm 3–5\n÷ tổng dư nợ\nlọc: phân khúc = bán lẻ\n   và loại trừ cho vay nội bộ\n   và tính khoản cơ cấu lần 2 vào nhóm 3',
    changes: [
      {
        change: '+ loại trừ cho vay nội bộ',
        reason: 'Theo quy định nội bộ, khớp số báo cáo đã nộp',
      },
      {
        change: '+ khoản cơ cấu lần 2 vào nhóm 3',
        reason: 'Áp dụng từ 01/01/2027 theo văn bản sửa đổi',
      },
    ],
    upgrade_note:
      'Hiệu chỉnh vẫn liên kết với bản gốc. Khi nâng cấp gói, phần gốc thay đổi được gộp với hai thay đổi trên; nếu đụng cùng một chỗ, người phụ trách chỉ số chọn ở màn Nâng cấp gói.',
    trial: null,
    trial_note: null,
  };
}

function upgradeFixture(): PackUpgrade {
  return {
    id: 'upg-retail-2026-4',
    pack_name: 'Ngân hàng · bán lẻ',
    from_version: '2026.3',
    to_version: '2026.4',
    summary_text:
      '42 thay đổi trong gói · 38 tự gộp · 3 cần bạn chọn · 1 bị bỏ qua vì tổ chức đã xoá phần đó',
    conflicts: [
      {
        id: 'cf-npl-filter',
        element: 'NPL_RATIO · bộ lọc',
        old: 'phân khúc = bán lẻ',
        new: 'phân khúc = bán lẻ và loại nợ được khoanh',
        org: '+ loại trừ vay nội bộ',
        options: [
          { value: 'merge', label: 'Gộp cả hai (đề xuất)' },
          { value: 'org', label: 'Giữ của tổ chức' },
          { value: 'pack', label: 'Dùng bản mới' },
        ],
        suggested: 'merge',
      },
      {
        id: 'cf-term-npl',
        element: 'Thuật ngữ “nợ xấu”',
        old: '→ NPL_RATIO',
        new: '→ NPL_RATIO, thêm nghĩa “nợ nhóm 2-5”',
        org: 'đã đổi nghĩa mặc định sang NPL_INTERNAL',
        options: [
          { value: 'org', label: 'Giữ của tổ chức' },
          { value: 'pack', label: 'Dùng bản mới' },
        ],
        suggested: 'org',
      },
      {
        id: 'cf-skill-variance',
        element: 'Kỹ năng Phân rã biến động',
        old: 'v2',
        new: 'v3 · thêm bước kiểm định',
        org: 'tham số kỳ mặc định = quý',
        options: [
          { value: 'pack_keep_params', label: 'Dùng bản mới, giữ tham số' },
          { value: 'org', label: 'Giữ của tổ chức' },
          { value: 'pack', label: 'Dùng bản mới' },
        ],
        suggested: 'pack_keep_params',
      },
    ],
    auto: Array.from({ length: 38 }, (_, i) => ({
      element: i === 0 ? 'Chỉ số LOAN_GROWTH' : `Thuật ngữ #${i + 1}`,
      text: i === 0 ? 'thêm chiều kỳ hạn' : 'thêm đồng nghĩa',
    })),
    skipped: [{ element: 'Chỉ số CARD_ACTIVE', reason: 'tổ chức đã xoá chỉ số này' }],
    benchmark_pct: null,
    status: 'dry_run',
  };
}

function draftFixture(): PackDraft {
  return {
    id: 'pv-retail-2026-4-draft',
    pack_name: 'Ngân hàng · bán lẻ',
    version: '2026.4',
    base_version: '2026.3',
    summary_text: 'Dành cho chuyên gia ngành · 42 thay đổi so với 2026.3',
    sections: [
      { label: 'Thực thể', changed: 2, total: 50 },
      { label: 'Chỉ số', changed: 31, total: 148 },
      { label: 'Thuật ngữ', changed: 6, total: 612 },
      { label: 'Kỹ năng', changed: 1, total: 12 },
      { label: 'Agent mẫu', changed: 0, total: 6 },
      { label: 'Luật kiểm tra', changed: 2, total: 84 },
      { label: 'Bộ dữ liệu mẫu', changed: 0, total: 1 },
      { label: 'Ghi chú phát hành', changed: 0, total: 0 },
    ],
    test: {
      run_text: 'chạy 10:12 · 4 phút',
      kpis: [
        { label: 'Câu hỏi chuẩn', value: '97,2% đúng', tone: 'neutral' },
        { label: 'Chỉ số tính được', value: '148/148', tone: 'neutral' },
        { label: 'Luật kiểm tra', value: '82/84 đạt', tone: 'warning' },
        { label: 'So với 2026.3', value: '+1,4 điểm', tone: 'success' },
      ],
      failures: [
        {
          item: 'Luật “dư nợ không âm” · bảng thẻ',
          kind: 'Luật kiểm tra',
          detail: 'Bộ dữ liệu mẫu có 3 dòng hoàn tiền âm hợp lệ; nên loại giao dịch hoàn tiền',
        },
        {
          item: 'Luật “ngày giải ngân ≤ ngày đáo hạn”',
          kind: 'Luật kiểm tra',
          detail: '1 dòng mẫu sai; lỗi ở dữ liệu mẫu, không phải luật',
        },
        {
          item: '“tỷ lệ nợ quá hạn 30+” theo vùng',
          kind: 'Câu hỏi chuẩn',
          detail: 'Hiểu “vùng” là chi nhánh; cần thêm đồng nghĩa “miền”',
        },
      ],
    },
    conditions: [
      { text: 'Câu hỏi chuẩn ≥ 95%', ok: true },
      { text: 'Mọi chỉ số tính được trên dữ liệu mẫu', ok: true },
      { text: 'Luật kiểm tra đạt hết hoặc có ghi chú', ok: false },
      { text: 'Có ghi chú phát hành', ok: true },
    ],
    after_text:
      '2 tổ chức đang dùng 2026.3 nhận thông báo có bản mới. Không tổ chức nào được nâng cấp tự động.',
  };
}

export function createPackHandlers() {
  let packs = packsFixture();
  let mappings = customerMappings();
  let install = installFixture();
  let calibration = calibrationFixture();
  let override = overrideFixture();
  let upgrade = upgradeFixture();
  const draft = draftFixture();
  // Drafts of the organisation's own packs (PROPOSAL POST /v1/packs), opened by Biên soạn gói.
  const created: PackDraft[] = [];
  const findDraft = (id: string) => (id === draft.id ? draft : created.find((d) => d.id === id));
  return [
    http.get('*/v1/packs', () => HttpResponse.json({ items: packs })),
    http.post('*/v1/packs', async ({ request }) => {
      const body = (await request.json()) as {
        name: string;
        based_on_version_id: string | null;
        includes: string[];
      };
      if (!body.name?.trim()) return problem(422, 'validation_failed', 'Đặt tên gói.');
      const base = packs.find((p) => p.latest.version_id === body.based_on_version_id);
      const id = `pv-own-${created.length + 1}`;
      created.push({
        id,
        pack_name: body.name.trim(),
        version: '0.1',
        base_version: base ? `${base.name} ${base.latest.version}` : 'mô hình hiện tại của tổ chức',
        summary_text: `Gồm ${body.includes.join(', ')} · chưa sửa mục nào`,
        sections: body.includes.map((label) => ({ label, changed: 0, total: 0 })),
        test: null,
        conditions: [{ text: 'Chạy kiểm tra trên dữ liệu mẫu', ok: false }],
        after_text: 'Sau khi phát hành, gói xuất hiện trong Gói ngành của tổ chức.',
      });
      return HttpResponse.json(
        { id: `pack-own-${created.length}`, draft_version_id: id },
        { status: 201 },
      );
    }),
    http.post('*/v1/packs\\:import', () =>
      HttpResponse.json({ id: 'pv-life-2026-2' }, { status: 201 }),
    ),
    http.get('*/v1/pack-versions', () =>
      HttpResponse.json({
        items: [draft, ...created].map((d) => ({
          id: d.id,
          pack_name: d.pack_name,
          version: d.version,
        })),
      }),
    ),
    http.get('*/v1/pack-versions/:id/draft', ({ params }) => {
      const found = findDraft(String(params.id));
      return found ? HttpResponse.json(found) : problem(404, 'not_found', 'Không có bản nháp.');
    }),
    http.get('*/v1/pack-versions/:id', ({ params }) => {
      const version = versionFixture(String(params.id));
      return version
        ? HttpResponse.json(version)
        : problem(404, 'not_found', 'Không tìm thấy phiên bản gói.');
    }),
    http.post('*/v1/pack-versions/:action', ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      const target = findDraft(id);
      if (!target) return problem(404, 'not_found', 'Không có bản nháp.');
      if (verb === 'validate') return HttpResponse.json({ id: 'check-run' }, { status: 202 });
      if (verb === 'publish') {
        if (target.conditions.some((c) => !c.ok))
          return problem(409, 'invalid_state', 'Bản nháp chưa qua kiểm tra.');
        return HttpResponse.json({ id });
      }
      return problem(404, 'not_found', 'Không có thao tác này.');
    }),
    http.post('*/v1/pack-installs', async ({ request }) => {
      const body = (await request.json()) as { data_space_ids: string[] };
      if (!body.data_space_ids?.length)
        return problem(422, 'validation_failed', 'Chọn ít nhất một Data Space.');
      return HttpResponse.json({ id: install.id }, { status: 201 });
    }),
    http.get('*/v1/pack-installs', () => HttpResponse.json({ items: [install] })),
    http.get('*/v1/pack-installs/:id/mappings', ({ params, request }) => {
      if (params.id !== install.id) return problem(404, 'not_found', 'Không tìm thấy lượt cài.');
      const entity = new URL(request.url).searchParams.get('entity');
      if (entity && entity !== 'customer') {
        const e = install.entities.find((x) => x.key === entity);
        if (!e) return problem(404, 'not_found', 'Không có thực thể này.');
        return HttpResponse.json({
          entity: e.key,
          name: e.name,
          main_table: 'core.' + e.key.toUpperCase(),
          rows: [],
          decisions: [],
        } satisfies EntityMappings);
      }
      return HttpResponse.json(mappings);
    }),
    http.put('*/v1/pack-installs/:id/mappings', async ({ request }) => {
      const body = (await request.json()) as {
        entity_mappings: { entity: string; attr: string; column: string | null }[];
      };
      const ours = body.entity_mappings.filter((m) => m.entity === mappings.entity);
      mappings = {
        ...mappings,
        rows: mappings.rows.map((r) => {
          const m = ours.find((x) => x.attr === r.attr);
          return m ? { ...r, column: m.column, approved: true } : r;
        }),
      };
      const done = mappings.rows.filter((r) => r.approved).length;
      install = {
        ...install,
        entities: install.entities.map((e) =>
          e.key === mappings.entity ? { ...e, mapped: done } : e,
        ),
      };
      return HttpResponse.json({ approved: ours.length });
    }),
    http.get('*/v1/pack-installs/:id/calibration', ({ params }) =>
      params.id === install.id
        ? HttpResponse.json(calibration)
        : problem(404, 'not_found', 'Không tìm thấy lượt cài.'),
    ),
    http.get('*/v1/pack-installs/:id', ({ params }) =>
      params.id === install.id
        ? HttpResponse.json(install)
        : problem(404, 'not_found', 'Không tìm thấy lượt cài.'),
    ),
    http.post('*/v1/pack-installs/:action', async ({ params, request }) => {
      const [id, verb] = String(params.action).split(':');
      if (id !== install.id) return problem(404, 'not_found', 'Không tìm thấy lượt cài.');
      const body = (await request.json().catch(() => ({}))) as Record<string, string>;
      const settle = (rowId: string, patch: Partial<Calibration['rows'][number]>) => {
        const rows = calibration.rows.map((r) => (r.id === rowId ? { ...r, ...patch } : r));
        calibration = {
          ...calibration,
          rows,
          matched: rows.filter((r) => r.ok || r.accepted_note).length,
          findings: calibration.findings.filter((f) => f.row_id !== rowId),
        };
        return HttpResponse.json(calibration);
      };
      if (verb === 'apply-fix')
        return settle('c6', { computed: '9.643 tỷ', diff: '0,00%', ok: true });
      if (verb === 'accept-gap') return settle(body.row_id, { accepted_note: body.note });
      if (verb === 'activate') {
        if (calibration.matched < calibration.total)
          return problem(422, 'business_rule_violated', 'Đối chiếu số chưa đạt (BR-36).');
        packs = packs.map((p) =>
          p.installed?.install_id === id
            ? { ...p, installed: { ...p.installed, status: 'active' } }
            : p,
        );
        return HttpResponse.json({ id });
      }
      return problem(404, 'not_found', 'Không có thao tác này.');
    }),
    http.get('*/v1/metric-overrides/:id', ({ params }) =>
      params.id === override.id
        ? HttpResponse.json(override)
        : problem(404, 'not_found', 'Không tìm thấy hiệu chỉnh.'),
    ),
    http.post('*/v1/metric-overrides/:action', ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      if (id !== override.id) return problem(404, 'not_found', 'Không tìm thấy hiệu chỉnh.');
      if (verb === 'trial') {
        override = {
          ...override,
          trial: [
            { period: '04/26', base: '1,58%', org: '1,55%' },
            { period: '05/26', base: '1,60%', org: '1,57%' },
            { period: '06/26', base: '1,63%', org: '1,60%' },
            { period: '07/26', base: '1,66%', org: '1,62%' },
            { period: '08/26', base: '1,66%', org: '1,63%' },
            { period: '09/26', base: '1,88%', org: '1,84%' },
          ],
          trial_note:
            'Bản của tổ chức khớp số đã báo cáo cả 6 kỳ; bản gốc cao hơn 0,03–0,04 điểm do gồm vay nội bộ.',
        };
        return HttpResponse.json(override);
      }
      if (verb === 'submit') {
        if (!override.trial)
          return problem(409, 'invalid_state', 'Chạy thử trước khi gửi chứng nhận.');
        override = { ...override, status: 'submitted' };
        return HttpResponse.json(override);
      }
      if (verb === 'discard') return HttpResponse.json({ id });
      return problem(404, 'not_found', 'Không có thao tác này.');
    }),
    http.get('*/v1/pack-upgrades/:id', ({ params }) =>
      params.id === upgrade.id
        ? HttpResponse.json(upgrade)
        : problem(404, 'not_found', 'Không tìm thấy lượt nâng cấp.'),
    ),
    http.put('*/v1/pack-upgrades/:id/decisions', () => HttpResponse.json(upgrade)),
    http.post('*/v1/pack-upgrades/:action', ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      if (id !== upgrade.id) return problem(404, 'not_found', 'Không tìm thấy lượt nâng cấp.');
      if (verb === 'benchmark') {
        upgrade = { ...upgrade, benchmark_pct: 97.2 };
        return HttpResponse.json(upgrade);
      }
      if (verb === 'apply') {
        upgrade = { ...upgrade, status: 'applied' };
        return HttpResponse.json(upgrade);
      }
      return problem(404, 'not_found', 'Không có thao tác này.');
    }),
  ];
}
