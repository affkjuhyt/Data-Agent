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
const KnowledgePageView = React.lazy(() => import('./features/knowledge/KnowledgePageView'));
const KnowledgeReviewPage = React.lazy(() => import('./features/knowledge/KnowledgeReviewPage'));
const CrawlSourcesPage = React.lazy(() => import('./features/crawl/CrawlSourcesPage'));
const CrawlRegisterPage = React.lazy(() => import('./features/crawl/CrawlRegisterPage'));
const CrawlSchemaPage = React.lazy(() => import('./features/crawl/CrawlSchemaPage'));
const CrawlDriftPage = React.lazy(() => import('./features/crawl/CrawlDriftPage'));
const CrawlReviewPage = React.lazy(() => import('./features/crawl/CrawlReviewPage'));
const CrawlConsolidationPage = React.lazy(() => import('./features/crawl/CrawlConsolidationPage'));
const DecisionRulePage = React.lazy(() => import('./features/rules/DecisionRulePage'));
const ProfilePage = React.lazy(() => import('./features/admin-platform/ProfilePage'));
const NotificationsPage = React.lazy(() => import('./features/admin-platform/NotificationsPage'));
const ChannelRulesPage = React.lazy(() => import('./features/admin-platform/ChannelRulesPage'));
const ShortcutsPage = React.lazy(() => import('./features/admin-platform/ShortcutsPage'));

