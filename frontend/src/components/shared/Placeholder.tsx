import React from 'react';
import { Sparkles, CheckCircle2 } from 'lucide-react';

interface PlaceholderProps {
  title: string;
  description: string;
  icon?: any;
  plannedFeatures?: string[];
  comingSoon?: boolean;
}

export const Placeholder = ({
  title,
  description,
  icon: Icon = Sparkles,
  plannedFeatures = [],
  comingSoon = true,
}: PlaceholderProps) => {
  return (
    <div className="content-card p-10 lg:p-12 max-w-3xl mx-auto">
      <div className="flex items-start gap-5">
        <div className="w-12 h-12 rounded-xl bg-[#F4E8C3] border border-[#BFA66A] flex items-center justify-center shrink-0">
          <Icon className="w-6 h-6 text-[#B88719]" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h2 className="text-xl font-bold text-[#111111] tracking-tight font-display">{title}</h2>
            {comingSoon && (
              <span className="px-2 py-0.5 bg-[#E8E2FF] text-[#4C1D95] border border-[#8B5CF6] text-[10px] font-bold uppercase tracking-wider rounded-full">
                Coming Soon
              </span>
            )}
          </div>
          <p className="mt-2 text-[14px] text-[#3F3F3F] leading-relaxed">{description}</p>

          {plannedFeatures.length > 0 && (
            <div className="mt-6">
              <h3 className="text-[11px] font-bold text-[#5F5F5F] uppercase tracking-widest mb-3">Planned capabilities</h3>
              <ul className="space-y-2">
                {plannedFeatures.map((feature, i) => (
                  <li key={i} className="flex items-start gap-2.5 text-[13px] text-[#3F3F3F]">
                    <CheckCircle2 className="w-4 h-4 text-[#B88719] shrink-0 mt-0.5" />
                    <span>{feature}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
