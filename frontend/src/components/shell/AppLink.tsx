import type { MouseEvent, ReactNode } from 'react';
import { useAppState } from '../../AppStateContext';
import { routePath } from '../../routePath';
import { Button, buttonClass, type ButtonKind } from '../../design-system/Button';
import { Icon, type IconName } from '../../design-system/Icon';

/*
 * A link to a page of the app (/<area>/<page>/<id>, router.tsx): a real href, so a page opens in
 * a new tab with Ctrl/Cmd-click, a middle click or the context menu, and its address can be
 * copied; a plain click stays in the app through navigate(), which the router serves. Pages are
 * written as ROUTE_MAP names them (DATA_SPACES); the URL lowercases them.
 */
type Target = { area: string; page?: string; id?: string };

function useOpen({ area, page, id }: Target) {
  const { navigate } = useAppState();
  return {
    href: routePath(area, page, id),
    onClick: (event: MouseEvent<HTMLAnchorElement>) => {
      // A modified click, or any button but the main one, is the browser's: a new tab or window.
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
        return;
      event.preventDefault();
      // The arguments the page names, as a caller of navigate() would pass them.
      if (id !== undefined) navigate(area, page, id);
      else if (page !== undefined) navigate(area, page);
      else navigate(area);
    },
  };
}

export function AppLink({
  area,
  page,
  id,
  children,
  ...rest
}: Target & {
  children: ReactNode;
  className?: string;
  'aria-current'?: 'page';
  'aria-label'?: string;
}) {
  return (
    <a {...useOpen({ area, page, id })} {...rest}>
      {children}
    </a>
  );
}

/**
 * A Button that opens a page: the look of `kind`, the behaviour of a link. Disabled, it is the
 * disabled Button, since a link cannot be.
 */
export function LinkButton({
  area,
  page,
  id,
  kind = 'secondary',
  size = 'md',
  icon,
  disabled,
  className,
  children,
  title,
  'aria-label': ariaLabel,
}: Target & {
  kind?: ButtonKind;
  size?: 'md' | 'sm';
  icon?: IconName;
  disabled?: boolean;
  className?: string;
  children?: ReactNode;
  title?: string;
  'aria-label'?: string;
}) {
  const open = useOpen({ area, page, id });
  if (disabled)
    return (
      <Button kind={kind} size={size} icon={icon} disabled className={className} title={title}>
        {children}
      </Button>
    );
  return (
    <a
      {...open}
      title={title}
      aria-label={ariaLabel}
      className={buttonClass({ kind, size, className })}
    >
      {icon && <Icon name={icon} />}
      {children}
    </a>
  );
}
