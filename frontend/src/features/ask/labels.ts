/**
 * Vietnamese labels for the closed vocabularies the backend sends.
 *
 * Slot keys mirror backend/libs/semantic/slots.py (RELATIVE_PERIODS, COMPARE_*),
 * reasons mirror backend/libs/semantic/ambiguity.Reason. Anything unknown falls back
 * to the raw value so a new backend key is still visible, never blank.
 */

const OPTION_LABELS: Record<string, string> = {
  this_month: 'Tháng này',
  last_month: 'Tháng trước',
  this_quarter: 'Quý này',
  last_quarter: 'Quý trước',
  this_year: 'Năm nay',
  last_year: 'Năm trước',
  this_week: 'Tuần này',
  last_week: 'Tuần trước',
  today: 'Hôm nay',
  yesterday: 'Hôm qua',
  previous_period: 'Kỳ trước',
  same_period_last_year: 'Cùng kỳ năm trước',
};

const REASON_LABELS: Record<string, string> = {
  missing_time_range: 'Thiếu kỳ thời gian',
  ambiguous_metric: 'Chưa rõ chỉ số',
  comparison_without_baseline: 'Thiếu mốc so sánh',
};

export const FRESHNESS_LABELS: Record<string, { label: string; tone: 'ok' | 'warn' | 'bad' }> = {
  fresh: { label: 'Dữ liệu mới', tone: 'ok' },
  stale: { label: 'Dữ liệu cũ, quá hạn làm mới', tone: 'bad' },
  unknown: { label: 'Chưa đo độ mới', tone: 'warn' },
};

export const TRUNCATION_LABELS: Record<string, string> = {
  row_limit: 'chỉ trả các dòng đầu (giới hạn số dòng)',
  byte_limit: 'chỉ trả các dòng đầu, chạm cả giới hạn dung lượng',
  cell_limit: 'một số giá trị bị cắt bớt',
};

export const GENERATED_BY_LABELS: Record<string, string> = {
  semantic_layer: 'Chỉ số đã duyệt',
  verified_query: 'Truy vấn đã kiểm',
  llm: 'SQL do mô hình viết',
};

export const CITATION_TAGS: Record<string, string> = {
  kb: 'Tài liệu', document: 'Tài liệu', web: 'Web', mcp: 'MCP', tool: 'Công cụ', db: 'CSDL',
};

export const optionLabel = (key: string): string => OPTION_LABELS[key] ?? key;
export const reasonLabel = (key?: string): string => (key ? REASON_LABELS[key] ?? key : '');
