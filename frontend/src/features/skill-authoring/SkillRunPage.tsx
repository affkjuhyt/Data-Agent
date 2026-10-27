import { useState } from 'react';
import { useAppState } from '../../AppStateContext';
import { PageHeader } from '../../components/shell/PageHeader';
import { AsyncRegion } from '../../design-system/AsyncRegion';
import { Button } from '../../design-system/Button';
import { Input } from '../../design-system/Input';
import { Notice } from '../../design-system/Notice';
import { Select } from '../../design-system/Select';
import { SortableTh, TablePager, TableToolbar, useTableView } from '../../design-system/tableView';
import { StatusDot } from '../../design-system/StatusDot';
import { useToast } from '../../design-system/Toast';
import {
  addRunToSession,
  getSkillRunForm,
  listSkillRuns,
  runSkill,
  type SkillRun,
  type SkillRunForm,
} from '../../lib/v1/skillAuthoring';
import type { Page } from '../../lib/v1/client';
import { useV1, type V1State } from '../../lib/v1/useV1';
import { ExternalFactorsView } from './ExternalFactorsView';
import { Waterfall } from './Waterfall';
import { AccessDenied, forbiddenOf } from '../access/AccessDenied';
import { LinkButton } from '../../components/shell/AppLink';

/*
 * Running a skill directly (UC-03-01, UC-03-02). The skill's run form decides the screen:
 * a decomposition skill is "P03 · Chạy kỹ năng · Phân rã biến động" (2293:18186) — parameters
 * on a 320px column (only valid choices: dimensions the metric allows), the result as one
 * sentence, a waterfall and the contribution table; the external-factor skill is "P03 · Tìm yếu
 * tố bên ngoài" (2293:18368). Runs use the runner's permissions and record their parameters and
 * skill version; the latest run is shown on arrival, a new one only on "Chạy lại".
 */
export default function SkillRunPage() {
  const { routeId } = useAppState();
  const form = useV1(['getSkillRunForm', routeId], routeId ? () => getSkillRunForm(routeId) : null);
  const latest = useV1(['listSkillRuns', routeId], routeId ? () => listSkillRuns(routeId) : null);
  // A form in a Data Space the viewer may not read: the designed "Thiếu quyền" state.
  const forbidden = forbiddenOf(form.error);
  if (forbidden) return <AccessDenied access={forbidden} />;
  return (
    <AsyncRegion state={form} what="kỹ năng">
      {(f) => <Runner key={f.skill_id} form={f} latest={latest} />}
    </AsyncRegion>
  );
}

function Runner({ form, latest }: { form: SkillRunForm; latest: V1State<Page<SkillRun>> }) {
  const { navigate } = useAppState();
  const toast = useToast();
  const [params, setParams] = useState<Record<string, string>>(() =>
    Object.fromEntries(form.params.map((p) => [p.key, p.value])),
  );
  const [run, setRun] = useState<SkillRun | null>();
  const [busy, setBusy] = useState<'run' | 'add' | null>(null);
  const [error, setError] = useState<string>();
  const current = run === undefined ? latest.data?.items[0] : run;

  const rerun = async (next = params) => {
    setBusy('run');
    setError(undefined);
    try {
      setRun(await runSkill(form.skill_id, next, form.kind));
    } catch (e) {
      setError(`Không chạy được: ${(e as Error).message}`);
    } finally {
      setBusy(null);
    }
  };

  const addToSession = async () => {
    if (!current) return;
    setBusy('add');
    setError(undefined);
    try {
      const { session_id } = await addRunToSession(current.id);
      toast({
        tone: 'success',
        message: 'Đã thêm kết quả vào phiên',
        action: { label: 'Mở phiên', onClick: () => navigate('analysis', 'SESSIONS', session_id) },
      });
    } catch (e) {
      setError(`Không thêm vào phiên được: ${(e as Error).message}`);
    } finally {
      setBusy(null);
    }
  };

  const certified = form.status === 'certified';
  const addButton = (kind: 'primary' | 'secondary') => (
    <Button
      kind={kind}
      loading={busy === 'add'}
      disabled={busy !== null || !current}
      onClick={addToSession}
    >
      Thêm vào phiên
    </Button>
  );

  if (form.kind === 'external_factors') {
    return (
      <ExternalFactorsView
        form={form}
        run={current ?? null}
        loading={latest.loading && current === undefined}
        error={error ?? (latest.error && !current ? latest.error.message : undefined)}
        metric={params.metric}
        busy={busy === 'run'}
        onChangeMetric={(metric) => {
          const next = { ...params, metric };
          setParams(next);
          void rerun(next);
        }}
        addButton={addButton('primary')}
      />
    );
  }

  const result = current?.decomposition;
  const outcome = current?.outcome;
  return (
    <div className="flex min-h-full flex-col">
      <PageHeader
        bordered
        breadcrumb={['Agents', 'Kỹ năng', `${form.name} v${form.version}`]}
        title={
          <span className="inline-flex items-center gap-[10px]">
            {form.name}
            {certified && <StatusDot tone="success">Đã chứng nhận</StatusDot>}
          </span>
        }
        description="Chạy trực tiếp hoặc để agent gọi khi điều tra · kết quả có thể thêm vào phiên"
        actions={
          <>
            <LinkButton area="agents" page="SKILL_EDITOR" id={form.skill_id} kind="ghost">
              Xem cách làm
            </LinkButton>
            {addButton('secondary')}
            <Button
              kind="primary"
              loading={busy === 'run'}
              disabled={busy !== null}
              onClick={() => rerun()}
            >
              Chạy lại
            </Button>
          </>
        }
      />
      <div className="flex flex-1 max-[1279px]:flex-col">
        <section className="flex w-[320px] shrink-0 flex-col gap-[14px] border-r border-border pb-da-2xl pl-da-3xl pr-[20px] pt-[20px] max-[1279px]:w-full max-[1279px]:border-b max-[1279px]:border-r-0">
          <h2 className="text-foreground da-heading-3">Tham số</h2>
          {form.params.map((p) => (
            <div key={p.key} className="flex flex-col gap-da-xs">
              <span aria-hidden className="text-muted-foreground da-caption">
                {p.label}
              </span>
              {p.options.length ? (
                <Select
                  label={p.label}
                  options={p.options}
                  value={params[p.key] ?? null}
                  onChange={(v) => setParams((prev) => ({ ...prev, [p.key]: v }))}
                />
              ) : (
                <Input
                  aria-label={p.label}
                  value={params[p.key] ?? ''}
                  onChange={(e) => {
                    const value = e.target.value;
                    setParams((prev) => ({ ...prev, [p.key]: value }));
                  }}
                />
              )}
            </div>
          ))}
          <p className="text-muted-foreground da-caption">{form.params_note}</p>
        </section>
        <section className="flex min-w-0 flex-1 flex-col gap-[14px] px-da-3xl pb-da-2xl pt-[20px]">
          {error && <Notice tone="error">{error}</Notice>}
          <AsyncRegion
            state={
              run === undefined
                ? latest
                : { ...latest, data: { items: run ? [run] : [], next_page_token: '' } }
            }
            what="kết quả chạy"
            isEmpty={() => !result && !outcome}
            empty={
              <p className="text-muted-foreground da-body-small">
                Chưa có lần chạy nào. Chọn tham số rồi bấm “Chạy lại”.
              </p>
            }
          >
            {() =>
              outcome ? (
                <Outcome outcome={outcome} />
              ) : (
                result && (
                  <>
                    <p className="text-foreground da-reading">{result.summary}</p>
                    <Waterfall start={result.start} steps={result.steps} end={result.end} />
                    <ContributionTable rows={result.contributions} />
                    {(result.method || result.confidence) && (
                      <p className="text-muted-foreground da-body-small">
                        {[result.method, result.confidence && `Mức tin cậy: ${result.confidence}.`]
                          .filter(Boolean)
                          .join(' ')}
                      </p>
                    )}
                    <p className="text-muted-foreground da-caption">
                      Chạy theo quyền của người chạy; mỗi lần chạy lưu tham số và phiên bản kỹ năng
                    </p>
                  </>
                )
              )
            }
          </AsyncRegion>
        </section>
      </div>
    </div>
  );
}

