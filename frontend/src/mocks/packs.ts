import { http, HttpResponse } from 'msw';
import type { components } from '../lib/v1/schema';
import type { IndustryPack } from '../lib/v1/packs';

/*
 * Gói ngành (P09) for the dev mock, in the api's shapes: the installed and available packs of
 * "Gói ngành · Danh sách" (2284:17173); the versions of Ngân hàng · bán lẻ, rủi ro tín dụng and the
 * available packs (2284:17334); onboarding Rủi ro tín dụng — mappings (2284:17560) and its
 * calibration run (2286:17389); the NPL_RATIO override (2286:17580); the 2026.3 → 2026.4 upgrade
 * (2286:17750); and the 2026.4 draft (2287:17550). GET /v1/packs carries what IndustryPackView
 * lacks (installed, latest, draft) under `display`.
 */
type Schemas = components['schemas'];
type Version = Schemas['PackVersionDetail'];
type Mapping = Schemas['MappingView'];
type Run = Schemas['CalibrationRunView'];
type Override = Schemas['MetricOverrideView'];
type Upgrade = Schemas['PackUpgradeView'];

const T0 = '2026-10-01T09:00:00+07:00';
const USER = 'user-1';
const SPACE = 'ds-credit-risk';

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
      draft: { version: '2026.4', version_id: 'pv-retail-2026-4-draft' },
    },
    {
      id: 'pack-bank-credit-risk',
      name: 'Ngân hàng · rủi ro tín dụng',
      publisher: 'Data Agent',
      installed: {
        install_id: 'inst-credit-risk',
        version: '2026.3',
        scope_text: 'Rủi ro tín dụng',
        mapped: 38,
        mapped_total: 45,
        overrides: 0,
        first_override_id: null,
        status: 'onboarding',
        upgrade: null,
      },
      latest: { version: '2026.3', version_id: 'pv-credit-2026-3', content_text: '' },
      draft: null,
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
      draft: null,
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
      draft: null,
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
      draft: null,
    },
  ];
}

function packWire(p: IndustryPack): Schemas['IndustryPackView'] & { display: IndustryPack } {
  return {
    id: p.id,
    code: p.id,
    name: p.name,
    publisher: p.publisher,
    industry_code: 'banking',
    origin: p.id.startsWith('pack-own') ? 'organisation' : 'vendor',
    description: null,
    publisher_key_id: 'key-1',
    created_at: T0,
    updated_at: T0,
    display: p,
  };
}

/* ─── Versions ──────────────────────────────────────────────────────── */

type Attribute = NonNullable<Schemas['CanonicalEntityView']['attributes']>[number];

const attribute = (
  entity: string,
  code: string,
  required = false,
  dataType = 'string',
): Attribute => ({
  id: `attr-${entity}-${code}`,
  code,
  name: code,
  data_type: dataType,
  required,
  pii: false,
  code_list: null,
});

// Rủi ro tín dụng: customer is spelled out, the others are numbered attributes.
const CREDIT_ENTITIES: [string, string, number][] = [
  ['loan', 'Khoản vay', 12],
  ['collateral', 'Tài sản bảo đảm', 8],
  ['repayment', 'Giao dịch trả nợ', 7],
  ['branch', 'Chi nhánh', 4],
  ['card', 'Thẻ tín dụng', 9],
];

function creditEntities(): Schemas['CanonicalEntityView'][] {
  const customer: Schemas['CanonicalEntityView'] = {
    id: 'ent-customer',
    code: 'customer',
    name: 'Khách hàng',
    description: null,
    key_attributes: ['customer_id'],
    attributes: [
      attribute('customer', 'customer_id', true),
      attribute('customer', 'segment'),
      attribute('customer', 'birth_date', false, 'date'),
      attribute('customer', 'income_band', false, 'code'),
      attribute('customer', 'kyc_level', true),
    ],
  };
  const others = CREDIT_ENTITIES.map(([code, name, n]) => ({
    id: `ent-${code}`,
    code,
    name,
    description: null,
    key_attributes: [],
    attributes: Array.from({ length: n }, (_, i) => attribute(code, `attr_${i + 1}`)),
  }));
  return [others[0], customer, ...others.slice(1)];
}

