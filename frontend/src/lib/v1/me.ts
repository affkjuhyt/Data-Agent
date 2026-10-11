import { v1Get } from './client';
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

export const getMe = () => v1Get<Me>('/me');

/** "Chuyên viên phân tích (nhóm GRP-RISK-ANALYST)" for each grant, as the profile shows it. */
export const roleLabel = (grant: RoleGrant): string =>
  grant.group_name ? `${grant.role.name} (nhóm ${grant.group_name})` : grant.role.name;
