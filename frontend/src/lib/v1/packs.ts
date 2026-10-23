import { pathId, v1Get, v1Upload, type Page, api, ok } from './client';
import type { components } from './schema';

/*
 * Gói ngành (pack service, P09, UC-09-01…05): industry packs — canonical entities, metrics,
 * rules, skills and template agents for one industry. Every call here is an A2 endpoint (or one
 * added on 12/10/2026: one install, its mappings, a calibration run, an override, an upgrade,
 * POST /v1/packs); each `readX` turns the api's row into what the screen draws. What the api does
 * not carry stays null and the screen shows "—" or hides that line. Approving an override or an
 * activation happens in Cần bạn, not here. Packs are organisation-wide: no X-Data-Space needed;
 * installs, mappings, runs, overrides and upgrades are of the current Data Space.
 */

type Schemas = components['schemas'];

const text = (value: number | string | null | undefined) =>
  value === null || value === undefined ? '—' : String(value);

/** Every page of a paged list (A1.6 page tokens), for the sets a PUT replaces. */
async function allPages<T>(path: string, query: Record<string, string> = {}): Promise<T[]> {
  const items: T[] = [];
  let token: string | undefined;
  do {
    const page = await v1Get<Page<T>>(path, { ...query, page_size: 500, page_token: token });
    items.push(...page.items);
    token = page.next_page_token || undefined;
  } while (token);
  return items;
}

/* ─── Danh sách ─────────────────────────────────────────────────────── */

/** A2: GET /v1/packs — IndustryPackView, with what the list shows about each. */
export interface IndustryPack {
  id: string;
  name: string;
  publisher: string;
  /**
   * The install of this pack in the current Data Space. IndustryPackView has none yet (the plan
   * adds an `installed` field to GET /v1/packs); null without it.
   */
  installed: {
    install_id: string;
    version: string;
    /** "Ngân hàng Việt Nam · 3 Data Space" */
    scope_text: string;
    mapped: number;
    mapped_total: number;
    overrides: number;
    first_override_id: string | null;
    status: 'active' | 'onboarding';
    /** A newer version is out; the upgrade it would be. */
    upgrade: { to_version: string; upgrade_id: string } | null;
  } | null;
  /** The newest published version; IndustryPackView lists none, so null without it. */
  latest: { version: string; version_id: string; content_text: string } | null;
  /** The draft version being authored (Biên soạn gói); null without it. */
  draft: { version: string; version_id: string } | null;
}

export type PackWire = Schemas['IndustryPackView'] & {
  /** What IndustryPackView lacks (installed, latest, draft), which the mock sends. */
  display?: Partial<IndustryPack>;
};

export function readPack(raw: PackWire): IndustryPack {
  return {
    id: raw.id,
    name: raw.name,
    publisher: raw.publisher,
    installed: null,
    latest: null,
    draft: null,
    ...raw.display,
  };
}

/*
 * GET /v1/packs: name and publisher. Without what is installed and the versions, a pack can be
 * read about but not installed, upgraded or authored from the list.
 */
export const listPacks = async () => {
  const page = await ok<Page<PackWire>>(api.GET('/v1/packs'));
  return { items: page.items.map(readPack) };
};

const packName = async (packId: string) =>
  (await listPacks()).items.find((p) => p.id === packId)?.name ?? '—';

/*
 * The organisation's own domain pack ("Hộp thoại · createPack", 2095:8808): POST /v1/packs, then
 * its first draft with POST /v1/packs/{id}/versions, opened in Biên soạn gói. Started from a pack
 * version, the draft copies the parts of that version's content the author keeps; started from the
 * organisation's current model, it is empty (the api has no export of the current model).
 */
const INCLUDES: {
  match: RegExp;
  take: (v: PackVersionWire) => Partial<Record<string, unknown[]>>;
}[] = [
  { match: /thực thể/i, take: (v) => ({ entities: v.entities ?? [] }) },
  { match: /chỉ số/i, take: (v) => ({ metrics: v.metrics ?? [] }) },
  { match: /luật/i, take: (v) => ({ rules: v.rules ?? [] }) },
];
const SKILL_KINDS: { match: RegExp; kind: string }[] = [
  { match: /kỹ năng/i, kind: 'skill' },
  { match: /agent mẫu/i, kind: 'agent_template' },
];
// The api's DEFAULT_CORE_VERSION (modules/pack/rules.py): what a draft needs when it has no base.
const CORE_VERSION = '3.0.0';

