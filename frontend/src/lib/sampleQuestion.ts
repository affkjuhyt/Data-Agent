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