const metric = (code: string, name: string, expression: string, regulatory = false) => ({
  id: `metric-${code}`,
  code,
  name,
  expression,
  unit: null,
  additivity: 'none',
  legal_basis: regulatory ? 'Thông tư 11/2021/TT-NHNN' : null,
  is_internal: !regulatory,
  in_regulatory_report: regulatory,
});

function version(
  id: string,
  packId: string,
  v: string,
  counts: { entities: number; metrics: number; skills: number; templates: number; rules: number },
  extra: Partial<Version> = {},
): Version {
  return {
    id,
    pack_id: packId,
    version: v,
    status: 'published',
    manifest: {},
    signature: 'sig',
    signature_verified_at: T0,
    min_core_version: '3.0.0',
    object_key: `packs/${id}.dapack`,
    release_notes: null,
    released_at: T0,
    breaking_changes: [],
    created_at: T0,
    updated_at: T0,
    entities: Array.from({ length: counts.entities }, (_, i) => ({
      id: `${id}-ent-${i}`,
      code: `entity_${i + 1}`,
      name: `Thực thể ${i + 1}`,
      description: null,
      key_attributes: [],
      attributes: [],
    })),
    metrics: Array.from({ length: counts.metrics }, (_, i) =>
      metric(`METRIC_${i + 1}`, `Chỉ số ${i + 1}`, `sum(x_${i + 1})`),
    ),
    rules: Array.from({ length: counts.rules }, (_, i) => ({
      id: `${id}-rule-${i}`,
      code: `RULE_${i + 1}`,
      description: `Luật ${i + 1}`,
      expression: 'x >= 0',
      severity: 'blocking',
      tolerance: null,
    })),
    skills: [
      ...Array.from({ length: counts.skills }, (_, i) => ({
        id: `${id}-skill-${i}`,
        code: `skill_${i + 1}`,
        name: `Kỹ năng ${i + 1}`,
        kind: 'skill',
        definition: {},
      })),
      ...Array.from({ length: counts.templates }, (_, i) => ({
        id: `${id}-tpl-${i}`,
        code: `agent_${i + 1}`,
        name: `Agent mẫu ${i + 1}`,
        kind: 'agent_template',
        definition: {},
      })),
    ],
    latest_check_run: null,
    ...extra,
  };
}

function versionsFixture(): Map<string, Version> {
  const retail = { entities: 50, metrics: 148, skills: 12, templates: 6, rules: 84 };
  const list = [
    version('pv-retail-2026-3', 'pack-bank-retail', '2026.3', retail),
    version('pv-retail-2026-4', 'pack-bank-retail', '2026.4', retail),
    version('pv-retail-2026-4-draft', 'pack-bank-retail', '2026.4', retail, {
      status: 'draft',
      signature: '',
      signature_verified_at: null,
      released_at: null,
      release_notes: 'Thêm chiều kỳ hạn cho LOAN_GROWTH; bỏ CARD_ACTIVE.',
      latest_check_run: {
        id: 'check-1',
        pack_version_id: 'pv-retail-2026-4-draft',
        checks: {
          legal_basis: [],
          code_reuse: ['CARD_ACTIVE'],
          breaking_changes_without_guide: [],
          sample_datasets: 'skipped',
          sample_questions: 'skipped',
        },
        passed: false,
        ran_by: USER,
        created_at: '2026-10-12T10:12:00+07:00',
      },
    }),
    version(
      'pv-credit-2026-3',
      'pack-bank-credit-risk',
      '2026.3',
      { entities: 0, metrics: 0, skills: 4, templates: 2, rules: 20 },
      {
        entities: creditEntities(),
        metrics: [
          metric('LOAN_BALANCE', 'Dư nợ cho vay', 'sum(loan.balance)', true),
          metric('NPL_RATIO', 'Tỷ lệ nợ xấu', 'dư nợ nhóm 3–5\n÷ tổng dư nợ', true),
          metric('LOAN_COUNT', 'Số khoản vay', 'count(loan)'),
          metric('CARD_BALANCE', 'Dư nợ thẻ tín dụng', 'sum(card.balance)'),
        ],
      },
    ),
    version('pv-life-2026-2', 'pack-life-insurance', '2026.2', {
      entities: 38,
      metrics: 112,
      skills: 9,
      templates: 4,
      rules: 40,
    }),
    version('pv-chain-2026-1', 'pack-retail-chain', '2026.1', {
      entities: 29,
      metrics: 86,
      skills: 7,
      templates: 3,
      rules: 30,
    }),
    version('pv-sec-2025-4', 'pack-securities', '2025.4', {
      entities: 31,
      metrics: 74,
      skills: 5,
      templates: 0,
      rules: 25,
    }),
  ];
  return new Map(list.map((v) => [v.id, v]));
}

