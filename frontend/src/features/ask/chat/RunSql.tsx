import { useEffect, useState } from 'react';
import { Icon } from '../../../design-system/Icon';
import { V1Error } from '../../../lib/v1/client';
import { getQuerySql } from '../../../lib/v1/explore';

/** The SQL a query run executed, read through /v1/query-runs/{id}/sql: that route checks
 *  view_detail and records the read, so neither a step nor an answer block carries the text. */
export function RunSql({ queryRunId }: { queryRunId: string }) {
  const [sql, setSql] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    getQuerySql(queryRunId)
      .then((v) => live && setSql(v.sql_executed ?? ''))
      .catch((e) => {
        if (!live) return;
        setProblem(
          e instanceof V1Error && (e.status === 403 || e.status === 404)
            ? 'Bạn không có quyền xem SQL của lượt chạy này.'
            : 'Không tải được SQL.',
        );
      });
    return () => {
      live = false;
    };
  }, [queryRunId]);
  if (problem) return <p className="m-0 text-muted-foreground da-caption">{problem}</p>;
  if (sql === null) return <p className="m-0 text-muted-foreground da-caption">Đang tải SQL…</p>;
  return (
    <div className="flex flex-col gap-da-xs">
      <div className="flex items-center justify-between">
        <span className="text-muted-foreground da-caption">SQL đã chạy</span>
        <button
          type="button"
          aria-label="Sao chép SQL"
          onClick={() => void navigator.clipboard?.writeText(sql)}
          className="inline-flex items-center gap-[4px] rounded-sm px-[4px] text-muted-foreground da-caption hover:text-foreground"
        >
          <Icon name="copy" size={12} /> Sao chép
        </button>
      </div>
      <pre className="m-0 max-h-[240px] overflow-auto whitespace-pre-wrap rounded-sm bg-card p-da-sm da-mono text-foreground">{sql}</pre>
    </div>
  );
}
