/**
 * Provisioning — agent templates saved in platform settings.
 *
 *   GET /api/platform/settings/agent_templates → { value: AgentTemplate[] | null }
 *
 * Picking a template (or the empty-state CTA) hands off to NewAgentWizard,
 * which is the only thing that actually creates an agent (POST /api/agents).
 */
import React from 'react';
import { LayoutTemplate, Plus, RefreshCw, ArrowRight } from 'lucide-react';
import { usePlatformSetting, type AgentTemplate } from '../../../lib/platformApi';
import { Badge, Button, EmptyState, ErrorState, LoadingState } from '../../ui';

interface ProvisioningProps {
  onUseTemplate: (template: AgentTemplate) => void;
  onCreateBlank: () => void;
}

export const ProvisioningView: React.FC<ProvisioningProps> = ({ onUseTemplate, onCreateBlank }) => {
  const templates = usePlatformSetting<AgentTemplate[]>('agent_templates', []);
  const list = Array.isArray(templates.value) ? templates.value : [];

  return (
    <div className="p-5 space-y-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-[12px] text-[#3F3F3F]">
          Template là điểm khởi đầu: chọn một template để mở Wizard với tên đã điền sẵn, sau đó chọn workflow và tạo agent.
        </p>
        <Button size="sm" variant="ghost" icon={RefreshCw} onClick={() => void templates.refetch()} loading={templates.loading && list.length > 0}>Làm mới</Button>
      </div>

      {templates.loading && list.length === 0 && <LoadingState label="Đang tải template…" />}
      {templates.error && <ErrorState message={templates.error} onRetry={() => void templates.refetch()} />}

      {!templates.loading && !templates.error && list.length === 0 && (
        <EmptyState
          icon={LayoutTemplate}
          title="Chưa có template"
          description="Chưa có template nào được lưu trong platform settings (key agent_templates). Bạn vẫn có thể tạo agent từ Wizard."
          action={<Button variant="primary" icon={Plus} onClick={onCreateBlank}>Tạo agent từ Wizard</Button>}
        />
      )}

      {list.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {list.map(t => (
            <div key={t.id} className="content-card content-card--static p-4 flex flex-col gap-3">
              <div>
                <h4 className="text-sm font-bold text-[#111111]">{t.name}</h4>
                <p className="text-[12px] text-[#3F3F3F] mt-1">{t.description || '—'}</p>
              </div>
              {t.tags?.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {t.tags.map(tag => <Badge key={tag} tone="draft">{tag}</Badge>)}
                </div>
              )}
              <div className="mt-auto">
                <Button size="sm" variant="primary" icon={ArrowRight} onClick={() => onUseTemplate(t)}>Dùng template</Button>
              </div>
            </div>
          ))}
          <div className="content-card content-card--static p-4 flex flex-col items-start justify-center gap-2 border-dashed">
            <p className="text-sm font-bold text-[#111111]">Bắt đầu trống</p>
            <p className="text-[12px] text-[#3F3F3F]">Không dùng template — đặt tên và chọn workflow trong Wizard.</p>
            <Button size="sm" icon={Plus} onClick={onCreateBlank}>Tạo agent từ Wizard</Button>
          </div>
        </div>
      )}
    </div>
  );
};
