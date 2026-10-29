import { type Page, api, ok } from './client';
import type { components } from './schema';

type Schemas = components['schemas'];
type SourceView = Schemas['services__api__modules__catalog__schemas__SourceView'];

/*
 * R1-15 "Onboarding checklist": how far a Data Space is from answering its customer, read from
 * what the api already serves for that space (its sources, metrics and context eval sets, each
 * asked with X-Data-Space set to it). The order is the order a customer is brought in: their
 * 10–20 questions, the sources holding the answers, the metrics that compute them, the eval run
 * that proves it.
 */
export type SetupStepKey = 'questions' | 'sources' | 'metrics' | 'eval';

interface SetupStep {
  key: SetupStepKey;
  done: boolean;
  /** One line: where the step stands. */
  summary: string;
  /** What is left, by name: a source not connected, a metric not approved. */
  pending: string[];
}

interface SpaceSetup {
  steps: SetupStep[];
  ready: boolean;
  /** The context eval set the questions and the eval run are counted on; null without one. */
  evalSetId: string | null;
}

/** The customer's own questions an eval set starts from (backlog R1-15). */
const MIN_QUESTIONS = 10;
// worker_agent/questions.py QUERYABLE: a source the agent may query in place.
const QUERYABLE = new Set(['active', 'testing']);
const SOURCE_STATUS: Record<string, string> = {
  draft: 'chưa kết nối',
  needs_fix: 'cần sửa kết nối',
  paused: 'đang tạm dừng',
};
const pct = (n: number) => `${Math.round(n * 1000) / 10}%`.replace('.', ',');

export const getSpaceSetup = async (spaceId: string): Promise<SpaceSetup> => {
  const headers = { 'X-Data-Space': spaceId };
  const [sources, metrics, sets] = await Promise.all([
    ok<Page<SourceView>>(api.GET('/v1/sources', { params: { query: { page_size: 500 } }, headers })),
    ok<Page<Schemas['MetricView']>>(
      api.GET('/v1/metrics', { params: { query: { page_size: 500 } }, headers }),
    ),
    ok<Page<Schemas['EvalSetView']>>(
      api.GET('/v1/eval-sets', {
        params: { query: { subject_type: 'context', status: 'active', page_size: 500 } },
        headers,
      }),
    ),
  ]);

  const live = sources.items.filter((s) => s.status !== 'archived');
  const connected = live.filter((s) => QUERYABLE.has(s.status));
  const sourceStep: SetupStep = {
    key: 'sources',
    done: connected.length > 0,
    summary:
      live.length === 0
        ? 'Chưa có nguồn nào'
        : `${connected.length}/${live.length} nguồn truy vấn được`,
    pending: live
      .filter((s) => !QUERYABLE.has(s.status))
      .map((s) => `${s.name}: ${SOURCE_STATUS[s.status] ?? s.status}${s.status_reason ? ` (${s.status_reason})` : ''}`),
  };

  const inUse = metrics.items.filter((m) => m.status !== 'retired');
  const unapproved = inUse.filter((m) => !m.certified);
  const metricStep: SetupStep = {
    key: 'metrics',
    done: inUse.length > 0 && unapproved.length === 0,
    summary:
      inUse.length === 0
        ? 'Chưa có chỉ số nào'
        : `${inUse.length - unapproved.length}/${inUse.length} chỉ số đã duyệt`,
    pending: unapproved.map((m) => m.display_name ?? m.code),
  };

  // The Data Space's own set with the most questions; an organisation-wide (pack) set is not it.
  const own = sets.items
    .filter((s) => s.data_space_id === spaceId)
    .sort((a, b) => b.item_count - a.item_count)[0];
  const asked = own?.item_count ?? 0;
  const questionStep: SetupStep = {
    key: 'questions',
    done: asked >= MIN_QUESTIONS,
    summary: own
      ? `${asked} câu hỏi của khách trong “${own.name}” (cần ${MIN_QUESTIONS}–20)`
      : 'Chưa có bộ câu hỏi của khách',
    pending: asked >= MIN_QUESTIONS ? [] : [`Thêm ${MIN_QUESTIONS - asked} câu hỏi nữa`],
  };

  const run = own?.latest_run ?? null;
  const rate = run ? Number(run.pass_rate ?? 0) : 0;
  const threshold = own ? Number(own.pass_threshold) : 0;
  const evalPassed =
    !!own &&
    !!run &&
    run.status === 'completed' &&
    run.item_count >= own.min_items &&
    rate >= threshold;
  const evalStep: SetupStep = {
    key: 'eval',
    done: evalPassed,
    summary: !run
      ? 'Chưa chạy bộ đánh giá'
      : run.status === 'running'
        ? 'Bộ đánh giá đang chạy'
        : `${run.passed}/${run.item_count} câu đạt (${pct(rate)}), cần ${pct(threshold)}`,
    pending:
      own && run && !evalPassed && run.status === 'completed'
        ? [
            run.item_count < own.min_items
              ? `Cần ít nhất ${own.min_items} câu để chấm`
              : `${run.failed} câu chưa đạt`,
          ]
        : [],
  };

  const steps = [questionStep, sourceStep, metricStep, evalStep];
  return { steps, ready: steps.every((s) => s.done), evalSetId: own?.id ?? null };
};
