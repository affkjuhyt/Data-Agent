import { v1Get } from './client';

/*
 * GET /v1/me (A2 · Quản trị, UC-11-01): who the signed-in user is in the organisation, the
 * organisation-wide roles they hold and why, and the capabilities those give. Identity comes
 * from the directory sync; roles inside a Data Space come with GET /v1/data-spaces.
 * A signed-in Keycloak account with no active account in the organisation gets 403.
 */

export interface RoleRef {
  id: string;
  code: string;
  name: string;
}

export interface RoleGrant {
  role: RoleRef;
  /** manual, emergency (expires within 24 h, BR-44) or group_mapping (through `group_name`). */
  source: 'manual' | 'emergency' | 'group_mapping';
  group_name: string | null;
  valid_until: string | null;
}

export interface Me {
  id: string;
  username: string;
  display_name: string;
  email: string | null;
  department: string | null;
  kind: 'human' | 'agent' | 'service';
  default_data_space_id: string | null;
  locale: string;
  time_zone: string;
  roles: RoleGrant[];
  /** Organisation-wide capabilities (A1.2); a Data Space can add more. */
  capabilities: string[];
}

export const getMe = () => v1Get<Me>('/me');

/** "Chuyên viên phân tích (nhóm GRP-RISK-ANALYST)" for each grant, as the profile shows it. */
export const roleLabel = (grant: RoleGrant): string =>
  grant.group_name ? `${grant.role.name} (nhóm ${grant.group_name})` : grant.role.name;
