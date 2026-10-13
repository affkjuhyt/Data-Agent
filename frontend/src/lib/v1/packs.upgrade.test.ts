import { describe, expect, it } from 'vitest';
import { readPackUpgrade, type PackUpgradeWire } from './packs';

describe('readPackUpgrade', () => {
  it('splits merge items into conflicts, automatic changes and skipped items', () => {
    const upgrade = readPackUpgrade(
      {
        id: 'upgrade-1',
        pack_install_id: 'install-1',
        from_version_id: 'version-1',
        to_version_id: 'version-2',
        merge_result: {
          items: [
            {
              kind: 'metric',
              code: 'NPL_RATIO',
              outcome: 'conflict',
              options: ['keep_override', 'take_new', 'defer'],
              decision: null,
            },
            { kind: 'mapping', code: 'card.flag', outcome: 'conflict', decision: 'take_new' },
            { kind: 'metric', code: 'LOAN_GROWTH', outcome: 'take_new' },
            { kind: 'metric', code: 'CARD_ACTIVE', outcome: 'removed' },
          ],
        },
        conflicts_open: 1,
        calibration_run_id: 'run-1',
        status: 'needs_decision',
        apply_from_period: null,
        rollback_until: null,
        approved_by: null,
        dry_run: false,
        created_at: null,
        updated_at: null,
        conflicts: [],
        decisions: [],
        calibration_run: {
          id: 'run-1',
          status: 'passed',
          trigger: 'upgrade',
          passed: 5,
          failed: 1,
          created_at: '2026-10-13T00:00:00Z',
          finished_at: null,
        },
      } as PackUpgradeWire,
      { pack_name: 'Ngân hàng', from_version: '2026.3', to_version: '2026.4' },
    );

    expect(upgrade).toMatchObject({
      pack_name: 'Ngân hàng',
      from_version: '2026.3',
      to_version: '2026.4',
      test: { matched: 5, total: 6, status: 'passed' },
    });
    expect(upgrade.conflicts).toEqual([
      expect.objectContaining({ id: 'NPL_RATIO', kind: 'metric', decision: null, old: null }),
      expect.objectContaining({ id: 'card.flag', kind: 'mapping', decision: 'take_new' }),
    ]);
    expect(upgrade.conflicts[0].options.map((o) => o.value)).toEqual([
      'keep_override',
      'take_new',
      'defer',
    ]);
    expect(upgrade.auto).toEqual([{ element: 'Chỉ số LOAN_GROWTH', text: 'Theo bản gói mới' }]);
    expect(upgrade.skipped).toEqual([
      { element: 'Chỉ số CARD_ACTIVE', reason: 'Gói mới đã bỏ thành phần này' },
    ]);
  });
});
