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

/** GET /v1/principals takes at most this many ids at once. */
const NAMES_PER_READ = 100;

/*
 * The names behind person ids (an owner, a steward, a certifier, an assignee): GET
 * /v1/principals?id=… answers anyone holding `ask`, people who have left included. An id it
 * does not name (a service account, another organisation, a failed read) stays unnamed: callers
 * leave its label out rather than print a blank one.
 */
export async function personNames(
  ids: Iterable<string | null | undefined>,
): Promise<Map<string, string>> {
  const wanted = [...new Set([...ids].filter((id): id is string => !!id))];
  const chunks: string[][] = [];
  for (let i = 0; i < wanted.length; i += NAMES_PER_READ)
    chunks.push(wanted.slice(i, i + NAMES_PER_READ));
  const pages = await Promise.all(
    chunks.map((id) =>
      ok(api.GET('/v1/principals', { params: { query: { id } } })).catch(() => null),
    ),
  );
  return new Map(pages.flatMap((p) => (p?.items ?? []).map((x) => [x.id, x.display_name] as const)));
}

/** "Chuyên viên phân tích (nhóm GRP-RISK-ANALYST)" for each grant, as the profile shows it. */
export const roleLabel = (grant: RoleGrant): string =>
  grant.group_name ? `${grant.role.name} (nhóm ${grant.group_name})` : grant.role.name;
