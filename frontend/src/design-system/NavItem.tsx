import { cn } from '../lib/utils';
import { Icon, type IconName } from './Icon';

/*
 * Figma "NavItem" (2015:136): an entry of the 220px secondary rail.
 * v3.2 visual rules: the secondary menu has no icons, so `icon` is optional and usually omitted.
 */
export function NavItem({
  label,
  active = false,
  icon,
  count,
  onClick,
}: {
  label: string;
  active?: boolean;
  icon?: IconName;
  count?: number;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={active ? 'page' : undefined}
      className={cn(
        'flex h-9 w-full items-center gap-da-md rounded-md px-da-lg text-left da-body-strong transition-colors duration-[120ms] ease-out',
        active ? 'bg-accent text-primary' : 'text-foreground hover:bg-subtle',
      )}
    >
      {icon && <Icon name={icon} />}
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {count !== undefined && <span className="text-muted-foreground da-caption">{count}</span>}
    </button>
  );
}
