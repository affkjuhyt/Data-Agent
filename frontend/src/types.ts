export type Role = 'PLATFORM_ADMIN' | 'AI_ENGINEER' | 'EXECUTIVE';
export type Environment = 'DEV' | 'UAT' | 'STAGING' | 'PROD';

export interface TenantContext {
  /** Selected tenant from GET /api/auth/me/tenants; null id when the user has none. */
  tenantId: string | null;
  tenantName: string | null;
  slug: string | null;
  environment: Environment;
}

export interface User {
  id: string;
  name: string;
  email: string;
  role: Role;
  avatar?: string;
}
