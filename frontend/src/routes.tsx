import React from 'react';
import type { AreaId } from './components/shell/areas';

const HomePage = React.lazy(() => import('./features/home/HomePage'));
const NeedsYouPage = React.lazy(() => import('./features/needs-you/NeedsYouPage'));
const ProposalPage = React.lazy(() => import('./features/needs-you/ProposalPage'));
const ConflictPage = React.lazy(() => import('./features/needs-you/ConflictPage'));
const AgentTeamPage = React.lazy(() => import('./features/agents/AgentTeamPage'));
const RunsPage = React.lazy(() => import('./features/agents/RunsPage'));
const RunDetailPage = React.lazy(() => import('./features/agents/RunDetailPage'));
const AnalysisPage = React.lazy(() => import('./features/analysis/AnalysisPage'));
const DataPage = React.lazy(() => import('./features/data/DataPage'));
const AdminPage = React.lazy(() => import('./features/admin/AdminPage'));
const AskDataPage = React.lazy(() => import('./features/ask/AskDataPage'));
const UsersPage = React.lazy(() => import('./features/admin-access/UsersPage'));
const DataSpacesPage = React.lazy(() => import('./features/admin-access/DataSpacesPage'));
const CreateDataSpacePage = React.lazy(() => import('./features/admin-access/CreateDataSpacePage'));
const PermissionComparePage = React.lazy(() => import('./features/admin-access/PermissionComparePage'));
const PoliciesPage = React.lazy(() => import('./features/admin-access/PoliciesPage'));
const AccessRequestsPage = React.lazy(() => import('./features/admin-access/AccessRequestsPage'));
const AuditPage = React.lazy(() => import('./features/admin-access/AuditPage'));
const PrivacyPage = React.lazy(() => import('./features/admin-access/PrivacyPage'));
const ModelsPage = React.lazy(() => import('./features/admin-platform/ModelsPage'));
const IntegrationsPage = React.lazy(() => import('./features/admin-platform/IntegrationsPage'));
const MonitoringPage = React.lazy(() => import('./features/admin-platform/MonitoringPage'));
const CreateAgentPage = React.lazy(() => import('./features/agent-builder-v3/CreateAgentPage'));
const AgentConfigPage = React.lazy(() => import('./features/agent-builder-v3/AgentConfigPage'));
const EvaluatePage = React.lazy(() => import('./features/agent-ops/EvaluatePage'));
const OrchestrationPage = React.lazy(() => import('./features/agent-ops/OrchestrationPage'));
const AgentOperationsPage = React.lazy(() => import('./features/admin-platform/AgentOperationsPage'));
const SessionsPage = React.lazy(() => import('./features/sessions/SessionsPage'));
const ReportReviewPage = React.lazy(() => import('./features/sessions/ReportReviewPage'));
const DashboardPage = React.lazy(() => import('./features/dashboards/DashboardPage'));
const SourcesPage = React.lazy(() => import('./features/data-sources/SourcesPage'));
const SourceDetailPage = React.lazy(() => import('./features/data-sources/SourceDetailPage'));
const TableProfilePage = React.lazy(() => import('./features/data-sources/TableProfilePage'));
const ConnectPage = React.lazy(() => import('./features/data-sources/ConnectPage'));
const JobsPage = React.lazy(() => import('./features/data-sources/JobsPage'));
const UploadPage = React.lazy(() => import('./features/data-sources/UploadPage'));
const CollectionsPage = React.lazy(() => import('./features/data-sources/CollectionsPage'));
const CollectionPage = React.lazy(() => import('./features/data-sources/CollectionPage'));
const PeriodClosePage = React.lazy(() => import('./features/periods/PeriodClosePage'));
const RetroChangePage = React.lazy(() => import('./features/periods/RetroChangePage'));
const CompareVersionsPage = React.lazy(() => import('./features/periods/CompareVersionsPage'));
const MetricPage = React.lazy(() => import('./features/semantics/MetricPage'));
const ExamplesPage = React.lazy(() => import('./features/semantics/ExamplesPage'));
const SourceAuthorityPage = React.lazy(() => import('./features/semantics/SourceAuthorityPage'));
const DecisionRulePage = React.lazy(() => import('./features/rules/DecisionRulePage'));
const ProfilePage = React.lazy(() => import('./features/admin-platform/ProfilePage'));
const NotificationsPage = React.lazy(() => import('./features/admin-platform/NotificationsPage'));
const ChannelRulesPage = React.lazy(() => import('./features/admin-platform/ChannelRulesPage'));
const ShortcutsPage = React.lazy(() => import('./features/admin-platform/ShortcutsPage'));

type RouteEntry = { render: () => React.ReactNode };

