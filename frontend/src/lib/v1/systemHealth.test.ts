/**
 * Giám sát hệ thống on A2's Prometheus sections: a service is a job, down when no target answers;
 * GPU use is the mean of the cards; alerts are the ones firing; the backup line joins backup age
 * and replication lag; without Prometheus the page says nothing is measured.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { getSystemHealth } from './adminPlatform';

const s = (labels: Record<string, string>, value: string) => ({ labels, value });

const server = setupServer(
  http.get('*/v1/admin/system/health', () =>
    HttpResponse.json({
      checked_at: '2026-10-13T08:00:00Z',
      status: 'degraded',
      sections: {
        services: {
          status: 'ok',
          series: [
            s({ job: 'api', instance: 'a:1' }, '1'),
            s({ job: 'api', instance: 'a:2' }, '0'),
            s({ job: 'worker-agent', instance: 'w:1' }, '1'),
            s({ job: 'kafka', instance: 'k:1' }, '0'),
          ],
        },
        gpu: {
          status: 'ok',
          series: [
            s({ gpu: '0', Hostname: 'gpu1' }, '60'),
            s({ gpu: '1', Hostname: 'gpu1' }, '80'),
          ],
        },
        queues: {
          status: 'ok',
          series: [s({ consumergroup: 'kb-ingest' }, '42'), s({ consumergroup: 'audit' }, '0')],
        },
        backup: { status: 'ok', series: [s({ job: 'pg-backup' }, '7200')] },
        dr: { status: 'ok', series: [s({ instance: 'pg-dr' }, '40')] },
        alerts: {
          status: 'ok',
          series: [
            s({ alertname: 'KafkaDown', severity: 'critical', job: 'kafka', instance: 'k:1' }, '1'),
          ],
        },
      },
    }),
  ),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('system health from the api', () => {
  it('reads the Prometheus sections into the snapshot', async () => {
    const health = await getSystemHealth();
    expect(health).toMatchObject({
      no_data: false,
      services_up: 1,
      services_total: 3,
      services_note: 'Hàng đợi kb-ingest trễ 42 tin',
      gpu_utilization: 0.7,
      gpu_hosts: 1,
      gpu_cards: 2,
      latency_p95_ms: null,
      error_rate_24h: null,
      models: [],
      backup_text: 'pg-backup: 2 giờ trước · sao chép sang site DR trễ 40 giây',
    });
    expect(health.services.map((x) => [x.name, x.status, x.note])).toEqual([
      ['api', 'degraded', '1/2 phiên bản trả lời'],
      ['worker-agent', 'ok', null],
      ['kafka', 'down', null],
    ]);
    expect(health.alerts).toEqual([
      { id: 'KafkaDown-k:1', severity: 'error', title: 'KafkaDown', detail: 'kafka · k:1' },
    ]);
  });

  it('says nothing is measured without Prometheus', async () => {
    server.use(
      http.get('*/v1/admin/system/health', () =>
        HttpResponse.json({ checked_at: '2026-10-13T08:00:00Z', status: 'no_data', sections: {} }),
      ),
    );
    const health = await getSystemHealth();
    expect(health).toMatchObject({
      no_data: true,
      services_total: 0,
      gpu_utilization: null,
      alerts: [],
      backup_text: 'Chưa có số liệu sao lưu.',
    });
  });
});
