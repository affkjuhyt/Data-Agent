import { cn } from '../lib/utils';
import { Icon } from './Icon';

/*
 * Figma "SuggestionChip" (2018:172): a suggested question or command, radius-full, card with a
 * border, the agent mark in primary. Clicking fills the composer or runs it.
 */
export function SuggestionChip({
  label,
  onSelect,
  showIcon = true,
  className,
}: {
  label: string;
  onSelect: () => void;
  showIcon?: boolean;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        'inline-flex items-center gap-da-sm whitespace-nowrap rounded-full border border-border bg-card px-da-lg py-da-sm text-foreground da-body-small',
        'transition-colors duration-[120ms] ease-out hover:border-border-strong hover:bg-subtle',
        className,
      )}
    >
      {showIcon && <Icon name="agent" className="text-primary" />}
      {label}
    </button>
  );
}
