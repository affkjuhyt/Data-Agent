import React from 'react';
import { Sparkles, CheckCircle2 } from 'lucide-react';
import { Badge } from '../ui';

interface PlaceholderProps {
  title: string;
  description: string;
  icon?: any;
  plannedFeatures?: string[];
  comingSoon?: boolean;
}

/** Module-level "not built yet" panel — same visual language as ui <EmptyState/> (dashed soft border, #FFFDF6). */
export const Placeholder = ({
  title,
  description,
  icon: Icon = Sparkles,
  plannedFeatures = [],
  comingSoon = true,
}: PlaceholderProps) => {
  return (
    <div className="ui-empty max-w-3xl mx-auto p-10 lg:p-12">
      <div className="w-11 h-11 rounded-2xl bg-[#F4E8C3] border border-[#BFA66A] flex items-center justify-center mb-3">
        <Icon className="w-5 h-5 text-[#8A5A00]" />
      </div>
      <div className="flex items-center justify-center gap-3 flex-wrap">
        <h2 className="text-xl font-bold text-[#111111] tracking-tight font-display">{title}</h2>
        {comingSoon && <Badge tone="running">Coming Soon</Badge>}
      </div>
      <p className="mt-2 text-[14px] text-[#3F3F3F] leading-relaxed max-w-xl">{description}</p>

      {plannedFeatures.length > 0 && (
        <div className="mt-6 text-left w-full max-w-md">
          <h3 className="text-[11px] font-bold text-[#3F3F3F] uppercase tracking-widest mb-3">Planned capabilities</h3>
          <ul className="space-y-2">
            {plannedFeatures.map((feature, i) => (
              <li key={i} className="flex items-start gap-2.5 text-[13px] text-[#3F3F3F]">
                <CheckCircle2 className="w-4 h-4 text-[#8A5A00] shrink-0 mt-0.5" />
                <span>{feature}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
};
