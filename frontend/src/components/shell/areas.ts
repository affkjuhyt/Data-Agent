/*
 * The six areas of the v3.1 navigation (Figma "IA · Kiến trúc v3", "Điều hướng v3.1",
 * approved 08/10/2026) and the secondary rail of the areas that have one.
 * URL: /<area>/<page>, both kebab-case; an area's first page is its landing.
 */
export type AreaId = 'home' | 'needs-you' | 'agents' | 'analysis' | 'data' | 'admin';

export interface AreaDef {
  id: AreaId;
  label: string;
  /** Stays in the top bar under 1280px; the others fold into "☰". */
  pinnedOnNarrow?: boolean;
}

export const AREAS: AreaDef[] = [
  { id: 'home', label: 'Trang chủ', pinnedOnNarrow: true },
  { id: 'needs-you', label: 'Cần bạn', pinnedOnNarrow: true },
  { id: 'agents', label: 'Agents' },
  { id: 'analysis', label: 'Phân tích' },
  { id: 'data', label: 'Dữ liệu' },
  { id: 'admin', label: 'Quản trị' },
];

export interface RailItem {
  id: string;
  label: string;
}

export interface RailGroup {
  label: string;
  items: RailItem[];
}
