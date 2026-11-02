import { Icon } from '../../../design-system/Icon';
import type { TurnStep } from '../steps';
import { RunSql } from './RunSql';

/*
 * What one step did, by its action (the step contract's detail). The SQL a query ran is not in
 * the step: it is read through its query run, which checks view_detail and records the read.
 */

const str = (v: unknown) => (typeof v === 'string' ? v : v === null || v === undefined ? '' : String(v));
const list = (v: unknown) => (Array.isArray(v) ? v : []);
const rec = (v: unknown) => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : {});

const READING_KIND: Record<string, string> = { metric: 'Chỉ số', dimension: 'Chiều', period: 'Kỳ', filter: 'Lọc' };

const READING_SOURCE: Record<string, string> = {
  default: 'từ câu hỏi',
  inherited: 'từ câu trước',
  user_edit: 'bạn sửa',
  user_pref: 'theo thói quen của bạn',
  memory: 'từ trí nhớ',
};

function Pairs({ rows }: { rows: [string, string][] }) {
  return (
    <dl className="m-0 grid grid-cols-[max-content_1fr] gap-x-da-md gap-y-[2px] da-caption">
      {rows
        .filter(([, value]) => value !== '')
        .map(([key, value]) => (
          <div key={key} className="contents">
            <dt className="text-muted-foreground">{key}</dt>
            <dd className="m-0 text-foreground">{value}</dd>
          </div>
        ))}
    </dl>
  );
}

function ModelCalls({ calls }: { calls: TurnStep[] }) {
  if (calls.length === 0) return null;
  return (
    <ul className="m-0 flex list-none flex-col gap-[2px] p-0 da-caption text-muted-foreground">
      {calls.map((call) => {
        const d = rec(call.detail);
        return (
          <li key={call.stepId}>
            {`Mô hình ${str(d.model)} · ${str(d.input_tokens)} + ${str(d.output_tokens)} token · ${str(d.latency_ms)} ms`}
            {call.status === 'failed' && call.summary ? ` · ${call.summary}` : ''}
          </li>
        );
      })}
    </ul>
  );
}

function Body({ step }: { step: TurnStep }) {
  const d = rec(step.detail);
  switch (step.action) {
    case 'understand': {
      const reading = list(d.reading).map(rec);
      return (
        <div className="flex flex-col gap-da-xs">
          {reading.length === 0 ? (
            <p className="m-0 text-muted-foreground da-caption">Chưa nhận ra chỉ số, chiều hay kỳ nào.</p>
          ) : (
            <ul className="m-0 flex list-none flex-wrap gap-da-xs p-0">
              {reading.map((chip, i) => (
                <li key={i} className="inline-flex items-center gap-[4px] rounded-sm border border-border bg-card px-[6px] py-[2px] da-caption">
                  <span className="text-muted-foreground">{READING_KIND[str(chip.kind)] ?? str(chip.kind)}</span>
                  <span className="text-foreground">{str(chip.label)}</span>
                  <span className="text-muted-foreground">· {READING_SOURCE[str(chip.source)] ?? str(chip.source)}</span>
                </li>
              ))}
            </ul>
          )}
          {list(d.unmapped).length > 0 && (
            <p className="m-0 text-muted-foreground da-caption">Chưa hiểu: {list(d.unmapped).map(str).join(', ')}</p>
          )}
        </div>
      );
    }
    case 'choose_metric': {
      const metric = rec(d.metric);
      return (
        <Pairs
          rows={[
            ['Chỉ số', `${str(metric.name)} (${str(metric.code)})`],
            ['Phiên bản', str(metric.version)],
            ['Trạng thái', metric.certified ? 'Đã chứng nhận' : 'Chưa chứng nhận'],
            ['Bảng', list(d.datasets).map(str).join(', ')],
            ['Nguồn', str(d.source)],
          ]}
        />
      );
    }
    case 'query':
    case 'use_verified':
    case 'write_sql': {
      const runId = str(d.query_run_id);
      return (
        <div className="flex flex-col gap-da-sm">
          <Pairs
            rows={[
              ['Kết quả', d.row_count !== undefined ? `${str(d.row_count)} dòng${d.truncated ? ' (đã cắt bớt)' : ''}` : ''],
              ['Cột', list(d.columns).map(str).join(', ')],
              ['Cách viết', d.candidates !== undefined ? `${str(d.agreeing)}/${str(d.candidates)} cách viết trùng kết quả` : ''],
              ['Bị loại', d.rejected ? `${str(d.rejected)} cách viết` : ''],
              ['Truy vấn đã duyệt', str(d.name)],
            ]}
          />
          {runId && <RunSql queryRunId={runId} />}
        </div>
      );
    }
    case 'validate':
      return (
        <ul className="m-0 flex list-none flex-col gap-[2px] p-0 da-caption">
          {list(d.checks).map(rec).map((check, i) => (
            <li key={i} className="flex items-center gap-da-xs">
              <Icon name={check.passed ? 'check' : 'x'} size={12} className={check.passed ? 'text-success' : 'text-error'} />
              <span className="text-foreground">{str(check.name)}</span>
              <span className="text-muted-foreground">{str(check.detail)}</span>
            </li>
          ))}
        </ul>
      );
    case 'visualize':
      return <Pairs rows={[['Biểu đồ', str(d.chart_type)], ['Vì', str(d.reason)]]} />;
    case 'search_documents':
      return (
        <div className="flex flex-col gap-[2px] da-caption">
          <span className="text-muted-foreground">{str(d.found)} đoạn tìm thấy</span>
          <ul className="m-0 list-none p-0">
            {list(d.hits).map(rec).map((hit, i) => (
              <li key={i} className="text-foreground">
                {str(hit.title)} · trang {str(hit.page)}
              </li>
            ))}
          </ul>
        </div>
      );
    case 'ask_user':
      return (
        <div className="flex flex-col gap-[2px] da-caption">
          <span className="text-foreground">{str(d.question)}</span>
          {list(d.options).length > 0 && (
            <span className="text-muted-foreground">Lựa chọn: {list(d.options).map(str).join(' · ')}</span>
          )}
        </div>
      );
    case 'use_tool':
      return (
        <div className="flex flex-col gap-da-xs da-caption">
          <Pairs rows={[['Công cụ', str(d.tool)], ['Tham số', d.args ? JSON.stringify(d.args) : '']]} />
          {d.result !== undefined && (
            <pre className="m-0 max-h-[160px] overflow-auto whitespace-pre-wrap rounded-sm bg-card p-da-sm da-mono">{str(d.result)}</pre>
          )}
        </div>
      );
    case 'write_answer':
      return (
        <Pairs
          rows={[
            ['Cách viết', d.by === 'model' ? 'Mô hình viết, số đã đối chiếu' : d.by === 'documents' ? 'Từ tài liệu, có trích dẫn' : d.by === 'general' ? 'Trả lời chung, không có bằng chứng' : d.by ? 'Theo mẫu' : ''],
            ['Câu bị bỏ', d.sentences_dropped ? str(d.sentences_dropped) : ''],
            ['Trích dẫn', d.citations !== undefined ? str(d.citations) : ''],
          ]}
        />
      );
    default:
      return (
        <Pairs rows={Object.entries(d).map(([k, v]) => [k, typeof v === 'object' ? JSON.stringify(v) : str(v)])} />
      );
  }
}

export function StepDetail({ step }: { step: TurnStep }) {
  return (
    <div className="flex flex-col gap-da-sm">
      <Body step={step} />
      <ModelCalls calls={step.children.filter((c) => c.action === 'model_call')} />
    </div>
  );
}
