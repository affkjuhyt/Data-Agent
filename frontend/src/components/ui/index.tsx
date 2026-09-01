/**
 * Shared UI primitives — the ONE place a button, card, stat tile, badge, input
 * or empty state is styled. Screens compose these; they do not re-declare
 * borders/colours with ad-hoc Tailwind hex values.
 *
 * Palette (see index.css :root tokens): text #111111 / #3F3F3F / #5F5F5F,
 * surfaces #FFFFFF / #FFF9E8 / #F4E8C3, borders var(--border) #A8945E (soft
 * #BFA66A, strong #8A5A00), accent #B88719 / #8A5A00.
 */
import React from 'react';
import type { LucideIcon } from 'lucide-react';
import { Loader2 } from 'lucide-react';
import { cn } from '../../lib/utils';

/* ─── Button ─────────────────────────────────────────────── */
type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';
type ButtonSize = 'sm' | 'md';

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: LucideIcon;
  loading?: boolean;
}

/** Every button is clickable or disabled — never decorative. */
export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ variant = 'secondary', size = 'md', icon: Icon, loading, className, children, disabled, type = 'button', ...rest }, ref) => (
    <button
      ref={ref}
      type={type}
      disabled={disabled || loading}
      className={cn(
        variant === 'primary' && 'btn-primary',
        variant === 'secondary' && 'btn-secondary',
        variant === 'ghost' && 'btn-ghost',
        variant === 'danger' && 'btn-danger',
        size === 'sm' && 'btn-sm',
        className,
      )}
      {...rest}
    >
      {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : Icon && <Icon className="w-3.5 h-3.5" />}
      {children}
    </button>
  ),
);
Button.displayName = 'Button';

/* ─── Card ───────────────────────────────────────────────── */
interface CardProps extends Omit<React.HTMLAttributes<HTMLDivElement>, 'title'> {
  title?: React.ReactNode;
  subtitle?: React.ReactNode;
  actions?: React.ReactNode;
  /** 'none' when the body manages its own padding (tables, lists). */
  padding?: 'none' | 'sm' | 'md';
  hover?: boolean;
}

export const Card = ({ title, subtitle, actions, padding = 'md', hover = false, className, children, ...rest }: CardProps) => (
  <div className={cn('content-card', !hover && 'content-card--static', className)} {...rest}>
    {(title || actions) && (
      <div className="flex items-start justify-between gap-3 px-5 pt-4 pb-3 border-b" style={{ borderColor: 'var(--border-soft)' }}>
        <div className="min-w-0">
          {title && <h3 className="text-[15px] font-bold text-[#111111] leading-tight font-display">{title}</h3>}
          {subtitle && <p className="text-[12px] text-[#5F5F5F] mt-0.5">{subtitle}</p>}
        </div>
        {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
      </div>
    )}
    <div className={cn(padding === 'md' && 'p-5', padding === 'sm' && 'p-3')}>{children}</div>
  </div>
);

/* ─── Section title (page-level header inside a module) ─── */
export const SectionTitle = ({ title, subtitle, actions }: { title: React.ReactNode; subtitle?: React.ReactNode; actions?: React.ReactNode }) => (
  <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-3 mb-5">
    <div className="min-w-0">
      <h2 className="text-2xl font-bold text-[#111111] tracking-tight font-display">{title}</h2>
      {subtitle && <p className="text-sm text-[#5F5F5F] mt-1 max-w-2xl">{subtitle}</p>}
    </div>
    {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
  </div>
);

/* ─── Stat tile ──────────────────────────────────────────── */
type Tone = 'neutral' | 'ok' | 'warn' | 'bad' | 'info';
const TONE_TEXT: Record<Tone, string> = {
  neutral: 'text-[#111111]', ok: 'text-[#1D6F4C]', warn: 'text-[#8A6212]', bad: 'text-[#A83A32]', info: 'text-[#0E5F63]',
};
const TONE_ICON_BG: Record<Tone, string> = {
  neutral: 'bg-[#F4E8C3] text-[#8A5A00]', ok: 'bg-[#DCEFE3] text-[#1D6F4C]', warn: 'bg-[#F3E7CC] text-[#8A6212]',
  bad: 'bg-[#F6DFDC] text-[#A83A32]', info: 'bg-[#DDEBF3] text-[#0E5F63]',
};

export const StatTile = ({ label, value, hint, icon: Icon, tone = 'neutral', loading }: {
  label: string; value: React.ReactNode; hint?: React.ReactNode; icon?: LucideIcon; tone?: Tone; loading?: boolean;
}) => (
  <div className="content-card content-card--static p-4">
    <div className="flex items-center justify-between mb-3">
      {Icon
        ? <div className={cn('w-9 h-9 rounded-xl flex items-center justify-center', TONE_ICON_BG[tone])}><Icon className="w-4 h-4" /></div>
        : <span />}
      {hint && <span className={cn('text-[11px] font-semibold', TONE_TEXT[tone])}>{hint}</span>}
    </div>
    <div className="text-2xl font-bold text-[#111111] font-display leading-none">
      {loading ? <span className="inline-block w-16 h-6 rounded bg-[#F4E8C3] animate-pulse" /> : value}
    </div>
    <div className="text-[10px] font-bold text-[#5F5F5F] uppercase tracking-wider mt-2">{label}</div>
  </div>
);

/* ─── Badge ──────────────────────────────────────────────── */
type BadgeTone = 'healthy' | 'warning' | 'failed' | 'running' | 'draft' | 'dark';
export const Badge = ({ tone = 'draft', className, children }: { tone?: BadgeTone; className?: string; children: React.ReactNode }) => (
  <span className={cn('inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wide', `badge-${tone}`, className)}>
    {children}
  </span>
);

/** Map a free-form status string to a badge tone. */
export const toneFor = (status: string | undefined | null): BadgeTone => {
  const s = (status ?? '').toLowerCase();
  if (/(ok|healthy|success|active|done|completed|up|stable|published|approved)/.test(s)) return 'healthy';
  if (/(warn|degraded|pending|expir|stale|review)/.test(s)) return 'warning';
  if (/(fail|error|down|reject|offline|blocked)/.test(s)) return 'failed';
  if (/(run|progress|processing|queued|streaming)/.test(s)) return 'running';
  return 'draft';
};

/* ─── Form fields ────────────────────────────────────────── */
export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...rest }, ref) => <input ref={ref} className={cn('ui-input', className)} {...rest} />,
);
Input.displayName = 'Input';