/** "Tín dụng tiêu dùng · nội bộ" → "tin-dung-tieu-dung-noi-bo", the code PackCreate wants. */
export function packCode(name: string): string {
  const code = name
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/đ/gi, 'd')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return code || 'goi-rieng';
}

export function draftManifest(base: PackVersionWire | null, includes: string[]) {
  if (!base) return {};
  const manifest: Record<string, unknown[]> = {};
  for (const part of includes) {
    for (const rule of INCLUDES)
      if (rule.match.test(part)) Object.assign(manifest, rule.take(base));
  }
  const kinds = SKILL_KINDS.filter((k) => includes.some((part) => k.match.test(part))).map(
    (k) => k.kind,
  );
  if (kinds.length) manifest.skills = (base.skills ?? []).filter((s) => kinds.includes(s.kind));
  return manifest;
}

export const createPack = async (body: {
  name: string;
  based_on_version_id: string | null;
  includes: string[];
}) => {
  const base = body.based_on_version_id
    ? await ok<PackVersionWire>(
        api.GET('/v1/pack-versions/{id}', { params: { path: { id: body.based_on_version_id } } }),
      )
    : null;
  const pack = await ok<Schemas['IndustryPackView']>(
    api.POST('/v1/packs', {
      body: {
        code: packCode(body.name),
        name: body.name,
      } satisfies Schemas['PackCreate'],
    }),
  );
  const draft = await ok<Schemas['PackVersionView']>(
    api.POST('/v1/packs/{id}/versions', {
      params: { path: { id: pack.id } },
      body: {
        version: '0.1',
        // The schema types the manifest as an empty object; the api reads entities, metrics, rules, skills.
        manifest: draftManifest(base, body.includes) as Schemas['PackVersionCreate']['manifest'],
        min_core_version: base?.min_core_version ?? CORE_VERSION,
      } satisfies Schemas['PackVersionCreate'],
    }),
  );
  return { id: pack.id, draft_version_id: draft.id };
};

/** A2: POST /v1/packs:import (multipart) — the signature is checked first (BR-128). */
export const importPack = async (file: File) => {
  const form = new FormData();
  form.append('file', file);
  return v1Upload<{ id: string }>('/packs:import', form);
};

/* ─── Cài gói ───────────────────────────────────────────────────────── */

export type PackVersionWire = Schemas['PackVersionDetail'];

/** A2: GET /v1/pack-versions/{id} — what a version adds and the checks before installing it. */
export interface PackVersion {
  id: string;
  pack_name: string;
  version: string;
  status: string;
  counts: { label: string; value: number }[];
  checks: { item: string; result: string; ok: boolean }[];
}

const skillsOf = (raw: PackVersionWire, kind: string) =>
  (raw.skills ?? []).filter((s) => s.kind === kind).length;

function versionCounts(raw: PackVersionWire) {
  return [
    { label: 'Thực thể', value: (raw.entities ?? []).length },
    { label: 'Chỉ số', value: (raw.metrics ?? []).length },
    { label: 'Kỹ năng', value: skillsOf(raw, 'skill') },
    { label: 'Agent mẫu', value: skillsOf(raw, 'agent_template') },
    { label: 'Luật kiểm tra', value: (raw.rules ?? []).length },
  ];
}

export function readPackVersion(raw: PackVersionWire, packName: string): PackVersion {
  const breaking = Array.isArray(raw.breaking_changes) ? raw.breaking_changes.length : 0;
  return {
    id: raw.id,
    pack_name: packName,
    version: raw.version,
    status: raw.status,
    counts: versionCounts(raw),
    checks: [
      {
        item: 'Chữ ký của nhà phát hành',
        result: raw.signature_verified_at ? 'Hợp lệ' : 'Chưa xác minh',
        ok: Boolean(raw.signature_verified_at),
      },
      {
        item: 'Thay đổi phá vỡ tương thích',
        result: breaking ? `${breaking} thay đổi, xem hướng dẫn chuyển đổi` : 'Không có',
        ok: breaking === 0,
      },
    ],
  };
}

export const getPackVersion = async (id: string) => {
  const raw = await ok<PackVersionWire>(
    api.GET('/v1/pack-versions/{id}', { params: { path: { id } } }),
  );
  return readPackVersion(raw, await packName(raw.pack_id));
};

