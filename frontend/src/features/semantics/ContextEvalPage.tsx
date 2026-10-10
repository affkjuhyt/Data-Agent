import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Notice } from '../../design-system/Notice';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import { Tooltip } from '../../design-system/Tooltip';
import { cn } from '../../lib/utils';
import {
  getContextEval,
  listEvalSets,
  runContextEval,
  type ContextEval,
} from '../../lib/v1/semanticLayer';
import { useV1 } from '../../lib/v1/useV1';

/*
 * "v3.1 · Semantic · Đánh giá lớp ngữ cảnh" (2232:15323, UC-07-06): the benchmark questions of a
 * Data Space run again whenever a metric, term or mapping changes; a change that lowers the number
 * answered right is not published. The figures of the latest run, each run with the change it
 * tried and what became of it, and the questions the latest run got wrong with why — most lead
 * back to the glossary. Opened without an id, the first evaluation set.
 */
const RESULT = {
  published: { label: 'Được phát hành', tone: 'success' },
  released: { label: 'Đã phát hành', tone: 'idle' },
  blocked: { label: 'Bị chặn', tone: 'error' },
} as const;
const KPI_TONE = { success: 'text-success', error: 'text-error', neutral: 'text-muted-foreground' };

export default function ContextEvalPage() {
  const { routeId } = useAppState();
  const sets = useV1(routeId ? null : listEvalSets, [routeId]);
  const id = routeId || sets.data?.items[0]?.id;
  const evaluation = useV1(id ? () => getContextEval(id) : null, [id]);
  return (
    <AsyncRegion state={evaluation} what="đánh giá lớp ngữ cảnh">
      {(e) => <EvalBody evaluation={e} />}
    </AsyncRegion>
  );
}

function EvalBody({ evaluation }: { evaluation: ContextEval }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();

  const rerun = async () => {
    setBusy(true);
    setError(undefined);
    try {
      await runContextEval(evaluation.id);
      toast({ tone: 'info', message: 'Đang chạy lại toàn bộ bộ câu hỏi chuẩn' });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-full flex-col gap-da-md px-da-xl pb-da-2xl pt-da-lg">
      <PageHeader
        breadcrumb={[
          'Dữ liệu',
          'Ngữ nghĩa',
          `Đánh giá lớp ngữ cảnh · ${evaluation.data_space_name}`,
        ]}
        title="Đánh giá lớp ngữ cảnh"
        description="Bộ câu hỏi chuẩn chạy lại mỗi khi chỉ số, thuật ngữ hay ánh xạ thay đổi. Thay đổi làm giảm số câu đúng không được phát hành."
        actions={
          <Button loading={busy} onClick={rerun}>
            Chạy lại toàn bộ
          </Button>
        }
      />
      {error && <Notice tone="error">Không chạy được: {error}</Notice>}
      <dl className="grid grid-cols-4 gap-da-md max-[1023px]:grid-cols-2">
        {evaluation.kpis.map((k) => (
          <div
            key={k.label}
            className="flex flex-col gap-[4px] rounded-md border border-border bg-card px-da-lg py-da-md"
          >
            <dt className="text-muted-foreground da-caption">{k.label}</dt>
            <dd className="text-foreground da-metric-small">{k.value}</dd>
            <dd className={cn('da-caption', KPI_TONE[k.tone])}>{k.note}</dd>
          </div>
        ))}
      </dl>
      <h2 className="text-foreground da-heading-3">Các lượt đánh giá</h2>
      <table className="w-full table-fixed border-collapse overflow-hidden rounded-md border border-border da-body-small">
        <thead>
          <tr className="border-b border-border bg-subtle text-left text-muted-foreground da-caption">
            <th className="w-[104px] px-da-md py-da-sm font-normal">Lượt</th>
            <th className="px-da-md py-da-sm font-normal">Thay đổi được thử</th>
            <th className="w-[90px] px-da-md py-da-sm font-normal">Đúng</th>
            <th className="w-[120px] px-da-md py-da-sm font-normal">So với trước</th>
            <th className="w-[220px] px-da-md py-da-sm font-normal">Kết quả</th>
          </tr>
        </thead>
        <tbody>
          {evaluation.runs.map((r) => (
            <tr key={r.id} className="border-b border-border text-foreground last:border-b-0">
              <td className="px-da-md py-[9px]">{r.label}</td>
              <td className="px-da-md py-[9px]">{r.change}</td>
              <td className="px-da-md py-[9px]">{r.correct}</td>
              <td className="px-da-md py-[9px]">
                {r.delta > 0 ? `+${r.delta}` : `${r.delta}`.replace('-', '−')}
              </td>
              <td className="px-da-md py-[9px]">
                <StatusDot tone={RESULT[r.result].tone}>{RESULT[r.result].label}</StatusDot>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <h2 className="text-foreground da-heading-3">
        Câu trả lời sai ở lượt {evaluation.latest_run_label}
      </h2>
      <table className="w-full table-fixed border-collapse overflow-hidden rounded-md border border-border da-body-small">
        <thead>
          <tr className="border-b border-border bg-subtle text-left text-muted-foreground da-caption">
            <th className="px-da-md py-da-sm font-normal">Câu hỏi</th>
            <th className="px-da-md py-da-sm font-normal">Mong đợi</th>
            <th className="px-da-md py-da-sm font-normal">Agent hiểu là</th>
            <th className="w-[200px] px-da-md py-da-sm font-normal">Nguyên nhân</th>
          </tr>
        </thead>
        <tbody>
          {evaluation.misses.map((m) => (
            <tr key={m.question} className="border-b border-border text-foreground last:border-b-0">
              <td className="px-da-md py-[9px]">{m.question}</td>
              <td className="px-da-md py-[9px]">{m.expected}</td>
              <td className="px-da-md py-[9px]">{m.understood}</td>
              <td className="px-da-md py-[9px]">{m.cause}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="flex flex-wrap gap-da-md">
        <Button kind="primary" onClick={() => navigate('data', 'GLOSSARY')}>
          Sửa từ điển nghiệp vụ
        </Button>
        <Tooltip content="Thêm câu hỏi từ phản hồi 👎 đang được làm.">
          <Button kind="ghost" disabled>
            Thêm câu hỏi từ phản hồi 👎
          </Button>
        </Tooltip>
      </div>
    </div>
  );
}
