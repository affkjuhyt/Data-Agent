import { Link } from '@tanstack/react-router';
import type { ReactNode } from 'react';

/*
 * A link to a page of the app (/<area>/<page>/<id>, router.tsx): a real href, so a page opens in
 * a new tab with Ctrl/Cmd-click or the context menu and its address can be copied, while a plain
 * click stays in the app. Pages are written as ROUTE_MAP names them (DATA_SPACES); the URL
 * lowercases them.
 */
export function AppLink({
  area,
  page,
  id,
  children,
  ...rest
}: {
  area: string;
  page?: string;
  id?: string;
  children: ReactNode;
  className?: string;
  'aria-current'?: 'page';
  'aria-label'?: string;
}) {
  if (page && id)
    return (
      <Link to="/$area/$page/$id" params={{ area, page: page.toLowerCase(), id }} {...rest}>
        {children}
      </Link>
    );
  if (page)
    return (
      <Link to="/$area/$page" params={{ area, page: page.toLowerCase() }} {...rest}>
        {children}
      </Link>
    );
  return (
    <Link to="/$area" params={{ area }} {...rest}>
      {children}
    </Link>
  );
}