/** A2: POST /v1/pack-installs — into the current Data Space (X-Data-Space). */
export const installPack = async (packVersionId: string) =>
  ok<Schemas['PackInstallView']>(
    api.POST('/v1/pack-installs', {
      body: {
        pack_version_id: packVersionId,
      },
    }),
  );

/* ─── Đưa vào ngành ─────────────────────────────────────────────────── */

/** GET /v1/pack-installs/{id} */
export interface PackInstall {
  id: string;
  pack_version_id: string;
  title: string;
  summary_text: string;
  status: string;
  latest_calibration_run_id: string | null;
  entities: { key: string; name: string; mapped: number; total: number }[];
}

export type PackInstallWire = Schemas['PackInstallDetail'];
export type MappingWire = Schemas['MappingView'];

export function readPackInstall(raw: PackInstallWire): PackInstall {
  return {
    id: raw.id,
    pack_version_id: raw.pack_version_id,
    title: `${raw.pack_name} · ${raw.version}`,
    summary_text: `${raw.mapped ?? 0}/${raw.total ?? 0} thuộc tính đã ánh xạ`,
    status: raw.status,
    latest_calibration_run_id: raw.latest_calibration_run?.id ?? null,
    entities: (raw.entities ?? []).map((entity) => ({
      key: entity.code,
      name: entity.name,
      mapped: entity.mapped,
      total: entity.total,
    })),
  };
}

export const getPackInstall = async (id: string) =>
  readPackInstall(
    await ok<PackInstallWire>(api.GET('/v1/pack-installs/{id}', { params: { path: { id } } })),
  );

/** The installs still onboarding, from GET /v1/packs (installed); empty while it lacks them. */
export const listPackInstalls = async () => ({
  items: (await listPacks()).items.flatMap((p) =>
    p.installed?.status === 'onboarding' ? [{ id: p.installed.install_id }] : [],
  ),
});

/**
 * One entity's attributes (from the installed version) beside their current mappings
 * (GET /v1/pack-installs/{id}/mappings?entity=). An attribute with no mapping has no row in the
 * api, so it shows with no column.
 */
export interface EntityMappings {
  entity: string;
  name: string;
  main_table: string;
  rows: {
    attribute_id: string;
    attr: string;
    required: boolean;
    column: string | null;
    dataset_id: string | null;
    /** The columns to choose from: the api gives only the current one, no alternatives. */
    options: string[];
    confidence: number | null;
    note: string;
    unsure: boolean;
    approved: boolean;
  }[];
}

export const NOT_APPLICABLE = '__not_applicable__';

export function readEntityMappings(
  version: PackVersionWire,
  items: MappingWire[],
  entity: string,
): EntityMappings {
  const spec = (version.entities ?? []).find((e) => e.code === entity);
  const byAttr = new Map(items.map((m) => [m.attribute_id, m]));
  const first = items.find((m) => m.column_expr)?.column_expr;
  return {
    entity,
    name: spec?.name ?? entity,
    main_table: first && first.includes('.') ? first.slice(0, first.lastIndexOf('.')) : '—',
    rows: (spec?.attributes ?? []).map((attr) => {
      const m = byAttr.get(attr.id);
      const confidence = m?.confidence === null || m === undefined ? null : Number(m.confidence);
      const suggested = m !== undefined && m.confirmed_by === null;
      return {
        attribute_id: attr.id,
        attr: attr.code,
        required: attr.required,
        column: m?.column_expr || null,
        dataset_id: m?.dataset_id ?? null,
        options: m?.column_expr ? [m.column_expr] : [],
        confidence,
        note: !m
          ? 'Chưa có cột phù hợp'
          : suggested
            ? 'Đề xuất từ agent'
            : m.origin === 'manual'
              ? 'Ánh xạ tay'
              : 'Đã xác nhận',
        unsure: !m || (suggested && (confidence === null || confidence < 0.9)),
        approved: m !== undefined && !suggested,
      };
    }),
  };
}

export const getEntityMappings = async (install: PackInstall, entity: string) => {
  const [version, items] = await Promise.all([
    ok<PackVersionWire>(
      api.GET('/v1/pack-versions/{id}', { params: { path: { id: install.pack_version_id } } }),
    ),
    allPages<MappingWire>(`/pack-installs/${pathId(install.id)}/mappings`, { entity }),
  ]);
  return readEntityMappings(version, items, entity);
};

