import React from 'react';
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
import { EmptyState } from '../../design-system/EmptyState';

/** Settings sections. IDs are the `settings` sub-tab IDs in constants.ts
 *  (MODULE_SUB_ITEMS) and routes.tsx — the module's secondary nav is the ONLY
 *  navigation for Settings; there is no nested sidebar. */
export type SettingsSection =
  | 'OVERVIEW'
  | 'ORGANIZATION'
  | 'IAM'
  | 'AUTH'
  | 'SECRETS'
  | 'SECURITY'
  | 'AI_PROVIDERS'
  | 'INFRASTRUCTURE'
  | 'STORAGE'
  | 'BILLING'
  | 'API'
  | 'ADVANCED';

const SECTIONS: Record<SettingsSection, () => React.ReactNode> = {
  OVERVIEW:       () => <OverviewSection />,
  ORGANIZATION:   () => <OrganizationSection />,
  IAM:            () => <IAMSection />,
  AUTH:           () => <AuthSection />,
  SECRETS:        () => <SecretsSection />,
  SECURITY:       () => <SecurityComplianceSection />,
  AI_PROVIDERS:   () => <AIInfrastructureSection />,
  INFRASTRUCTURE: () => <InfrastructureSection />,
  STORAGE:        () => <StorageSection />,
  BILLING:        () => <BillingQuotasSection />,
  API:            () => <APISection />,
  ADVANCED:       () => <AdvancedPlatformSection />,
};

const SettingsView = ({ section }: { section: SettingsSection }) => {
  // Rendered straight into the app's <main> like every other module: no nested
  // frame, no inner scroll — the page scrolls.
  const render = SECTIONS[section];
  return render
    ? <>{render()}</>
    : <EmptyState icon="alert" title="Không có mục này" description={`Mục "${section}" không còn trong Quản trị. Chọn một mục ở menu bên trái.`} />;
};

export default SettingsView;
