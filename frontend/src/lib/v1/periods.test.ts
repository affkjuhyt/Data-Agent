/**
 * Kỳ chốt and Thẩm quyền nguồn as the api sends them: period statuses in the screen's words,
 * retro changes gathered per period and decided as A3 names it, a comparison item by item, and
 * the materiality threshold between words and BR-120's fractions.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { listSourceAuthorities, parseMateriality } from './authorities';
import {
  comparePeriodVersions,
  decideRetroChange,
  getPeriod,
  getRetroChange,
  listPeriods,
} from './periods';

const at = '2026-10-12T08:00:00Z';
let decideBody: unknown;
const server = setupServer(
  http.get('*/v1/periods', () =>
    HttpResponse.json({
      items: [
        { id: 'pc-9', period_label: 'T9/2026', status: 'filed', version: 2 },
        { id: 'pc-10', period_label: 'T10/2026', status: 'close_failed', version: 1 },
      ],
      next_page_token: '',
    }),
  ),
  http.get('*/v1/periods/compare', () =>
    HttpResponse.json({
      a: {
        period_close_id: 'pc-9a',
        period_label: 'T9/2026',
        status: 'filed',
        values_from: 'published',
        version: 1,
      },
      b: {
        period_close_id: 'pc-9',
        period_label: 'T9/2026',
        status: 'filed',
        values_from: 'retro',
        version: 2,
      },
      items: [
        {
          metric_code: 'NPL_RATIO',
          a_value: '1.84',
          b_value: '1.96',
          delta: '0.12',
          cause: 'retro',
        },
      ],
      rows_id: null,
    }),
  ),
  http.get('*/v1/periods/:id/retro-changes', ({ params }) =>
    HttpResponse.json({
      items:
        params.id === 'pc-9'
          ? [
              {
                id: '0192f0aa-4444-7000-8000-000000000001',
                period_close_id: 'pc-9',
                current_value: '1.96',
                delta: '0.12',
                below_materiality: false,
                compliance_notified: true,
                decision: null,
                decision_note: null,
                detected_at: at,
              },
            ]
          : [],
      next_page_token: '',
    }),
  ),
  http.post('*/v1/retro-changes/:action', async ({ request }) => {
    decideBody = await request.json();
    return HttpResponse.json({});
  }),
  http.get('*/v1/source-authorities', () =>
    HttpResponse.json({
      items: [
        {
          id: 'sa-1',
          data_kind: 'du_no',
          ranking: ['src-core', 'src-file'],
          materiality: { amount_pct: 0.001, count: 5 },
          status: 'active',
        },
        { id: 'sa-0', data_kind: 'du_no', ranking: [], materiality: null, status: 'superseded' },
      ],
      next_page_token: '',
    }),
  ),
  http.get('*/v1/sources', () =>
    HttpResponse.json({
      items: [
        {
          id: 'src-core',
          name: 'Core banking',
          kind: 'oracle',
          authority_level: 'level_1',
          mode: 'federated',
          status: 'active',
        },
        {
          id: 'src-file',
          name: 'Tệp tải tay',
          kind: 'file',
          authority_level: 'level_3',
          mode: 'ingest',
          status: 'active',
        },
      ],
      next_page_token: '',
    }),
  ),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('periods from the api', () => {
  it('says the close statuses in the screen’s words and counts adjustments from versions', async () => {
    const [sept, oct] = (await listPeriods()).items;
    expect(sept).toEqual({ id: 'pc-9', code: 'T9/2026', status: 'published', adjustment_count: 1 });
    expect(oct.status).toBe('closing');
  });

  it('reads the reconciliation by status and offers :close to an open period', async () => {
    server.use(
      http.get('*/v1/periods/:id', () =>
        HttpResponse.json({
          id: 'pc-10',
          period_label: 'T10/2026',
          status: 'close_failed',
          version: 1,
          business_date: '2026-10-31',
          snapshot_hash: 'ab12',
          closed_at: null,
          filed_at: null,
          filed_report_code: null,
          reconciliation: { matched: 40, mismatched: 2, exception_pending: 1 },
        }),
      ),
    );
    const period = await getPeriod('pc-10');
    expect(period.steps.map((s) => [s.title, s.state, s.detail])).toEqual([
      ['Ảnh chụp dữ liệu', 'done', 'đã băm'],
      ['Đối chiếu sổ cái', 'needs_you', '40 khớp · 2 lệch · 1 chờ duyệt ngoại lệ'],
      ['Khoá kỳ', 'pending', 'chưa khoá'],
      ['Nộp báo cáo', 'pending', 'chưa nộp'],
    ]);
    expect(period.can_start_close).toBe(true);
  });

  it('finds a retro change among the periods and decides it as A3 names it', async () => {
    const change = await getRetroChange('0192f0aa-4444-7000-8000-000000000001');
    expect(change).toMatchObject({ period_code: 'T9/2026', status: 'open' });
    expect(change.impact[0]).toMatchObject({ locked: '1,84', recalculated: '1,96', delta: '0,12' });
    expect(change.escalation).not.toBeNull();
    await decideRetroChange(change.id, { decision: 'no_change', decision_note: 'Dưới ngưỡng' });
    expect(decideBody).toEqual({ decision: 'no_action', decision_note: 'Dưới ngưỡng' });
  });

  it('compares two closes item by item with the cause in words', async () => {
    const result = await comparePeriodVersions('pc-9a', 'pc-9');
    expect(result.rows).toEqual([
      {
        metric: 'NPL_RATIO',
        left: '1,84',
        right: '1,96',
        delta: '0,12',
        changed: true,
        reason: 'điều chỉnh hồi tố',
        retro_change_id: null,
      },
    ]);
  });

  it('opens on the newest period’s two versions and links a gap to its retro change', async () => {
    let asked: URLSearchParams | undefined;
    const close = (id: string, label: string, version: number, date: string) => ({
      id,
      period_label: label,
      status: 'filed',
      version,
      business_date: date,
    });
    server.use(
      http.get('*/v1/periods', () =>
        HttpResponse.json({
          items: [
            close('pc-8', 'T8/2026', 1, '2026-08-31'),
            close('pc-9a', 'T9/2026', 1, '2026-09-30'),
            close('pc-9', 'T9/2026', 2, '2026-09-30'),
            close('pc-10', 'T10/2026', 1, '2026-10-31'),
          ],
          next_page_token: '',
        }),
      ),
      http.get('*/v1/periods/compare', ({ request }) => {
        asked = new URL(request.url).searchParams;
        const sideOf = (id: string, version: number) => ({
          period_close_id: id,
          period_label: 'T9/2026',
          status: 'filed',
          values_from: version === 2 ? 'retro' : 'published',
          version,
        });
        return HttpResponse.json({
          a: sideOf('pc-9a', 1),
          b: sideOf('pc-9', 2),
          items: [
            {
              metric_id: 'm-1',
              metric_code: 'NPL_RATIO',
              dimension_key: { region: 'Bắc Bộ' },
              a_metric_version_id: 'mv-1',
              b_metric_version_id: 'mv-1',
              a_value: '1.84',
              b_value: '1.96',
              delta: '0.12',
              cause: 'retro',
            },
          ],
          rows_id: 'cmp-1',
        });
      }),
      http.get('*/v1/periods/compare/cmp-1/rows', () =>
        HttpResponse.json({
          items: [
            {
              retro_change_id: 'rc-12',
              period_close_id: 'pc-9',
              metric_version_id: 'mv-1',
              dimension_key: { region: 'Bắc Bộ' },
              published_value: '1.84',
              current_value: '1.96',
              delta: '0.12',
              below_materiality: false,
              detected_at: at,
            },
          ],
          next_page_token: '',
        }),
      ),
    );
    const result = await comparePeriodVersions();
    expect(asked?.get('a')).toBe('pc-9a');
    expect(asked?.get('b')).toBe('pc-9');
    expect(result.versions.map((v) => v.id)).toEqual(['pc-10', 'pc-9', 'pc-9a', 'pc-8']);
    expect(result.rows[0]).toMatchObject({
      metric: 'NPL_RATIO · region: Bắc Bộ',
      retro_change_id: 'rc-12',
    });
  });
});

describe('source authority from the api', () => {
  it('names the ranked sources, marks the manual one and says the threshold', async () => {
    const [a] = (await listSourceAuthorities()).items;
    expect(a.ranking.map((s) => [s.name, s.authority_level, s.manual])).toEqual([
      ['Core banking', 'level_1', false],
      ['Tệp tải tay', 'level_3', true],
    ]);
    expect(a.materiality).toBe('0,1% hoặc 5 khoản');
  });

  it('reads a typed threshold back into BR-120 fractions', () => {
    expect(parseMateriality('0,1% hoặc 5 khoản')).toEqual({ amount_pct: 0.001, count: 5 });
    expect(parseMateriality('')).toBeNull();
  });
});
