import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, render, renderHook, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ConflictNotice } from '../../components/shared/ConflictNotice';
import { mockFixture } from '../../test-utils/contractFixtures';
import { decideReviewCase, getReviewCase, type ReviewCaseRef } from './indicatorApi';
import { queryKeys } from './queryKeys';
import { diffFields, useVersionedMutation } from './useVersionedMutation';

vi.mock('../config', () => ({ INDICATOR_API_URL: 'http://indicator.test' }));

const CASE_ID = '2e3f4a5b-6c7d-4e8f-9a0b-1c2d3e4f5a06';
// What the server holds after A saved: the mock's case, at version 3.
const latest = mockFixture<ReviewCaseRef[]>('happy/review_cases.json').find(
  (c) => c.case_id === CASE_ID,
)!;
// What B still sees: the version before A's save.
const seenByB: ReviewCaseRef = { ...latest, version: 2 };

const jsonResponse = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

function setup() {
  const fetchMock = vi.fn((_url: string, init?: RequestInit) =>
    Promise.resolve(
      init?.method === 'POST'
        ? jsonResponse(409, mockFixture('version_conflict/response.409.json'))
        : jsonResponse(200, latest),
    ),
  );
  vi.stubGlobal('fetch', fetchMock);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(queryKeys.reviewCases.detail(CASE_ID), seenByB);
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const hook = renderHook(
    () =>
      useVersionedMutation({
        queryKey: queryKeys.reviewCases.detail(CASE_ID),
        queryFn: () => getReviewCase(CASE_ID),
        mutationFn: (expectedVersion: number) =>
          decideReviewCase(CASE_ID, 'approve', expectedVersion),
      }),
    { wrapper },
  );
  return { fetchMock, client, hook };
}

afterEach(() => vi.unstubAllGlobals());

describe('useVersionedMutation (AC-4)', () => {
  it("A saved first: B gets the conflict, sees A's version, and nothing is re-sent", async () => {
    const { fetchMock, client, hook } = setup();

    await act(async () => {
      await hook.result.current.mutateAsync(seenByB.version).catch(() => undefined);
    });

    await waitFor(() => expect(hook.result.current.conflict).not.toBeNull());
    const conflict = hook.result.current.conflict!;
    expect(conflict.previous).toEqual(seenByB);
    expect(conflict.latest).toEqual(latest);
    expect(conflict.changes).toEqual([{ field: 'version', before: 2, after: 3 }]);
    // The screen now holds the latest version…
    expect(client.getQueryData(queryKeys.reviewCases.detail(CASE_ID))).toEqual(latest);
    // …and B's decision went out once, with B's version: never re-sent with A's.
    const posts = fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST');
    expect(posts).toHaveLength(1);
    expect(JSON.parse(String(posts[0][1]!.body))).toEqual({ expected_version: 2 });

    render(<ConflictNotice changes={conflict.changes} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Hồ sơ đã được người khác cập nhật');
    expect(screen.getByText('version: 2 → 3')).toBeInTheDocument();
  });

  it('a successful save replaces the cached record and reports no conflict', async () => {
    const { client, hook } = setup();
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(200, { ...latest, version: 4 })));
    await act(async () => {
      await hook.result.current.mutateAsync(3);
    });
    expect(hook.result.current.conflict).toBeNull();
    expect(client.getQueryData(queryKeys.reviewCases.detail(CASE_ID))).toEqual({
      ...latest,
      version: 4,
    });
  });
});

describe('ConflictNotice', () => {
  it('names who saved and when, when the record says', () => {
    render(<ConflictNotice actor="NV A" at="2026-10-07T09:12:00" changes={[]} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Hồ sơ đã được NV A cập nhật lúc 09:12');
  });
});

describe('diffFields', () => {
  it('lists only the fields whose value changed', () => {
    expect(
      diffFields({ a: 1, b: { x: 1 }, c: 'same' }, { a: 2, b: { x: 1 }, c: 'same', d: true }),
    ).toEqual([
      { field: 'a', before: 1, after: 2 },
      { field: 'd', before: undefined, after: true },
    ]);
  });
});
