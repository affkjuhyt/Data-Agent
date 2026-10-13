import { describe, expect, it } from 'vitest';
import {
  draftManifest,
  packCode,
  readEntityMappings,
  readPack,
  readPackDraft,
  readPackInstall,
  readPackVersion,
  type MappingWire,
  type PackInstallWire,
  type PackVersionWire,
} from './packs';

const version = {
  id: 'version-1',
  pack_id: 'pack-1',
  version: '2.0',
  status: 'draft',
  manifest: {},
  signature: '',
  signature_verified_at: null,
  min_core_version: '3.1.0',
  object_key: '',
  release_notes: null,
  released_at: null,
  breaking_changes: [{ code: 'OLD', kind: 'metric', migration: '' }],
  created_at: '2026-10-13T00:00:00Z',
  updated_at: '2026-10-13T00:00:00Z',
  entities: [
    {
      id: 'ent-1',
      code: 'customer',
      name: 'Khách hàng',
      description: null,
      key_attributes: ['customer_id'],
      attributes: [
        {
          id: 'attr-1',
          code: 'customer_id',
          name: 'Mã',
          data_type: 'string',
          required: true,
          pii: false,
          code_list: null,
        },
        {
          id: 'attr-2',
          code: 'kyc_level',
          name: 'KYC',
          data_type: 'string',
          required: true,
          pii: false,
          code_list: null,
        },
        {
          id: 'attr-3',
          code: 'segment',
          name: 'Phân khúc',
          data_type: 'string',
          required: false,
          pii: false,
          code_list: null,
        },
      ],
    },
  ],
  metrics: [],
  rules: [],
  skills: [
    { id: 's1', code: 'a', name: 'A', kind: 'skill', definition: {} },
    { id: 's2', code: 'b', name: 'B', kind: 'agent_template', definition: {} },
  ],
  latest_check_run: {
    id: 'check-1',
    pack_version_id: 'version-1',
    checks: { legal_basis: [], code_reuse: ['CARD_ACTIVE'], sample_datasets: 'skipped' },
    passed: false,
    ran_by: 'user-1',
    created_at: '2026-10-13T00:00:00Z',
  },
} as PackVersionWire;

const mapping = (over: Partial<MappingWire>): MappingWire => ({
  id: 'mapping-1',
  pack_install_id: 'install-1',
  attribute_id: 'attr-1',
  dataset_id: 'dataset-1',
  column_expr: 'crm.customers.cif',
  confidence: 0.98,
  origin: 'confirmed',
  valid_from: '2026-10-13T00:00:00Z',
  valid_to: null,
  confirmed_by: 'user-1',
  created_at: '2026-10-13T00:00:00Z',
  updated_at: '2026-10-13T00:00:00Z',
  entity_code: 'customer',
  attribute_code: 'customer_id',
  attribute_name: 'Mã khách hàng',
  required: true,
  code_mappings: [],
  ...over,
});

describe('pack adapters', () => {
  it('reads a pack row, its display fields winning when the mock sends them', () => {
    const row = {
      id: 'pack-1',
      code: 'banking',
      name: 'Ngân hàng',
      publisher: 'Data Agent',
      industry_code: 'banking',
      origin: 'vendor' as const,
      description: null,
      publisher_key_id: null,
      created_at: '',
      updated_at: '',
    };
    expect(readPack(row)).toEqual({
      id: 'pack-1',
      name: 'Ngân hàng',
      publisher: 'Data Agent',
      installed: null,
      latest: null,
      draft: null,
    });
    expect(
      readPack({ ...row, display: { draft: { version: '2.1', version_id: 'v-2' } } }).draft,
    ).toEqual({ version: '2.1', version_id: 'v-2' });
  });

  it('adapts install progress', () => {
    const install = readPackInstall({
      id: 'install-1',
      data_space_id: 'space-1',
      pack_version_id: 'version-1',
      previous_pack_version_id: null,
      status: 'mapping',
      activated_at: null,
      activated_by: null,
      proposal_id: null,
      created_at: '2026-10-13T00:00:00Z',
      updated_at: '2026-10-13T00:00:00Z',
      pack_id: 'pack-1',
      pack_code: 'banking',
      pack_name: 'Ngân hàng Việt Nam',
      version: '2.0',
      entities: [
        {
          code: 'customer',
          name: 'Khách hàng',
          mapped: 2,
          total: 3,
          required_mapped: 1,
          required_total: 1,
        },
      ],
      mapped: 2,
      total: 3,
      latest_calibration_run: null,
    } as PackInstallWire);
    expect(install).toMatchObject({
      title: 'Ngân hàng Việt Nam · 2.0',
      pack_version_id: 'version-1',
      latest_calibration_run_id: null,
      entities: [{ key: 'customer', mapped: 2, total: 3 }],
    });
  });

  it('lists every attribute of the entity beside its current mapping', () => {
    const mappings = readEntityMappings(
      version,
      [
        mapping({}),
        mapping({
          id: 'mapping-3',
          attribute_id: 'attr-3',
          attribute_code: 'segment',
          column_expr: 'crm.customers.type',
          confidence: '0.61',
          origin: 'suggested',
          confirmed_by: null,
        }),
      ],
      'customer',
    );
    expect(mappings).toMatchObject({ name: 'Khách hàng', main_table: 'crm.customers' });
    expect(mappings.rows).toEqual([
      expect.objectContaining({
        attr: 'customer_id',
        column: 'crm.customers.cif',
        approved: true,
        unsure: false,
      }),
      expect.objectContaining({
        attr: 'kyc_level',
        column: null,
        dataset_id: null,
        options: [],
        unsure: true,
      }),
      expect.objectContaining({
        attr: 'segment',
        confidence: 0.61,
        approved: false,
        unsure: true,
        note: 'Đề xuất từ agent',
      }),
    ]);
  });

  it('reads a version for the install preview and the author', () => {
    const preview = readPackVersion(version, 'Ngân hàng');
    expect(preview.counts).toContainEqual({ label: 'Agent mẫu', value: 1 });
    expect(preview.checks).toEqual([
      expect.objectContaining({ item: 'Chữ ký của nhà phát hành', ok: false }),
      expect.objectContaining({ item: 'Thay đổi phá vỡ tương thích', ok: false }),
    ]);
    const draft = readPackDraft(version, 'Ngân hàng');
    expect(draft.base_version).toBeNull();
    expect(draft.test?.kpis).toContainEqual(
      expect.objectContaining({
        label: 'Mã chỉ số dùng lại (BR-131)',
        value: '1 chưa đạt',
        tone: 'warning',
      }),
    );
    expect(draft.test?.failures).toEqual([
      { item: 'CARD_ACTIVE', kind: 'Mã chỉ số dùng lại (BR-131)', detail: '—' },
    ]);
    expect(draft.conditions).toEqual([{ text: 'Kiểm thử trước phát hành đạt', ok: false }]);
  });

  it('builds the code and the first draft of an own pack', () => {
    expect(packCode('Tín dụng tiêu dùng · nội bộ')).toBe('tin-dung-tieu-dung-noi-bo');
    expect(draftManifest(null, ['Thực thể'])).toEqual({});
    const manifest = draftManifest(version, ['Thực thể', 'agent mẫu']);
    expect(Object.keys(manifest)).toEqual(['entities', 'skills']);
    expect(manifest.skills).toEqual([expect.objectContaining({ kind: 'agent_template' })]);
  });
});
