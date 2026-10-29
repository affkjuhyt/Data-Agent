import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Notice } from '../../design-system/Notice';
import { StatusDot } from '../../design-system/StatusDot';
import { getDataSpace } from '../../lib/v1/adminAccess';
import { getSpaceSetup, type SetupStepKey } from '../../lib/v1/spaceSetup';
import { useV1 } from '../../lib/v1/useV1';

/*
 * R1-15 "Thiết lập Data Space": the onboarding checklist of one Data Space for a design customer —
 * their 10–20 questions, the sources holding the answers, the metrics that compute them, and the
 * eval run that proves it — each with where it stands, what is left by name, and the screen that
 * does the work. No frame in the design: it is made of the screens it points to. Those screens act
 * on the Data Space of the top bar, so going to one first selects this Data Space there.
 */
const STEPS: Record<SetupStepKey, { title: string; cta: string; page: string }> = {
  questions: { title: 'Câu hỏi của khách', cta: 'Thêm câu hỏi', page: 'CONTEXT_EVAL' },
  sources: { title: 'Nguồn dữ liệu', cta: 'Kết nối nguồn', page: 'SOURCES' },
  metrics: { title: 'Chỉ số', cta: 'Duyệt chỉ số', page: 'METRIC_CATALOG' },
  eval: { title: 'Bộ đánh giá', cta: 'Chạy bộ đánh giá', page: 'CONTEXT_EVAL' },
};

export default function DataSpaceSetupPage() {
  const { routeId, navigate, setDataSpace } = useAppState();
  const id = routeId ?? '';
  const space = useV1(['getDataSpace', id], id ? () => getDataSpace(id) : null);
  const setup = useV1(['getSpaceSetup', id], id ? () => getSpaceSetup(id) : null);
  const name = space.data?.name;

  const open = (page: string) => {
    if (space.data) setDataSpace({ id: space.data.id, name: space.data.name });
    navigate('data', page);
  };

  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Quản trị', 'Data Space']}
        title={name ? `${name} · thiết lập` : 'Thiết lập Data Space'}
        description="Từ câu hỏi của khách tới nguồn, chỉ số và bộ đánh giá đạt."
        actions={
          <Button kind="ghost" onClick={() => navigate('admin', 'DATA_SPACES', id)}>
            Thành viên và quyền
          </Button>
        }
      />
      <div className="flex flex-col gap-da-lg px-da-3xl py-da-xl">
        <AsyncRegion state={setup} what="trạng thái thiết lập" skeletonRows={4}>
          {(s) => (
            <>
              {s.ready ? (
                <Notice tone="success">
                  Data Space đã sẵn sàng: câu hỏi của khách được trả lời đúng trên nguồn đã kết nối.
                </Notice>
              ) : (
                <Notice tone="info">
                  Còn {s.steps.filter((step) => !step.done).length} bước trước khi Data Space sẵn
                  sàng cho khách.
                </Notice>
              )}
              <ol className="flex flex-col gap-da-md">
                {s.steps.map((step, i) => (
                  <li
                    key={step.key}
                    aria-label={STEPS[step.key].title}
                    className="flex items-start gap-da-lg rounded-md border border-border bg-card px-da-xl py-da-lg"
                  >
                    <span className="w-[20px] shrink-0 text-muted-foreground da-mono">{i + 1}</span>
                    <div className="flex min-w-0 flex-1 flex-col gap-da-xs">
                      <div className="flex items-center gap-da-md">
                        <span className="text-foreground da-body-strong">
                          {STEPS[step.key].title}
                        </span>
                        <StatusDot tone={step.done ? 'success' : 'warning'}>
                          {step.done ? 'Xong' : 'Chưa xong'}
                        </StatusDot>
                      </div>
                      <span className="text-muted-foreground da-body-small">{step.summary}</span>
                      {step.pending.length > 0 && (
                        <ul className="flex flex-col gap-[2px] pt-da-xs">
                          {step.pending.map((left) => (
                            <li key={left} className="text-foreground da-caption">
                              · {left}
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                    {!step.done && (
                      <Button size="sm" onClick={() => open(STEPS[step.key].page)}>
                        {STEPS[step.key].cta}
                      </Button>
                    )}
                  </li>
                ))}
              </ol>
            </>
          )}
        </AsyncRegion>
      </div>
    </div>
  );
}
