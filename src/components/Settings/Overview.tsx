import React from 'react';
import { SettingsLayout, SettingsSection } from './SettingsLayout';
import { OverviewSection } from './OverviewSection';
import { OrganizationSection } from './OrganizationSection';
import { IAMSection } from './IAMSection';
import { AuthSection } from './AuthSection';
import { AIInfrastructureSection } from './AIInfrastructureSection';
import { InfrastructureSection } from './InfrastructureSection';
import { StorageSection } from './StorageSection';
import { SecretsSection } from './SecretsSection';
import { SecurityComplianceSection } from './SecurityComplianceSection';
import { BillingQuotasSection } from './BillingQuotasSection';
import { APISection } from './APISection';
import { AdvancedPlatformSection } from './AdvancedPlatformSection';
import { Placeholder } from '../shared/Placeholder';
import { Boxes, Code, Flag, ShieldHalf } from 'lucide-react';

const SettingsView = ({ initialSection = 'OVERVIEW' }: { initialSection?: SettingsSection }) => {
  // Settings' own section nav is independent of the module nav.
  const [activeSection, setActiveSection] = React.useState<SettingsSection>(initialSection);

  const renderSection = () => {
    switch (activeSection) {
      case 'OVERVIEW':       return <OverviewSection />;
      case 'ORGANIZATION':   return <OrganizationSection />;
      case 'IAM':            return <IAMSection />;
      case 'AUTH':           return <AuthSection />;
      case 'AI_PROVIDERS':   return <AIInfrastructureSection />;
      case 'INFRASTRUCTURE': return <InfrastructureSection />;
      case 'STORAGE':        return <StorageSection />;
      case 'SECRETS':        return <SecretsSection />;
      case 'SECURITY':       return <SecurityComplianceSection />;
      case 'BILLING':        return <BillingQuotasSection />;
      case 'API':            return <APISection />;
      case 'ADVANCED':       return <AdvancedPlatformSection />;
      case 'WORKSPACES' as SettingsSection:
        return <Placeholder
          title="Workspaces"
          icon={Boxes}
          description="Create and manage isolated workspaces inside this organization, each with its own projects, members, and resource quotas."
          plannedFeatures={[
            'Workspace creation and archival',
            'Per-workspace member assignment',
            'Workspace-level quotas and budgets',
            'Cross-workspace governance policies',
          ]}
        />;
      case 'RBAC' as SettingsSection:
        return <Placeholder
          title="Role-Based Access Control"
          icon={ShieldHalf}
          description="Fine-grained permissions across all resources. Define custom roles, scope them to environments, and audit every grant."
          plannedFeatures={[
            'Custom role builder',
            'Per-resource permission matrix',
            'Time-bound elevated access',
            'RBAC change audit trail',
          ]}
        />;
      case 'MODEL_REG' as SettingsSection:
        return <Placeholder
          title="Model Registry"
          icon={Boxes}
          description="Catalog every model used across agents and workflows. Track versions, evaluation scores, and rollout status."
          plannedFeatures={[
            'Versioned model catalog',
            'Model evaluation dashboards',
            'Rollout & canary management',
            'Model deprecation lifecycle',
          ]}
        />;
      case 'FLAGS' as SettingsSection:
        return <Placeholder
          title="Feature Flags"
          icon={Flag}
          description="Roll out features safely with progressive flags, targeting rules, and instant kill-switches."
          plannedFeatures={[
            'Targeted feature rollouts',
            'A/B & multivariate experiments',
            'Per-tenant overrides',
            'Real-time kill-switch',
          ]}
        />;
      default:
        return <Placeholder
          title="Initializing"
          description={`Section "${activeSection}" is initializing...`}
          comingSoon={false}
        />;
    }
  };

  return (
    <div className="max-w-[1600px] mx-auto">
      <SettingsLayout activeSection={activeSection} onSectionChange={setActiveSection}>
        {renderSection()}
      </SettingsLayout>
    </div>
  );
};

export default SettingsView;