/* ─── Install, mappings, calibration ────────────────────────────────── */

const INSTALL_ID = 'inst-credit-risk';
const INSTALL_VERSION = 'pv-credit-2026-3';

function mapping(
  entity: string,
  code: string,
  column: string,
  confidence: number,
  confirmed = true,
): Mapping {
  return {
    id: `map-${entity}-${code}`,
    pack_install_id: INSTALL_ID,
    attribute_id: `attr-${entity}-${code}`,
    dataset_id: `dataset-${entity}`,
    column_expr: column,
    confidence,
    origin: confirmed ? 'confirmed' : 'suggested',
    confirmed_by: confirmed ? USER : null,
    valid_from: T0,
    valid_to: null,
    created_at: T0,
    updated_at: T0,
    entity_code: entity,
    attribute_code: code,
    attribute_name: code,
    required: false,
    code_mappings: [],
  };
}

function mappingsFixture(): Mapping[] {
  // How many attributes of each entity are mapped already.
  const done: Record<string, number> = {
    loan: 12,
    collateral: 6,
    repayment: 7,
    branch: 4,
    card: 0,
  };
  const numbered = CREDIT_ENTITIES.flatMap(([code]) =>
    Array.from({ length: done[code] }, (_, i) =>
      mapping(code, `attr_${i + 1}`, `core.${code.toUpperCase()}.COL_${i + 1}`, 0.95),
    ),
  );
  return [
    ...numbered,
    { ...mapping('customer', 'customer_id', 'core.CUSTOMERS.CIF_NO', 0.99), required: true },
    mapping('customer', 'segment', 'core.CUSTOMERS.CUST_TYPE', 0.93),
    mapping('customer', 'birth_date', 'core.CUSTOMERS.DOB', 0.97),
    mapping('customer', 'income_band', 'core.CUSTOMERS.INCOME_GRP', 0.61, false),
  ];
}

const item = (
  id: string,
  code: string,
  period: string,
  computed: number,
  filed: number,
  passed = true,
  cause: string | null = null,
) => ({
  id,
  item_kind: 'metric',
  item_code: code,
  period,
  computed_value: computed,
  filed_value: filed,
  delta: Math.round((computed - filed) * 1000) / 1000,
  passed,
  suggested_cause: cause,
});

function runsFixture(): Map<string, Run> {
  const credit: Run = {
    id: 'cal-credit-1',
    pack_install_id: INSTALL_ID,
    trigger: 'onboarding',
    periods: ['2026-08', '2026-09'],
    tolerance_pp: 0.1,
    status: 'failed',
    passed: 5,
    failed: 1,
    created_at: T0,
    finished_at: T0,
    items: [
      item('c1', 'LOAN_BALANCE', '2026-09', 182431, 182430),
      item('c2', 'LOAN_BALANCE', '2026-08', 180416, 180416),
      item('c3', 'NPL_RATIO', '2026-09', 1.85, 1.84),
      item('c4', 'NPL_RATIO', '2026-08', 1.63, 1.62),
      item('c5', 'LOAN_COUNT', '2026-09', 1284562, 1284562),
      item(
        'c6',
        'CARD_BALANCE',
        '2026-09',
        9721,
        9643,
        false,
        'Số đã báo cáo không tính 2.140 thẻ phụ có dư nợ; ánh xạ hiện tại gộp thẻ phụ vào thẻ chính.',
      ),
    ],
  };
  const override: Run = {
    ...credit,
    id: 'cal-ovr-npl',
    trigger: 'override',
    status: 'passed',
    passed: 2,
    failed: 0,
    items: [
      item('o1', 'NPL_RATIO', '2026-08', 1.62, 1.62),
      item('o2', 'NPL_RATIO', '2026-09', 1.84, 1.84),
    ],
  };
  return new Map([credit, override].map((r) => [r.id, r]));
}

