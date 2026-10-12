import { cn } from '../lib/utils';
import toggleOff from './assets/toggle-off.svg';
import toggleOn from './assets/toggle-on.svg';
import toggleDisabled from './assets/toggle-disabled.svg';

/*
 * Figma "Toggle" (2080:586): 32×18, State = Off | On | Disabled; the track is the Figma asset of
 * each state. role=switch, applies immediately — confirm the change with a toast.
 */
export function Toggle({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  /** Accessible name; pair with a visible label next to it. */
  label: string;
  disabled?: boolean;
}) {
  const src = disabled ? toggleDisabled : checked ? toggleOn : toggleOff;
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn('block h-[18px] w-8 shrink-0 rounded-full', disabled && 'cursor-not-allowed')}
    >
      <img src={src} alt="" width={32} height={18} className="block" />
    </button>
  );
}
