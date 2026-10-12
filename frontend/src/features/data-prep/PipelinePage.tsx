import { Fragment, useEffect, useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Composer } from '../../design-system/Composer';
import { EmptyState } from '../../design-system/EmptyState';
import { Notice } from '../../design-system/Notice';
import { useToast } from '../../design-system/Toast';
import { formatNumber } from '../../lib/formatVN';
import { cn } from '../../lib/utils';
import {
  getPipeline,
  getStepPreview,
  sendPrepMessage,
  testRunPipeline,
  type PipelineDetail,
  type PipelineStep,
} from '../../lib/v1/dataPrep';
import { useV1 } from '../../lib/v1/useV1';
import { AccessDenied, forbiddenOf } from '../access/AccessDenied';

/*
 * "P06 · Làm sạch bằng hội thoại" (2288:17795, UC-06-01): the user describes the cleaning in
 * words, the Agent Chuẩn bị dữ liệu adds or edits steps and points out what looks wrong; every
 * step shows rows in and out, and one step at a time is previewed before and after. Each step is
 * readable SQL ("Xem SQL"), the agent never writes to the source, and rows it drops go to an
 * exceptions table. "Chạy thử toàn bộ" runs every step on the full input without writing;
 * "Lưu thành bảng" goes on to the output table and schedule.
 */
function changeText(step: PipelineStep): { text: string; tone: 'new' | 'muted' } | null {
  if (step.is_new) return { text: 'mới', tone: 'new' };
  if (step.kind_label) return { text: step.kind_label, tone: 'muted' };
  const delta = step.rows_out - step.rows_in;
  return delta === 0
    ? null
    : { text: `${delta < 0 ? '−' : '+'}${formatNumber(Math.abs(delta))}`, tone: 'muted' };
}