/** Page key for areas that have a single screen and no rail. */
export const SINGLE_SCREEN = '_';

/**
 * area → page → screen, for the six v3.1 areas and Cài đặt (constants.ts AREA_RAILS, AREA_LANDING).
 * SINGLE_SCREEN is an area's landing.
 * Nothing here is a placeholder: every entry is a real screen.
 */
export const ROUTE_MAP: Record<AreaId, Record<string, RouteEntry>> = {
  home: {
    [SINGLE_SCREEN]: { render: () => <HomePage /> },
  },

  'needs-you': {
    [SINGLE_SCREEN]: { render: () => <NeedsYouPage /> },
    // Detail pages, addressed /needs-you/<page>/<id>.
    PROPOSAL:        { render: () => <ProposalPage /> },
    CONFLICT:        { render: () => <ConflictPage /> },
  },

  agents: {
    TEAM:          { render: () => <AgentTeamPage /> },
    // Off the rail, under "Đội agent": P13 Tạo agent từ mô tả and one agent's page (/agents/agent/<id>).
    BUILDER:       { render: () => <CreateAgentPage /> },
    AGENT:         { render: () => <AgentConfigPage /> },
    RUNS:          { render: () => <RunsPage /> },
    // /agents/run/<id>, under the "Lượt chạy" rail item.
    RUN:           { render: () => <RunDetailPage /> },
    EVALUATE:      { render: () => <EvaluatePage /> },
    ORCHESTRATION: { render: () => <OrchestrationPage /> },
    OPERATIONS:    { render: () => <AgentOperationsPage /> },
  },

  analysis: {
    [SINGLE_SCREEN]: { render: () => <AnalysisPage /> },
    ASK:       { render: () => <AskDataPage /> },
    // /analysis/sessions lists sessions, /analysis/sessions/<id> opens one as a notebook.
    SESSIONS:  { render: () => <SessionsPage /> },
    // /analysis/report/<id>, under "Phiên làm việc"; Cần bạn report_publish cards open it.
    REPORT:    { render: () => <ReportReviewPage /> },
    DASHBOARD: { render: () => <DashboardPage /> },
  },

  data: {
    [SINGLE_SCREEN]: { render: () => <DataPage /> },
    SOURCES:         { render: () => <SourcesPage /> },
    SOURCE:          { render: () => <SourceDetailPage /> },
    TABLE:           { render: () => <TableProfilePage /> },
    CONNECT:         { render: () => <ConnectPage /> },
    JOBS:            { render: () => <JobsPage /> },
    UPLOAD:          { render: () => <UploadPage /> },
    COLLECTIONS:     { render: () => <CollectionsPage /> },
    COLLECTION:      { render: () => <CollectionPage /> },
    // Kỳ chốt: /data/period/<id>, /data/retro/<id>; without an id each opens its first item.
    PERIOD:          { render: () => <PeriodClosePage /> },
    RETRO:           { render: () => <RetroChangePage /> },
    COMPARE:         { render: () => <CompareVersionsPage /> },
    // Ngữ nghĩa (lớp ngữ cảnh): /data/metric/<id>, /data/rule/<id>.
    METRIC:          { render: () => <MetricPage /> },
    EXAMPLES:        { render: () => <ExamplesPage /> },
    AUTHORITY:       { render: () => <SourceAuthorityPage /> },
    RULE:            { render: () => <DecisionRulePage /> },
  },

  admin: {
    [SINGLE_SCREEN]:    { render: () => <AdminPage /> },
    USERS:              { render: () => <UsersPage /> },
    DATA_SPACES:        { render: () => <DataSpacesPage /> },
    DATA_SPACE_NEW:     { render: () => <CreateDataSpacePage /> },
    PERMISSION_COMPARE: { render: () => <PermissionComparePage /> },
    POLICIES:           { render: () => <PoliciesPage /> },
    ACCESS_REQUESTS:    { render: () => <AccessRequestsPage /> },
    MODELS:             { render: () => <ModelsPage /> },
    INTEGRATIONS:       { render: () => <IntegrationsPage /> },
    MONITORING:         { render: () => <MonitoringPage /> },
    AUDIT:              { render: () => <AuditPage /> },
    PRIVACY:            { render: () => <PrivacyPage /> },
  },

  // Cài đặt: the user's own settings, opened from the avatar menu.
  account: {
    PROFILE:       { render: () => <ProfilePage /> },
    NOTIFICATIONS: { render: () => <NotificationsPage /> },
    CHANNEL_RULES: { render: () => <ChannelRulesPage /> },
    SHORTCUTS:     { render: () => <ShortcutsPage /> },
  },
};