export const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, ...rest }, ref) => <select ref={ref} className={cn('ui-input', className)} {...rest}>{children}</select>,
);
Select.displayName = 'Select';

export const Field = ({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) => (
  <label className="block">
    <span className="block text-[11px] font-bold text-[#3F3F3F] uppercase tracking-wide mb-1.5">{label}</span>
    {children}
    {hint && <span className="block text-[11px] text-[#5F5F5F] mt-1">{hint}</span>}
  </label>
);

/* ─── Empty / loading / error states ────────────────────── */
export const EmptyState = ({ icon: Icon, title, description, action }: {
  icon?: LucideIcon; title: string; description?: React.ReactNode; action?: React.ReactNode;
}) => (
  <div className="ui-empty">
    {Icon && <div className="w-11 h-11 rounded-2xl bg-[#F4E8C3] border border-[#BFA66A] flex items-center justify-center mb-3"><Icon className="w-5 h-5 text-[#8A5A00]" /></div>}
    <p className="text-sm font-bold text-[#111111]">{title}</p>
    {description && <p className="text-[12px] text-[#5F5F5F] mt-1 max-w-md">{description}</p>}
    {action && <div className="mt-4">{action}</div>}
  </div>
);

export const LoadingState = ({ label = 'Đang tải…' }: { label?: string }) => (
  <div className="ui-empty"><Loader2 className="w-5 h-5 animate-spin text-[#8A5A00] mb-2" /><p className="text-[12px] text-[#5F5F5F]">{label}</p></div>
);

export const ErrorState = ({ message, onRetry }: { message: string; onRetry?: () => void }) => (
  <div className="p-4 rounded-xl border border-[#9F1D1D] bg-[#FAD7D7] flex items-start justify-between gap-3">
    <div>
      <p className="text-sm font-bold text-[#7A1010]">Không tải được dữ liệu</p>
      <p className="text-[12px] text-[#7A1010] font-mono mt-0.5 break-all">{message}</p>
    </div>
    {onRetry && <Button size="sm" onClick={onRetry}>Thử lại</Button>}
  </div>
);

/* ─── Simple table ───────────────────────────────────────── */
export interface Column<T> { header: string; cell: (row: T) => React.ReactNode; className?: string; align?: 'left' | 'right' | 'center' }
export function Table<T>({ rows, columns, rowKey, onRowClick, empty }: {
  rows: T[]; columns: Column<T>[]; rowKey: (row: T) => string; onRowClick?: (row: T) => void; empty?: React.ReactNode;
}) {
  if (rows.length === 0 && empty) return <>{empty}</>;
  return (
    <div className="overflow-x-auto">
      <table className="ui-table">
        <thead><tr>{columns.map(c => <th key={c.header} className={cn(c.className, c.align === 'right' && 'text-right', c.align === 'center' && 'text-center')}>{c.header}</th>)}</tr></thead>
        <tbody>
          {rows.map(r => (
            <tr key={rowKey(r)} onClick={onRowClick ? () => onRowClick(r) : undefined} className={cn(onRowClick && 'cursor-pointer')}>
              {columns.map(c => <td key={c.header} className={cn(c.className, c.align === 'right' && 'text-right', c.align === 'center' && 'text-center')}>{c.cell(r)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
