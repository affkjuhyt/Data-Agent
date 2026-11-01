import {
  createBrowserHistory,
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
  type RouterHistory,
} from '@tanstack/react-router';
import type { ReactElement } from 'react';
import App from './App';
import { landingFor } from './AppStateContext';
import { routePath } from './routePath';

/*
 * The app's URLs: /<area>[/<page>[/<id>]], the page lowercased (/admin/data_spaces/<id>). The
 * routes only match and canonicalise an upper-case page to its lower-case form. Which screen a page draws stays in ROUTE_MAP, read by App
 * through useAppState(), so the screens did not change when the router did.
 */
type Params = { area: string; page?: string; id?: string };

function canonical({ area, page, id }: Params): void {
  if (page !== undefined && page !== page.toLowerCase())
    throw redirect({
      href: `${routePath(area, page.toUpperCase(), id)}${window.location.search}`,
      replace: true,
    });
}

function routeTreeOf(layout: () => ReactElement) {
  const rootRoute = createRootRoute({ component: layout, notFoundComponent: layout });

  const indexRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '/',
    beforeLoad: () => {
      throw redirect({
        href: `${routePath(landingFor().module)}${window.location.search}`,
        replace: true,
      });
    },
  });
  const areaRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '$area',
    beforeLoad: ({ params }) => canonical(params),
  });
  const pageRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '$area/$page',
    beforeLoad: ({ params }) => canonical(params),
  });
  const recordRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: '$area/$page/$id',
    beforeLoad: ({ params }) => canonical(params),
  });
  return rootRoute.addChildren([indexRoute, areaRoute, pageRoute, recordRoute]);
}

/** The app's router; tests pass a memory history and a small layout in place of App. */
export function createAppRouter({
  history = createBrowserHistory(),
  layout = App,
}: { history?: RouterHistory; layout?: () => ReactElement } = {}) {
  return createRouter({ routeTree: routeTreeOf(layout), history });
}

type AppRouter = ReturnType<typeof createAppRouter>;

declare module '@tanstack/react-router' {
  interface Register {
    router: AppRouter;
  }
}
