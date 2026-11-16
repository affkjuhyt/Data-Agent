import type { ExploreMetric, ExploreModel } from './v1/explore';

/*
 * Example and starter questions built from the Data Space's own model (GEN-41): a screen never
 * shows an example from one industry ("dư nợ", "xăng dầu") to a customer of another.
 *
 * No question names a period relative to today ("năm nay", "quý này"): a space's data may stop
 * years before (AdventureWorks ends in early 2014), and those starters were answered "không có dữ
 * liệu". Without a period the agent reads the latest period the data has (E5).
 */

const lower = (text: string) => text.charAt(0).toLowerCase() + text.slice(1);

// A cut along time read as a member list: "Top 5 năm tài chính có …" (17/11). Time is asked as
// "theo tháng" / "theo quý" instead.
const TIME_LABEL = /^(năm|quý|tháng|tuần|ngày|kỳ)(?=\s|$)/i;

/** The first cut of a metric that is not along time. */
const cutOf = (model: ExploreModel | null | undefined, metric: ExploreMetric) =>
  model?.dimensions.find(
    (d) => metric.allowed_dimension_ids.includes(d.id) && !d.is_time && !TIME_LABEL.test(d.label),
  );

/** The certified metrics, those cut by the most dimensions first: the list's own order put the
 *  narrowest ones ("Số tài chính ngân sách", "Sự cố trên 100 cuộc gọi") first. */
function startingMetrics(model: ExploreModel | null | undefined): ExploreMetric[] {
  return (model?.metrics ?? [])
    .filter((m) => m.certified)
    .map((m, i) => ({ m, i }))
    .sort((a, b) => b.m.allowed_dimension_ids.length - a.m.allowed_dimension_ids.length || a.i - b.i)
    .map(({ m }) => m);
}

export function sampleQuestion(
  model: ExploreModel | null | undefined,
  shape: 'this_month' | 'compare_quarter' = 'this_month',
): string {
  const metric = startingMetrics(model)[0] ?? model?.metrics[0];
  if (!metric) {
    return shape === 'compare_quarter' ? 'so sánh một chỉ số theo quý' : 'một chỉ số theo tháng';
  }
  const dimension = cutOf(model, metric);
  const by = dimension ? ` theo ${dimension.label.toLowerCase()}` : ' theo tháng';
  return shape === 'compare_quarter' ? `so sánh ${lower(metric.label)} theo quý` : `${lower(metric.label)}${by}`;
}

/**
 * Questions to start from on an empty chat, one per certified metric (up to `count`), in shapes the
 * agent answers from the data alone: the metric, cut by a dimension it allows, by month, and its
 * top five. A space with no certified metric offers none rather than an example that would be
 * refused.
 */
export function starterQuestions(model: ExploreModel | null | undefined, count = 4): string[] {
  return startingMetrics(model)
    .slice(0, count)
    .map((metric, index) => {
      const dimension = cutOf(model, metric);
      const name = metric.label;
      const by = dimension ? dimension.label.toLowerCase() : null;
      const shapes = [
        by ? `${name} theo ${by}` : `${name} theo tháng`,
        `${name} theo tháng`,
        by ? `Top 5 ${by} có ${lower(name)} cao nhất` : `${name} theo quý`,
        `${name} theo quý`,
      ];
      return shapes[index % shapes.length];
    });
}

/**
 * Starters on the documents the Data Space holds, each naming one by its title ("Tóm tắt các điểm
 * chính của tài liệu “Dealer Return and Warranty Policy”"): the generic "Quy định hoặc chính sách
 * nào đang hiệu lực liên quan đến chủ đề này?" named no topic to look for.
 */
export function documentStarterQuestions(titles: string[], count = 2): string[] {
  return [...new Set(titles.map((t) => t.trim()).filter(Boolean))]
    .slice(0, count)
    .map((title) => `Tóm tắt các điểm chính của tài liệu “${title}”`);
}
