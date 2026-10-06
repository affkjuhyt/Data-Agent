import type { EvidenceRun, ResultSet } from './types';

/** Compatibility adapter for evidence widgets. Query rows always come from
 * `__result_set`; `__evidence.runs` is no longer the table source of truth. */
export function resultSetToEvidenceRun(result: ResultSet): EvidenceRun {
  const provenance = result.provenance;
  const freshness = provenance.freshness
    ? {
        ...provenance.freshness,
        tables: provenance.freshness.tables?.length
          ? provenance.freshness.tables
          : provenance.tables.map(table => ({ table })),
      }
    : provenance.tables.length
      ? { status: 'unknown', tables: provenance.tables.map(table => ({ table })) }
      : null;

  return {
    id: result.result_id,
    kind: provenance.engine === 'pandas' ? 'pandas' : 'sql',
    db_name: provenance.connection_id,
    db_type: provenance.engine,
    sql: provenance.sql ?? '',
    sql_hash: provenance.sql_hash,
    executed_at: provenance.executed_at,
    watermark: provenance.watermark,
    row_count: result.row_count,
    generated_by: provenance.generated_by,
    trust: result.trust,
    metric: provenance.metric,
    provenance: freshness,
    truncation: provenance.truncation,
    aggregation: provenance.aggregation,
    columns: result.columns.map(column => column.name),
    column_units: Object.fromEntries(
      result.columns.filter(column => column.unit).map(column => [column.name, column.unit as string]),
    ),
    rows: result.rows_preview,
    rows_capped: result.row_count > result.rows_preview.length,
    result_warnings: result.warnings,
    stats: result.stats,
    citation_index: result.citation_index ?? undefined,
    export: result.full_rows_available
      ? {
          result_id: result.result_id,
          preview_rows: result.rows_preview.length,
          total_rows: result.row_count,
          full_available: true,
          xlsx_available: true,
          file_name: `ask-result-${result.result_id.slice(0, 8)}.xlsx`,
          sheets: ['Dữ liệu', 'Nguồn'],
        }
      : undefined,
  };
}