/*
 * A2: PUT /v1/pack-installs/{id}/mappings replaces the install's whole set and confirms every
 * mapping it is sent. So the body is the confirmed mappings of the other entities, unchanged,
 * plus the rows of this entity the person approved; an unconfirmed suggestion not approved here
 * closes (BR-37 keeps it as history) rather than being confirmed by someone who never saw it.
 * "Không áp dụng" sends no mapping for that attribute.
 */
export const saveMappings = async (
  installId: string,
  entity: string,
  approved: {
    attribute_id: string;
    dataset_id: string;
    column_expr: string;
    confidence: number | null;
  }[],
) => {
  const current = await allPages<MappingWire>(`/pack-installs/${pathId(installId)}/mappings`);
  const kept = current.filter((m) => m.entity_code !== entity && m.confirmed_by !== null);
  const body: Schemas['MappingsPut'] = {
    entity_mappings: [
      ...kept.map((m) => ({
        attribute_id: m.attribute_id,
        dataset_id: m.dataset_id,
        column_expr: m.column_expr,
        confidence: m.confidence,
        origin: m.origin === 'manual' ? ('manual' as const) : ('confirmed' as const),
      })),
      ...approved.map((m) => ({ ...m, origin: 'confirmed' as const })),
    ],
  };
  await ok<Schemas['EntityMappingView'][]>(
    api.PUT('/v1/pack-installs/{id}/mappings', { params: { path: { id: installId } }, body }),
  );
  return { approved: approved.length };
};

/** The last calibration run of an install (GET /v1/calibration-runs/{id}). */
export interface Calibration {
  install_id: string;
  install_title: string;
  /** null before the first :calibrate; "running" until the worker writes its items. */
  run_status: string | null;
  matched: number;
  total: number;
  tolerance_text: string;
  periods: string[];
  rows: {
    id: string;
    metric: string;
    period: string;
    computed: string;
    reported: string;
    diff: string;
    ok: boolean;
  }[];
  /** A failed metric: what the Agent Đối soát suggests as the cause. */
  findings: { row_id: string; metric_code: string; title: string; text: string }[];
  after_text: string;
}

export type CalibrationRunWire = Schemas['CalibrationRunView'];

const AFTER_CALIBRATION =
  'Gửi duyệt kích hoạt. Chỉ số thuộc báo cáo gửi cơ quan quản lý còn lệch phải có hiệu chỉnh đã duyệt; chỉ số khác không chặn kích hoạt (BR-36).';

export function readCalibrationRun(
  raw: CalibrationRunWire,
  install: Pick<PackInstall, 'id' | 'title'>,
): Calibration {
  const items = raw.items ?? [];
  return {
    install_id: install.id,
    install_title: install.title,
    run_status: raw.status,
    matched: raw.passed,
    total: raw.passed + raw.failed,
    tolerance_text: `sai số cho phép ±${raw.tolerance_pp} điểm phần trăm`,
    periods: raw.periods,
    rows: items.map((item) => ({
      id: item.id,
      metric: item.item_code,
      period: item.period ?? '—',
      computed: text(item.computed_value),
      reported: text(item.filed_value),
      diff: text(item.delta),
      ok: item.passed,
    })),
    findings: items
      .filter((item) => !item.passed && item.item_kind === 'metric')
      .map((item) => ({
        row_id: item.id,
        metric_code: item.item_code,
        title: `${item.item_code}${item.period ? ` · ${item.period}` : ''} lệch ${text(item.delta)}`,
        text: item.suggested_cause ?? 'Agent Đối soát chưa tìm được nguyên nhân.',
      })),
    after_text: AFTER_CALIBRATION,
  };
}

export const getCalibration = async (installId: string): Promise<Calibration> => {
  const install = await getPackInstall(installId);
  if (!install.latest_calibration_run_id)
    return {
      install_id: install.id,
      install_title: install.title,
      run_status: null,
      matched: 0,
      total: 0,
      tolerance_text: '',
      periods: [],
      rows: [],
      findings: [],
      after_text: AFTER_CALIBRATION,
    };
  const run = await ok<CalibrationRunWire>(
    api.GET('/v1/calibration-runs/{id}', {
      params: { path: { id: install.latest_calibration_run_id } },
    }),
  );
  return readCalibrationRun(run, install);
};

/** A2: :calibrate (202) — reconcile again, e.g. after the mappings changed (UC-09-02 5a). */
export const calibratePackInstall = async (installId: string) =>
  ok<{ id: string; status: string }>(
    api.POST('/v1/pack-installs/{id}:calibrate', { params: { path: { id: installId } }, body: {} }),
  );

