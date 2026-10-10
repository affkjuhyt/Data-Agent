import { cn } from '../lib/utils';

/** Figma "Avatar" (2015:137): a person's initials on accent. */
export function Avatar({
  name,
  initials,
  className,
}: {
  name: string;
  initials?: string;
  className?: string;
}) {
  const text =
    initials ??
    name
      .split(/\s+/)
      .filter(Boolean)
      .slice(-2)
      .map((part) => part[0]?.toUpperCase())
      .join('');
  return (
    <span
      title={name}
      aria-label={name}
      className={cn(
        'inline-flex size-7 shrink-0 items-center justify-center rounded-full bg-accent text-primary da-label',
        className,
      )}
    >
      {text}
    </span>
  );
}
