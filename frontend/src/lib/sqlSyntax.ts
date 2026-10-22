import { PostgreSQL } from '@codemirror/lang-sql';
import { unknownSqlRelations } from './sqlText';

export interface SqlDiagnostic {
  from: number;
  to: number;
  message: string;
}

/**
 * What the editor can know before the server runs the statement. The SQL parser recovers
 * from almost anything, so a red mark is either a token it could not place or a FROM/JOIN
 * table that is not in the catalog. Write statements stay the server's decision.
 */
export function sqlDiagnostics(sql: string, tables: { name: string }[]): SqlDiagnostic[] {
  const diagnostics: SqlDiagnostic[] = [];
  PostgreSQL.language.parser.parse(sql).iterate({
    enter(node) {
      if (!node.type.isError || diagnostics.length >= 8) return;
      const to = node.to > node.from ? node.to : node.from + 1;
      diagnostics.push({
        from: node.from,
        to,
        message: 'Cú pháp SQL không hợp lệ',
      });
    },
  });
  if (diagnostics.length >= 8) return diagnostics;
  for (const relation of unknownSqlRelations(sql, tables)) {
    diagnostics.push({
      from: relation.from,
      to: relation.to > relation.from ? relation.to : relation.from + 1,
      message: `Không có bảng ${relation.name}`,
    });
    if (diagnostics.length >= 8) break;
  }
  return diagnostics;
}
