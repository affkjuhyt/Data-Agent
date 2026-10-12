import { pathId, v1Get, v1Send, v1Upload, type Page } from './client';
import type { components } from './schema';

/*
 * Gói ngành (pack service, P09, UC-09-01…05): industry packs — canonical entities, metrics,
 * glossary, skills and template agents for one industry. A2 lists GET /v1/packs, :import,
 * GET /v1/pack-versions/{id}, versions, :validate, :publish, pack installs (:suggest-mappings,
 * mappings, :calibrate, :activate), metric overrides and upgrades (:upgrade, decisions, :apply).
 * Reading one install, its mappings, its calibration, one override and one upgrade, and the
 * display fields, are PROPOSALs (docs/v3/05-ui-v3.md §4). Packs are organisation-wide: no
 * X-Data-Space needed.
 */

/** A2: GET /v1/packs — IndustryPack; `installed` and `latest` carry what the list shows. */
export interface IndustryPack {
  id: string;
  name: string;
  publisher: string;
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
  /** PROPOSAL: IndustryPackView has no version; null without it (A2 lists no pack versions). */
  latest: { version: string; version_id: string; content_text: string } | null;
}

type PackWire = components['schemas']['IndustryPackView'] & {
  /** PROPOSAL: the list row as the screen draws it, which the mock sends. */
  display?: IndustryPack;
};

/*
 * GET /v1/packs as IndustryPackView: name and publisher. What is installed and the latest
 * version are PROPOSAL, so without them the pack can be read about but not installed from here.
 */
export const listPacks = async () => {
  const page = await v1Get<{ items: PackWire[] }>('/packs');
  return {
    items: page.items.map(
      (p): IndustryPack =>
        p.display ?? {
          id: p.id,
          name: p.name,
          publisher: p.publisher,
          installed: null,
          latest: null,
        },
    ),
  };
};

/**
 * PROPOSAL: POST /v1/packs — the organisation's own domain pack ("Hộp thoại · createPack",
 * 2095:8808), started from a pack version or from the organisation's current model. A2 only has
 * POST /packs/{id}/versions, which needs a pack to exist; this creates the pack and its first
 * draft version, opened in Biên soạn gói.
 */
export const createPack = async (body: {
  name: string;
  based_on_version_id: string | null;
  includes: string[];
  sharing: 'organisation' | 'invited_tenants';
}) => v1Send<{ id: string; draft_version_id: string }>('POST', '/packs', body);

/** A2: POST /v1/packs:import (multipart) — the signature is checked first (BR-128). */
export const importPack = async (file: File) => {
  const form = new FormData();
  form.append('file', file);
  return v1Upload<{ id: string }>('/packs:import', form);
};

export type ConflictChoice = 'keep_org' | 'use_pack' | 'keep_both';

/** A2: GET /v1/pack-versions/{id} — PackVersion; the preview fields are PROPOSAL. */
export interface PackVersion {
  id: string;
  pack_name: string;
  version: string;
  status: 'draft' | 'published';
  counts: { label: string; value: number }[];
  checks: { item: string; result: string; ok: boolean }[];
  code_conflicts: {
    code: string;
    org_text: string;
    pack_text: string;
    suggested: ConflictChoice;
  }[];
  /** Data Spaces it may go into, and whether they are ticked by default. */
  targets: { id: string; name: string; default: boolean }[];
}

export const getPackVersion = async (id: string) =>
  v1Get<PackVersion>(`/pack-versions/${pathId(id)}`);

/** A2 body is pack_version_id; several Data Spaces and the conflict choices are PROPOSAL. */
export const installPack = async (body: {
  pack_version_id: string;
  data_space_ids: string[];
  conflict_choices: Record<string, ConflictChoice>;
}) => v1Send<{ id: string }>('POST', '/pack-installs', body);

/* ─── Đưa vào ngành ─────────────────────────────────────────────────── */

/** PROPOSAL: GET /v1/pack-installs/{id} */
export interface PackInstall {
  id: string;
  title: string;
  summary_text: string;
  entities: { key: string; name: string; mapped: number; total: number }[];
}

export type PackInstallWire = components['schemas']['PackInstallDetail'];
export type MappingWire = components['schemas']['MappingView'];

export function readPackInstall(raw: PackInstallWire): PackInstall {
  if ('title' in raw) return raw as unknown as PackInstall;
  return {
    id: raw.id,
    title: `${raw.pack_name} · ${raw.version}`,
    summary_text: `${raw.mapped}/${raw.total} thuộc tính đã ánh xạ`,
    entities: (raw.entities ?? []).map((entity) => ({
      key: entity.code,
      name: entity.name,
      mapped: entity.mapped,
      total: entity.total,
    })),
  };
}

