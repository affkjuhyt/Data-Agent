// Sub-tab keys are uppercase internally (ROUTE_MAP, REDIRECT_MAP, ...) but the
// URL renders them lowercase — /build/workflows reads better than
// /build/WORKFLOWS. The router (router.tsx) redirects an upper-case page to this form.
export const routePath = (module: string, sub?: string, id?: string) =>
  `/${module}${sub ? `/${sub.toLowerCase()}` : ''}${sub && id ? `/${encodeURIComponent(id)}` : ''}`;
