// Sub-tab keys are uppercase internally (ROUTE_MAP, ...) but the URL renders them
// lowercase — /agents/orchestration reads better than /agents/ORCHESTRATION. The router (router.tsx) redirects an upper-case page to this form.
export const routePath = (module: string, sub?: string, id?: string) =>
  `/${module}${sub ? `/${sub.toLowerCase()}` : ''}${sub && id ? `/${encodeURIComponent(id)}` : ''}`;