export const getPackInstall = async (id: string) =>
  readPackInstall(await v1Get<PackInstallWire>(`/pack-installs/${pathId(id)}`));

export const listPackInstalls = async () =>
  v1Get<{ items: PackInstall[] }>('/pack-installs', { status: 'onboarding' });

/** PROPOSAL: GET /v1/pack-installs/{id}/mappings?entity= — one entity's attributes. */
export interface EntityMappings {
  entity: string;
  name: string;
  main_table: string;
  rows: {
    attr: string;
    required: boolean;
    column: string | null;
    options: string[];
    confidence: number | null;
    note: string;
    unsure: boolean;
    approved: boolean;
  }[];
  decisions: {
    text: string;
    /** A code table the agent proposes to merge, shown on request. */
    code_mapping: { attr: string; rows: { from: string; to: string }[] } | null;
  }[];
}

export const NOT_APPLICABLE = '__not_applicable__';

export function readEntityMappings(raw: Page<MappingWire> | EntityMappings, entity: string): EntityMappings {
  if ('rows' in raw) return raw;
  const items = raw.items.filter((item) => item.entity_code === entity);
  return {
    entity,
    name: entity,
    main_table: items[0]?.column_expr.split('.')[0] ?? '—',
    rows: items.map((item) => ({
      attr: item.attribute_code,
      required: item.required,
      column: item.column_expr || null,
      options: item.column_expr ? [item.column_expr] : [],
      confidence: item.confidence === null ? null : Number(item.confidence),
      note: item.origin === 'confirmed' ? 'Đã xác nhận' : 'Đề xuất từ agent',
      unsure: item.confidence !== null && Number(item.confidence) < 0.9,
      approved: item.origin === 'confirmed' && item.confirmed_by !== null,
    })),
    decisions: [],
  };
}

export const getEntityMappings = async (installId: string, entity: string) => {
  const raw = await v1Get<Page<MappingWire> | EntityMappings>(
    `/pack-installs/${pathId(installId)}/mappings`,
    { entity },
  );
  return readEntityMappings(raw, entity);
};

/** A2: PUT /v1/pack-installs/{id}/mappings */
export const saveMappings = async (
  installId: string,
  body: {
    entity_mappings: { entity: string; attr: string; column: string | null }[];
    code_mappings?: { attr: string; rows: { from: string; to: string }[] }[];
  },
) => v1Send<{ approved: number }>('PUT', `/pack-installs/${pathId(installId)}/mappings`, body);

/** PROPOSAL: GET /v1/pack-installs/{id}/calibration — the last :calibrate run. */
export interface Calibration {
  install_title: string;
  matched: number;
  total: number;
  tolerance_text: string;
  rows: {
    id: string;
    metric: string;
    period: string;
    computed: string;
    reported: string;
    diff: string;
    ok: boolean;
    accepted_note: string | null;
  }[];
  findings: {
    row_id: string;
    title: string;
    text: string;
    fix: { id: string; label: string } | null;
  }[];
  after_text: string;
}

export type CalibrationRunWire = components['schemas']['CalibrationRunView'];

export function readCalibrationRun(raw: CalibrationRunWire, installTitle: string): Calibration {
  if ('install_title' in raw) return raw as unknown as Calibration;
  const passed = raw.passed;
  const total = raw.passed + raw.failed;
  return {
    install_title: installTitle,
    matched: passed,
    total,
    tolerance_text: `Sai số cho phép ±${raw.tolerance_pp} điểm phần trăm`,
    rows: (raw.items ?? []).map((item) => ({
      id: item.id,
      metric: item.item_code,
      period: item.period ?? '—',
      computed: item.computed_value === null ? '—' : String(item.computed_value),
      reported: item.filed_value === null ? '—' : String(item.filed_value),
      diff: item.delta === null ? '—' : String(item.delta),
      ok: item.passed,
      accepted_note: null,
    })),
    findings: (raw.items ?? [])
      .filter((item) => !item.passed)
      .map((item) => ({
        row_id: item.id,
        title: item.item_code,
        text: item.suggested_cause ?? 'Cần kiểm tra lại số liệu đối chiếu.',
        fix: null,
      })),
    after_text: raw.status === 'passed'
      ? 'Mọi chỉ số đã khớp trong ngưỡng cho phép.'
      : 'Xử lý các dòng lệch hoặc ghi nhận lý do trước khi kích hoạt.',
  };
}

