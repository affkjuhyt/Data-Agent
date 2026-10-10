/** A tenant the signed-in user belongs to (GET /api/auth/me/tenants). */
export interface Tenant {
  id: string;
  name: string;
  slug: string;
}

/** Pick the tenant to activate: the persisted one when still available, else the first. */
export const pickTenant = (tenants: Tenant[], storedId: string | null | undefined): Tenant | null => {
  if (tenants.length === 0) return null;
  return tenants.find(t => t.id === storedId) ?? tenants[0];
};