const SkillLibraryPage = React.lazy(() => import('./features/skills/SkillLibraryPage'));
const SkillSuggestionsPage = React.lazy(() => import('./features/skills/SkillSuggestionsPage'));
const SkillVersionsPage = React.lazy(() => import('./features/skills/SkillVersionsPage'));
const SkillsToolsPage = React.lazy(() => import('./features/skills/SkillsToolsPage'));
const SkillEditorPage = React.lazy(() => import('./features/skill-authoring/SkillEditorPage'));
const SkillFromSessionPage = React.lazy(() => import('./features/skill-authoring/SkillFromSessionPage'));
const SkillTestsPage = React.lazy(() => import('./features/skill-authoring/SkillTestsPage'));
const SkillRunPage = React.lazy(() => import('./features/skill-authoring/SkillRunPage'));
const ExplorePage = React.lazy(() => import('./features/explore/ExplorePage'));
const ArtifactPage = React.lazy(() => import('./features/explore/ArtifactPage'));
const HowItWorksPage = React.lazy(() => import('./features/onboarding/HowItWorksPage'));
const ForecastPage = React.lazy(() => import('./features/forecast/ForecastPage'));
const ForecastJobPage = React.lazy(() => import('./features/forecast/ForecastJobPage'));
const ForecastStudioPage = React.lazy(() => import('./features/forecast/ForecastStudioPage'));
const ForecastModelPage = React.lazy(() => import('./features/forecast/ForecastModelPage'));
const DocumentAnswerPage = React.lazy(() => import('./features/documents/DocumentAnswerPage'));
const DocumentsPage = React.lazy(() => import('./features/documents/DocumentsPage'));
const DocumentViewerPage = React.lazy(() => import('./features/documents/DocumentViewerPage'));
const QualityQueuePage = React.lazy(() => import('./features/documents/QualityQueuePage'));
const ValidityPage = React.lazy(() => import('./features/documents/ValidityPage'));
const ExtractionSchemaPage = React.lazy(() => import('./features/documents/ExtractionSchemaPage'));
const SourceDriftPage = React.lazy(() => import('./features/data-sources/SourceDriftPage'));
const ErrorRecordsPage = React.lazy(() => import('./features/data-sources/ErrorRecordsPage'));
const JobSetupPage = React.lazy(() => import('./features/data-sources/JobSetupPage'));
const TableExplorePage = React.lazy(() => import('./features/data-sources/TableExplorePage'));
const ApiConnectPage = React.lazy(() => import('./features/data-sources/ApiConnectPage'));
const SqlPage = React.lazy(() => import('./features/data-prep/SqlPage'));
const PipelinesPage = React.lazy(() => import('./features/data-prep/PipelinesPage'));
const PipelinePage = React.lazy(() => import('./features/data-prep/PipelinePage'));
const PipelineSavePage = React.lazy(() => import('./features/data-prep/PipelineSavePage'));
const WritebackPage = React.lazy(() => import('./features/data-prep/WritebackPage'));
const LineagePage = React.lazy(() => import('./features/data-prep/LineagePage'));
const DataMartPage = React.lazy(() => import('./features/data-products/DataMartPage'));
const DataProductsPage = React.lazy(() => import('./features/data-products/DataProductsPage'));
const QualityRulesPage = React.lazy(() => import('./features/data-products/QualityRulesPage'));

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
    // "Data Agent hoạt động thế nào", opened after onboarding and from the guide link.
    HOW_IT_WORKS: { render: () => <HowItWorksPage /> },
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
    // Kỹ năng (P03): the library pages are rail items; the editor, tests and runs sit under them.
    SKILLS:             { render: () => <SkillLibraryPage /> },
    SKILL_SUGGESTIONS:  { render: () => <SkillSuggestionsPage /> },
    SKILL_VERSIONS:     { render: () => <SkillVersionsPage /> },
    TOOLS:              { render: () => <SkillsToolsPage /> },
    SKILL_EDITOR:       { render: () => <SkillEditorPage /> },
    SKILL_FROM_SESSION: { render: () => <SkillFromSessionPage /> },
    SKILL_TESTS:        { render: () => <SkillTestsPage /> },
    SKILL_RUN:          { render: () => <SkillRunPage /> },
  },

  analysis: {
    [SINGLE_SCREEN]: { render: () => <AnalysisPage /> },
    ASK:       { render: () => <AskDataPage /> },
    // /analysis/sessions lists sessions, /analysis/sessions/<id> opens one as a notebook.
    SESSIONS:  { render: () => <SessionsPage /> },
    // /analysis/report/<id>, under "Phiên làm việc"; Cần bạn report_publish cards open it.
    REPORT:    { render: () => <ReportReviewPage /> },
    DASHBOARD: { render: () => <DashboardPage /> },
    EXPLORE:         { render: () => <ExplorePage /> },
    // /analysis/artifact/<id>: an answer's or notebook's artifact, full screen.
    ARTIFACT:        { render: () => <ArtifactPage /> },
    FORECAST:        { render: () => <ForecastPage /> },
    // A forecast question answered asynchronously; it belongs to a session.
    FORECAST_JOB:    { render: () => <ForecastJobPage /> },
    FORECAST_STUDIO: { render: () => <ForecastStudioPage /> },
    FORECAST_MODEL:  { render: () => <ForecastModelPage /> },
    DOC_ASK:         { render: () => <DocumentAnswerPage /> },
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
    // Thu thập ngoài (P14): the list, registering a source, one source's schema (/data/crawl_source/<id>).
    CRAWL:           { render: () => <CrawlSourcesPage /> },
    CRAWL_NEW:       { render: () => <CrawlRegisterPage /> },
    CRAWL_SOURCE:    { render: () => <CrawlSchemaPage /> },
    CRAWL_DRIFT:     { render: () => <CrawlDriftPage /> },
    CRAWL_RUN:       { render: () => <CrawlReviewPage /> },
    CRAWL_MERGE:     { render: () => <CrawlConsolidationPage /> },
    // Kỳ chốt: /data/period/<id>, /data/retro/<id>; without an id each opens its first item.
    PERIOD:          { render: () => <PeriodClosePage /> },
    RETRO:           { render: () => <RetroChangePage /> },
    COMPARE:         { render: () => <CompareVersionsPage /> },
    // Ngữ nghĩa (lớp ngữ cảnh): /data/metric/<id>, /data/rule/<id>.
    METRIC:          { render: () => <MetricPage /> },
    EXAMPLES:        { render: () => <ExamplesPage /> },
    // /data/knowledge/<id>; without an id the first page of the index.
    KNOWLEDGE:       { render: () => <KnowledgePageView /> },
    AUTHORITY:       { render: () => <SourceAuthorityPage /> },
    RULE:            { render: () => <DecisionRulePage /> },
    KNOWLEDGE_REVIEW: { render: () => <KnowledgeReviewPage /> },
    // P04 rest.
    DRIFT:              { render: () => <SourceDriftPage /> },
    ERRORS:             { render: () => <ErrorRecordsPage /> },
    JOB_NEW:            { render: () => <JobSetupPage /> },
    EXPLORE:            { render: () => <TableExplorePage /> },
    CONNECT_API:        { render: () => <ApiConnectPage /> },
    // P05 Tài liệu; /data/document/<id> is the viewer.
    DOCUMENTS:          { render: () => <DocumentsPage /> },
    DOCUMENT:           { render: () => <DocumentViewerPage /> },
    DOC_QUALITY:        { render: () => <QualityQueuePage /> },
    DOC_VALIDITY:       { render: () => <ValidityPage /> },
    EXTRACTION_SCHEMAS: { render: () => <ExtractionSchemaPage /> },
    // P06 Chuẩn bị.
    SQL:                { render: () => <SqlPage /> },
    PIPELINES:          { render: () => <PipelinesPage /> },
    PIPELINE:           { render: () => <PipelinePage /> },
    PIPELINE_SAVE:      { render: () => <PipelineSavePage /> },
    DATA_MARTS:         { render: () => <DataMartPage /> },
    DATA_MART:          { render: () => <DataMartPage /> },
    DATA_PRODUCTS:      { render: () => <DataProductsPage /> },
    QUALITY:            { render: () => <QualityRulesPage /> },
    WRITEBACK:          { render: () => <WritebackPage /> },
    LINEAGE:            { render: () => <LineagePage /> },
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
