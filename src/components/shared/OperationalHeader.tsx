import React from 'react';
import { ChevronLeft } from 'lucide-react';
import { cn } from '../../lib/utils';

interface OperationalHeaderProps {
  title: string;
  subtitle?: string;
  breadcrumbs: { label: string; href?: string }[];
  actions?: React.ReactNode;
  status?: React.ReactNode;
  onBack?: () => void;
  className?: string;
}

export const OperationalHeader = ({
  title, subtitle, breadcrumbs, actions, status, onBack, className,
}: OperationalHeaderProps) => {
  return (
    <div className={cn('hero-band mb-7', className)}>
      <div className="relative z-[1]">
        <div className="flex items-center gap-1.5 mb-3">
          {onBack && (
            <button
              onClick={onBack}
              aria-label="Back"
              className="w-7 h-7 flex items-center justify-center rounded-lg border border-white/25 text-white/70 hover:bg-white/10 hover:text-white transition-all mr-1"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
          )}
          {breadcrumbs.map((crumb, i) => (
            <React.Fragment key={crumb.label}>
              {i > 0 && <span className="text-white/40 text-xs">/</span>}
              <span className={cn(
                'text-xs font-semibold',
                i === breadcrumbs.length - 1 ? 'text-[#F3E2A7]' : 'text-white/60',
              )}>
                {crumb.label}
              </span>
            </React.Fragment>
          ))}
        </div>

        <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-4">
          <div className="space-y-1.5 min-w-0">
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="text-2xl sm:text-3xl font-bold !text-white tracking-tight leading-none font-display">
                {title}
              </h1>
              {status}
            </div>
            {subtitle && (
              <p className="text-sm text-white/70 leading-relaxed max-w-2xl">{subtitle}</p>
            )}
          </div>
          {actions && (
            <div className="flex flex-wrap items-center gap-2 shrink-0">
              {actions}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
