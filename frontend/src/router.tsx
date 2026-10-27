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
import { landingFor, resolveRoute } from './AppStateContext';
import { routePath } from './routePath';

/*
 * The app's URLs: /<area>[/<page>[/<id>]], the page lowercased (/admin/data_spaces/<id>). The
 * routes only match and canonicalise: an area or page of an earlier IA redirects to its v3.1
 * place (REDIRECT_MAP), an upper-case page to its lower-case form, and an id survives only when
 * the page is not redirected elsewhere. Which screen a page draws stays in ROUTE_MAP, read by App
 * through useAppState(), so the screens did not change when the router did.
 */
type Params = { area: string; page?: string; id?: string };

function canonical({ area, page, id }: Params): void {
  const resolved = resolveRoute(area, page ? page.toUpperCase() : undefined);
  const kept = resolved.module === area && resolved.sub === page?.toUpperCase() ? id : undefined;
  const target = routePath(resolved.module, resolved.sub, kept);
  const here = routePath(area, page?.toUpperCase(), id);
  if (target !== here || (page !== undefined && page !== page.toLowerCase()))
    throw redirect({ href: `${target}${window.location.search}`, replace: true });
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

/**
 * Links of the hash era (#/build/workflows) open as the path they name. Only a hash that is a
 * path: Keycloak comes back from its check-sso round trip with #state=…&code=…, which keycloak-js
 * reads after this runs; rewriting that hash lost the code and sent the page back to Keycloak in
 * a loop.
 */
function fromLegacyHash(): void {
  if (!window.location.hash.startsWith('#/')) return;
  const path = window.location.hash.slice(1);
  window.history.replaceState(null, '', `${path}${window.location.search}`);
}

/** The app's router; tests pass a memory history and a small layout in place of App. */
export function createAppRouter({
  history = createBrowserHistory(),
  layout = App,
}: { history?: RouterHistory; layout?: () => ReactElement } = {}) {
  fromLegacyHash();
  return createRouter({ routeTree: routeTreeOf(layout), history });
}

type AppRouter = ReturnType<typeof createAppRouter>;

declare module '@tanstack/react-router' {
  interface Register {
    router: AppRouter;
  }
}
