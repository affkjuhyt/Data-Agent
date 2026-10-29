import { useEffect, type CSSProperties, type ReactNode } from 'react';
import { toast as sonner, Toaster } from 'sonner';
import { cn } from '../lib/utils';
import { Icon, type IconName } from './Icon';

/*
 * Figma "Toast" (2080:689) and "Spec · Component · Toast": inverted (foreground) surface,
 * radius-lg, shadow-lg, 360–440 wide, bottom-right 24px from the edges.
 * Auto-dismiss after 2.5 s, 5 s when it carries an action (Hoàn tác); hovering pauses the count.
 * At most three stacked; a new toast pushes the older ones up. Sonner keeps the stack and the
 * clock; the surface is ours, drawn with toast.custom.
 */
type ToastTone = 'success' | 'error' | 'info';

interface ToastOptions {
  tone: ToastTone;
  message: string;
  action?: { label: string; onClick: () => void };
  duration?: number;
}

const ICON: Record<ToastTone, { name: IconName; className: string }> = {
  success: { name: 'check', className: 'text-success' },
  error: { name: 'alert', className: 'text-error' },
  info: { name: 'info', className: 'text-info' },
};

const ACTION: Record<ToastTone, string> = {
  success: 'text-success',
  error: 'text-error',
  info: 'text-info',
};

const MAX_STACK = 3;

function show(options: ToastOptions) {
  sonner.custom((id) => <ToastBody id={id} {...options} />, {
    duration: options.duration ?? (options.action ? 5000 : 2500),
  });
}

/** Stable: the same function on every render, so it is safe in an effect's dependencies. */
export function useToast() {
  return show;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  // Sonner's store outlives the provider; a remount (a test, a sign-out) starts with no toasts.
  useEffect(() => () => void sonner.dismiss(), []);
  return (
    <>
      {children}
      <Toaster
        position="bottom-right"
        offset={24}
        gap={12}
        visibleToasts={MAX_STACK}
        expand
        // Sonner lays its stack out at --width (356 by default); the spec's toast is 420 wide.
        style={{ zIndex: 'var(--z-toast)', '--width': '420px' } as CSSProperties}
      />
    </>
  );
}

function ToastBody({ id, tone, message, action }: ToastOptions & { id: number | string }) {
  const icon = ICON[tone];
  return (
    <div
      role={tone === 'error' ? 'alert' : 'status'}
      className="flex w-[420px] [--icon-fill:transparent] max-w-[calc(100vw-48px)] items-center gap-da-md rounded-lg bg-foreground px-da-lg py-da-md shadow-lg"
    >
      <Icon name={icon.name} className={icon.className} />
      <p className="min-w-0 flex-1 text-card da-body-small">{message}</p>
      {action && (
        <button
          type="button"
          onClick={() => {
            action.onClick();
            sonner.dismiss(id);
          }}
          className={cn('shrink-0 da-body-strong', ACTION[tone])}
        >
          {action.label}
        </button>
      )}
      <button
        type="button"
        aria-label="Đóng thông báo"
        onClick={() => sonner.dismiss(id)}
        className="shrink-0 text-muted-foreground hover:text-card"
      >
        <Icon name="x" />
      </button>
    </div>
  );
}