export const getCalibration = async (installId: string) => {
  const rawInstall = await v1Get<PackInstallWire>(`/pack-installs/${pathId(installId)}`);
  if ('title' in rawInstall) {
    return v1Get<Calibration>(`/pack-installs/${pathId(installId)}/calibration`);
  }
  const runId = rawInstall.latest_calibration_run?.id;
  if (!runId) throw new Error('Chưa có lượt đối chiếu số cho lượt đưa vào ngành.');
  const run = await v1Get<CalibrationRunWire>(`/calibration-runs/${pathId(runId)}`);
  return readCalibrationRun(run, `${rawInstall.pack_name} · ${rawInstall.version}`);
};

/** PROPOSAL: apply the fix the agent found; the server runs A2's :calibrate again. */
export const applyCalibrationFix = async (installId: string, fixId: string) =>
  v1Send<Calibration>('POST', `/pack-installs/${pathId(installId)}:apply-fix`, { fix_id: fixId });

/** PROPOSAL: the data owner accepts a gap with a note. */
export const acceptCalibrationGap = async (installId: string, rowId: string, note: string) =>
  v1Send<Calibration>('POST', `/pack-installs/${pathId(installId)}:accept-gap`, {
    row_id: rowId,
    note,
  });

/** A2: :activate — 422 while calibration has not passed (BR-36). */
export const activatePackInstall = async (installId: string) =>
  v1Send<{ id: string }>('POST', `/pack-installs/${pathId(installId)}:activate`);

/* ─── Hiệu chỉnh chỉ số ─────────────────────────────────────────────── */

/** PROPOSAL: GET /v1/metric-overrides/{id} */
export interface MetricOverride {
  id: string;
  metric_code: string;
  status: 'draft' | 'submitted' | 'certified';
  summary_text: string;
  base_expression: string;
  org_expression: string;
  changes: { change: string; reason: string }[];
  upgrade_note: string;
  trial: { period: string; base: string; org: string }[] | null;
  trial_note: string | null;
}

export type MetricOverrideWire = components['schemas']['MetricOverrideDetail'];

export function readMetricOverride(raw: MetricOverrideWire): MetricOverride {
  if ('metric_code' in raw) return raw as unknown as MetricOverride;
  const status = raw.status === 'pending_review' ? 'submitted' : raw.status === 'approved' ? 'certified' : 'draft';
  return {
    id: raw.id,
    metric_code: raw.pack_metric_code,
    status,
    summary_text: `${raw.metric_name ?? raw.pack_metric_code} · ${raw.reason}`,
    base_expression: raw.base_expression ?? 'Chưa có công thức gốc',
    org_expression: raw.expression,
    changes: [{ change: raw.scope_condition ? 'Điều kiện phạm vi' : 'Công thức tổ chức', reason: raw.reason }],
    upgrade_note: 'Hiệu chỉnh vẫn liên kết với công thức gốc của gói; xung đột sẽ được xử lý khi nâng cấp.',
    trial: null,
    trial_note: raw.calibration_run ? `Đã có lượt đối chiếu ${raw.calibration_run.id}.` : null,
  };
}

export const getMetricOverride = async (id: string) =>
  readMetricOverride(await v1Get<MetricOverrideWire>(`/metric-overrides/${pathId(id)}`));

/** PROPOSAL: compute both expressions on the last 6 periods. */
export const trialMetricOverride = async (id: string) =>
  v1Send<MetricOverride>('POST', `/metric-overrides/${pathId(id)}:trial`, { periods: 6 });

/** PROPOSAL: send for certification; the approver then calls A2's :approve. */
export const submitMetricOverride = async (id: string) =>
  v1Send<MetricOverride>('POST', `/metric-overrides/${pathId(id)}:submit`);

/** PROPOSAL: drop the override; the metric follows the pack again. */
export const discardMetricOverride = async (id: string) =>
  v1Send<unknown>('POST', `/metric-overrides/${pathId(id)}:discard`);

/* ─── Nâng cấp gói ──────────────────────────────────────────────────── */

/** A2: PackUpgrade (POST :upgrade?dry_run); GET by id and the display fields are PROPOSAL. */
export interface PackUpgrade {
  id: string;
  pack_name: string;
  from_version: string;
  to_version: string;
  summary_text: string;
  conflicts: {
    id: string;
    element: string;
    old: string;
    new: string;
    org: string;
    options: { value: string; label: string }[];
    suggested: string;
  }[];
  auto: { element: string; text: string }[];
  skipped: { element: string; reason: string }[];
  benchmark_pct: number | null;
  status: 'dry_run' | 'applied';
}

