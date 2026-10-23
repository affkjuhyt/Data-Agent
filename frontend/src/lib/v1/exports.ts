import { api, ok, okAs } from './client';
import type { components } from './schema';

/*
 * Xuất kết quả (UC-01-06): POST /v1/answers/{id}/exports answers 202 with an export job that a
 * worker builds; GET /v1/exports/{id} gives a signed download_url once `ready` (A1.1: downloads
 * are signed, expiring links, never bytes in the API). Detail rows with personal data stop at
 * `waiting_approval` until someone approves in Cần bạn (BR-85).
 */

type ExportJob = components['schemas']['ExportView'];
type ExportFormat = components['schemas']['ExportCreate']['format'];

const POLL_MS = 800;
const GIVE_UP_MS = 2 * 60_000;

const getExport = async (id: string) =>
  ok<ExportJob>(api.GET('/v1/exports/{id}', { params: { path: { id } } }));

/** Request an export and wait for its file; resolves with the job once ready. */
export async function exportAnswer(answerId: string, format: ExportFormat): Promise<ExportJob> {
  let job = await okAs<ExportJob>(
    api.POST('/v1/answers/{id}/exports', {
      params: { path: { id: answerId } },
      body: {
        format,
        include_detail_rows: false,
      },
    }),
  );
  const started = Date.now();
  while (job.status === 'queued' || job.status === 'running') {
    if (Date.now() - started > GIVE_UP_MS) throw new Error('Tệp xuất chưa sẵn sàng sau 2 phút; thử lại sau.');
    await new Promise((resolve) => setTimeout(resolve, POLL_MS));
    job = await getExport(job.id);
  }
  if (job.status === 'waiting_approval')
    throw new Error('Tệp có dòng chi tiết cần duyệt trước; theo dõi ở Cần bạn.');
  if (job.status !== 'ready' || !job.download_url) throw new Error('Không tạo được tệp xuất.');
  return job;
}