function overridesFixture(): Map<string, Override> {
  const npl: Override = {
    id: 'ovr-npl-ratio',
    pack_install_id: INSTALL_ID,
    pack_metric_code: 'NPL_RATIO',
    expression: 'dư nợ nhóm 3–5\n÷ tổng dư nợ\nloại trừ cho vay nội bộ',
    scope_condition: 'phân khúc = bán lẻ',
    reason: 'Theo quy định nội bộ, khớp số báo cáo đã nộp',
    status: 'pending_review',
    approved_by: null,
    compliance_approved_by: null,
    calibration_run_id: 'cal-ovr-npl',
    created_at: T0,
    updated_at: T0,
  };
  return new Map([[npl.id, npl]]);
}

function upgradeFixture(): Upgrade {
  return {
    id: 'upg-retail-2026-4',
    pack_install_id: 'inst-retail',
    from_version_id: 'pv-retail-2026-3',
    to_version_id: 'pv-retail-2026-4',
    merge_result: {
      items: [
        {
          kind: 'metric',
          code: 'NPL_RATIO',
          outcome: 'conflict',
          options: ['keep_override', 'take_new', 'defer'],
          decision: null,
        },
        {
          kind: 'mapping',
          code: 'card.supplementary_flag',
          outcome: 'conflict',
          options: ['keep_override', 'take_new', 'defer'],
          decision: null,
        },
        { kind: 'metric', code: 'LOAN_GROWTH', outcome: 'take_new' },
        { kind: 'metric', code: 'CASA_RATIO', outcome: 'added' },
        { kind: 'metric', code: 'CARD_ACTIVE', outcome: 'removed' },
      ],
    },
    conflicts_open: 2,
    calibration_run_id: null,
    status: 'needs_decision',
    apply_from_period: null,
    rollback_until: null,
    approved_by: null,
    dry_run: false,
    created_at: T0,
    updated_at: T0,
  };
}

/* ─── Handlers ──────────────────────────────────────────────────────── */

