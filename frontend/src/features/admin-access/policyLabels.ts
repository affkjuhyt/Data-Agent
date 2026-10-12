import type { AccessPolicy } from '../../lib/v1/adminAccess';

export const POLICY_KIND: Record<AccessPolicy['kind'], string> = {
  row: 'Chính sách theo dòng',
  column: 'Chính sách theo cột',
  mask: 'Che giá trị',
};

export const POLICY_STATUS: Record<
  AccessPolicy['status'],
  { tone: 'success' | 'idle' | 'neutral'; label: string }
> = {
  active: { tone: 'success', label: 'Đang bật' },
  draft: { tone: 'idle', label: 'Nháp' },
  disabled: { tone: 'neutral', label: 'Đã tắt' },
};
