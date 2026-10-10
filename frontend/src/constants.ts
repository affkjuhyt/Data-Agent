import type { AreaId, RailGroup } from './components/shell/areas';

export { AREAS } from './components/shell/areas';

/*
 * Secondary rail per area (v3.1). The rail is drawn on its pages only: an area with a landing
 * (AREA_LANDING) opens on that full-width page and shows the rail once one of its pages is open,
 * as in Figma, where "v3.1 · Phân tích" has no rail and the detail screens do.
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

/** Areas that open on a landing page (route key SINGLE_SCREEN) rather than on a rail page. */
export const AREA_LANDING: ReadonlySet<AreaId> = new Set(['home', 'needs-you', 'analysis', 'data', 'admin']);

/** The page an area opens on: undefined = its landing; otherwise the first rail item. */
export const firstPageOf = (area: AreaId): string | undefined =>
  AREA_LANDING.has(area) ? undefined : AREA_RAILS[area]?.[0]?.items[0]?.id;

/** Detail pages that live under a rail item: /agents/run/<id> keeps "Lượt chạy" selected. */
const RAIL_PARENT: Partial<Record<AreaId, Record<string, string>>> = {
  agents: { RUN: 'RUNS' },
};

/** The rail item a page highlights: itself, or the item its detail page belongs to. */
export const railItemOf = (area: AreaId, page: string | undefined): string | undefined =>
  page ? (RAIL_PARENT[area]?.[page] ?? page) : page;

/** Whether `page` of `area` sits in the area's rail, itself or through its parent (and so draws it). */
export const isRailPage = (area: AreaId, page: string | undefined): boolean => {
  const item = railItemOf(area, page);
  return Boolean(item && AREA_RAILS[area]?.some(group => group.items.some(entry => entry.id === item)));
};