export function createPackHandlers() {
  let packs = packsFixture();
  const versions = versionsFixture();
  let mappings = mappingsFixture();
  const runs = runsFixture();
  const overrides = overridesFixture();
  let upgrade = upgradeFixture();
  let latestRun: string | null = 'cal-credit-1';
  let installStatus = 'mapping';

  const installView = (): Schemas['PackInstallView'] => ({
    id: INSTALL_ID,
    data_space_id: SPACE,
    pack_version_id: INSTALL_VERSION,
    previous_pack_version_id: null,
    status: installStatus,
    activated_at: null,
    activated_by: null,
    proposal_id: null,
    created_at: T0,
    updated_at: T0,
  });

  const installDetail = (): Schemas['PackInstallDetail'] => {
    const entities = (versions.get(INSTALL_VERSION)!.entities ?? []).map((e) => {
      const attrs = e.attributes ?? [];
      const mapped = attrs.filter((a) => mappings.some((m) => m.attribute_id === a.id)).length;
      const required = attrs.filter((a) => a.required);
      return {
        code: e.code,
        name: e.name,
        mapped,
        total: attrs.length,
        required_total: required.length,
        required_mapped: required.filter((a) => mappings.some((m) => m.attribute_id === a.id))
          .length,
      };
    });
    const run = latestRun ? runs.get(latestRun)! : null;
    return {
      ...installView(),
      pack_id: 'pack-bank-credit-risk',
      pack_code: 'bank-credit-risk',
      pack_name: 'Ngân hàng · rủi ro tín dụng',
      version: '2026.3',
      entities,
      mapped: entities.reduce((n, e) => n + e.mapped, 0),
      total: entities.reduce((n, e) => n + e.total, 0),
      latest_calibration_run: run
        ? {
            id: run.id,
            status: run.status,
            trigger: run.trigger,
            passed: run.passed,
            failed: run.failed,
            created_at: run.created_at,
            finished_at: run.finished_at,
          }
        : null,
    };
  };

  const summary = (id: string | null | undefined) => {
    const run = id ? runs.get(id) : undefined;
    return run
      ? {
          id: run.id,
          status: run.status,
          trigger: run.trigger,
          passed: run.passed,
          failed: run.failed,
          created_at: run.created_at,
          finished_at: run.finished_at,
        }
      : null;
  };

  const upgradeItems = () =>
    ((upgrade.merge_result as { items: Record<string, unknown>[] }).items ?? []).map((i) => ({
      ...i,
    }));

  return [
    http.get('*/v1/packs', () => HttpResponse.json({ items: packs.map(packWire) })),
    http.post('*/v1/packs', async ({ request }) => {
      const body = (await request.json()) as Schemas['PackCreate'];
      if (!body.name?.trim() || !/^[a-z0-9][a-z0-9_.-]*$/.test(body.code ?? ''))
        return problem(422, 'validation_failed', 'Đặt tên gói.');
      const id = `pack-own-${packs.filter((p) => p.id.startsWith('pack-own')).length + 1}`;
      const pack: IndustryPack = {
        id,
        name: body.name,
        publisher: 'Ngân hàng Việt Nam',
        installed: null,
        latest: null,
        draft: null,
      };
      packs = [...packs, pack];
      const { display: _display, ...view } = packWire(pack);
      return HttpResponse.json(view, { status: 201 });
    }),
    http.post('*/v1/packs\\:import', () =>
      HttpResponse.json({ id: 'pv-life-2026-2' }, { status: 201 }),
    ),
    http.post('*/v1/packs/:id/versions', async ({ params, request }) => {
      const pack = packs.find((p) => p.id === params.id);
      if (!pack) return problem(404, 'not_found', 'Pack not found.');
      const body = (await request.json()) as Schemas['PackVersionCreate'];
      const content = body.manifest as Partial<Version>;
      const id = `pv-own-${[...versions.keys()].filter((k) => k.startsWith('pv-own')).length + 1}`;
      const draft = version(
        id,
        pack.id,
        body.version,
        { entities: 0, metrics: 0, skills: 0, templates: 0, rules: 0 },
        {
          status: 'draft',
          signature: '',
          signature_verified_at: null,
          released_at: null,
          min_core_version: body.min_core_version,
          entities: content.entities ?? [],
          metrics: content.metrics ?? [],
          rules: content.rules ?? [],
          skills: content.skills ?? [],
        },
      );
      versions.set(id, draft);
      packs = packs.map((p) =>
        p.id === pack.id ? { ...p, draft: { version: body.version, version_id: id } } : p,
      );
      const {
        entities: _e,
        metrics: _m,
        rules: _r,
        skills: _s,
        latest_check_run: _c,
        ...view
      } = draft;
      return HttpResponse.json(view, { status: 201 });
    }),
    http.get('*/v1/pack-versions/:id', ({ params }) => {
      const found = versions.get(String(params.id));
      return found
        ? HttpResponse.json(found)
        : problem(404, 'not_found', 'Không tìm thấy phiên bản gói.');
    }),
    http.post('*/v1/pack-versions/:action', ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      const target = versions.get(id);
      if (!target) return problem(404, 'not_found', 'Không tìm thấy phiên bản gói.');
      if (target.status !== 'draft') return problem(409, 'invalid_state', 'Không phải bản nháp.');
      if (verb === 'validate')
        return HttpResponse.json({ id: 'check-2', status: 'queued' }, { status: 202 });
      if (verb === 'publish') {
        if (!target.latest_check_run?.passed)
          return problem(409, 'invalid_state', 'Bản nháp chưa qua kiểm tra.');
        versions.set(id, { ...target, status: 'published' });
        return HttpResponse.json({ id });
      }
      return problem(404, 'not_found', 'Không có thao tác này.');
    }),
    http.post('*/v1/pack-installs', async ({ request }) => {
      const body = (await request.json()) as Schemas['PackInstallCreate'];
      if (!versions.has(body.pack_version_id))
        return problem(422, 'validation_failed', 'pack_version_id is not an imported version.');
      return HttpResponse.json(installView(), { status: 201 });
    }),
    http.get('*/v1/pack-installs/:id/mappings', ({ params, request }) => {
      if (params.id !== INSTALL_ID) return problem(404, 'not_found', 'Không tìm thấy lượt cài.');
      const entity = new URL(request.url).searchParams.get('entity');
      return HttpResponse.json({
        items: mappings.filter((m) => !entity || m.entity_code === entity),
        next_page_token: '',
      });
    }),
    http.put('*/v1/pack-installs/:id/mappings', async ({ params, request }) => {
      if (params.id !== INSTALL_ID) return problem(404, 'not_found', 'Không tìm thấy lượt cài.');
      const body = (await request.json()) as Schemas['MappingsPut'];
      const attrs = (versions.get(INSTALL_VERSION)!.entities ?? []).flatMap((e) =>
        (e.attributes ?? []).map((a) => ({ ...a, entity: e.code })),
      );
      const next: Mapping[] = [];
      for (const input of body.entity_mappings) {
        const attr = attrs.find((a) => a.id === input.attribute_id);
        if (!attr)
          return problem(
            422,
            'validation_failed',
            'An attribute is not in the installed pack version.',
          );
        const old = mappings.find((m) => m.attribute_id === input.attribute_id);
        next.push({
          ...(old ?? mapping(attr.entity, attr.code, input.column_expr, 1)),
          required: attr.required,
          dataset_id: input.dataset_id,
          column_expr: input.column_expr,
          origin: input.origin ?? 'confirmed',
          confirmed_by: USER,
        });
      }
      // PUT replaces the set: what was not sent closes.
      mappings = next;
      return HttpResponse.json(
        mappings.map(
          ({
            entity_code: _e,
            attribute_code: _a,
            attribute_name: _n,
            required: _r,
            code_mappings: _c,
            ...m
          }) => m,
        ),
      );
    }),
    http.get('*/v1/pack-installs/:id', ({ params }) =>
      params.id === INSTALL_ID
        ? HttpResponse.json(installDetail())
        : problem(404, 'not_found', 'Không tìm thấy lượt cài.'),
    ),
    http.post('*/v1/pack-installs/:action', ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      if (id !== INSTALL_ID) return problem(404, 'not_found', 'Không tìm thấy lượt cài.');
      if (verb === 'calibrate') {
        const run: Run = {
          ...runs.get('cal-credit-1')!,
          id: `cal-credit-${runs.size + 1}`,
          status: 'running',
          passed: 0,
          failed: 0,
          finished_at: null,
          items: [],
        };
        runs.set(run.id, run);
        latestRun = run.id;
        installStatus = 'calibrating';
        return HttpResponse.json({ id: run.id, status: 'running' }, { status: 202 });
      }
      if (verb === 'activate') {
        const run = latestRun ? runs.get(latestRun) : undefined;
        if (!run || run.status === 'running')
          return problem(
            422,
            'business_rule_violated',
            'The numbers are not reconciled yet (BR-36).',
          );
        const regulatory = new Set(
          (versions.get(INSTALL_VERSION)!.metrics ?? [])
            .filter((m) => m.in_regulatory_report)
            .map((m) => m.code),
        );
        const explained = new Set(
          [...overrides.values()]
            .filter((o) => o.status === 'active')
            .map((o) => o.pack_metric_code),
        );
        const blocking = (run.items ?? []).filter(
          (i) => !i.passed && regulatory.has(i.item_code) && !explained.has(i.item_code),
        );
        if (blocking.length)
          return problem(
            422,
            'business_rule_violated',
            `Chỉ số thuộc báo cáo gửi cơ quan quản lý còn lệch: ${blocking.map((i) => i.item_code).join(', ')} (BR-36).`,
          );
        installStatus = 'pending_activation';
        return HttpResponse.json(installView());
      }
      return problem(404, 'not_found', 'Không có thao tác này.');
    }),
    http.get('*/v1/calibration-runs/:id', ({ params }) => {
      const run = runs.get(String(params.id));
      return run ? HttpResponse.json(run) : problem(404, 'not_found', 'Calibration run not found.');
    }),
    http.post('*/v1/metric-overrides', async ({ request }) => {
      const body = (await request.json()) as Schemas['MetricOverrideCreate'];
      if (!body.reason?.trim())
        return problem(422, 'validation_failed', 'An override needs its reason (BR-129).');
      const metrics = versions.get(INSTALL_VERSION)!.metrics ?? [];
      if (!metrics.some((m) => m.code === body.pack_metric_code))
        return problem(
          422,
          'validation_failed',
          'pack_metric_code is not a metric of the installed pack.',
        );
      const row: Override = {
        id: `ovr-${overrides.size + 1}`,
        pack_install_id: body.pack_install_id,
        pack_metric_code: body.pack_metric_code,
        expression: body.expression,
        scope_condition: body.scope_condition ?? null,
        reason: body.reason.trim(),
        status: 'pending_review',
        approved_by: null,
        compliance_approved_by: null,
        calibration_run_id: null,
        created_at: T0,
        updated_at: T0,
      };
      overrides.set(row.id, row);
      return HttpResponse.json(row, { status: 201 });
    }),
    http.get('*/v1/metric-overrides/:id', ({ params }) => {
      const row = overrides.get(String(params.id));
      if (!row) return problem(404, 'not_found', 'Metric override not found.');
      const m = (versions.get(INSTALL_VERSION)!.metrics ?? []).find(
        (x) => x.code === row.pack_metric_code,
      );
      return HttpResponse.json({
        ...row,
        metric_name: m?.name ?? null,
        base_expression: m?.expression ?? null,
        in_regulatory_report: Boolean(m?.in_regulatory_report),
        awaiting:
          row.status === 'pending_review' ? (row.approved_by ? 'compliance' : 'approve') : null,
        calibration_run: summary(row.calibration_run_id),
      } satisfies Schemas['MetricOverrideDetail']);
    }),
    http.get('*/v1/pack-upgrades/:id', ({ params }) => {
      if (params.id !== upgrade.id)
        return problem(404, 'not_found', 'Không tìm thấy lượt nâng cấp.');
      const items = upgradeItems();
      return HttpResponse.json({
        ...upgrade,
        conflicts: items.filter((i) => i.outcome === 'conflict'),
        decisions: items.filter((i) => i.decision),
        calibration_run: summary(upgrade.calibration_run_id),
      });
    }),
    http.put('*/v1/pack-upgrades/:id/decisions', async ({ params, request }) => {
      if (params.id !== upgrade.id)
        return problem(404, 'not_found', 'Không tìm thấy lượt nâng cấp.');
      if (upgrade.status !== 'needs_decision')
        return problem(409, 'invalid_state', `The upgrade is ${upgrade.status}.`);
      const body = (await request.json()) as Schemas['DecisionsPut'];
      const items = upgradeItems();
      for (const d of body.decisions) {
        const found = items.find(
          (i) => i.outcome === 'conflict' && i.code === d.code && i.kind === (d.kind ?? 'metric'),
        );
        if (!found)
          return problem(
            422,
            'validation_failed',
            `${d.kind} ${d.code} is not a conflict of this upgrade.`,
          );
        found.decision = d.decision;
      }
      const open = items.filter((i) => i.outcome === 'conflict' && !i.decision).length;
      // The mock finishes the test run at once: with nothing open it waits for approval.
      const run: Run = {
        ...runs.get('cal-credit-1')!,
        id: 'cal-upgrade-1',
        pack_install_id: upgrade.pack_install_id,
        trigger: 'upgrade',
        status: 'passed',
        passed: 6,
        failed: 0,
        items: [],
      };
      if (!open) runs.set(run.id, run);
      upgrade = {
        ...upgrade,
        merge_result: { items },
        conflicts_open: open,
        status: open ? 'needs_decision' : 'pending_approval',
        calibration_run_id: open ? null : run.id,
      };
      return HttpResponse.json(upgrade);
    }),
    http.post('*/v1/pack-upgrades/:action', ({ params }) => {
      const [id, verb] = String(params.action).split(':');
      if (id !== upgrade.id) return problem(404, 'not_found', 'Không tìm thấy lượt nâng cấp.');
      if (verb === 'apply') {
        if (upgrade.status !== 'pending_approval')
          return problem(409, 'invalid_state', `The upgrade is ${upgrade.status}.`);
        upgrade = { ...upgrade, status: 'applied', approved_by: USER };
        return HttpResponse.json(upgrade);
      }
      return problem(404, 'not_found', 'Không có thao tác này.');
    }),
  ];
}
