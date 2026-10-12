import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { cn } from '../lib/utils';
import { Icon, type IconName } from './Icon';

/*
 * Figma "Toast" (2080:689) and "Spec · Component · Toast": inverted (foreground) surface,
 * radius-lg, shadow-lg, 360–440 wide, bottom-right 24px from the edges.
 * Auto-dismiss after 2.5 s, 5 s when it carries an action (Hoàn tác); hovering pauses the count.
 * At most three stacked; a new toast pushes the older ones up.
 */
type ToastTone = 'success' | 'error' | 'info';

interface ToastOptions {
  tone: ToastTone;
  message: string;
  action?: { label: string; onClick: () => void };
  duration?: number;
}

interface ToastEntry extends ToastOptions {
  id: number;
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

const ToastContext = createContext<((options: ToastOptions) => void) | null>(null);

export function useToast() {
  const toast = useContext(ToastContext);
  if (!toast) throw new Error('useToast must be used inside <ToastProvider>');
  return toast;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastEntry[]>([]);
  const nextId = useRef(0);

  const dismiss = useCallback(
    (id: number) => setToasts((list) => list.filter((t) => t.id !== id)),
    [],
  );
  const toast = useCallback((options: ToastOptions) => {
    nextId.current += 1;
    const entry = { ...options, id: nextId.current };
    setToasts((list) => [...list, entry].slice(-MAX_STACK));
  }, []);

  const value = useMemo(() => toast, [toast]);
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed bottom-da-2xl right-da-2xl z-[var(--z-toast)] flex flex-col gap-da-md">
        {toasts.map((entry) => (
          <ToastItem key={entry.id} entry={entry} onDismiss={dismiss} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function ToastItem({ entry, onDismiss }: { entry: ToastEntry; onDismiss: (id: number) => void }) {
  const [paused, setPaused] = useState(false);
  const duration = entry.duration ?? (entry.action ? 5000 : 2500);

  // onDismiss is the provider's stable callback, so the count restarts only on hover-out.
  useEffect(() => {
    if (paused) return;
    const timer = window.setTimeout(() => onDismiss(entry.id), duration);
    return () => window.clearTimeout(timer);
  }, [paused, duration, onDismiss, entry.id]);

  const icon = ICON[entry.tone];
  return (
    <div
      role={entry.tone === 'error' ? 'alert' : 'status'}
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
      className="pointer-events-auto flex w-[420px] [--icon-fill:transparent] max-w-[calc(100vw-48px)] items-center gap-da-md rounded-lg bg-foreground px-da-lg py-da-md shadow-lg"
    >
      <Icon name={icon.name} className={icon.className} />
      <p className="min-w-0 flex-1 text-card da-body-small">{entry.message}</p>
      {entry.action && (
        <button
          type="button"
          onClick={() => {
            entry.action?.onClick();
            onDismiss(entry.id);
          }}
          className={cn('shrink-0 da-body-strong', ACTION[entry.tone])}
        >
          {entry.action.label}
        </button>
      )}
      <button
        type="button"
        aria-label="Đóng thông báo"
        onClick={() => onDismiss(entry.id)}
        className="shrink-0 text-muted-foreground hover:text-card"
      >
        <Icon name="x" />
      </button>
    </div>
  );
}