/** A skill A2 serves: its result has no designed view, so the conclusion and figures as written. */
function ContributionTable({
  rows,
}: {
  rows: { label: string; points_text: string; share_text: string }[];
}) {
  const view = useTableView(rows, {
    search: (c) => `${c.label} ${c.points_text} ${c.share_text}`,
    filters: [{ id: 'share', label: 'Tỷ trọng', value: (c) => c.share_text }],
    sorts: [
      { id: 'label', value: (c) => c.label },
      { id: 'points', value: (c) => c.points_text },
      { id: 'share', value: (c) => c.share_text },
    ],
    pageSize: 50,
  });
  return (
    <>
      <TableToolbar view={view} placeholder="Tìm đóng góp…" searchLabel="Tìm đóng góp" />
      <table className="w-full table-fixed border-collapse">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground da-caption">
            <SortableTh view={view} column="label" className="px-da-lg py-da-md font-normal">
              Đóng góp
            </SortableTh>
            <SortableTh view={view} column="points" className="w-[80px] px-da-lg py-da-md font-normal">
              Điểm
            </SortableTh>
            <SortableTh view={view} column="share" className="w-[80px] px-da-lg py-da-md font-normal">
              Tỷ trọng
            </SortableTh>
          </tr>
        </thead>
        <tbody>
          {view.rows.map((c) => (
            <tr key={c.label} className="border-b border-border">
              <td className="px-da-lg py-[9px] text-foreground da-body-small">{c.label}</td>
              <td className="px-da-lg py-[9px] text-foreground da-mono">{c.points_text}</td>
              <td className="px-da-lg py-[9px] text-muted-foreground da-mono">{c.share_text}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <TablePager view={view} />
    </>
  );
}

function Outcome({ outcome }: { outcome: NonNullable<SkillRun['outcome']> }) {
  if (outcome.status === 'running')
    return <StatusDot tone="info">Đang chạy · kết quả hiện khi lượt chạy xong</StatusDot>;
  if (outcome.status === 'failed')
    return <Notice tone="error">Lượt chạy lỗi{outcome.error ? `: ${outcome.error}` : ''}</Notice>;
  return (
    <>
      {outcome.summary && <p className="text-foreground da-reading">{outcome.summary}</p>}
      {outcome.figures.length > 0 && (
        <table className="w-full table-fixed border-collapse">
          <tbody>
            {outcome.figures.map((f) => (
              <tr key={f.label} className="border-b border-border">
                <td className="px-da-lg py-[9px] text-muted-foreground da-body-small">{f.label}</td>
                <td className="px-da-lg py-[9px] text-foreground da-mono">{f.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
