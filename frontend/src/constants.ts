import type { AreaId, RailGroup } from './components/shell/areas';

export { AREAS } from './components/shell/areas';

/*
 * Secondary rail per area (v3.1). The rail is drawn on its pages only: an area with a landing
 * (AREA_LANDING) opens on that full-width page and shows the rail once one of its pages is open,
 * as in Figma, where "v3.1 · Phân tích" has no rail and the detail screens do.
 * Page ids are upper-case internally and lower-case in the URL (/agents/runs ↔ RUNS);
 * routes.tsx maps every id below to a screen.
 * Rail items follow the Figma rails, minus the items that have no screen yet.
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
        { id: 'OPERATIONS', label: 'Vận hành' },
      ],
    },
    {
      label: 'Kỹ năng',
      items: [
        { id: 'SKILLS', label: 'Thư viện' },
        { id: 'SKILL_SUGGESTIONS', label: 'Đề xuất từ agent' },
        { id: 'SKILL_VERSIONS', label: 'Đang chờ duyệt' },
        { id: 'TOOLS', label: 'Công cụ' },
      ],
    },
  ],
  analysis: [
    {
      label: 'Phân tích',
      items: [
        { id: 'ASK', label: 'Hỏi đáp' },
        { id: 'EXPLORE', label: 'Khám phá' },
        // P16: only leaders with the exec_vault role have a vault.
        { id: 'VAULT', label: 'Kho riêng', requires: 'exec_vault' },
        { id: 'FORECAST', label: 'Dự báo' },
        { id: 'SESSIONS', label: 'Phiên làm việc' },
      ],
    },
    {
      label: 'Theo dõi & báo cáo',
      items: [
        { id: 'WATCH', label: 'Theo dõi' },
        { id: 'ALERTS', label: 'Cảnh báo' },
        { id: 'REPORTS', label: 'Báo cáo định kỳ' },
        { id: 'DIGEST', label: 'Bản tin' },
      ],
    },
  ],
  data: [
    {
      label: 'Nguồn & nạp',
      items: [
        { id: 'SOURCES', label: 'Nguồn' },
        { id: 'JOBS', label: 'Job nạp' },
        { id: 'DRIFT', label: 'Lệch cấu trúc' },
        { id: 'ERRORS', label: 'Bản ghi lỗi' },
        { id: 'UPLOAD', label: 'Tải tệp' },
        { id: 'CRAWL', label: 'Thu thập ngoài' },
      ],
    },
    {
      label: 'Tài liệu',
      items: [
        { id: 'COLLECTIONS', label: 'Bộ sưu tập' },
        { id: 'DOCUMENTS', label: 'Tệp & tài liệu' },
        { id: 'DOC_QUALITY', label: 'Hàng chờ chất lượng' },
        { id: 'DOC_VALIDITY', label: 'Hiệu lực văn bản' },
        { id: 'EXTRACTION_SCHEMAS', label: 'Lược đồ trích xuất' },
      ],
    },
    {
      label: 'Chuẩn bị',
      items: [
        { id: 'SQL', label: 'SQL' },
        { id: 'PIPELINES', label: 'Pipeline' },
        { id: 'DATA_MARTS', label: 'Data mart' },
        { id: 'DATA_PRODUCTS', label: 'Sản phẩm dữ liệu' },
        { id: 'QUALITY', label: 'Chất lượng' },
        { id: 'WRITEBACK', label: 'Ghi ngược' },
        { id: 'LINEAGE', label: 'Dòng dữ liệu' },
      ],
    },
    {
      label: 'Kỳ chốt',
      items: [
        { id: 'PERIOD', label: 'Các kỳ' },
        { id: 'RETRO', label: 'Thay đổi hồi tố' },
        { id: 'COMPARE', label: 'So sánh bản' },
      ],
    },
    {
      label: 'Ngữ nghĩa',
      items: [
        { id: 'CATALOG', label: 'Danh mục' },
        { id: 'ENTITIES', label: 'Mô hình thực thể' },
        { id: 'METRIC_CATALOG', label: 'Chỉ số' },
        { id: 'GLOSSARY', label: 'Từ điển nghiệp vụ' },
        { id: 'EXAMPLES', label: 'Câu hỏi mẫu' },
        { id: 'KNOWLEDGE', label: 'Trang tri thức' },
        { id: 'AUTHORITY', label: 'Thẩm quyền nguồn' },
        { id: 'RULE', label: 'Quy tắc từ quyết định' },
        { id: 'KNOWLEDGE_REVIEW', label: 'Rà soát tri thức' },
        { id: 'CONTEXT_EVAL', label: 'Đánh giá lớp ngữ cảnh' },
        { id: 'ANSWER_PRODUCTS', label: 'Data product' },
        { id: 'ERROR_BOOK', label: 'Error Book' },
      ],
    },
    {
      label: 'Gói ngành',
      items: [
        { id: 'PACKS', label: 'Gói đã cài' },
        { id: 'PACK_ONBOARD', label: 'Đưa vào ngành' },
        { id: 'PACK_AUTHOR', label: 'Biên soạn gói' },
      ],
    },
  ],
  account: [
    {
      label: 'Cá nhân',
      items: [
        { id: 'PROFILE', label: 'Hồ sơ' },
        { id: 'NOTIFICATIONS', label: 'Thông báo' },
        { id: 'SHORTCUTS', label: 'Phím tắt' },
      ],
    },
  ],
  admin: [
    {
      label: 'Người & quyền',
      items: [
        { id: 'USERS', label: 'Người dùng & vai trò' },
        { id: 'DATA_SPACES', label: 'Data Space' },
        { id: 'POLICIES', label: 'Chính sách dữ liệu' },
        { id: 'ACCESS_REQUESTS', label: 'Yêu cầu quyền' },
      ],
    },
    {
      label: 'Hệ thống',
      items: [
        { id: 'MODELS', label: 'Mô hình AI' },
        { id: 'INTEGRATIONS', label: 'Tích hợp' },
        { id: 'MONITORING', label: 'Giám sát hệ thống' },
      ],
    },
    {
      label: 'Tuân thủ',
      items: [
        { id: 'AUDIT', label: 'Kiểm toán' },
        { id: 'PRIVACY', label: 'Quyền riêng tư' },
      ],
    },
  ],
};

/** Areas that open on a landing page (route key SINGLE_SCREEN) rather than on a rail page. */
export const AREA_LANDING: ReadonlySet<AreaId> = new Set([
  'home',
  'needs-you',
  'analysis',
  'data',
  'admin',
]);