export default function PipelinePage() {
  const { navigate, routeId } = useAppState();
  const loaded = useV1(routeId ? () => getPipeline(routeId) : null, [routeId]);
  const toast = useToast();
  const [pipeline, setPipeline] = useState<PipelineDetail>();
  const [selected, setSelected] = useState<number>();
  const [showSql, setShowSql] = useState(false);
  const [sending, setSending] = useState(false);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState<string>();

  useEffect(() => {
    setPipeline(loaded.data);
  }, [loaded.data]);

  const steps = pipeline?.steps ?? [];
  // Preview the first step the agent just added, so the change is the first thing checked.
  const previewSeq = selected ?? steps.find((s) => s.is_new)?.seq ?? steps.at(-1)?.seq;
  const preview = useV1(
    pipeline && previewSeq !== undefined ? () => getStepPreview(pipeline.id, previewSeq) : null,
    [pipeline?.id, previewSeq],
  );

  const send = async (text: string) => {
    if (!pipeline) return;
    setSending(true);
    setError(undefined);
    try {
      setPipeline(await sendPrepMessage(pipeline.id, text));
      setSelected(undefined);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  const testRun = async () => {
    if (!pipeline) return;
    setTesting(true);
    try {
      const r = await testRunPipeline(pipeline.id);
      toast({
        tone: 'success',
        message: `Chạy thử xong · ${formatNumber(r.rows_out)} dòng ra · ${formatNumber(r.excluded_rows)} dòng vào bảng ngoại lệ`,
      });
    } catch (e) {
      toast({ tone: 'error', message: `Chạy thử lỗi: ${(e as Error).message}` });
    } finally {
      setTesting(false);
    }
  };

  // A loaded in a Data Space the viewer may not read: the designed "Thiếu quyền" state.
  const forbidden = forbiddenOf(loaded.error);
  if (forbidden) return <AccessDenied access={forbidden} />;
  if (!routeId)
    return (
      <EmptyState
        className="m-da-3xl"
        icon="layers"
        title="Chưa chọn pipeline"
        description="Mở một pipeline từ danh sách để làm sạch dữ liệu bằng hội thoại."
        action={
          <Button onClick={() => navigate('data', 'PIPELINES')}>Về danh sách pipeline</Button>
        }
      />
    );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <PageHeader
        bordered
        breadcrumb={['Dữ liệu', 'Pipeline', pipeline?.name ?? '…']}
        title={pipeline?.name ?? 'Pipeline'}
        description={
          pipeline
            ? `Đầu vào ${pipeline.input_table} · ${formatNumber(pipeline.input_rows)} dòng · xem trước trên ${formatNumber(pipeline.preview_sample)} dòng`
            : undefined
        }
        actions={
          <>
            <Button kind="ghost" aria-pressed={showSql} onClick={() => setShowSql(!showSql)}>
              {showSql ? 'Ẩn SQL' : 'Xem SQL'}
            </Button>
            <Button loading={testing} disabled={!pipeline || steps.length === 0} onClick={testRun}>
              Chạy thử toàn bộ
            </Button>
            <Button
              kind="primary"
              disabled={!pipeline || steps.length === 0}
              onClick={() => pipeline && navigate('data', 'PIPELINE_SAVE', pipeline.id)}
            >
              Lưu thành bảng
            </Button>
          </>
        }
      />
      <AsyncRegion state={loaded} what="pipeline" skeletonRows={6}>
        {() =>
          pipeline ? (
            <div className="flex min-h-0 flex-1 max-[1023px]:flex-col">
              <section
                aria-label="Hội thoại với Agent Chuẩn bị dữ liệu"
                className="flex w-[360px] shrink-0 flex-col gap-da-lg border-r border-border bg-background px-[20px] pb-da-xl pt-da-xl max-[1023px]:w-full"
              >
                <ol className="flex flex-col gap-da-lg">
                  {pipeline.messages.map((m) => (
                    <li key={m.id} className="flex flex-col gap-da-xs">
                      <p className="text-muted-foreground da-caption">
                        {m.author === 'user' ? 'Bạn' : 'Agent Chuẩn bị dữ liệu'}
                      </p>
                      <p
                        className={cn(
                          'rounded-md px-da-lg py-[10px] text-foreground da-body-small',
                          m.author === 'user'
                            ? 'border border-border bg-card'
                            : 'border-l-2 border-border bg-background',
                        )}
                      >
                        {m.text}
                      </p>
                    </li>
                  ))}
                </ol>
                {error && <Notice tone="error">{error}</Notice>}
                <span className="flex-1" />
                <Composer
                  placeholder="Mô tả bước tiếp theo…"
                  sending={sending}
                  onSubmit={(text) => void send(text)}
                />
              </section>
              <section className="flex min-w-0 flex-1 flex-col gap-da-lg px-da-2xl pb-da-2xl pt-da-xl">
                <h2 className="text-foreground da-heading-3">Các bước</h2>
                {steps.length === 0 ? (
                  <p className="text-muted-foreground da-body-small">
                    Chưa có bước nào. Mô tả việc cần làm ở khung hội thoại bên trái.
                  </p>
                ) : (
                  <table className="w-full table-fixed border-collapse">
                    <thead>
                      <tr className="border-b border-border text-left text-muted-foreground da-caption">
                        <th className="w-[48px] px-da-lg py-da-md font-normal">#</th>
                        <th className="px-da-lg py-da-md font-normal">Bước</th>
                        <th className="w-[124px] px-da-lg py-da-md font-normal">Dòng vào</th>
                        <th className="w-[124px] px-da-lg py-da-md font-normal">Dòng ra</th>
                        <th className="w-[84px] px-da-lg py-da-md font-normal">
                          <span className="sr-only">Thay đổi</span>
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {steps.map((s) => {
                        const change = changeText(s);
                        return (
                          <Fragment key={s.seq}>
                            <tr
                              aria-selected={s.seq === previewSeq}
                              tabIndex={0}
                              onClick={() => setSelected(s.seq)}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter' || e.key === ' ') {
                                  e.preventDefault();
                                  setSelected(s.seq);
                                }
                              }}
                              className={cn(
                                'cursor-pointer border-b border-border',
                                s.seq === previewSeq ? 'bg-accent' : 'hover:bg-subtle',
                              )}
                            >
                              <td className="px-da-lg py-[9px] text-muted-foreground da-mono">
                                {s.seq}
                              </td>
                              <td className="px-da-lg py-[9px] text-foreground da-body-small">
                                {s.title}
                              </td>
                              <td className="px-da-lg py-[9px] text-muted-foreground da-mono">
                                {formatNumber(s.rows_in)}
                              </td>
                              <td className="px-da-lg py-[9px] text-foreground da-mono">
                                {formatNumber(s.rows_out)}
                              </td>
                              <td
                                className={cn(
                                  'px-da-lg py-[9px] da-caption',
                                  change?.tone === 'new' ? 'text-primary' : 'text-muted-foreground',
                                )}
                              >
                                {change?.text}
                              </td>
                            </tr>
                            {showSql && (
                              <tr className="border-b border-border bg-subtle">
                                <td />
                                <td colSpan={4} className="px-da-lg py-da-md">
                                  <pre className="whitespace-pre-wrap text-foreground da-mono">
                                    {s.sql}
                                  </pre>
                                </td>
                              </tr>
                            )}
                          </Fragment>
                        );
                      })}
                    </tbody>
                  </table>
                )}
                {previewSeq !== undefined && (
                  <>
                    <h2 className="text-foreground da-heading-3">
                      Xem trước bước {previewSeq} · trước và sau
                    </h2>
                    <AsyncRegion state={preview} what="bản xem trước" skeletonRows={3}>
                      {(p) => (
                        <table className="w-full table-fixed border-collapse">
                          <thead>
                            <tr className="border-b border-border text-left text-muted-foreground da-caption">
                              <th className="w-[164px] px-da-lg py-da-md font-normal">
                                {p.key_column}
                              </th>
                              <th className="w-[174px] px-da-lg py-da-md font-normal">
                                {p.column} trước
                              </th>
                              <th className="w-[174px] px-da-lg py-da-md font-normal">
                                {p.column} sau
                              </th>
                              <th className="px-da-lg py-da-md font-normal">Ghi chú</th>
                            </tr>
                          </thead>
                          <tbody>
                            {p.rows.map((r) => (
                              <tr key={r.key} className="border-b border-border">
                                <td className="px-da-lg py-[9px] text-foreground da-mono">
                                  {r.key}
                                </td>
                                <td className="px-da-lg py-[9px] text-muted-foreground da-mono">
                                  {r.before}
                                </td>
                                <td
                                  className={cn(
                                    'px-da-lg py-[9px] da-mono',
                                    r.before === r.after ? 'text-foreground' : 'text-primary',
                                  )}
                                >
                                  {r.after}
                                </td>
                                <td className="px-da-lg py-[9px] text-muted-foreground da-body-small">
                                  {r.note}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                    </AsyncRegion>
                  </>
                )}
                <p className="text-muted-foreground da-caption">
                  Mỗi bước là SQL đọc được; agent không ghi vào nguồn.
                </p>
              </section>
            </div>
          ) : null
        }
      </AsyncRegion>
    </div>
  );
}