/** A2: :activate — 422 while a regulatory metric differs with no approved override (BR-36). */
export const activatePackInstall = async (installId: string) =>
  ok<Schemas['PackInstallView']>(
    api.POST('/v1/pack-installs/{id}:activate', { params: { path: { id: installId } } }),
  );

/* ─── Hiệu chỉnh chỉ số ─────────────────────────────────────────────── */

/**
 * A2: POST /v1/metric-overrides — creating it is sending it for approval (UC-09-03 step 4); the
 * reviewer decides in Cần bạn. A gap in the calibration is accepted only this way (BR-36).
 */
export const createMetricOverride = async (body: Schemas['MetricOverrideCreate']) =>
  ok<Schemas['MetricOverrideView']>(api.POST('/v1/metric-overrides', { body }));

/** GET /v1/metric-overrides/{id} */
export interface MetricOverride {
  id: string;
  metric_code: string;
  status: 'submitted' | 'certified' | 'superseded';
  /** Who it waits for while submitted: the approver, then compliance for a regulatory metric. */
  awaiting_text: string | null;
  summary_text: string;
  base_expression: string;
  org_expression: string;
  changes: { change: string; reason: string }[];
  upgrade_note: string;
  /** Its calibration run's rows for this metric; null before one ran. */
  trial: { period: string; reported: string; org: string }[] | null;
  trial_note: string | null;
}

export type MetricOverrideWire = Schemas['MetricOverrideDetail'];

const OVERRIDE_STATUS: Record<string, MetricOverride['status']> = {
  pending_review: 'submitted',
  active: 'certified',
  superseded: 'superseded',
};

export function readMetricOverride(
  raw: MetricOverrideWire,
  run: CalibrationRunWire | null = null,
): MetricOverride {
  const rows = (run?.items ?? []).filter((item) => item.item_code === raw.pack_metric_code);
  return {
    id: raw.id,
    metric_code: raw.pack_metric_code,
    status: OVERRIDE_STATUS[raw.status] ?? 'submitted',
    awaiting_text:
      raw.awaiting === 'approve'
        ? 'Chờ người phụ trách chỉ số duyệt trong Cần bạn.'
        : raw.awaiting === 'compliance'
          ? 'Đã duyệt; chỉ số thuộc báo cáo gửi cơ quan quản lý nên chờ thêm bộ phận tuân thủ.'
          : null,
    summary_text: `${raw.metric_name ?? raw.pack_metric_code}${raw.in_regulatory_report ? ' · thuộc báo cáo gửi cơ quan quản lý' : ''}`,
    base_expression: raw.base_expression ?? '—',
    org_expression: raw.expression,
    changes: [
      { change: 'Công thức của tổ chức', reason: raw.reason },
      ...(raw.scope_condition
        ? [{ change: `Phạm vi: ${raw.scope_condition}`, reason: raw.reason }]
        : []),
    ],
    upgrade_note:
      'Hiệu chỉnh vẫn liên kết với công thức gốc của gói. Khi gói đổi công thức gốc, người phụ trách chỉ số chọn giữ hiệu chỉnh hay theo bản mới ở màn Nâng cấp gói.',
    trial: run
      ? rows.map((item) => ({
          period: item.period ?? '—',
          reported: text(item.filed_value),
          org: text(item.computed_value),
        }))
      : null,
    trial_note: raw.calibration_run
      ? `Đối chiếu với số đã nộp: ${raw.calibration_run.passed}/${raw.calibration_run.passed + raw.calibration_run.failed} khớp.`
      : null,
  };
}

export const getMetricOverride = async (id: string) => {
  const raw = await ok<MetricOverrideWire>(
    api.GET('/v1/metric-overrides/{id}', { params: { path: { id } } }),
  );
  const run = raw.calibration_run_id
    ? await ok<CalibrationRunWire>(
        api.GET('/v1/calibration-runs/{id}', { params: { path: { id: raw.calibration_run_id } } }),
      )
    : null;
  return readMetricOverride(raw, run);
};

/* ─── Nâng cấp gói ──────────────────────────────────────────────────── */

export type UpgradeChoice = Schemas['UpgradeDecision']['decision'];

export const UPGRADE_CHOICES: { value: UpgradeChoice; label: string }[] = [
  { value: 'keep_override', label: 'Giữ của tổ chức' },
  { value: 'take_new', label: 'Dùng bản mới' },
  { value: 'defer', label: 'Để sau' },
];

