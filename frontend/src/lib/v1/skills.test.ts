/**
 * Kỹ năng as the api sends it: a skill's status from its row and versions, its owner named, the
 * versions read from the detail, suggestions said from their pattern, and sharing by scope.
 */
import { http, HttpResponse } from 'msw';
import { setupServer } from 'msw/node';
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest';
import {
  createSkill,
  getSkill,
  listSkillSuggestions,
  listSkillVersions,
  listSkills,
  shareSkill,
} from './skills';

const at = '2026-10-12T08:00:00Z';
const row = (extra: object = {}) => ({
  id: 'sk-1',
  code: 'phan_ra',
  name: 'Phân rã biến động',
  description: null,
  category: 'decomposition',
  origin: 'manual',
  scope: 'org',
  owner_id: 'u-1',
  current_version: 2,
  replaced_by_skill_id: null,
  run_count_30d: 14,
  created_at: at,
  updated_at: at,
  ...extra,
});
let bodies: unknown[] = [];
const server = setupServer(
  http.get('*/v1/admin/users', () =>
    HttpResponse.json({
      items: [{ id: 'u-1', display_name: 'Đỗ Hải Yến', kind: 'human', status: 'active' }],
      next_page_token: '',
    }),
  ),
  http.get('*/v1/skills', () =>
    HttpResponse.json({
      items: [row(), row({ id: 'sk-2', current_version: null, scope: 'personal' })],
      next_page_token: '',
    }),
  ),
  http.get('*/v1/skills/:id', () =>
    HttpResponse.json({
      ...row(),
      versions: [
        { id: 'v-2', version: 2, status: 'published' },
        { id: 'v-3', version: 3, status: 'pending_review' },
      ],
      published_version: { steps: [{}, {}, {}] },
    }),
  ),
  http.post('*/v1/skills', async ({ request }) => {
    bodies.push(await request.json());
    return HttpResponse.json(row({ id: 'sk-9', current_version: null, scope: 'personal' }), {
      status: 201,
    });
  }),
  http.post('*/v1/skills/:action', async ({ request }) => {
    const body = (await request.json()) as { scope: string };
    bodies.push(body);
    return HttpResponse.json(row({ scope: body.scope === 'org' ? 'data_space' : body.scope }));
  }),
  http.get('*/v1/skill-suggestions', () =>
    HttpResponse.json({
      items: [
        {
          id: 'sg-1',
          pattern: [{ kind: 'query', name: 'Lọc vùng' }, { kind: 'chart' }],
          session_ids: ['s1', 's2'],
          user_count: 3,
        },
      ],
      next_page_token: '',
    }),
  ),
);
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  bodies = [];
});
afterAll(() => server.close());

describe('skills from the api', () => {
  it('says a skill’s status, scope and owner', async () => {
    const [org, personal] = (await listSkills()).items;
    expect(org).toMatchObject({
      status: 'certified',
      scope: 'organization',
      scope_label: 'Toàn tổ chức',
      owner: { name: 'Đỗ Hải Yến' },
      runs_30d: 14,
    });
    expect(personal).toMatchObject({ status: 'draft', scope_label: 'Cá nhân' });
  });

  it('reads the detail: a version waiting for review, the steps, the versions newest first', async () => {
    expect(await getSkill('sk-1')).toMatchObject({
      status: 'pending_review',
      pending_version_id: 'v-3',
      step_count: 3,
    });
    expect((await listSkillVersions('sk-1')).items.map((v) => [v.version, v.status])).toEqual([
      [3, 'pending_review'],
      [2, 'active'],
    ]);
  });

  it('creates a personal custom draft and shares by scope', async () => {
    await createSkill({ name: 'Phân rã biến động' });
    expect(bodies[0]).toEqual({
      code: 'phan_ra_bien_dong',
      name: 'Phân rã biến động',
      category: 'custom',
      origin: 'manual',
      scope: 'personal',
    });
    expect(await shareSkill('sk-1', 'ds-1')).toEqual({ status: 'shared' });
    expect(await shareSkill('sk-1', null)).toEqual({ status: 'pending_admin' });
    expect(bodies.slice(1)).toEqual([{ scope: 'data_space' }, { scope: 'org' }]);
  });

  it('says a suggestion from its generalised steps', async () => {
    const [s] = (await listSkillSuggestions()).items;
    expect(s).toMatchObject({
      title: 'Lọc vùng → chart',
      count_text: '2 phiên · 3 người',
      session_ids: ['s1', 's2'],
    });
  });
});
