import { api, ok } from './client';
import type { components } from './schema';

/*
 * GET /v1/me (A2 · Quản trị, UC-11-01): who the signed-in user is in the organisation, the
 * organisation-wide roles they hold and why, and the capabilities those give. Identity comes
 * from the directory sync; roles inside a Data Space come with GET /v1/data-spaces.
 * A signed-in Keycloak account with no active account in the organisation gets 403.
 */

// Generated from the api's OpenAPI (make api-types, tab 7 F3): the server is the contract.
/** source: manual, emergency (expires within 24 h, BR-44) or group_mapping (through `group_name`). */
type RoleGrant = components['schemas']['RoleGrant'];
/** capabilities: the organisation-wide ones (A1.2); a Data Space can add more. */
export type Me = components['schemas']['Me'];

export const getMe = () => ok<Me>(api.GET('/v1/me'));

/*
 * The names behind person ids, from what the viewer may read: GET /v1/admin/users (admin), the
 * members of the given Data Spaces (GET /v1/data-spaces/{id}/members, steward) and the viewer
 * (GET /v1/me). A refused list adds nothing, so an id no list holds stays unnamed: callers
 * leave its label out rather than print a blank one. /v1/principals cannot resolve ids (it
 * searches by name).
 */
export async function personNames(spaceIds: (string | null | undefined)[] = []) {
  const spaces = [...new Set(spaceIds.filter((id): id is string => !!id))];
  const [users, members, me] = await Promise.all([
    ok(api.GET('/v1/admin/users', { params: { query: { page_size: 500 } } })).catch(() => null),
    Promise.all(
      spaces.map((id) =>
        ok(
          api.GET('/v1/data-spaces/{id}/members', {
            params: { path: { id }, query: { page_size: 500 } },
          }),
        ).catch(() => null),
      ),
    ),
    getMe().catch(() => null),
  ]);
  const names = new Map<string, string>();
  for (const page of members)
    for (const m of page?.items ?? [])
      if (m.principal_name) names.set(m.principal_id, m.principal_name);
  for (const u of users?.items ?? []) if (u.display_name) names.set(u.id, u.display_name);
  if (me) names.set(me.id, me.display_name);
  return names;
}

/** "Chuyên viên phân tích (nhóm GRP-RISK-ANALYST)" for each grant, as the profile shows it. */
export const roleLabel = (grant: RoleGrant): string =>
  grant.group_name ? `${grant.role.name} (nhóm ${grant.group_name})` : grant.role.name;