/** The page an area opens on: undefined = its landing; otherwise the first rail item. */
export const firstPageOf = (area: AreaId): string | undefined =>
  AREA_LANDING.has(area) ? undefined : AREA_RAILS[area]?.[0]?.items[0]?.id;

/** Detail pages that live under a rail item: /agents/run/<id> keeps "Lượt chạy" selected. */
const RAIL_PARENT: Partial<Record<AreaId, Record<string, string>>> = {
  agents: {
    RUN: 'RUNS',
    AGENT: 'TEAM',
    BUILDER: 'TEAM',
    SKILL_EDITOR: 'SKILLS',
    SKILL_RUN: 'SKILLS',
    SKILL_FROM_SESSION: 'SKILLS',
    SKILL_TESTS: 'SKILL_VERSIONS',
  },
  analysis: {
    REPORT: 'SESSIONS',
    ARTIFACT: 'SESSIONS',
    FORECAST_JOB: 'SESSIONS',
    FORECAST_STUDIO: 'FORECAST',
    FORECAST_MODEL: 'FORECAST',
    FUEL_FORECAST: 'FORECAST',
    FORECAST_LEDGER: 'FORECAST',
    RECEIPT: 'ASK',
    DOC_ASK: 'ASK',
    WATCH_NEW: 'WATCH',
  },
  data: {
    SOURCE: 'SOURCES',
    TABLE: 'SOURCES',
    CONNECT: 'SOURCES',
    COLLECTION: 'COLLECTIONS',
    JOB_NEW: 'JOBS',
    EXPLORE: 'SOURCES',
    CONNECT_API: 'SOURCES',
    DOCUMENT: 'DOCUMENTS',
    PIPELINE: 'PIPELINES',
    PIPELINE_SAVE: 'PIPELINES',
    DATA_MART: 'DATA_MARTS',
    CRAWL_NEW: 'CRAWL',
    CRAWL_SOURCE: 'CRAWL',
    CRAWL_DRIFT: 'CRAWL',
    CRAWL_RUN: 'CRAWL',
    CRAWL_MERGE: 'CRAWL',
    PACK_INSTALL: 'PACKS',
    PACK_UPGRADE: 'PACKS',
    PACK_CALIBRATE: 'PACK_ONBOARD',
    METRIC_OVERRIDE: 'METRIC_CATALOG',
    // The definition editor of one metric, opened from the catalog.
    METRIC: 'METRIC_CATALOG',
  },
  admin: { DATA_SPACE_NEW: 'DATA_SPACES', PERMISSION_COMPARE: 'DATA_SPACES' },
  account: { CHANNEL_RULES: 'NOTIFICATIONS' },
};

/** The rail item a page highlights: itself, or the item its detail page belongs to. */
export const railItemOf = (area: AreaId, page: string | undefined): string | undefined =>
  page ? (RAIL_PARENT[area]?.[page] ?? page) : page;

/** Whether `page` of `area` sits in the area's rail, itself or through its parent (and so draws it). */
export const isRailPage = (area: AreaId, page: string | undefined): boolean => {
  const item = railItemOf(area, page);
  return Boolean(
    item && AREA_RAILS[area]?.some((group) => group.items.some((entry) => entry.id === item)),
  );
};
