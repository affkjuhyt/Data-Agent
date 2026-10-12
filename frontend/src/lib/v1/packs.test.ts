import { describe, expect, it } from 'vitest';
import { readEntityMappings, readPackInstall, type MappingWire, type PackInstallWire } from './packs';

describe('pack adapters', () => {
  it('adapts install progress and mapping rows from the API', () => {
    const install = readPackInstall({
      id: 'install-1',
      data_space_id: 'space-1',
      pack_version_id: 'version-1',
      previous_pack_version_id: null,
      status: 'onboarding',
      activated_at: null,
      activated_by: null,
      proposal_id: null,
      created_at: '2026-10-13T00:00:00Z',
      updated_at: '2026-10-13T00:00:00Z',
      pack_id: 'pack-1',
      pack_code: 'banking',
      pack_name: 'Ngân hàng Việt Nam',
      version: '2.0',
      entities: [{ code: 'customer', name: 'Khách hàng', mapped: 2, total: 3, required_mapped: 1, required_total: 1 }],
      mapped: 2,
      total: 3,
      latest_calibration_run: null,
    } as PackInstallWire);
    expect(install).toMatchObject({
      title: 'Ngân hàng Việt Nam · 2.0',
      entities: [{ key: 'customer', mapped: 2, total: 3 }],
    });

    const mappings = readEntityMappings(
      {
        items: [
          {
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
          },
        ],
        next_page_token: '',
      } as { items: MappingWire[]; next_page_token: string },
      'customer',
    );
    expect(mappings.rows[0]).toMatchObject({
      attr: 'customer_id',
      column: 'crm.customers.cif',
      approved: true,
      unsure: false,
    });
  });
});