export type PackUpgradeWire = components['schemas']['PackUpgradeDetail'];

export function readPackUpgrade(raw: PackUpgradeWire): PackUpgrade {
  if ('pack_name' in raw) return raw as unknown as PackUpgrade;
  const merge = raw.merge_result && typeof raw.merge_result === 'object' ? raw.merge_result : {};
  const items = Array.isArray((merge as { items?: unknown }).items)
    ? ((merge as { items: unknown[] }).items ?? [])
    : [];
  const row = (value: unknown) =>
    typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : {};
  const conflicts = items.flatMap((value) => {
    const item = row(value);
    if (item.outcome !== 'conflict') return [];
    const code = String(item.code ?? '—');
    const options = Array.isArray(item.options)
      ? item.options.map((option) => ({ value: String(option), label: String(option) }))
      : [];
    return [{
      id: code,
      element: code,
      old: 'Bản đang dùng',
      new: 'Bản gói mới',
      org: 'Tuỳ chỉnh của tổ chức',
      options,
      suggested: options[0]?.value ?? 'defer',
    }];
  });
  const skipped = items.flatMap((value) => {
    const item = row(value);
    return item.outcome === 'removed'
      ? [{ element: String(item.code ?? '—'), reason: 'Gói mới đã bỏ thành phần này' }]
      : [];
  });
  const auto = items.flatMap((value) => {
    const item = row(value);
    return item.outcome && item.outcome !== 'conflict' && item.outcome !== 'removed'
      ? [{ element: String(item.code ?? '—'), text: String(item.outcome) }]
      : [];
  });
  return {
    id: raw.id,
    pack_name: 'Gói ngành',
    from_version: raw.from_version_id,
    to_version: raw.to_version_id,
    summary_text: `${auto.length} tự gộp · ${conflicts.length} cần chọn · ${skipped.length} bị bỏ qua`,
    conflicts,
    auto,
    skipped,
    benchmark_pct: null,
    status: raw.status === 'applied' ? 'applied' : 'dry_run',
  };
}

export const getPackUpgrade = async (id: string) =>
  readPackUpgrade(await v1Get<PackUpgradeWire>(`/pack-upgrades/${pathId(id)}`));

/** PROPOSAL: run the pack's benchmark questions against the merged result. */
export const benchmarkPackUpgrade = async (id: string) =>
  v1Send<PackUpgrade>('POST', `/pack-upgrades/${pathId(id)}:benchmark`);

export const decidePackUpgrade = async (id: string, decisions: Record<string, string>) =>
  v1Send<PackUpgrade>('PUT', `/pack-upgrades/${pathId(id)}/decisions`, { decisions });

export const applyPackUpgrade = async (id: string) =>
  v1Send<PackUpgrade>('POST', `/pack-upgrades/${pathId(id)}:apply`, {
    apply_from_period: 'next',
  });

/* ─── Biên soạn gói ─────────────────────────────────────────────────── */

/** PROPOSAL: the author's view of a draft PackVersion and its last :validate run. */
export interface PackDraft {
  id: string;
  pack_name: string;
  version: string;
  base_version: string;
  summary_text: string;
  sections: { label: string; changed: number; total: number }[];
  test: {
    run_text: string;
    kpis: { label: string; value: string; tone: 'success' | 'warning' | 'neutral' }[];
    failures: { item: string; kind: string; detail: string }[];
  } | null;
  conditions: { text: string; ok: boolean }[];
  after_text: string;
}

export const getPackDraft = async (id: string) =>
  v1Get<PackDraft>(`/pack-versions/${pathId(id)}/draft`);

export const listPackDrafts = async () =>
  v1Get<{ items: Pick<PackDraft, 'id' | 'pack_name' | 'version'>[] }>('/pack-versions', {
    status: 'draft',
  });

/** A2: :validate (202) */
export const validatePackVersion = async (id: string) =>
  v1Send<unknown>('POST', `/pack-versions/${pathId(id)}:validate`);

/** A2: :publish — 409 until checks pass, 422 when a metric lacks grounds (BR-131). */
export const publishPackVersion = async (id: string) =>
  v1Send<{ id: string }>('POST', `/pack-versions/${pathId(id)}:publish`);