/** GET /v1/pack-upgrades/{id} — the three-way merge, its test run and where it stands. */
export interface PackUpgrade {
  id: string;
  pack_name: string;
  from_version: string;
  to_version: string;
  summary_text: string;
  /** needs_decision → testing → pending_approval → applied (or rolled_back). */
  status: string;
  conflicts: {
    id: string;
    kind: 'metric' | 'mapping';
    element: string;
    /** The old pack's, the new pack's and the organisation's text; the merge item has none. */
    old: string | null;
    new: string | null;
    org: string | null;
    options: { value: UpgradeChoice; label: string }[];
    /** The choice already recorded, if any. */
    decision: UpgradeChoice | null;
  }[];
  auto: { element: string; text: string }[];
  skipped: { element: string; reason: string }[];
  /** The test run against the filed numbers (step 5); null before it ran. */
  test: { matched: number; total: number; status: string } | null;
}

export type PackUpgradeWire = Schemas['PackUpgradeDetail'];

const OUTCOME: Record<string, string> = {
  take_new: 'Theo bản gói mới',
  added: 'Mới trong gói',
  keep_override: 'Giữ hiệu chỉnh của tổ chức, gói không đổi chỉ số này',
};

const record = (value: unknown) =>
  typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : {};

const isChoice = (value: unknown): value is UpgradeChoice =>
  UPGRADE_CHOICES.some((c) => c.value === value);

export function readPackUpgrade(
  raw: PackUpgradeWire,
  names: { pack_name: string; from_version: string; to_version: string },
): PackUpgrade {
  const items = Array.isArray(record(raw.merge_result).items)
    ? (record(raw.merge_result).items as unknown[]).map(record)
    : [];
  const element = (item: Record<string, unknown>) =>
    `${item.kind === 'mapping' ? 'Ánh xạ' : 'Chỉ số'} ${String(item.code ?? '—')}`;
  const conflicts = items
    .filter((item) => item.outcome === 'conflict')
    .map((item) => {
      const allowed = Array.isArray(item.options)
        ? item.options
        : UPGRADE_CHOICES.map((c) => c.value);
      return {
        id: String(item.code ?? '—'),
        kind: item.kind === 'mapping' ? ('mapping' as const) : ('metric' as const),
        element: element(item),
        old: null,
        new: null,
        org: null,
        options: UPGRADE_CHOICES.filter((c) => allowed.includes(c.value)),
        decision: isChoice(item.decision) ? item.decision : null,
      };
    });
  const skipped = items
    .filter((item) => item.outcome === 'removed')
    .map((item) => ({ element: element(item), reason: 'Gói mới đã bỏ thành phần này' }));
  const auto = items
    .filter((item) => item.outcome && item.outcome !== 'conflict' && item.outcome !== 'removed')
    .map((item) => ({
      element: element(item),
      text: OUTCOME[String(item.outcome)] ?? String(item.outcome),
    }));
  const run = raw.calibration_run;
  return {
    id: raw.id,
    ...names,
    summary_text: `${auto.length} tự gộp · ${conflicts.length} cần chọn · ${skipped.length} bị bỏ qua`,
    status: raw.status,
    conflicts,
    auto,
    skipped,
    test: run ? { matched: run.passed, total: run.passed + run.failed, status: run.status } : null,
  };
}

export const getPackUpgrade = async (id: string) => {
  const raw = await ok<PackUpgradeWire>(
    api.GET('/v1/pack-upgrades/{id}', { params: { path: { id } } }),
  );
  const [from, to] = await Promise.all(
    [raw.from_version_id, raw.to_version_id].map((v) =>
      ok<PackVersionWire>(api.GET('/v1/pack-versions/{id}', { params: { path: { id: v } } })),
    ),
  );
  return readPackUpgrade(raw, {
    pack_name: await packName(to.pack_id),
    from_version: from.version,
    to_version: to.version,
  });
};

/** A2: PUT decisions — with none left open the upgrade goes to its test run (step 5). */
export const decidePackUpgrade = async (
  id: string,
  decisions: { code: string; kind: 'metric' | 'mapping'; decision: UpgradeChoice }[],
) =>
  ok<Schemas['PackUpgradeView']>(
    api.PUT('/v1/pack-upgrades/{id}/decisions', {
      params: { path: { id } },
      body: {
        decisions,
      } satisfies Schemas['DecisionsPut'],
    }),
  );

