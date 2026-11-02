import type { ExploreModel } from './v1/explore';

/**
 * An example question built from the Data Space's own model (GEN-41): its first certified metric,
 * cut by the first dimension that metric allows. A screen never shows an example from one
 * industry ("dư nợ", "xăng dầu") to a customer of another; with no model loaded yet the example
 * names no metric at all.
 */
export function sampleQuestion(
  model: ExploreModel | null | undefined,
  shape: 'this_month' | 'compare_quarter' = 'this_month',
): string {
  const metric = model?.metrics.find((m) => m.certified) ?? model?.metrics[0];
  if (!metric) {
    return shape === 'compare_quarter'
      ? 'so sánh một chỉ số quý này với quý trước'
      : 'một chỉ số theo tháng năm nay';
  }
  const dimension = model?.dimensions.find((d) => metric.allowed_dimension_ids.includes(d.id));
  const by = dimension ? ` theo ${dimension.label.toLowerCase()}` : '';
  const name = metric.label.charAt(0).toLowerCase() + metric.label.slice(1);
  return shape === 'compare_quarter'
    ? `so sánh ${name}${by} quý này với quý trước`
    : `${name}${by} tháng này`;
}

/**
 * Questions to start from on an empty chat, one per certified metric (up to `count`), in the shapes
 * the Understander reads by rule: the metric this year, cut by a dimension it allows, against last
 * quarter, and its top five. Built from the Data Space's own model, so every one names a metric that
 * exists; a space with no certified metric offers none rather than an example that would be refused.
 */
export function starterQuestions(model: ExploreModel | null | undefined, count = 4): string[] {
  const metrics = (model?.metrics ?? []).filter((m) => m.certified);
  const out: string[] = [];
  metrics.slice(0, count).forEach((metric, index) => {
    const dimension = model?.dimensions.find((d) => metric.allowed_dimension_ids.includes(d.id));
    const name = metric.label;
    const lowered = name.charAt(0).toLowerCase() + name.slice(1);
    const by = dimension ? dimension.label.toLowerCase() : null;
    const shapes = [
      `${name} năm nay`,
      by ? `${name} theo ${by} năm nay` : `${name} theo tháng năm nay`,
      `${name} quý này so với quý trước`,
      by ? `Top 5 ${by} có ${lowered} cao nhất năm nay` : `${name} năm nay`,
    ];
    out.push(shapes[index % shapes.length]);
  });
  return out;
}
