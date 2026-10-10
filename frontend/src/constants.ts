import type { AreaId, RailGroup } from './components/shell/areas';

export { AREAS } from './components/shell/areas';

/*
 * Secondary rail per area (v3.1). An area absent here is a single screen at full width.
 * Page ids are upper-case internally and lower-case in the URL (/agents/runs ↔ RUNS);
 * routes.tsx maps every id below to a screen.
 *
 * Until each screen is rebuilt on the v3 design, the rail points at the existing screen that
 * does the job: Lượt chạy is the trace explorer, Điều phối the workflow canvas, Đánh giá &
 * triển khai the playground, Phiên làm việc the conversation list.
 */
export const AREA_RAILS: Partial<Record<AreaId, RailGroup[]>> = {
  agents: [
    {
      label: 'Agents',
      items: [
        { id: 'TEAM', label: 'Đội agent' },
        { id: 'RUNS', label: 'Lượt chạy' },
        { id: 'EVALUATE', label: 'Đánh giá & triển khai' },
        { id: 'ORCHESTRATION', label: 'Điều phối' },
      ],
    },
  ],
  analysis: [
    {
      label: 'Phân tích',
      items: [
        { id: 'ASK', label: 'Hỏi đáp' },
        { id: 'SESSIONS', label: 'Phiên làm việc' },
        { id: 'DASHBOARD', label: 'Dashboard' },
      ],
    },
  ],
  // Ids = SettingsSection (components/Settings/Overview.tsx).
  admin: [
    {
      label: 'Chung',
      items: [
        { id: 'OVERVIEW', label: 'Tổng quan' },
        { id: 'ORGANIZATION', label: 'Tổ chức' },
      ],
    },
    {
      label: 'Bảo mật',
      items: [
        { id: 'IAM', label: 'Người dùng & quyền' },
        { id: 'AUTH', label: 'Đăng nhập & SSO' },
        { id: 'SECRETS', label: 'Kho bí mật' },
        { id: 'SECURITY', label: 'Bảo mật & tuân thủ' },
      ],
    },
    {
      label: 'Nền tảng',
      items: [
        { id: 'AI_PROVIDERS', label: 'Mô hình AI' },
        { id: 'INFRASTRUCTURE', label: 'Hạ tầng runtime' },
        { id: 'STORAGE', label: 'Lưu trữ' },
      ],
    },
    {
      label: 'Vận hành',
      items: [
        { id: 'BILLING', label: 'Chi phí' },
        { id: 'API', label: 'API & nhà phát triển' },
        { id: 'ADVANCED', label: 'Nâng cao' },
      ],
    },
  ],
};

/** First page of an area with a rail; undefined for single-screen areas. */
export const firstPageOf = (area: AreaId): string | undefined => AREA_RAILS[area]?.[0]?.items[0]?.id;