/** A2: :apply — after the test run passed, by someone other than who started it. */
export const applyPackUpgrade = async (id: string) =>
  ok<Schemas['PackUpgradeView']>(
    api.POST('/v1/pack-upgrades/{id}:apply', {
      params: { path: { id } },
      body: {
        apply_from_period: null,
      },
    }),
  );

/* ─── Biên soạn gói ─────────────────────────────────────────────────── */

/** A2: GET /v1/pack-versions/{id} — a draft version and its last :validate run. */
export interface PackDraft {
  id: string;
  pack_name: string;
  version: string;
  /** The version it builds on; the api does not record it, so null. */
  base_version: string | null;
  status: string;
  summary_text: string;
  /** What it holds; how much changed since its base is not in the api (null). */
  sections: { label: string; changed: number | null; total: number }[];
  test: {
    run_text: string;
    kpis: { label: string; value: string; tone: 'success' | 'warning' | 'neutral' }[];
    failures: { item: string; kind: string; detail: string }[];
  } | null;
  conditions: { text: string; ok: boolean }[];
  after_text: string;
}

const CHECKS: Record<string, string> = {
  legal_basis: 'Căn cứ pháp lý của chỉ số',
  code_reuse: 'Mã chỉ số dùng lại (BR-131)',
  breaking_changes_without_guide: 'Hướng dẫn cho thay đổi phá vỡ',
  sample_datasets: 'Bộ dữ liệu mẫu',
  sample_questions: 'Câu hỏi mẫu',
};

const asText = (value: unknown) => (typeof value === 'string' ? value : JSON.stringify(value));

export function readPackDraft(raw: PackVersionWire, packName: string): PackDraft {
  const run = raw.latest_check_run ?? null;
  const checks = Object.entries(record(run?.checks));
  return {
    id: raw.id,
    pack_name: packName,
    version: raw.version,
    base_version: null,
    status: raw.status,
    summary_text: raw.release_notes ?? 'Chưa có ghi chú phát hành',
    sections: versionCounts(raw).map((c) => ({ label: c.label, changed: null, total: c.value })),
    test: run
      ? {
          run_text: `chạy ${new Date(run.created_at).toLocaleString('vi-VN')}`,
          kpis: checks.map(([key, value]) => {
            const failed = Array.isArray(value) ? value.length : 0;
            return {
              label: CHECKS[key] ?? key,
              value: Array.isArray(value)
                ? failed
                  ? `${failed} chưa đạt`
                  : 'Đạt'
                : value === 'skipped'
                  ? 'Bỏ qua'
                  : asText(value),
              tone: failed ? ('warning' as const) : ('neutral' as const),
            };
          }),
          failures: checks.flatMap(([key, value]) =>
            Array.isArray(value)
              ? value.map((entry) => ({
                  item: asText(entry),
                  kind: CHECKS[key] ?? key,
                  detail: '—',
                }))
              : [],
          ),
        }
      : null,
    // :publish answers 409 until the last check run passed.
    conditions: [{ text: 'Kiểm thử trước phát hành đạt', ok: Boolean(run?.passed) }],
    after_text:
      'Phát hành ký số phiên bản. Tổ chức đang dùng bản cũ nhận thông báo có bản mới; không tổ chức nào được nâng cấp tự động.',
  };
}

export const getPackDraft = async (id: string) => {
  const raw = await ok<PackVersionWire>(
    api.GET('/v1/pack-versions/{id}', { params: { path: { id } } }),
  );
  return readPackDraft(raw, await packName(raw.pack_id));
};

/** The drafts being authored, from GET /v1/packs (draft); empty while it lacks them. */
export const listPackDrafts = async () => ({
  items: (await listPacks()).items.flatMap((p) =>
    p.draft ? [{ id: p.draft.version_id, pack_name: p.name, version: p.draft.version }] : [],
  ),
});

/** A2: :validate (202) */
export const validatePackVersion = async (id: string) =>
  ok<unknown>(api.POST('/v1/pack-versions/{id}:validate', { params: { path: { id } } }));

/** A2: :publish — 409 until checks pass, 422 when a metric lacks grounds (BR-131). */
export const publishPackVersion = async (id: string) =>
  ok<{ id: string }>(api.POST('/v1/pack-versions/{id}:publish', { params: { path: { id } } }));
