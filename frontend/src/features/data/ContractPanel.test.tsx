/**
 * DF-09: the contract of one table. A steward saves a draft over the version read (If-Match),
 * another activates it; a write over a version someone else changed, or its writer activating it,
 * says why in plain words.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import { ToastProvider } from '../../design-system/Toast';
import { ContractPanel } from './ContractPanel';

const DATASET = '11111111-1111-1111-1111-111111111111';
const contract = (version: number, status: string, extra: object = {}) => ({
  dataset_id: DATASET,
  status,
  version,
  schema_hash_expected: null,
  schema_hash_current: 'abcdef0123456789',
  freshness_sla: null,
  required_rules: [],
  broken_reason: null,
  updated_at: null,
  updated_by: null,
  ...extra,
});

const writes: { ifMatch: string | null; body: Record<string, unknown> }[] = [];
const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  writes.length = 0;
});
afterAll(() => server.close());

function serve(answer: (version: number) => Response | null) {
  let current = contract(0, 'none');
  server.use(
    http.get(`*/v1/datasets/${DATASET}/contract`, () => HttpResponse.json(current)),
    http.put(`*/v1/datasets/${DATASET}/contract`, async ({ request }) => {
      const body = (await request.json()) as Record<string, unknown>;
      writes.push({ ifMatch: request.headers.get('If-Match'), body });
      const refused = answer(current.version);
      if (refused) return refused;
      current = contract(current.version + 1, String(body.status), body);
      return HttpResponse.json(current);
    }),
  );
}

const draw = () =>
  render(
    <ToastProvider>
      <ContractPanel datasetId={DATASET} />
    </ToastProvider>,
  );

describe('ContractPanel', () => {
  it('saves a draft over the version read, with its rules and freshness', async () => {
    serve(() => null);
    draw();
    expect(await screen.findByText('Chưa có hợp đồng')).toBeInTheDocument();
    // Nothing to activate before a contract is written.
    expect(screen.getByRole('button', { name: 'Kích hoạt' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    fireEvent.change(screen.getByLabelText('Độ mới tối đa'), { target: { value: '26h' } });
    fireEvent.click(screen.getByRole('button', { name: 'Thêm quy tắc' }));
    fireEvent.change(screen.getByLabelText('Cột của quy tắc 1'), { target: { value: 'ngay' } });
    fireEvent.click(screen.getByRole('button', { name: 'Ghim cấu trúc hiện tại' }));
    fireEvent.click(screen.getByRole('button', { name: 'Lưu bản nháp' }));
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0].ifMatch).toBe('W/"contract-0"');
    expect(writes[0].body).toEqual({
      status: 'draft',
      schema_hash_expected: 'abcdef0123456789',
      freshness_sla: { kind: 'max_age', value: '26h' },
      required_rules: [
        { expectation: 'expect_column_values_to_not_be_null', column: 'ngay', severity: 'high' },
      ],
    });
    expect(await screen.findByText('Bản nháp')).toBeInTheDocument();
  });

  it('says why a write was refused: changed meanwhile, or its writer activating it', async () => {
    serve((version) =>
      version === 0
        ? HttpResponse.json(
            { status: 412, code: 'precondition_failed', title: 'Precondition failed' },
            { status: 412 },
          )
        : null,
    );
    draw();
    await screen.findByText('Chưa có hợp đồng');
    fireEvent.click(screen.getByRole('button', { name: 'Lưu bản nháp' }));
    expect(await screen.findByText(/vừa được người khác sửa/)).toBeInTheDocument();
  });

  it('a malformed freshness is held before it is sent', async () => {
    serve(() => null);
    draw();
    await screen.findByText('Chưa có hợp đồng');
    fireEvent.change(screen.getByLabelText('Độ mới tối đa'), { target: { value: 'một ngày' } });
    expect(screen.getByRole('button', { name: 'Lưu bản nháp' })).toHaveAttribute(
      'aria-disabled',
      'true',
    );
    expect(screen.getByText(/ví dụ 26h/)).toBeInTheDocument();
  });
});
