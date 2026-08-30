import React from 'react';
import { cn } from '../../lib/utils';
import { CheckCircle2, AlertCircle, XCircle, Clock } from 'lucide-react';

type StatusType =
  | 'HEALTHY' | 'WARNING' | 'CRITICAL' | 'STABLE' | 'PROCESSING'
  | 'PENDING' | 'ACTIVE' | 'INACTIVE' | string;

interface StatusBadgeProps {
  status: StatusType;
  className?: string;
  size?: 'sm' | 'md' | 'lg';
}

/* WCAG-readable warm status palette — dark text on pale backgrounds */
const getStyles = (status: string) => {
  const s = status.toUpperCase();
  if (s.includes('HEALTHY') || s.includes('STABLE') || s.includes('ACTIVE') || s.includes('COMPLETED') || s.includes('SUCCESS') || s.includes('SECURE') || s.includes('OPTIMAL') || s.includes('COMPLIANT')) {
    return { bg: 'bg-[#E7F2D4]', border: 'border-[#6B8E23]', text: 'text-[#2F4F0B]', icon: CheckCircle2 };
  }
  if (s.includes('WARNING') || s.includes('PENDING') || s.includes('DRIFT') || s.includes('DEGRADED') || s.includes('ACTION')) {
    return { bg: 'bg-[#FFF1BF]', border: 'border-[#B88719]', text: 'text-[#5C3900]', icon: AlertCircle };
  }
  if (s.includes('CRITICAL') || s.includes('FAILED') || s.includes('ERROR') || s.includes('BLOCKED')) {
    return { bg: 'bg-[#FAD7D7]', border: 'border-[#9F1D1D]', text: 'text-[#7A1010]', icon: XCircle };
  }
  if (s.includes('PROCESSING') || s.includes('RUNNING') || s.includes('SYNCING') || s.includes('DEPLOYING') || s.includes('AUTOSCALING')) {
    return { bg: 'bg-[#E8E2FF]', border: 'border-[#7C3AED]', text: 'text-[#3B0764]', icon: Clock };
  }
  if (s.includes('INACTIVE') || s.includes('DORMANT') || s.includes('PAUSED') || s.includes('DRAFT')) {
    return { bg: 'bg-[#EFEAE0]', border: 'border-[#8A5A00]', text: 'text-[#2A2A2A]', icon: Clock };
  }
  return { bg: 'bg-[#EFEAE0]', border: 'border-[#8A5A00]', text: 'text-[#2A2A2A]', icon: Clock };
};

const sizeStyles = {
  sm: 'px-2 py-0.5 text-[10px] gap-1',
  md: 'px-2.5 py-1 text-[11px] gap-1.5',
  lg: 'px-3 py-1.5 text-[11px] gap-1.5',
};

const iconSizes = {
  sm: 'w-3 h-3',
  md: 'w-3.5 h-3.5',
  lg: 'w-3.5 h-3.5',
};

export const StatusBadge = ({ status, className, size = 'md' }: StatusBadgeProps) => {
  const styles     = getStyles(status);
  const Icon       = styles.icon;
  const isSpinning = status.toUpperCase().includes('PROCESSING') || status.toUpperCase().includes('SYNCING');

  return (
    <div className={cn(
      'inline-flex items-center font-bold uppercase tracking-wide rounded-full border',
      styles.bg, styles.border, styles.text,
      sizeStyles[size],
      className,
    )}>
      <Icon className={cn(iconSizes[size], isSpinning && 'animate-spin-slow')} />
      {status}
    </div>
  );
};
