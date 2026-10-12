import { describe, expect, it } from 'vitest';
import { readPackUpgrade, type PackUpgradeWire } from './packs';

describe('readPackUpgrade', () => {
  it('splits merge items into conflicts, automatic changes and skipped items', () => {
    const upgrade = readPackUpgrade({
      id: 'upgrade-1',
      pack_install_id: 'install-1',
      from_version_id: 'version-1',
      to_version_id: 'version-2',
      merge_result: {
        items: [
          { kind: 'metric', code: 'NPL_RATIO', outcome: 'conflict', options: ['keep_override', 'take_new', 'defer'], decision: null },
          { kind: 'metric', code: 'LOAN_GROWTH', outcome: 'take_new' },
          { kind: 'metric', code: 'CARD_ACTIVE', outcome: 'removed' },
        ],
      },
      conflicts_open: 1,
      calibration_run_id: null,
      status: 'needs_decision',
      apply_from_period: null,
      rollback_until: null,
      approved_by: null,
      dry_run: false,
      created_at: null,
      updated_at: null,
      conflicts: [],
      decisions: [],
      calibration_run: null,
    } as PackUpgradeWire);

    expect(upgrade.conflicts[0]).toMatchObject({ id: 'NPL_RATIO', suggested: 'keep_override' });
    expect(upgrade.auto).toEqual([{ element: 'LOAN_GROWTH', text: 'take_new' }]);
    expect(upgrade.skipped).toEqual([{ element: 'CARD_ACTIVE', reason: 'Gói mới đã bỏ thành phần này' }]);
  });
});
