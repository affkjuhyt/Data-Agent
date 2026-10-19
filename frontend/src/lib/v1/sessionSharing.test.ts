/**
 * Chia sẻ and Bình luận of a session as the api sends them: each recipient, author and assignee
 * by the name the api adds to the row, never by its id.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import type { components } from './schema';
import { getSharing, getTileThread } from './sessions';

type Schemas = components['schemas'];
const at = '2026-10-12T08:00:00Z';

const share = (
  principal_type: string,
  principal_id: string,
  permission: string,
  principal_name: string | null,
): Schemas['ShareView'] => ({
  id: `sh-${principal_id}`,
  workspace_id: 'ws-1',
  principal_type,
  principal_id,
  permission,
  granted_by: 'u-owner',
  expires_at: null,
  created_at: at,
  updated_at: at,
  principal_name,
});

const comment = (
  id: string,
  author_name: string | null,
  assignee: { id: string; name: string } | null,
): Schemas['CommentView'] => ({
  id,
  workspace_id: 'ws-1',
  tile_id: 'tile-1',
  parent_id: null,
  author_id: 'u-an',
  author_name,
  body: id === 'c-task' ? 'Đối chiếu sổ cái' : 'Số lạ',
  mentions: [],
  assignee_id: assignee?.id ?? null,
  assignee_name: assignee?.name ?? null,
  due_at: assignee ? '2026-10-20T10:00:00Z' : null,
  status: 'open',
  resolved_at: null,
  agent_run_id: null,
  created_at: at,
  updated_at: at,
});

const server = setupServer(
  http.get('*/v1/workspaces/:id/shares', () =>
    HttpResponse.json({
      items: [
        share('user', 'u-binh', 'edit', 'Trần Bình'),
        share('group', 'g-risk', 'view', 'GRP-RISK-ANALYST'),
        share('user', 'u-gone', 'view', null),
      ],
      next_page_token: '',
    }),
  ),
  http.get('*/v1/tiles/:id/comments', () =>
    HttpResponse.json({
      items: [
        comment('c-1', 'Nguyễn An', null),
        comment('c-task', 'Nguyễn An', { id: 'u-binh', name: 'Trần Bình' }),
      ],
      next_page_token: '',
    }),
  ),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

describe('session sharing and comments on the api', () => {
  it('names each recipient and never shows an id', async () => {
    const sharing = await getSharing('ws-1');
    expect(sharing.members.map((m) => [m.name, m.kind, m.role])).toEqual([
      ['Trần Bình', 'user', 'editor'],
      ['GRP-RISK-ANALYST', 'group', 'viewer'],
      ['Người dùng', 'user', 'viewer'],
    ]);
  });

  it('names the author of a comment and the assignee of a task', async () => {
    const thread = await getTileThread('ws-1', 'tile-1');
    expect(thread.items.map((c) => c.author.name)).toEqual(['Nguyễn An', 'Nguyễn An']);
    expect(thread.tasks).toEqual([
      expect.objectContaining({ id: 'c-task', assignee_name: 'Trần Bình' }),
    ]);
  });
});
